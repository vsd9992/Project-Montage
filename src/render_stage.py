"""High-res spread renderer (idea §12 "the deterministic renderer converts this into the
actual page", idea §19 print production). Deterministic, no AI here: it places each
photo's pre-computed crop (crop_stage.py) into its slot's rectangle (layout_geometry.py)
on a canvas, applies a design style's background/mat/caption typography (style_stage.py,
idea §13), and writes a JPEG.

Not attempted here: bleed/safe-zone preflight checks + PDF export (idea §19, see
export_pdf.py), multi-size support (canvas is fixed at layout_geometry.CANVAS_W/H) --
later Phase 2 work once a print-size selection UI exists.
"""

import argparse
import json

from PIL import Image, ImageDraw, ImageFont, ImageOps

from db import connect
from layout_geometry import DEFAULT_ORIENTATION, DEFAULT_SIZE, get_geometry
from style_stage import DEFAULT_STYLE, get_style

JPEG_QUALITY = 92


def _load_font(font_path: str | None, size: int) -> ImageFont.FreeTypeFont:
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def render_spread(conn, spread: dict, crops_by_slot: dict, geometry, style_name: str = DEFAULT_STYLE) -> Image.Image:
    style = get_style(style_name)
    canvas = Image.new("RGB", (geometry.canvas_w, geometry.canvas_h), style["background_color"])
    rects = geometry.layout_rects[spread["layout"]]

    for slot, (rx, ry, rw, rh) in rects.items():
        crop_info = crops_by_slot.get(slot)
        if crop_info is None or "error" in crop_info:
            continue
        row = conn.execute(
            "SELECT path FROM photos WHERE filename = ?", (crop_info["filename"],)
        ).fetchone()
        if not row:
            continue
        x1, y1, x2, y2 = crop_info["crop_box"]
        with Image.open(row[0]) as img:
            # crop_box is in EXIF-corrected "display" space (matching face_stage's
            # cv2.imread, which auto-rotates) -- exif_transpose puts these pixels in the
            # same space before cropping, or the box would land on the wrong region.
            img = ImageOps.exif_transpose(img).convert("RGB")
            cropped = img.crop((round(x1), round(y1), round(x2), round(y2)))

            # "Contain" fit, not "cover"/crop-to-fill (changed 2026-09-01 per user report:
            # crop-to-fill was cutting off faces whenever a photo's aspect didn't match its
            # slot exactly). The safety-margin crop from crop_stage.py is scaled down
            # uniformly to fit inside the slot rect and centred -- the whole photo (and any
            # face crop_stage protected) is always fully visible; any leftover space within
            # the rect shows the background/mat color instead of being cropped away.
            cw, ch = cropped.size
            scale = min(rw / cw, rh / ch) if cw and ch else 1.0
            fit_w, fit_h = round(cw * scale), round(ch * scale)
            resized = cropped.resize((fit_w, fit_h), Image.LANCZOS)
            paste_x = round(rx + (rw - fit_w) / 2)
            paste_y = round(ry + (rh - fit_h) / 2)

            if style["mat_width"]:
                mw = style["mat_width"]
                mat_box = (round(rx - mw), round(ry - mw), round(rw + 2 * mw), round(rh + 2 * mw))
                ImageDraw.Draw(canvas).rectangle(
                    [mat_box[0], mat_box[1], mat_box[0] + mat_box[2], mat_box[1] + mat_box[3]],
                    fill=style["mat_color"],
                )
            # Fill the slot rect itself with the background color first so any leftover
            # space around a contain-fit photo reads as intentional matting, not a hole.
            ImageDraw.Draw(canvas).rectangle(
                [round(rx), round(ry), round(rx + rw), round(ry + rh)], fill=style["background_color"],
            )
            canvas.paste(resized, (paste_x, paste_y))

    caption = spread.get("event", "").replace("_", " ").title()
    if caption:
        font = _load_font(style["caption_font"], style["caption_size"])
        draw = ImageDraw.Draw(canvas)
        caption_y = geometry.canvas_h - geometry.margin // 2
        if style["caption_align"] == "center":
            bbox = draw.textbbox((0, 0), caption, font=font)
            x = (geometry.canvas_w - (bbox[2] - bbox[0])) / 2
        else:
            x = geometry.margin
        draw.text((x, caption_y - style["caption_size"]), caption, fill=style["caption_color"], font=font)

    return canvas


def run(db_path: str, spreads_path: str, crops_path: str, out_dir: str, limit: int | None,
        style_name: str = DEFAULT_STYLE, size: str = DEFAULT_SIZE,
        orientation: str = DEFAULT_ORIENTATION) -> None:
    with open(spreads_path, encoding="utf-8") as f:
        spreads = {s["spread"]: s for s in json.load(f)}
    with open(crops_path, encoding="utf-8") as f:
        crops = {c["spread"]: c["crops"] for c in json.load(f)}

    geometry = get_geometry(size, orientation)
    conn = connect(db_path)
    import os
    os.makedirs(out_dir, exist_ok=True)

    spread_numbers = sorted(spreads.keys())
    if limit:
        spread_numbers = spread_numbers[:limit]

    for n in spread_numbers:
        spread = spreads[n]
        canvas = render_spread(conn, spread, crops.get(n, {}), geometry, style_name)
        out_path = os.path.join(out_dir, f"spread_{n:03d}_{spread['layout']}.jpg")
        canvas.save(out_path, "JPEG", quality=JPEG_QUALITY)
        print(f"Rendered spread {n} ({spread['layout']}, {spread['event']}) -> {out_path}")

    conn.close()
    print(f"\nRendered {len(spread_numbers)} spreads to {out_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render spread plans + crops into page images")
    parser.add_argument("--db", default="cache/project_full.db")
    parser.add_argument("--spreads", default="exports/spreads.json")
    parser.add_argument("--crops", default="exports/crops.json")
    parser.add_argument("--out-dir", default="exports/rendered_spreads")
    parser.add_argument("--limit", type=int, default=None, help="Render only the first N spreads")
    parser.add_argument("--style", default=DEFAULT_STYLE, help="Design style name (see style_stage.STYLES)")
    parser.add_argument("--size", default=DEFAULT_SIZE, help="Print size (see layout_geometry.PRINT_SIZES)")
    parser.add_argument("--orientation", default=DEFAULT_ORIENTATION, choices=["landscape", "portrait"])
    args = parser.parse_args()
    run(args.db, args.spreads, args.crops, args.out_dir, args.limit, args.style, args.size, args.orientation)
