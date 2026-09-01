"""Phase 4 slice 1: conversational spread editing (idea §18).

Converts a natural-language instruction about one spread ("give the bride portrait more
prominence", "these two look repetitive") into a small set of structured operations, via
Qwen3-VL, restricted to what the Phase 3 spread editor already knows how to execute:
swapping a slot's photo for another same-event candidate. The model never touches pixels
directly (idea §18) and is only ever shown filenames that actually exist for this spread/
event, so it cannot hallucinate an operation the editor can't apply.

Reuses qwen_stage's llama-server process management -- same model, same lifecycle
pattern (start once, batch requests, stop) -- rather than standing up a second server.
"""

import json
import re

import requests

from qwen_stage import BASE_URL

SYSTEM_PROMPT = (
    "You are a layout assistant for a printed photo album. You are given one spread "
    "(a two-page layout) and the user's instruction about it. You may ONLY propose "
    "swapping which photo fills a slot, using photos from the provided candidate pool. "
    "You cannot resize, move photos between spreads, add text, or change the layout "
    "template. Respond with ONLY a JSON object (no markdown, no extra text): "
    '{"ops": [{"op": "swap_slot", "slot": "<slot name>", "filename": "<candidate filename>"}]}. '
    "Use an empty ops list if the instruction doesn't map to a sensible slot swap, or if "
    "the current slots already satisfy it. Only use slot names and filenames that appear "
    "in the input."
)


def _describe_photo(info: dict) -> str:
    parts = [info["filename"]]
    if info.get("event_tag"):
        parts.append(f"event={info['event_tag']}")
    if info.get("album_value") is not None:
        parts.append(f"album_value={info['album_value']}")
    if info.get("description"):
        parts.append(f"desc={info['description']}")
    return " | ".join(parts)


def candidate_pool(conn, spread: dict, limit: int = 12) -> list[dict]:
    """Same-event photos not already used in this spread, best album_value first."""
    used = {info["filename"] for info in spread["slots"].values()}
    event = spread.get("event")
    rows = conn.execute(
        "SELECT filename, event_tag, album_value, ai_description FROM photos "
        "WHERE event_tag = ? AND filename NOT IN ({}) "
        "ORDER BY album_value IS NULL, album_value DESC LIMIT ?".format(
            ",".join("?" * len(used)) if used else "''"
        ),
        (event, *used, limit) if used else (event, limit),
    ).fetchall()
    return [
        {"filename": r[0], "event_tag": r[1], "album_value": r[2], "description": r[3]}
        for r in rows
    ]


def _slot_info(conn, filename: str) -> dict:
    row = conn.execute(
        "SELECT event_tag, album_value, ai_description FROM photos WHERE filename = ?",
        (filename,),
    ).fetchone()
    return {
        "filename": filename,
        "event_tag": row[0] if row else None,
        "album_value": row[1] if row else None,
        "description": row[2] if row else None,
    }


def build_user_message(conn, spread: dict, instruction: str, pool: list[dict]) -> str:
    slot_lines = [
        f"  {slot}: {_describe_photo(_slot_info(conn, info['filename']))}"
        for slot, info in spread["slots"].items()
    ]
    pool_lines = [f"  {_describe_photo(p)}" for p in pool]
    return (
        f"Spread layout: {spread['layout']}\n"
        f"Current slots:\n" + "\n".join(slot_lines) + "\n"
        f"Candidate photos available to swap in (same event, not currently used):\n"
        + ("\n".join(pool_lines) if pool_lines else "  (none available)") + "\n"
        f"User instruction: {instruction}"
    )


def parse_ops_response(text: str) -> list[dict]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    ops = data.get("ops", [])
    return ops if isinstance(ops, list) else []


def validate_ops(ops: list[dict], spread: dict, pool: list[dict]) -> list[dict]:
    """Drops any op referencing a slot or filename that doesn't actually exist -- the
    model is a suggestion source, not a trusted mutator."""
    valid_filenames = {p["filename"] for p in pool}
    valid_slots = set(spread["slots"].keys())
    valid = []
    for op in ops:
        if not isinstance(op, dict) or op.get("op") != "swap_slot":
            continue
        if op.get("slot") not in valid_slots:
            continue
        if op.get("filename") not in valid_filenames:
            continue
        valid.append({"op": "swap_slot", "slot": op["slot"], "filename": op["filename"]})
    return valid


def propose_edits(conn, spread: dict, instruction: str) -> list[dict]:
    """Calls the running llama-server (caller is responsible for start/stop -- see
    qwen_stage.start_server/stop_server) and returns validated swap_slot ops."""
    pool = candidate_pool(conn, spread)
    user_message = build_user_message(conn, spread, instruction, pool)
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": 300,
        "temperature": 0.2,
    }
    r = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=120)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    ops = parse_ops_response(content)
    return validate_ops(ops, spread, pool)
