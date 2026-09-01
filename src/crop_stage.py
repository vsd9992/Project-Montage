"""Intelligent cropping (idea §15): compute a safe crop region per photo, per spread slot,
so the renderer (later work) never blindly centre-crops a photo that would cut off a face.

Subject region priority:
1. Union of detected face bounding boxes (from `faces`, populated by face_stage.py) --
   the strongest signal idea §15 calls out ("if a photograph contains two faces... the
   renderer cannot blindly centre-crop it").
2. Fallback saliency region when no faces are present: a gradient-magnitude heuristic
   (Sobel edge energy) bounding the highest-energy area of the frame. Not a proper
   saliency model (cv2.saliency needs opencv-contrib, not part of this project's verified
   OpenCV install) -- good enough to avoid dumb centre-crops on faceless photos (decor,
   venue, landscape shots).

Per-slot target aspect ratios come from `layout_geometry.py` -- the same rectangles
render_stage.py places photos into, so a crop computed here always fits its slot exactly
with no squash/stretch at render time.
"""

import argparse
import json

import cv2
import numpy as np

from db import connect
from layout_geometry import DEFAULT_SIZE, get_geometry

FACE_MARGIN_FRAC = 0.35  # padding added around the subject bbox before fitting aspect ratio
SALIENCY_ENERGY_FRACTION = 0.6  # fraction of total gradient energy the saliency bbox must contain


def union_bbox(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[2] for b in boxes)
    y2 = max(b[3] for b in boxes)
    return x1, y1, x2, y2


