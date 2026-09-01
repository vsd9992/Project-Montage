"""Phase 3 (project-plan.md) last slice: spread editor with lock/regenerate (idea §16/§17,
MVP-locked per idea §30 "Editable spreads. Lock/regenerate.").

Scope: view every spread, swap which photo fills a slot, lock a spread (locking blocks
both slot edits and regenerate on it -- "regeneration never touches locked spreads", idea
§17), and regenerate a single spread (recompute its crops + re-render just that one page)
or every unlocked spread in one action ("regenerate this spread... not the entire album",
idea §16). Out of scope for this first pass, per the same MVP note that explicitly warns
against the editor growing into Photoshop/Lightroom (idea §30 closing remarks): resizing
frames, changing layout template, adding text/background, and freehand crop repositioning
-- all real idea §16 items, left for a later iteration once this baseline is validated.

Stdlib-only (http.server), same pattern as the other Phase 3 apps. Reuses
`crop_stage.compute_crop` and `render_stage.render_spread` directly (not as subprocesses)
since a single-spread regenerate is fast enough to run synchronously in the request.
"""

import argparse
import glob
import html
import io
import json
import os
import sqlite3
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageOps

import web_theme
from conversation_stage import candidate_pool, propose_edits
from crop_stage import compute_crop
from db import connect
from layout_geometry import DEFAULT_SIZE, get_geometry
from qwen_stage import start_server, stop_server
from render_stage import render_spread
from spread_stage import effective_orientation
from style_stage import DEFAULT_STYLE

THUMB_MAX = 320


def _load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _slot_order(slot_keys: list[str]) -> list[str]:
    def key(s):
        if s == "hero":
            return (0, 0)
        if s.startswith("support_"):
            try:
                return (1, int(s.split("_", 1)[1]))
            except ValueError:
                return (1, 0)
        return (2, s)
    return sorted(slot_keys, key=key)


def _sync_hero_supporting(spread: dict) -> None:
    slots = spread["slots"]
    ordered = _slot_order(slots.keys())
    spread["hero"] = slots["hero"]["filename"] if "hero" in slots else None
    spread["supporting"] = [slots[k]["filename"] for k in ordered if k != "hero"]


def _rendered_path(out_dir: str, spread_number: int) -> str | None:
    matches = glob.glob(os.path.join(out_dir, f"spread_{spread_number:03d}_*.jpg"))
    return matches[0] if matches else None


def _regenerate_spread(conn: sqlite3.Connection, spread: dict, crops_all: list[dict],
                        out_dir: str, size: str, style: str) -> str:
    """Recomputes crops for `spread`'s current slots and re-renders it. Returns the
    written image path. Mutates crops_all in place (replacing/adding this spread's entry)."""
    geometry = get_geometry(size)
    aspects = geometry.slot_aspect_ratios(spread["layout"])
    crops = {}
    for slot, info in spread["slots"].items():
        target_aspect = aspects.get(slot, 1.33)
        crops[slot] = compute_crop(conn, info["filename"], target_aspect)

    entry = next((c for c in crops_all if c["spread"] == spread["spread"]), None)
    if entry is None:
        entry = {"spread": spread["spread"], "event": spread["event"], "layout": spread["layout"], "crops": crops}
        crops_all.append(entry)
    else:
        entry["crops"] = crops
        entry["layout"] = spread["layout"]

    # remove any stale rendered file (filename encodes layout, which could differ from a
    # previous render if the spread's layout ever changes)
    old_path = _rendered_path(out_dir, spread["spread"])
    if old_path:
        os.remove(old_path)

    canvas = render_spread(conn, spread, crops, geometry, style)
    out_path = os.path.join(out_dir, f"spread_{spread['spread']:03d}_{spread['layout']}.jpg")
    canvas.save(out_path, "JPEG", quality=92)
    return out_path


def _grid_html(spreads: list[dict], mount: str) -> str:
    cards = []
    for s in sorted(spreads, key=lambda x: x["spread"]):
        locked = s.get("locked", False)
        event = html.escape(s.get("event") or "")
        cards.append(f"""
        <a class="card spread" href="{mount}/spread/{s['spread']}">
          <img src="{mount}/thumb/{s['spread']}" loading="lazy">
          <div class="meta">#{s['spread']} {'&#128274;' if locked else ''}<br>{event}<br>{html.escape(s['layout'])}</div>
        </a>""")
    return "".join(cards)


