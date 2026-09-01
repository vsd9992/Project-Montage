"""Import stage: hash + basic metadata for every JPG/JPEG in a source directory.

Resumable: photos already present (by file_hash) are skipped, so re-running after
interruption or adding new files only processes what's new (per _projectIdea.md §22, §26).
"""

import argparse
import hashlib
import sys
from pathlib import Path

from PIL import ExifTags, Image

from db import connect

GPS_TAGS = {v: k for k, v in ExifTags.GPSTAGS.items()}
EXIF_TAGS = {v: k for k, v in ExifTags.TAGS.items()}


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _dms_to_deg(dms, ref) -> float | None:
    try:
        deg = dms[0] + dms[1] / 60.0 + dms[2] / 3600.0
    except (TypeError, IndexError, ZeroDivisionError):
        return None
    if ref in ("S", "W"):
        deg = -deg
    return deg


def read_metadata(path: Path) -> dict:
    meta = {
        "width": None, "height": None, "orientation": None,
        "datetime_orig": None, "camera_make": None, "camera_model": None,
        "gps_lat": None, "gps_lon": None,
    }
    try:
        with Image.open(path) as img:
            meta["width"], meta["height"] = img.size
            exif = img.getexif()
            if not exif:
                return meta
            meta["orientation"] = exif.get(EXIF_TAGS.get("Orientation"))
            meta["camera_make"] = exif.get(EXIF_TAGS.get("Make"))
            meta["camera_model"] = exif.get(EXIF_TAGS.get("Model"))
            meta["datetime_orig"] = exif.get(EXIF_TAGS.get("DateTimeOriginal")) or exif.get(
                EXIF_TAGS.get("DateTime")
            )

            gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo) if hasattr(ExifTags, "IFD") else None
            if gps_ifd:
                lat = _dms_to_deg(gps_ifd.get(GPS_TAGS.get("GPSLatitude")), gps_ifd.get(GPS_TAGS.get("GPSLatitudeRef")))
                lon = _dms_to_deg(gps_ifd.get(GPS_TAGS.get("GPSLongitude")), gps_ifd.get(GPS_TAGS.get("GPSLongitudeRef")))
                meta["gps_lat"], meta["gps_lon"] = lat, lon
    except Exception as e:
        print(f"  warning: could not read metadata for {path.name}: {e}", file=sys.stderr)
    return meta


def import_directory(src_dir: str, db_path: str) -> None:
    src = Path(src_dir)
    files = sorted(
        p for p in src.iterdir() if p.suffix.lower() in (".jpg", ".jpeg") and p.is_file()
    )
    print(f"Found {len(files)} JPG/JPEG files in {src}")

    conn = connect(db_path)
    cur = conn.cursor()

    existing = {row[0] for row in cur.execute("SELECT file_hash FROM photos")}
    print(f"{len(existing)} already imported, skipping those")

    imported = 0
    for i, path in enumerate(files, 1):
        file_hash = sha256_file(path)
        if file_hash in existing:
            continue
        meta = read_metadata(path)
        cur.execute(
            """
            INSERT INTO photos (
                file_hash, path, filename, file_size, width, height, orientation,
                datetime_orig, camera_make, camera_model, gps_lat, gps_lon
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_hash, str(path.resolve()), path.name, path.stat().st_size,
                meta["width"], meta["height"], meta["orientation"],
                meta["datetime_orig"], meta["camera_make"], meta["camera_model"],
                meta["gps_lat"], meta["gps_lon"],
            ),
        )
        imported += 1
        if i % 50 == 0 or i == len(files):
            conn.commit()
            print(f"  {i}/{len(files)} scanned, {imported} newly imported")

    conn.commit()
    total = cur.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    conn.close()
    print(f"Done. {imported} newly imported, {total} total in database.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import JPG/JPEG photos into the project DB")
    parser.add_argument("src_dir", help="Directory of source photos")
    parser.add_argument("--db", default="cache/project.db", help="SQLite DB path")
    args = parser.parse_args()
    import_directory(args.src_dir, args.db)
