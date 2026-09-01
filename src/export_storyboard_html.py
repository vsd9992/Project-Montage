"""Generate a local, offline HTML gallery of the current storyboard for visual review.

Not published anywhere -- writes a single self-contained HTML file with embedded
thumbnails (base64) so it opens directly from disk. These are real personal photos; they
should not be uploaded to any third-party service.
"""

import argparse
import base64
import html
import io

from PIL import Image

from db import connect
from shortlist_stage import build_shortlist, build_storyboard, compute_selection_scores

THUMB_MAX_DIM = 480


def thumb_data_uri(path: str) -> str:
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        scale = THUMB_MAX_DIM / max(w, h)
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


def build_html(storyboard, path_lookup) -> str:
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>Storyboard Review</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;background:#1a1a1a;color:#eee;margin:0;padding:24px;}",
        "h1{font-size:20px;} h2{font-size:16px;color:#ffd479;border-bottom:1px solid #444;"
        "padding-bottom:4px;margin-top:36px;}",
        ".meta{color:#999;font-size:12px;}",
        ".grid{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px;}",
        ".card{width:220px;background:#242424;border-radius:6px;overflow:hidden;}",
        ".card img{width:100%;display:block;}",
        ".card .info{padding:6px 8px;font-size:11px;color:#bbb;}",
        ".card .fn{color:#eee;font-weight:600;}",
        "</style></head><body>",
        f"<h1>Storyboard Review — {sum(len(p) for _, p in storyboard)} photos, "
        f"{len(storyboard)} sections</h1>",
        "<div class='meta'>Generated locally, not uploaded anywhere. Order = chronological "
        "by section, then by timestamp within section.</div>",
    ]
    for tag, photos in storyboard:
        parts.append(f"<h2>{html.escape(tag)} ({len(photos)} photos)</h2><div class='grid'>")
        for filename, event_tag, dt, quality, album_value, sel_score in photos:
            uri = thumb_data_uri(path_lookup[filename])
            parts.append(
                "<div class='card'>"
                f"<img src='{uri}' loading='lazy'>"
                "<div class='info'>"
                f"<div class='fn'>{html.escape(filename)}</div>"
                f"q={quality:.0f} av={album_value:.0f} sel={sel_score:.1f}<br>{html.escape(dt or '')}"
                "</div></div>"
            )
        parts.append("</div>")
    parts.append("</body></html>")
    return "".join(parts)


def run(db_path: str, target_count: int, out_path: str) -> None:
    conn = connect(db_path)
    compute_selection_scores(conn)
    shortlist = build_shortlist(conn, target_count)
    storyboard = build_storyboard(shortlist)

    cur = conn.cursor()
    path_lookup = {}
    for filename, *_ in shortlist:
        row = cur.execute("SELECT path FROM photos WHERE filename = ?", (filename,)).fetchone()
        if row:
            path_lookup[filename] = row[0]
    conn.close()

    print(f"Rendering thumbnails for {len(shortlist)} photos...")
    doc = build_html(storyboard, path_lookup)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export storyboard as a local HTML gallery")
    parser.add_argument("--db", default="cache/project.db")
    parser.add_argument("--target-count", type=int, default=180)
    parser.add_argument("--out", default="exports/storyboard_review.html")
    args = parser.parse_args()
    run(args.db, args.target_count, args.out)