def _render_grid(spreads_path: str, mount: str) -> bytes:
    spreads = _load_json(spreads_path)
    extra_head = """
<style>
.grid { display: flex; flex-wrap: wrap; gap: 1em; }
.spread { width: 200px; padding: 0.6em; text-decoration: none; color: inherit; display: block; }
.spread img { width: 100%; height: 130px; object-fit: cover; border-radius: 6px; }
.meta { font-size: 0.85em; margin-top: 0.5em; color: var(--text-muted); }
#bulkStatus { margin-left: 1em; color: var(--emerald-strong); font-weight: 600; }
</style>"""
    body = f"""
<p><button id="bulkBtn" class="btn btn-outline">Regenerate all unlocked spreads</button><span id="bulkStatus"></span></p>
<div class="grid">
{_grid_html(spreads, mount)}
</div>"""
    extra_script = ("""
document.getElementById('bulkBtn').addEventListener('click', () => {
  const btn = document.getElementById('bulkBtn');
  const status = document.getElementById('bulkStatus');
  btn.disabled = true;
  status.textContent = 'Regenerating...';
  fetch('""" + mount + """/regenerate_all', {method: 'POST'}).then(r => r.json()).then(data => {
    status.textContent = 'Done: ' + data.regenerated + ' regenerated, ' + data.skipped_locked + ' skipped (locked).';
    btn.disabled = false;
  });
});
""")
    return web_theme.page_shell(
        "/editor/", "Spread Editor", f"{len(spreads)} spreads", body, extra_head, extra_script,
    )


