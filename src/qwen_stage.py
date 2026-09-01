"""Qwen3-VL stage: scene/event understanding + album-value judgement (idea §6, §8).

Only runs on a *bounded candidate set* -- the best-quality photo per burst/moment, plus
every singleton -- not all 1848 photos. This is the expensive VLM stage; the whole point
of duplicate/burst detection + quality scoring first is to avoid wasting it on near-
duplicates (idea §4: "we shouldn't ask Qwen to analyse everything immediately").

Manages the llama-server subprocess lifecycle itself (start -> batch requests -> stop),
per idea §21's load/batch/unload model lifecycle design.
"""

import argparse
import base64
import io
import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests
from PIL import Image

from db import connect

# Portable by default (relative to the repo checkout, models/ excluded from git -- see
# README's "Models" section for download links); override any of these with an env var if
# your models live elsewhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
LLAMA_SERVER = os.environ.get(
    "ALBUM_STUDIO_LLAMA_SERVER", str(REPO_ROOT / "models" / "llama-cpp" / "server" / "llama-server.exe")
)
MODEL_PATH = os.environ.get(
    "ALBUM_STUDIO_QWEN_MODEL", str(REPO_ROOT / "models" / "qwen3-vl" / "Qwen3VL-8B-Instruct-Q4_K_M.gguf")
)
MMPROJ_PATH = os.environ.get(
    "ALBUM_STUDIO_QWEN_MMPROJ", str(REPO_ROOT / "models" / "qwen3-vl" / "mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf")
)
PORT = 8090
BASE_URL = f"http://127.0.0.1:{PORT}"
MAX_IMAGE_DIM = 1024  # downscale before sending -- full 5760x3840 originals are unnecessary
                       # for scene/event judgement and slow down inference a lot

PROMPT = (
    "You are helping curate photos for a wedding/event album. Look at this photo and "
    "respond with ONLY a JSON object (no markdown, no extra text) with these fields:\n"
    '{"event_tag": "short label for what is happening, e.g. \'ceremony\', \'bride portrait\', '
    '\'reception dancing\', \'candid guests\'", '
    '"album_value": integer 1-10 for how much this photo deserves a place in the final '
    "album (consider emotional moment, composition, subject prominence -- not just "
    'technical quality), "description": "one sentence description"}'
)


def select_candidates(conn) -> list[tuple[str, str]]:
    """Best quality_score per moment_group_id, plus every singleton (moment_group_id IS NULL)."""
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE photos SET is_qwen_candidate = 1
        WHERE moment_group_id IS NULL
        """
    )
    cur.execute(
        """
        UPDATE photos SET is_qwen_candidate = 1
        WHERE file_hash IN (
            SELECT file_hash FROM (
                SELECT file_hash, moment_group_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY moment_group_id ORDER BY quality_score DESC
                       ) AS rn
                FROM photos
                WHERE moment_group_id IS NOT NULL
            )
            WHERE rn = 1
        )
        """
    )
    conn.commit()
    return cur.execute(
        "SELECT file_hash, path FROM photos WHERE is_qwen_candidate = 1 AND ai_description IS NULL"
    ).fetchall()


def start_server() -> subprocess.Popen:
    proc = subprocess.Popen(
        [LLAMA_SERVER, "-m", MODEL_PATH, "--mmproj", MMPROJ_PATH, "-ngl", "99", "--port", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            r = requests.get(f"{BASE_URL}/health", timeout=2)
            if r.status_code == 200 and r.json().get("status") == "ok":
                return proc
        except requests.exceptions.RequestException:
            pass
        time.sleep(2)
    proc.kill()
    raise RuntimeError("llama-server did not become healthy in time")


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def encode_image(path: str) -> str:
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        scale = MAX_IMAGE_DIM / max(w, h)
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()


def parse_json_response(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def analyze_photo(path: str) -> dict | None:
    img_b64 = encode_image(path)
    payload = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ]}
        ],
        "max_tokens": 200,
        "temperature": 0.2,
    }
    r = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=120)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return parse_json_response(content)


def run(db_path: str, limit: int | None) -> None:
    conn = connect(db_path)
    candidates = select_candidates(conn)
    total_candidates = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE is_qwen_candidate = 1"
    ).fetchone()[0]
    print(f"{total_candidates} total candidates (best-per-moment + singletons), "
          f"{len(candidates)} not yet analyzed")

    if limit:
        candidates = candidates[:limit]
        print(f"Limiting this run to {len(candidates)} candidates")

    if not candidates:
        conn.close()
        return

    print("Starting llama-server...")
    proc = start_server()
    print("Server ready.")

    cur = conn.cursor()
    ok, failed = 0, 0
    try:
        for i, (file_hash, path) in enumerate(candidates, 1):
            try:
                result = analyze_photo(path)
            except Exception as e:
                print(f"  [{i}/{len(candidates)}] error on {path}: {e}")
                failed += 1
                continue
            if result is None:
                print(f"  [{i}/{len(candidates)}] could not parse response for {path}")
                failed += 1
                continue
            cur.execute(
                "UPDATE photos SET event_tag = ?, album_value = ?, ai_description = ? "
                "WHERE file_hash = ?",
                (result.get("event_tag"), result.get("album_value"), result.get("description"), file_hash),
            )
            ok += 1
            if i % 10 == 0 or i == len(candidates):
                conn.commit()
                print(f"  [{i}/{len(candidates)}] ok={ok} failed={failed}")
    finally:
        conn.commit()
        print("Stopping llama-server...")
        stop_server(proc)

    conn.close()
    print(f"Done. {ok} analyzed, {failed} failed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Qwen3-VL scene/event/album-value stage")
    parser.add_argument("--db", default="cache/project.db", help="SQLite DB path")
    parser.add_argument("--limit", type=int, default=None, help="Only process N candidates (for testing)")
    args = parser.parse_args()
    run(args.db, args.limit)
