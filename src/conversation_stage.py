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

from layout_geometry import ORIENTATIONS, PRINT_SIZES
from qwen_stage import BASE_URL


def _call_model(system_prompt: str, user_message: str, max_tokens: int = 300) -> str:
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    r = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


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
        "WHERE event_tag = ? AND is_duplicate = 0 AND filename NOT IN ({}) "
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
    content = _call_model(SYSTEM_PROMPT, user_message)
    ops = parse_ops_response(content)
    return validate_ops(ops, spread, pool)


# --- People screen chat (idea §18 extended to every stage, per user request 2026-09-02) ---

PEOPLE_SYSTEM_PROMPT = (
    "You help the user manage face clusters on a photo album's People screen. You are "
    "given a list of clusters (person_id, current label, face count, whether ignored) and "
    "the user's instruction. You may ONLY propose these operations: "
    '{"op": "rename", "person_id": <int>, "label": "<new name>"}, '
    '{"op": "ignore", "person_id": <int>} (deprioritize a cluster, e.g. a random guest), '
    '{"op": "restore", "person_id": <int>} (undo ignore). '
    "Naming two clusters the same label merges them -- that's expected, just propose two "
    "rename ops with the same label if the user asks to merge or says two clusters are the "
    "same person. Respond with ONLY a JSON object (no markdown, no extra text): "
    '{"ops": [...]}. Use an empty ops list if the instruction doesn\'t map to a sensible '
    "operation. Only use person_id values that appear in the input."
)


def build_people_context(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT p.person_id, p.label, p.ignored, COUNT(f.id) AS face_count "
        "FROM people p JOIN faces f ON f.person_id = p.person_id "
        "GROUP BY p.person_id ORDER BY face_count DESC"
    ).fetchall()
    return [
        {"person_id": r[0], "label": r[1] or "", "ignored": bool(r[2]), "face_count": r[3]}
        for r in rows
    ]


def build_people_user_message(people: list[dict], instruction: str) -> str:
    lines = [
        f"  person_id={p['person_id']} label=\"{p['label']}\" faces={p['face_count']}"
        f"{' (ignored)' if p['ignored'] else ''}"
        for p in people
    ]
    return "Clusters:\n" + ("\n".join(lines) if lines else "  (none)") + f"\nUser instruction: {instruction}"


def validate_people_ops(ops: list[dict], people: list[dict]) -> list[dict]:
    valid_ids = {p["person_id"] for p in people}
    valid = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        if kind not in ("rename", "ignore", "restore"):
            continue
        if op.get("person_id") not in valid_ids:
            continue
        entry = {"op": kind, "person_id": op["person_id"]}
        if kind == "rename":
            label = op.get("label")
            if not isinstance(label, str) or not label.strip():
                continue
            entry["label"] = label.strip()
        valid.append(entry)
    return valid


def propose_people_edits(conn, instruction: str) -> list[dict]:
    people = build_people_context(conn)
    content = _call_model(PEOPLE_SYSTEM_PROMPT, build_people_user_message(people, instruction))
    return validate_people_ops(parse_ops_response(content), people)


# --- Storyboard screen chat ---

STORYBOARD_SYSTEM_PROMPT = (
    "You help the user reorder spreads on a photo album's Storyboard screen. You are given "
    "the current sequence of spreads (number, event, layout, photo count) and the user's "
    "instruction. You may ONLY propose moving one spread to a new 1-indexed position: "
    '{"op": "move_spread", "spread": <int>, "to_position": <int>}. You cannot merge, '
    "delete, or change what's inside a spread. Respond with ONLY a JSON object (no "
    'markdown, no extra text): {"ops": [...]}. Use an empty ops list if the instruction '
    "doesn't map to a sensible move. Only use spread numbers that appear in the input, and "
    "to_position must be between 1 and the total number of spreads."
)


def build_storyboard_context(spreads: list[dict]) -> list[dict]:
    return [
        {
            "spread": s["spread"], "event": s.get("event") or "",
            "layout": s.get("layout") or "",
            "photos": len(s.get("supporting") or []) + (1 if s.get("hero") else 0),
        }
        for s in sorted(spreads, key=lambda s: s["spread"])
    ]


def build_storyboard_user_message(spreads: list[dict], instruction: str) -> str:
    lines = [
        f"  spread={s['spread']} event=\"{s['event']}\" layout={s['layout']} photos={s['photos']}"
        for s in build_storyboard_context(spreads)
    ]
    return "Current order:\n" + "\n".join(lines) + f"\nUser instruction: {instruction}"


def validate_storyboard_ops(ops: list[dict], spreads: list[dict]) -> list[dict]:
    valid_numbers = {s["spread"] for s in spreads}
    n = len(spreads)
    valid = []
    for op in ops:
        if not isinstance(op, dict) or op.get("op") != "move_spread":
            continue
        spread, to_position = op.get("spread"), op.get("to_position")
        if spread not in valid_numbers:
            continue
        if not isinstance(to_position, int) or not (1 <= to_position <= n):
            continue
        valid.append({"op": "move_spread", "spread": spread, "to_position": to_position})
    return valid


def propose_storyboard_edits(spreads: list[dict], instruction: str) -> list[dict]:
    content = _call_model(STORYBOARD_SYSTEM_PROMPT, build_storyboard_user_message(spreads, instruction))
    return validate_storyboard_ops(parse_ops_response(content), spreads)


# --- Setup screen chat ---

SETUP_SYSTEM_PROMPT = (
    "You help the user configure the Setup screen of a photo album tool before the "
    "pipeline runs. You may ONLY propose these operations: "
    '{"op": "set_size", "size": "<print size>"}, {"op": "set_orientation", "orientation": '
    '"landscape"|"portrait"}. Valid print sizes: ' + ", ".join(PRINT_SIZES) + ". "
    "Respond with ONLY a JSON object (no markdown, no extra text): "
    '{"ops": [...]}. Use an empty ops list if the instruction doesn\'t map to a sensible '
    "operation (e.g. it's about something this screen doesn't control, like importing "
    "photos or changing photo content)."
)


def validate_setup_ops(ops: list[dict]) -> list[dict]:
    valid = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        if kind == "set_size" and op.get("size") in PRINT_SIZES:
            valid.append({"op": "set_size", "size": op["size"]})
        elif kind == "set_orientation" and op.get("orientation") in ORIENTATIONS:
            valid.append({"op": "set_orientation", "orientation": op["orientation"]})
    return valid


def propose_setup_edits(instruction: str) -> list[dict]:
    content = _call_model(SETUP_SYSTEM_PROMPT, f"User instruction: {instruction}")
    return validate_setup_ops(parse_ops_response(content))