def _render_detail(spreads_path: str, spread_number: int, mount: str) -> bytes | None:
    spreads = _load_json(spreads_path)
    spread = next((s for s in spreads if s["spread"] == spread_number), None)
    if spread is None:
        return None
    locked = spread.get("locked", False)
    rows = []
    for slot in _slot_order(spread["slots"].keys()):
        info = spread["slots"][slot]
        filename = html.escape(info["filename"])
        rows.append(f"""
        <div class="slot card">
          <img src="{mount}/slot_thumb/{spread_number}/{urllib.parse.quote(slot)}">
          <div>
            <div><strong>{html.escape(slot)}</strong></div>
            <div style="font-size:11.5px;color:var(--text-faint);max-width:14em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{filename}</div>
            <button type="button" class="btn btn-outline swap-btn" data-slot="{html.escape(slot)}" {'disabled' if locked else ''}>Swap&hellip;</button>
          </div>
        </div>""")
    lock_label = "Unlock" if locked else "Lock"
    extra_head = """
<style>
a.back { display: inline-block; margin-bottom: 1em; font-size: 12.5px; color: var(--text-muted); }
.rendered { max-width: 100%; max-height: 55vh; border-radius: 10px; border: 1px solid var(--border); }
.slots { display: flex; flex-wrap: wrap; gap: 1em; margin-top: 0.4em; }
.slot { display: flex; gap: 0.6em; align-items: center; padding: 0.6em; }
.slot img { width: 70px; height: 70px; object-fit: cover; border-radius: 6px; }
.slot input[type=text] { width: 14em; }
.controls { margin: 0.4em 0; display: flex; gap: 0.6em; align-items: center; flex-wrap: wrap; }
#regenStatus { color: var(--emerald-strong); font-weight: 600; }
.locked-note { color: var(--brown); font-weight: 600; }
.chat { padding: 1em 1.2em; max-width: 42em; }
.chat .hint { font-size: 0.85em; color: var(--text-faint); }
#chatStatus { margin-top: 0.5em; color: var(--emerald-strong); font-weight: 600; }
#chatProposal ul { padding-left: 1.2em; }
#pickerOverlay { display:none; position:fixed; inset:0; background:oklch(20% 0.01 255 / 0.55); z-index:50;
                 align-items:center; justify-content:center; }
#pickerOverlay.show { display:flex; }
#pickerModal { background:var(--bg); border-radius:14px; padding:20px 22px; max-width:640px; width:90%;
               max-height:80vh; overflow-y:auto; border:1px solid var(--border); }
#pickerGrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(110px,1fr)); gap:10px; margin-top:12px; }
.pick-card { cursor:pointer; border:2px solid transparent; border-radius:8px; padding:4px; text-align:center; background:none; }
.pick-card:hover { border-color:var(--royal); }
.pick-card img { width:100%; height:80px; object-fit:cover; border-radius:6px; }
.pick-card div { font-size:10.5px; color:var(--text-faint); margin-top:3px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
</style>"""
    body = f"""
<a class="back" href="{mount}/">&larr; All spreads</a>
<h1 style="font-size:18px;">Spread {spread_number} &mdash; {html.escape(spread.get('event') or '')} ({html.escape(spread['layout'])})</h1>
{'<p class="locked-note">Locked -- unlock to edit or regenerate.</p>' if locked else ''}
<img class="rendered" src="{mount}/rendered/{spread_number}?t={hash(json.dumps(spread))}">
<div class="controls">
  <form method="post" action="{mount}/spread/{spread_number}/lock" style="display:inline">
    <button type="submit" class="btn btn-outline">{lock_label}</button>
  </form>
  <button id="regenBtn" class="btn btn-outline" {'disabled' if locked else ''}>Regenerate this spread</button>
  <span id="regenStatus"></span>
</div>
<div class="slots">
{''.join(rows)}
</div>
<div class="chat card">
  <h3 style="font-size:14px;">Ask for a change</h3>
  <p class="hint">e.g. "give the bride portrait more prominence" -- only slot swaps within
  this spread's event are possible; layout/slot changes aren't supported yet.</p>
  <form id="chatForm" style="display:flex;gap:8px;">
    <input type="text" id="chatInput" placeholder="Describe the change you want"
           style="flex:1;" {'disabled' if locked else ''}>
    <button type="submit" class="btn btn-primary" {'disabled' if locked else ''}>Ask</button>
  </form>
  <div id="chatStatus"></div>
  <div id="chatProposal"></div>
</div>
<div id="pickerOverlay">
  <div id="pickerModal">
    <div style="display:flex;align-items:center;justify-content:space-between;">
      <strong style="font-size:14px;">Choose a photo for <span id="pickerSlotName"></span></strong>
      <button type="button" class="btn btn-outline" id="pickerCloseBtn">Close</button>
    </div>
    <div id="pickerGrid">Loading&hellip;</div>
  </div>
</div>"""
    extra_script = ("""
const chatForm = document.getElementById('chatForm');
if (chatForm) {
  chatForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const instruction = document.getElementById('chatInput').value.trim();
    if (!instruction) return;
    const status = document.getElementById('chatStatus');
    const proposal = document.getElementById('chatProposal');
    status.textContent = 'Thinking... (first request loads the model, can take a minute)';
    proposal.innerHTML = '';
    fetch('""" + mount + f"""/spread/{spread_number}/chat', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{instruction}}),
    }}).then(r => r.json()).then(data => {{
      if (!data.ops || data.ops.length === 0) {{
        status.textContent = 'No change proposed for that instruction.';
        return;
      }}
      status.textContent = 'Proposed ' + data.ops.length + ' change(s):';
      const list = data.ops.map(op => '<li>' + op.slot + ' &rarr; ' + op.filename + '</li>').join('');
      proposal.innerHTML = '<ul>' + list + '</ul><button id="applyBtn" class="btn btn-primary">Apply</button>';
      document.getElementById('applyBtn').addEventListener('click', () => {{
        status.textContent = 'Applying...';
        fetch('{mount}/spread/{spread_number}/chat/apply', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{ops: data.ops}}),
        }}).then(r => {{ if (r.ok) location.reload(); else status.textContent = 'Failed to apply.'; }});
      }});
    }}).catch(() => {{ status.textContent = 'Request failed.'; }});
  }});
}}
const btn = document.getElementById('regenBtn');
if (btn) {{
  btn.addEventListener('click', () => {{
    btn.disabled = true;
    document.getElementById('regenStatus').textContent = 'Regenerating...';
    fetch('{mount}/spread/{spread_number}/regenerate', {{method: 'POST'}}).then(r => {{
      if (r.ok) location.reload();
      else {{ document.getElementById('regenStatus').textContent = 'Failed.'; btn.disabled = false; }}
    }});
  }});
}}

const pickerOverlay = document.getElementById('pickerOverlay');
const pickerGrid = document.getElementById('pickerGrid');
document.querySelectorAll('.swap-btn').forEach(swapBtn => {{
  swapBtn.addEventListener('click', () => {{
    const slot = swapBtn.dataset.slot;
    document.getElementById('pickerSlotName').textContent = slot;
    pickerGrid.innerHTML = 'Loading&hellip;';
    pickerOverlay.classList.add('show');
    fetch('{mount}/spread/{spread_number}/candidates/' + encodeURIComponent(slot)).then(r => r.json()).then(data => {{
      if (!data.candidates || data.candidates.length === 0) {{
        pickerGrid.innerHTML = '<p style="grid-column:1/-1;color:var(--text-faint);font-size:12.5px;">No other same-event photos available to swap in.</p>';
        return;
      }}
      pickerGrid.innerHTML = data.candidates.map(c =>
        '<button type="button" class="pick-card" data-filename="' + c.filename + '">' +
        '<img src="{mount}/photo_thumb/' + encodeURIComponent(c.filename) + '" loading="lazy">' +
        '<div>' + (c.event_tag || '') + '</div></button>'
      ).join('');
      pickerGrid.querySelectorAll('.pick-card').forEach(card => {{
        card.addEventListener('click', () => {{
          const fd = new URLSearchParams();
          fd.set('filename', card.dataset.filename);
          fetch('{mount}/spread/{spread_number}/slot/' + encodeURIComponent(slot), {{
            method: 'POST', headers: {{'Content-Type': 'application/x-www-form-urlencoded'}}, body: fd.toString(),
          }}).then(() => location.reload());
        }});
      }});
    }});
  }});
}});
document.getElementById('pickerCloseBtn').addEventListener('click', () => pickerOverlay.classList.remove('show'));
pickerOverlay.addEventListener('click', (e) => {{ if (e.target === pickerOverlay) pickerOverlay.classList.remove('show'); }});
""")
    return web_theme.page_shell(
        "/editor/", f"Spread {spread_number}", f"{spread.get('layout')} &middot; {'locked' if locked else 'unlocked'}",
        body, extra_head, extra_script,
    )


