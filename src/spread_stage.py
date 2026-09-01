"""Spread data model + layout grammar (idea §12/§14) — Phase 2 first milestone.

Turns the Phase 1 storyboard (event_tag-grouped shortlist) into structured spread plans:
each spread gets a layout template and a hero/supporting photo assignment. This is the
"AI decides structure" half of idea §2 -- no pixels are touched here, no rendering, no
cropping math. That's deferred to later Phase 2 work.

Orientation-awareness (Phase 1 review gap #1): EXIF orientation values 5-8 mean the image
is stored rotated 90 degrees relative to its logical display orientation, so width/height
must be swapped before classifying landscape vs portrait.
"""

import argparse
import json
from collections import defaultdict

from db import connect
from shortlist_stage import build_shortlist, build_storyboard, compute_selection_scores

# EXIF orientation tags where the stored width/height are swapped relative to display.
_ROTATED_90_TAGS = {5, 6, 7, 8}


def effective_orientation(width: int | None, height: int | None, exif_orientation: int | None) -> str:
    """Classify a photo as 'landscape', 'portrait', or 'square', correcting for EXIF rotation."""
    if not width or not height:
        return "landscape"  # unknown -- assume the common case rather than block layout choice
    if exif_orientation in _ROTATED_90_TAGS:
        width, height = height, width
    if width == height:
        return "square"
    return "landscape" if width > height else "portrait"


# Layout grammar (idea §12): each template names its slot roles and each slot's preferred
# orientation ("any" = no preference). The renderer (later work) will use these roles to
# place frames; here we only choose which template fits a chunk and which photo fills
# which slot.
LAYOUTS = {
    "hero": {
        "slots": ["hero"],
        "preferred_orientation": {"hero": "any"},
    },
    "duo": {
        "slots": ["hero", "support_1"],
        "preferred_orientation": {"hero": "any", "support_1": "any"},
    },
    "hero_plus_two": {
        "slots": ["hero", "support_1", "support_2"],
        "preferred_orientation": {"hero": "landscape", "support_1": "any", "support_2": "any"},
    },
    "hero_plus_three": {
        "slots": ["hero", "support_1", "support_2", "support_3"],
        "preferred_orientation": {"hero": "landscape", "support_1": "any", "support_2": "any", "support_3": "any"},
    },
    "documentary_grid": {
        "slots": ["support_1", "support_2", "support_3", "support_4", "support_5"],
        "preferred_orientation": {s: "any" for s in
                                   ["support_1", "support_2", "support_3", "support_4", "support_5"]},
    },
}

# Photo count -> candidate layout names, tried in order; first is preferred when the hero
# photo's orientation matches that layout's hero slot preference.
_LAYOUT_BY_COUNT = {
    1: ["hero"],
    2: ["duo"],
    3: ["hero_plus_two"],
    4: ["hero_plus_three"],
    5: ["documentary_grid"],
}

MAX_PHOTOS_PER_SPREAD = 5


def choose_layout(chunk_orientations: list[str]) -> str:
    """Pick a layout template name for a chunk, preferring one whose hero slot orientation
    preference matches the actual hero photo (chunk_orientations[0])."""
    n = len(chunk_orientations)
    candidates = _LAYOUT_BY_COUNT.get(n, ["documentary_grid"])
    hero_orientation = chunk_orientations[0]
    for name in candidates:
        pref = LAYOUTS[name]["preferred_orientation"].get("hero", "any")
        if pref in ("any", hero_orientation):
            return name
    return candidates[0]


def chunk_section(photos: list[tuple]) -> list[list[tuple]]:
    """Split a storyboard section's photos into spread-sized chunks (idea keeps ~1-5 photos
    per spread; the last chunk of a section may be smaller)."""
    return [photos[i:i + MAX_PHOTOS_PER_SPREAD] for i in range(0, len(photos), MAX_PHOTOS_PER_SPREAD)]


def build_spread_plan(event_tag: str, spread_number: int, chunk: list[tuple], orientations: dict[str, str]) -> dict:
    """chunk rows are (filename, event_tag, dt, quality, album_value, sel_score), already
    sorted by selection_score descending within the section by the caller."""
    ordered = sorted(chunk, key=lambda r: r[5], reverse=True)
    chunk_orientations = [orientations[r[0]] for r in ordered]
    layout = choose_layout(chunk_orientations)
    slots = LAYOUTS[layout]["slots"]

    assignment = {}
    for slot, row in zip(slots, ordered):
        filename = row[0]
        assignment[slot] = {"filename": filename, "orientation": orientations[filename],
                             "selection_score": round(row[5], 1)}

    return {
        "spread": spread_number,
        "event": event_tag,
        "layout": layout,
        "hero": assignment.get("hero", {}).get("filename"),
        "supporting": [v["filename"] for k, v in assignment.items() if k != "hero"],
        "slots": assignment,
    }


def build_spreads(storyboard: list[tuple[str, list[tuple]]], orientations: dict[str, str]) -> list[dict]:
    spreads = []
    spread_number = 1
    for event_tag, photos in storyboard:
        for chunk in chunk_section(photos):
            spreads.append(build_spread_plan(event_tag, spread_number, chunk, orientations))
            spread_number += 1
    return spreads


def load_orientations(conn, filenames: list[str]) -> dict[str, str]:
    cur = conn.cursor()
    result = {}
    for filename in filenames:
        row = cur.execute(
            "SELECT width, height, orientation FROM photos WHERE filename = ?", (filename,)
        ).fetchone()
        result[filename] = effective_orientation(*row) if row else "landscape"
    return result


def run(db_path: str, target_count: int, out_path: str) -> None:
    conn = connect(db_path)
    compute_selection_scores(conn)
    shortlist = build_shortlist(conn, target_count)
    storyboard = build_storyboard(shortlist)

    all_filenames = [row[0] for _, photos in storyboard for row in photos]
    orientations = load_orientations(conn, all_filenames)
    conn.close()

    spreads = build_spreads(storyboard, orientations)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(spreads, f, indent=2)

    layout_counts = defaultdict(int)
    for s in spreads:
        layout_counts[s["layout"]] += 1
    print(f"Built {len(spreads)} spreads from {len(all_filenames)} photos across {len(storyboard)} sections")
    print("Layout distribution:", dict(layout_counts))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spread data model + layout grammar stage")
    parser.add_argument("--db", default="cache/project_full.db")
    parser.add_argument("--target-count", type=int, default=180)
    parser.add_argument("--out", default="exports/spreads.json")
    args = parser.parse_args()
    run(args.db, args.target_count, args.out)
