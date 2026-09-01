"""Duplicate/burst detection (per _projectIdea.md §5).

Two photos are grouped into the same "moment" if they were taken within a short time
window AND look visually near-identical (perceptual hash distance below a threshold).
This catches wedding-photographer bursts (5-10 near-identical frames in a few seconds)
without needing the expensive SigLIP2/Qwen stages yet.

Ranking within a moment (which shot is "best") is deferred to the quality-scoring stage;
this stage only groups.
"""

import argparse
import sqlite3
from datetime import datetime

import imagehash
from PIL import Image

from db import connect

TIME_WINDOW_SECONDS = 5
HASH_DISTANCE_THRESHOLD = 8  # out of 64 bits for a default 8x8 phash


def parse_exif_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def compute_phashes(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT file_hash, path FROM photos WHERE phash IS NULL"
    ).fetchall()
    print(f"Computing perceptual hashes for {len(rows)} photos without one")
    for i, (file_hash, path) in enumerate(rows, 1):
        try:
            with Image.open(path) as img:
                ph = str(imagehash.phash(img))
        except Exception as e:
            print(f"  warning: phash failed for {path}: {e}")
            continue
        cur.execute("UPDATE photos SET phash = ? WHERE file_hash = ?", (ph, file_hash))
        if i % 100 == 0 or i == len(rows):
            conn.commit()
            print(f"  {i}/{len(rows)} hashed")
    conn.commit()


class UnionFind:
    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def group_moments(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT file_hash, datetime_orig, phash FROM photos WHERE phash IS NOT NULL"
    ).fetchall()

    records = []
    for file_hash, dt_str, phash_str in rows:
        dt = parse_exif_datetime(dt_str)
        if dt is None or phash_str is None:
            continue
        records.append((file_hash, dt, imagehash.hex_to_hash(phash_str)))

    records.sort(key=lambda r: r[1])
    print(f"Clustering {len(records)} photos with valid datetime+phash into moments")

    uf = UnionFind([r[0] for r in records])

    # sliding window: compare each photo to nearby-in-time ones only (records are sorted)
    for i in range(len(records)):
        hi_hash, hi_dt, hi_phash = records[i]
        for j in range(i + 1, len(records)):
            hj_hash, hj_dt, hj_phash = records[j]
            delta = (hj_dt - hi_dt).total_seconds()
            if delta > TIME_WINDOW_SECONDS:
                break  # sorted by time, no need to look further
            if (hi_phash - hj_phash) <= HASH_DISTANCE_THRESHOLD:
                uf.union(hi_hash, hj_hash)

    groups: dict[str, list[str]] = {}
    for file_hash, _, _ in records:
        root = uf.find(file_hash)
        groups.setdefault(root, []).append(file_hash)

    moment_id = 0
    multi_photo_moments = 0
    for root, members in groups.items():
        if len(members) < 2:
            continue  # singletons get no moment_group_id (nothing to dedupe)
        moment_id += 1
        multi_photo_moments += 1
        for file_hash in members:
            cur.execute(
                "UPDATE photos SET moment_group_id = ? WHERE file_hash = ?",
                (moment_id, file_hash),
            )
    conn.commit()

    total = len(records)
    grouped = sum(len(m) for m in groups.values() if len(m) >= 2)
    print(
        f"Found {multi_photo_moments} multi-photo moments covering {grouped}/{total} photos "
        f"({total - grouped} photos are singletons, no burst detected)"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Duplicate/burst detection stage")
    parser.add_argument("--db", default="cache/project.db", help="SQLite DB path")
    args = parser.parse_args()
    conn = connect(args.db)
    compute_phashes(conn)
    group_moments(conn)
    conn.close()