def _photo_jpeg(conn: sqlite3.Connection, path: str, box: tuple | None = None) -> bytes | None:
    if not Path(path).exists():
        return None
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    if box:
        img = img.crop(box)
    img.thumbnail((THUMB_MAX, THUMB_MAX))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def make_handler(db_path: str, spreads_path: str, crops_path: str, out_dir: str, size, style: str,
                  mount: str = "", engine_state: dict | None = None):
    # `size` is either a plain print-size string, or the project-wide state dict shared
    # with project_app's Dashboard (app.py wires it that way) so a size chosen there after
    # this handler was constructed is still picked up -- resolved fresh on every use.
    def _size() -> str:
        return (size.get("size") or DEFAULT_SIZE) if isinstance(size, dict) else size

    # Lazily started on first chat request, kept running for the process lifetime -- one
    # model load per editing session rather than per request (idea §21 load/batch/unload).
    # engine_state (shared with app.py's /api/engine-status) tracks idle/loading/ready so
    # the nav bar's AI-engine indicator reflects this on-demand load.
    llama_proc = {"proc": None}
    if engine_state is None:
        engine_state = {}

    def _ensure_llama_server():
        if llama_proc["proc"] is None:
            engine_state["state"] = "loading"
            try:
                llama_proc["proc"] = start_server()
            finally:
                engine_state["state"] = "ready" if llama_proc["proc"] is not None else "idle"

    def _release_llama_server():
        # Shut down right after use rather than keeping it resident for the rest of the
        # session, per user request (2026-09-01): the model was staying loaded/using
        # memory long after the chat reply came back.
        proc = llama_proc["proc"]
        if proc is not None:
            llama_proc["proc"] = None
            engine_state["state"] = "idle"
            stop_server(proc)

    _llama_proc_ref = llama_proc

    class Handler(BaseHTTPRequestHandler):
        llama_proc = _llama_proc_ref

        def log_message(self, fmt, *args):
            pass

        def _send_bytes(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == "/":
                self._send_bytes(_render_grid(spreads_path, mount), "text/html; charset=utf-8")
                return
            if path.startswith("/spread/") and "/candidates/" not in path:
                try:
                    n = int(path.rsplit("/", 1)[-1])
                except ValueError:
                    self.send_response(400); self.end_headers(); return
                body = _render_detail(spreads_path, n, mount)
                if body is None:
                    self.send_response(404); self.end_headers(); return
                self._send_bytes(body, "text/html; charset=utf-8")
                return
            if path.startswith("/rendered/"):
                try:
                    n = int(path.rsplit("/", 1)[-1])
                except ValueError:
                    self.send_response(400); self.end_headers(); return
                rp = _rendered_path(out_dir, n)
                if rp is None:
                    self.send_response(404); self.end_headers(); return
                with open(rp, "rb") as f:
                    self._send_bytes(f.read(), "image/jpeg")
                return
            if path.startswith("/thumb/"):
                try:
                    n = int(path.rsplit("/", 1)[-1])
                except ValueError:
                    self.send_response(400); self.end_headers(); return
                spreads = _load_json(spreads_path)
                spread = next((s for s in spreads if s["spread"] == n), None)
                if spread is None:
                    self.send_response(404); self.end_headers(); return
                rp = _rendered_path(out_dir, n)
                conn = connect(db_path)
                try:
                    if rp:
                        jpeg = _photo_jpeg(conn, rp)
                    else:
                        filename = spread.get("hero") or (spread.get("supporting") or [None])[0]
                        row = conn.execute("SELECT path FROM photos WHERE filename = ?", (filename,)).fetchone() if filename else None
                        jpeg = _photo_jpeg(conn, row[0]) if row else None
                finally:
                    conn.close()
                if jpeg is None:
                    self.send_response(404); self.end_headers(); return
                self._send_bytes(jpeg, "image/jpeg")
                return
            if path.startswith("/photo_thumb/"):
                filename = urllib.parse.unquote(path[len("/photo_thumb/"):])
                conn = connect(db_path)
                try:
                    row = conn.execute("SELECT path FROM photos WHERE filename = ?", (filename,)).fetchone()
                    jpeg = _photo_jpeg(conn, row[0]) if row else None
                finally:
                    conn.close()
                if jpeg is None:
                    self.send_response(404); self.end_headers(); return
                self._send_bytes(jpeg, "image/jpeg")
                return
            if path.startswith("/spread/") and "/candidates/" in path:
                parts = path.split("/")
                try:
                    n = int(parts[2])
                    slot = urllib.parse.unquote(parts[4])
                except (IndexError, ValueError):
                    self.send_response(400); self.end_headers(); return
                spreads = _load_json(spreads_path)
                spread = next((s for s in spreads if s["spread"] == n), None)
                if spread is None or slot not in spread["slots"]:
                    self.send_response(404); self.end_headers(); return
                conn = connect(db_path)
                try:
                    candidates = candidate_pool(conn, spread, limit=60)
                finally:
                    conn.close()
                self._send_bytes(json.dumps({"candidates": candidates}).encode("utf-8"), "application/json")
                return
            if path.startswith("/slot_thumb/"):
                parts = path.split("/")
                try:
                    n = int(parts[2])
                    slot = urllib.parse.unquote(parts[3])
                except (IndexError, ValueError):
                    self.send_response(400); self.end_headers(); return
                spreads = _load_json(spreads_path)
                spread = next((s for s in spreads if s["spread"] == n), None)
                if spread is None or slot not in spread["slots"]:
                    self.send_response(404); self.end_headers(); return
                filename = spread["slots"][slot]["filename"]
                conn = connect(db_path)
                try:
                    row = conn.execute("SELECT path FROM photos WHERE filename = ?", (filename,)).fetchone()
                    jpeg = _photo_jpeg(conn, row[0]) if row else None
                finally:
                    conn.close()
                if jpeg is None:
                    self.send_response(404); self.end_headers(); return
                self._send_bytes(jpeg, "image/jpeg")
                return
            self.send_response(404); self.end_headers()

        def _read_body(self) -> str:
            length = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(length).decode("utf-8")

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/regenerate_all":
                spreads = _load_json(spreads_path)
                crops_all = _load_json(crops_path) if Path(crops_path).exists() else []
                conn = connect(db_path)
                regenerated = skipped = 0
                try:
                    for spread in spreads:
                        if spread.get("locked", False):
                            skipped += 1
                            continue
                        _regenerate_spread(conn, spread, crops_all, out_dir, _size(), style)
                        regenerated += 1
                finally:
                    conn.close()
                _save_json(crops_path, crops_all)
                body = json.dumps({"regenerated": regenerated, "skipped_locked": skipped}).encode("utf-8")
                self._send_bytes(body, "application/json")
                return

            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "spread":
                try:
                    n = int(parts[1])
                except ValueError:
                    self.send_response(400); self.end_headers(); return
                spreads = _load_json(spreads_path)
                spread = next((s for s in spreads if s["spread"] == n), None)
                if spread is None:
                    self.send_response(404); self.end_headers(); return

                action = parts[2] if len(parts) > 2 else None

                if action == "lock":
                    spread["locked"] = not spread.get("locked", False)
                    _save_json(spreads_path, spreads)
                    self.send_response(303); self.send_header("Location", f"{mount}/spread/{n}"); self.end_headers()
                    return

                if action == "slot" and len(parts) == 4:
                    if spread.get("locked", False):
                        self.send_response(409); self.end_headers(); return
                    slot = urllib.parse.unquote(parts[3])
                    if slot not in spread["slots"]:
                        self.send_response(404); self.end_headers(); return
                    fields = urllib.parse.parse_qs(self._read_body())
                    new_filename = fields.get("filename", [""])[0].strip()
                    conn = connect(db_path)
                    try:
                        row = conn.execute(
                            "SELECT width, height, orientation, selection_score FROM photos WHERE filename = ?",
                            (new_filename,),
                        ).fetchone()
                        if row is None:
                            conn.close()
                            self._send_bytes(b"Photo not found in DB", "text/plain; charset=utf-8", 400)
                            return
                        width, height, exif_orientation, selection_score = row
                    finally:
                        conn.close()
                    spread["slots"][slot] = {
                        "filename": new_filename,
                        "orientation": effective_orientation(width, height, exif_orientation),
                        "selection_score": selection_score,
                    }
                    _sync_hero_supporting(spread)
                    _save_json(spreads_path, spreads)
                    self.send_response(303); self.send_header("Location", f"{mount}/spread/{n}"); self.end_headers()
                    return

                if action == "chat" and len(parts) == 3:
                    if spread.get("locked", False):
                        self.send_response(409); self.end_headers(); return
                    try:
                        fields = json.loads(self._read_body())
                    except json.JSONDecodeError:
                        self.send_response(400); self.end_headers(); return
                    instruction = (fields.get("instruction") or "").strip()
                    if not instruction:
                        self._send_bytes(b'{"ops": []}', "application/json")
                        return
                    _ensure_llama_server()
                    conn = connect(db_path)
                    try:
                        ops = propose_edits(conn, spread, instruction)
                    finally:
                        conn.close()
                        _release_llama_server()
                    self._send_bytes(json.dumps({"ops": ops}).encode("utf-8"), "application/json")
                    return

                if action == "chat" and len(parts) == 4 and parts[3] == "apply":
                    if spread.get("locked", False):
                        self.send_response(409); self.end_headers(); return
                    try:
                        fields = json.loads(self._read_body())
                    except json.JSONDecodeError:
                        self.send_response(400); self.end_headers(); return
                    ops = fields.get("ops") or []
                    conn = connect(db_path)
                    try:
                        for op in ops:
                            if op.get("op") != "swap_slot":
                                continue
                            slot = op.get("slot")
                            filename = op.get("filename")
                            if slot not in spread["slots"]:
                                continue
                            row = conn.execute(
                                "SELECT width, height, orientation, selection_score FROM photos WHERE filename = ?",
                                (filename,),
                            ).fetchone()
                            if row is None:
                                continue
                            width, height, exif_orientation, selection_score = row
                            spread["slots"][slot] = {
                                "filename": filename,
                                "orientation": effective_orientation(width, height, exif_orientation),
                                "selection_score": selection_score,
                            }
                    finally:
                        conn.close()
                    _sync_hero_supporting(spread)
                    _save_json(spreads_path, spreads)
                    self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
                    return

                if action == "regenerate":
                    if spread.get("locked", False):
                        self.send_response(409); self.end_headers(); return
                    crops_all = _load_json(crops_path) if Path(crops_path).exists() else []
                    conn = connect(db_path)
                    try:
                        _regenerate_spread(conn, spread, crops_all, out_dir, _size(), style)
                    finally:
                        conn.close()
                    _save_json(crops_path, crops_all)
                    self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
                    return

            self.send_response(404); self.end_headers()

    return Handler


def run(db_path: str, spreads_path: str, crops_path: str, out_dir: str, size: str, style: str, port: int,
        mount: str = "", engine_state: dict | None = None) -> None:
    handler_cls = make_handler(db_path, spreads_path, crops_path, out_dir, size, style, mount, engine_state)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    print(f"Serving spread editor at http://127.0.0.1:{port}/  (spreads: {spreads_path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        proc = handler_cls.llama_proc["proc"]
        if proc is not None:
            print("Stopping llama-server...")
            stop_server(proc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local web UI to edit spreads (swap photos, lock, regenerate)")
    parser.add_argument("--db", default="cache/project_full.db")
    parser.add_argument("--spreads", default="exports/spreads.json")
    parser.add_argument("--crops", default="exports/crops.json")
    parser.add_argument("--out-dir", default="exports/rendered_spreads")
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument("--style", default=DEFAULT_STYLE)
    parser.add_argument("--port", type=int, default=8003)
    args = parser.parse_args()
    run(args.db, args.spreads, args.crops, args.out_dir, args.size, args.style, args.port)
