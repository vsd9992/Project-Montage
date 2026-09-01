"""Technical quality scoring: sharpness + exposure (per _projectIdea.md §6).

This is only the "technical" dimension (focus/blur/exposure) -- composition, human
(eyes/expression), and "album value" dimensions are explicitly deferred to the Qwen3-VL
stage, which is expensive and should only run on already-plausible candidates.

Resumable: only photos with sharpness_score IS NULL are (re)processed.
"""

import argparse
import sqlite3

import cv2
import numpy as np

from db import connect

RESIZE_MAX_DIM = 1024  # fixed size so Laplacian variance is comparable across photos
CLIP_LOW = 5      # pixel value considered "black"
CLIP_HIGH = 250   # pixel value considered "blown out"


def _load_resized_gray(path: str) -> np.ndarray | None:
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h, w = img.shape
    scale = RESIZE_MAX_DIM / max(h, w)
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img


def compute_sharpness(gray: np.ndarray) -> float:
    return cv2.Laplacian(gray, cv2.CV_64F).var()


def compute_exposure_score(gray: np.ndarray) -> float:
    """1.0 = well exposed, lower = more clipped (over/under-exposed) pixels."""
    total = gray.size
    clipped_low = np.count_nonzero(gray <= CLIP_LOW)
    clipped_high = np.count_nonzero(gray >= CLIP_HIGH)
    clipped_frac = (clipped_low + clipped_high) / total
    return max(0.0, 1.0 - clipped_frac * 5)  # 20% clipped -> score 0


def run(db_path: str) -> None:
    conn = connect(db_path)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT file_hash, path FROM photos WHERE sharpness_score IS NULL"
    ).fetchall()
    print(f"Scoring {len(rows)} photos without a sharpness_score")

    for i, (file_hash, path) in enumerate(rows, 1):
        gray = _load_resized_gray(path)
        if gray is None:
            print(f"  warning: could not read {path}")
            continue
        sharpness = compute_sharpness(gray)
        exposure = compute_exposure_score(gray)
        cur.execute(
            "UPDATE photos SET sharpness_score = ?, exposure_score = ? WHERE file_hash = ?",
            (sharpness, exposure, file_hash),
        )
        if i % 100 == 0 or i == len(rows):
            conn.commit()
            print(f"  {i}/{len(rows)} scored")
    conn.commit()

    # normalize sharpness to 0-100 via percentile rank, combine with exposure into quality_score
    all_rows = cur.execute(
        "SELECT file_hash, sharpness_score, exposure_score FROM photos "
        "WHERE sharpness_score IS NOT NULL"
    ).fetchall()
    sharpness_vals = sorted(r[1] for r in all_rows)
    n = len(sharpness_vals)

    def percentile_rank(v: float) -> float:
        import bisect
        idx = bisect.bisect_left(sharpness_vals, v)
        return 100.0 * idx / max(1, n - 1)

    for file_hash, sharpness, exposure in all_rows:
        sharpness_pct = percentile_rank(sharpness)
        quality = sharpness_pct * 0.7 + exposure * 100 * 0.3
        cur.execute(
            "UPDATE photos SET quality_score = ? WHERE file_hash = ?", (quality, file_hash)
        )
    conn.commit()
    conn.close()
    print(f"Done. quality_score computed for {n} photos (sharpness percentile x0.7 + exposure x0.3).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Technical quality scoring stage")
    parser.add_argument("--db", default="cache/project.db", help="SQLite DB path")
    args = parser.parse_args()
    run(args.db)
