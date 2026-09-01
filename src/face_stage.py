"""Face detection for cropping (idea §7 people recognition, §15 "maintain face bounding
boxes"). Scoped narrowly for Phase 2: only detects faces on photos actually assigned to a
spread (from spread_stage's output), not the full import -- clustering into named people
(idea §7's Person 01/02/...) is separate, deferred work; this stage only exists to feed
crop_stage.py subject regions.

Resumable: skips file_hashes that already have a row in `faces`.
"""

import argparse
import json

import numpy as np
from insightface.app import FaceAnalysis

from db import connect

_app = None


def get_app() -> FaceAnalysis:
    global _app
    if _app is None:
        _app = FaceAnalysis(
            name="buffalo_l",
            root="models/insightface",
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        _app.prepare(ctx_id=0, det_size=(640, 640))
    return _app


def detect_faces_for_photo(app: FaceAnalysis, path: str) -> list[dict]:
    import cv2

    img = cv2.imread(path)
    if img is None:
        return []
    faces = app.get(img)
    return [
        {
            "bbox": tuple(float(v) for v in f.bbox),  # x1, y1, x2, y2
            "embedding": f.normed_embedding.astype(np.float32).tobytes(),
        }
        for f in faces
    ]


def run(db_path: str, spreads_path: str) -> None:
    with open(spreads_path, encoding="utf-8") as f:
        spreads = json.load(f)
    filenames = sorted({v["filename"] for s in spreads for v in s["slots"].values()})

    conn = connect(db_path)
    cur = conn.cursor()

    already_done = {
        row[0] for row in cur.execute(
            "SELECT DISTINCT file_hash FROM photos WHERE file_hash IN "
            "(SELECT file_hash FROM faces)"
        ).fetchall()
    }

    app = get_app()
    processed = detected = 0
    for filename in filenames:
        row = cur.execute(
            "SELECT file_hash, path FROM photos WHERE filename = ?", (filename,)
        ).fetchone()
        if not row:
            continue
        file_hash, path = row
        if file_hash in already_done:
            continue

        faces = detect_faces_for_photo(app, path)
        for f in faces:
            x1, y1, x2, y2 = f["bbox"]
            cur.execute(
                "INSERT INTO faces (file_hash, bbox_x1, bbox_y1, bbox_x2, bbox_y2, embedding) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (file_hash, x1, y1, x2, y2, f["embedding"]),
            )
        processed += 1
        detected += len(faces)
        if processed % 20 == 0:
            conn.commit()
            print(f"  {processed}/{len(filenames)} photos processed, {detected} faces so far")

    conn.commit()
    conn.close()
    print(f"Done: {processed} photos processed ({len(filenames) - processed} already had faces), "
          f"{detected} faces detected")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Face detection for spread-assigned photos")
    parser.add_argument("--db", default="cache/project_full.db")
    parser.add_argument("--spreads", default="exports/spreads.json")
    args = parser.parse_args()
    run(args.db, args.spreads)
