"""Shortlist + storyboard: the Phase 1 success-criterion stage (idea §10, §11).

Combines technical quality_score with Qwen's album_value into a single selection_score,
picks the top N candidates, then sequences the selection into a simple chronological
storyboard grouped by event_tag.

Not attempted here: people-priority weighting (idea §7 "prioritize bride and groom"),
section reordering by the user (idea §11's drag-to-reorder) -- both need a UI/CLI the
user drives, out of scope for this automated stage.
"""

import argparse
from collections import defaultdict

from db import connect


def compute_selection_scores(conn) -> None:
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT file_hash, quality_score, album_value FROM photos "
        "WHERE quality_score IS NOT NULL AND album_value IS NOT NULL"
    ).fetchall()
    for file_hash, quality, album_value in rows:
        # quality_score is already 0-100ish; album_value is 1-10 -> scale to 0-100
        score = quality * 0.4 + (album_value * 10) * 0.6
        cur.execute("UPDATE photos SET selection_score = ? WHERE file_hash = ?", (score, file_hash))
    conn.commit()
    print(f"Computed selection_score for {len(rows)} candidates")


def build_shortlist(conn, target_count: int) -> list[tuple]:
    """Proportional quota by event_tag: each event gets a shortlist share proportional to
    how much of the candidate pool it represents, so a long portrait session doesn't crowd
    out brief-but-important events (idea §7/§12/§14 balance concern)."""
    cur = conn.cursor()
    cols = "filename, event_tag, datetime_orig, quality_score, album_value, selection_score"

    all_candidates = cur.execute(
        f"SELECT {cols} FROM photos WHERE selection_score IS NOT NULL "
        "ORDER BY selection_score DESC"
    ).fetchall()
    total = len(all_candidates)

    by_tag = defaultdict(list)
    for row in all_candidates:
        by_tag[row[1] or "unclassified"].append(row)

    quotas = {tag: max(1, round(target_count * len(rows) / total)) for tag, rows in by_tag.items()}

    selected, selected_keys = [], set()
    for tag, rows in by_tag.items():
        for row in rows[: quotas[tag]]:
            selected.append(row)
            selected_keys.add(row[0])

    # rounding can over/under-shoot target_count -- correct against the global ranking
    if len(selected) > target_count:
        selected.sort(key=lambda r: r[5], reverse=True)
        selected = selected[:target_count]
    elif len(selected) < target_count:
        for row in all_candidates:
            if len(selected) >= target_count:
                break
            if row[0] not in selected_keys:
                selected.append(row)
                selected_keys.add(row[0])

    selected.sort(key=lambda r: r[5], reverse=True)
    return selected


def build_storyboard(shortlist: list[tuple]) -> list[tuple[str, list[tuple]]]:
    """Group by event_tag, order groups by each group's earliest datetime, order photos
    within a group chronologically."""
    groups = defaultdict(list)
    for row in shortlist:
        groups[row[1] or "unclassified"].append(row)

    for tag in groups:
        groups[tag].sort(key=lambda r: r[2] or "")

    ordered_tags = sorted(groups.keys(), key=lambda t: min(r[2] or "9999" for r in groups[t]))
    return [(tag, groups[tag]) for tag in ordered_tags]


def run(db_path: str, target_count: int) -> None:
    conn = connect(db_path)
    compute_selection_scores(conn)
    shortlist = build_shortlist(conn, target_count)
    conn.close()

    print(f"\nShortlist: {len(shortlist)} photos selected (target {target_count})\n")

    storyboard = build_storyboard(shortlist)
    print(f"Storyboard: {len(storyboard)} sections\n")
    for tag, photos in storyboard:
        first_dt = photos[0][2]
        print(f"=== {tag} ({len(photos)} photos, starting {first_dt}) ===")
        for filename, _, dt, quality, album_value, sel_score in photos[:3]:
            print(f"  {filename}  q={quality:.0f} av={album_value:.0f} sel={sel_score:.1f}  {dt}")
        if len(photos) > 3:
            print(f"  ... and {len(photos) - 3} more")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Shortlist + storyboard stage")
    parser.add_argument("--db", default="cache/project.db", help="SQLite DB path")
    parser.add_argument("--target-count", type=int, default=180,
                         help="Approx photos for a 30-spread album (idea's own ~5.8 photos/spread ratio)")
    args = parser.parse_args()
    run(args.db, args.target_count)
