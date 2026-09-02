"""SQLite schema and connection helper for the album project cache.

Design note (per _projectIdea.md §4, §22): every expensive analysis result is keyed by
file hash so re-opening a project or adding new files never re-runs existing analysis.
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    file_hash       TEXT PRIMARY KEY,      -- sha256 of file bytes
    path            TEXT NOT NULL,         -- absolute source path (read-only reference)
    filename        TEXT NOT NULL,
    file_size       INTEGER NOT NULL,
    width           INTEGER,
    height          INTEGER,
    orientation     INTEGER,
    datetime_orig   TEXT,                  -- EXIF DateTimeOriginal, ISO-ish string as found
    camera_make     TEXT,
    camera_model    TEXT,
    gps_lat         REAL,
    gps_lon         REAL,
    imported_at     TEXT NOT NULL DEFAULT (datetime('now')),

    -- filled in by later pipeline stages; NULL until computed
    phash               TEXT,              -- perceptual hash (hex), for duplicate/burst detection
    sharpness_score     REAL,
    exposure_score      REAL,
    quality_score       REAL,
    embedding           BLOB,              -- SigLIP2 embedding, float32 bytes
    duplicate_group_id  INTEGER,
    moment_group_id     INTEGER,
    ai_description      TEXT,
    event_tag           TEXT,              -- Qwen3-VL scene/event classification, free text
    album_value         REAL,              -- Qwen3-VL judged album-worthiness, 1-10
    is_qwen_candidate   INTEGER DEFAULT 0, -- 1 if selected to run through Qwen (best-per-moment + singles)
    selection_score     REAL,
    is_duplicate        INTEGER NOT NULL DEFAULT 0  -- user-marked duplicate; excluded from swap candidates
);

CREATE INDEX IF NOT EXISTS idx_photos_datetime ON photos(datetime_orig);
CREATE INDEX IF NOT EXISTS idx_photos_duplicate_group ON photos(duplicate_group_id);
CREATE INDEX IF NOT EXISTS idx_photos_moment_group ON photos(moment_group_id);

CREATE TABLE IF NOT EXISTS faces (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash       TEXT NOT NULL REFERENCES photos(file_hash),
    bbox_x1         REAL NOT NULL,
    bbox_y1         REAL NOT NULL,
    bbox_x2         REAL NOT NULL,
    bbox_y2         REAL NOT NULL,
    embedding       BLOB NOT NULL,         -- InsightFace 512-dim embedding, float32 bytes
    person_id       INTEGER                -- assigned after clustering; NULL until then
);

CREATE INDEX IF NOT EXISTS idx_faces_file_hash ON faces(file_hash);
CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces(person_id);

CREATE TABLE IF NOT EXISTS people (
    person_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT,                  -- user-assigned, e.g. "Bride"; NULL until set
    ignored         INTEGER NOT NULL DEFAULT 0  -- 1 = not a priority person (e.g. random guest)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a DB already existed (SQLite has no ADD COLUMN IF NOT EXISTS)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(photos)")}
    if "phash" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN phash TEXT")
        conn.commit()
    if "event_tag" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN event_tag TEXT")
        conn.execute("ALTER TABLE photos ADD COLUMN album_value REAL")
        conn.execute("ALTER TABLE photos ADD COLUMN is_qwen_candidate INTEGER DEFAULT 0")
        conn.commit()

    if "is_duplicate" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN is_duplicate INTEGER NOT NULL DEFAULT 0")
        conn.commit()

    people_cols = {row[1] for row in conn.execute("PRAGMA table_info(people)")}
    if "ignored" not in people_cols:
        conn.execute("ALTER TABLE people ADD COLUMN ignored INTEGER NOT NULL DEFAULT 0")
        conn.commit()