def saliency_bbox(path: str, max_dim: int = 512) -> tuple[float, float, float, float] | None:
    """Gradient-energy fallback subject region for photos with no detected faces."""
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h, w = img.shape
    scale = max_dim / max(h, w)
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3)
    energy = cv2.magnitude(gx, gy)
    energy = cv2.GaussianBlur(energy, (15, 15), 0)

    total = energy.sum()
    if total <= 0:
        return None

    # Find the smallest axis-aligned box (grown outward from the peak) containing
    # SALIENCY_ENERGY_FRACTION of total gradient energy.
    peak_y, peak_x = np.unravel_index(np.argmax(energy), energy.shape)
    eh, ew = energy.shape
    x1 = x2 = peak_x
    y1 = y2 = peak_y
    contained = energy[y1:y2 + 1, x1:x2 + 1].sum()
    step = max(1, min(eh, ew) // 40)
    while contained < total * SALIENCY_ENERGY_FRACTION and (x1 > 0 or y1 > 0 or x2 < ew - 1 or y2 < eh - 1):
        x1 = max(0, x1 - step)
        y1 = max(0, y1 - step)
        x2 = min(ew - 1, x2 + step)
        y2 = min(eh - 1, y2 + step)
        contained = energy[y1:y2 + 1, x1:x2 + 1].sum()

    inv_scale = 1.0 / scale if scale < 1.0 else 1.0
    return x1 * inv_scale, y1 * inv_scale, (x2 + 1) * inv_scale, (y2 + 1) * inv_scale


def fit_to_aspect(box, img_w: int, img_h: int, target_aspect: float,
                   margin_frac: float = 0.0) -> tuple[float, float, float, float]:
    """Expand `box` by margin_frac, then grow it (never shrink) to hit target_aspect,
    keeping it centred on the original box, then shift (not squash) to stay in bounds."""
    x1, y1, x2, y2 = box
    bw, by = x2 - x1, y2 - y1
    x1 -= bw * margin_frac
    x2 += bw * margin_frac
    y1 -= by * margin_frac
    y2 += by * margin_frac
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(float(img_w), x2), min(float(img_h), y2)

    box_w, box_h = x2 - x1, y2 - y1
    if box_w <= 0 or box_h <= 0:
        return 0.0, 0.0, float(img_w), float(img_h)
    cur_aspect = box_w / box_h

    if cur_aspect < target_aspect:
        new_w = min(box_h * target_aspect, img_w)
        cx = (x1 + x2) / 2
        x1, x2 = cx - new_w / 2, cx + new_w / 2
    else:
        new_h = min(box_w / target_aspect, img_h)
        cy = (y1 + y2) / 2
        y1, y2 = cy - new_h / 2, cy + new_h / 2

    # Shift back into bounds (don't resize -- keep the exact target aspect ratio).
    if x1 < 0:
        x2 -= x1
        x1 = 0.0
    if x2 > img_w:
        x1 -= (x2 - img_w)
        x2 = float(img_w)
    if y1 < 0:
        y2 -= y1
        y1 = 0.0
    if y2 > img_h:
        y1 -= (y2 - img_h)
        y2 = float(img_h)

    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(float(img_w), x2), min(float(img_h), y2)
    return x1, y1, x2, y2


_ROTATED_90_TAGS = {5, 6, 7, 8}  # EXIF orientation tags where display dims are swapped


def compute_crop(conn, filename: str, target_aspect: float) -> dict:
    cur = conn.cursor()
    row = cur.execute(
        "SELECT file_hash, path, width, height, orientation FROM photos WHERE filename = ?", (filename,)
    ).fetchone()
    if not row:
        return {"filename": filename, "error": "not found in db"}
    file_hash, path, img_w, img_h, exif_orientation = row
    # face_stage.py's cv2.imread and render_stage.py's PIL load both auto-apply EXIF
    # rotation, so face bboxes (and the final rendered pixels) are in "display" space --
    # width/height must match that, not the DB's as-stored raw dimensions.
    if exif_orientation in _ROTATED_90_TAGS:
        img_w, img_h = img_h, img_w

    faces = cur.execute(
        "SELECT bbox_x1, bbox_y1, bbox_x2, bbox_y2 FROM faces WHERE file_hash = ?", (file_hash,)
    ).fetchall()

    if faces:
        subject_box = union_bbox(faces)
        source = "faces"
        margin = FACE_MARGIN_FRAC
    else:
        subject_box = saliency_bbox(path)
        source = "saliency"
        margin = 0.1
        if subject_box is None:
            subject_box = (0.0, 0.0, float(img_w), float(img_h))
            source = "full_image"

    crop = fit_to_aspect(subject_box, img_w, img_h, target_aspect, margin_frac=margin)
    return {
        "filename": filename,
        "subject_source": source,
        "num_faces": len(faces),
        "target_aspect": target_aspect,
        "crop_box": [round(v, 1) for v in crop],
        "image_size": [img_w, img_h],
    }


def run(db_path: str, spreads_path: str, out_path: str, size: str = DEFAULT_SIZE) -> None:
    with open(spreads_path, encoding="utf-8") as f:
        spreads = json.load(f)

    geometry = get_geometry(size)
    conn = connect(db_path)
    results = []
    for spread in spreads:
        aspects = geometry.slot_aspect_ratios(spread["layout"])
        crops = {}
        for slot, info in spread["slots"].items():
            target_aspect = aspects.get(slot, 1.33)
            crops[slot] = compute_crop(conn, info["filename"], target_aspect)
        results.append({"spread": spread["spread"], "event": spread["event"],
                         "layout": spread["layout"], "crops": crops})
    conn.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    face_based = sum(1 for s in results for c in s["crops"].values() if c.get("subject_source") == "faces")
    saliency_based = sum(1 for s in results for c in s["crops"].values() if c.get("subject_source") == "saliency")
    full_image = sum(1 for s in results for c in s["crops"].values() if c.get("subject_source") == "full_image")
    print(f"Computed crops for {sum(len(s['crops']) for s in results)} slot assignments across {len(results)} spreads")
    print(f"  face-based: {face_based}, saliency-based: {saliency_based}, full-image fallback: {full_image}")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Intelligent cropping stage")
    parser.add_argument("--db", default="cache/project_full.db")
    parser.add_argument("--spreads", default="exports/spreads.json")
    parser.add_argument("--out", default="exports/crops.json")
    parser.add_argument("--size", default=DEFAULT_SIZE, help="Print size (see layout_geometry.PRINT_SIZES)")
    args = parser.parse_args()
    run(args.db, args.spreads, args.out, args.size)
