"""Phase 3 (project-plan.md) second slice: drag-and-drop spread reorder UI (idea §11,
Phase 1 review gap #2). `src/reorder_spreads.py` already provides the underlying
renumbering operation as a CLI; this wraps it in a browser UI so the user can drag spreads
into a new order instead of typing an explicit permutation.

Stdlib-only (http.server), consistent with `label_people_app.py`: no web framework is
installed in this project's venv.

Thumbnails are read from the *source* photo of each spread's representative slot (hero, or
the first supporting photo for documentary_grid, which has no hero) via the DB, not from
`exports/rendered_spreads/*.jpg` -- those files are named after the spread number at
render time, which goes stale the moment spreads are reordered (idea/Phase 2 note: a
re-render is needed after reordering to get correctly-numbered output files). Reading
straight from the source photo keys thumbnails by content, so they stay correct through any
number of reorders with no re-render dependency.
"""

import argparse
import html
import io
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageOps

import project_app
import web_theme
from db import connect
from reorder_spreads import apply_reorder

DB_PATH = "cache/project_full.db"
SPREADS_PATH = "exports/spreads.json"
CROPS_PATH = "exports/crops.json"
THUMB_MAX = 320


def _load_spreads(spreads_path: str) -> list[dict]:
    with open(spreads_path, encoding="utf-8") as f:
        return json.load(f)


def _rep_filename(spread: dict) -> str | None:
    if spread.get("hero"):
        return spread["hero"]
    supporting = spread.get("supporting") or []
    return supporting[0] if supporting else None


def _not_ready_page(mount: str) -> bytes:
    body = f"""
<div class="card" style="padding:22px 26px;">
  <p style="font-weight:600;">Not ready yet.</p>
  <p style="color:var(--text-faint);font-size:12.5px;">The storyboard (spread layout plan) hasn't been
  generated yet -- start the stage below (photos must already be imported and shortlisted on Setup).</p>
</div>
{project_app.stage_group_html(project_app.STORYBOARD_STAGES, "Storyboard stage")}"""
    return web_theme.page_shell("/storyboard/", "Storyboard", "Not ready yet",
                                 body, f"<style>{project_app.STAGE_GROUP_CSS}</style>", project_app.STAGE_GROUP_SCRIPT)


def _render_index(spreads_path: str, mount: str) -> bytes:
    spreads = sorted(_load_spreads(spreads_path), key=lambda s: s["spread"])
    cards = []
    for s in spreads:
        event = html.escape(s.get("event") or "")
        layout = html.escape(s.get("layout") or "")
        n_photos = len(s.get("supporting") or []) + (1 if s.get("hero") else 0)
        cards.append(f"""
        <div class="spread card" draggable="true" data-spread="{s['spread']}">
          <img src="{mount}/spread_thumb/{s['spread']}" loading="lazy">
          <div class="meta">#{s['spread']} &mdash; {event}<br>{layout}, {n_photos} photo(s)</div>
        </div>""")
    extra_head = f"""
<style>
.grid {{ display: flex; flex-wrap: wrap; gap: 1em; }}
.spread {{ width: 200px; padding: 0.6em; cursor: grab; }}
.spread img {{ width: 100%; height: 130px; object-fit: cover; border-radius: 6px; }}
.spread.dragging {{ opacity: 0.4; }}
.spread.drag-over {{ border-color: var(--emerald); border-width: 2px; }}
.meta {{ font-size: 0.85em; margin-top: 0.5em; color: var(--text-muted); }}
#status {{ margin-bottom: 1em; color: var(--emerald-strong); font-weight: 600; min-height: 1.2em; }}
{project_app.STAGE_GROUP_CSS}
</style>"""
    body = f"""
{project_app.stage_group_html(project_app.STORYBOARD_STAGES, "Storyboard stage")}
<p style="font-size:12.5px;color:var(--text-muted);">Drag a card to a new position. Order saves
automatically on drop (re-render afterwards to produce the reordered album pages).</p>
<div id="status"></div>
<div class="grid" id="grid">
{''.join(cards)}
</div>"""
    extra_script = ("""
const grid = document.getElementById('grid');
const status = document.getElementById('status');
let dragEl = null;

grid.addEventListener('dragstart', e => {
  dragEl = e.target.closest('.spread');
  dragEl.classList.add('dragging');
});
grid.addEventListener('dragend', e => {
  if (dragEl) dragEl.classList.remove('dragging');
  dragEl = null;
  document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
});
grid.addEventListener('dragover', e => {
  e.preventDefault();
  const target = e.target.closest('.spread');
  if (!target || target === dragEl) return;
  document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
  target.classList.add('drag-over');
});
grid.addEventListener('drop', e => {
  e.preventDefault();
  const target = e.target.closest('.spread');
  if (!target || !dragEl || target === dragEl) return;
  target.classList.remove('drag-over');
  const rect = target.getBoundingClientRect();
  const before = (e.clientX - rect.left) < rect.width / 2;
  target.insertAdjacentElement(before ? 'beforebegin' : 'afterend', dragEl);
  saveOrder();
});

function saveOrder() {
  const order = [...grid.querySelectorAll('.spread')].map(el => el.dataset.spread).join(',');
  status.textContent = 'Saving...';
  fetch('""" + mount + """/reorder', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'order=' + encodeURIComponent(order),
  }).then(r => {
    if (r.ok) { status.textContent = 'Saved. Reloading...'; setTimeout(() => location.reload(), 400); }
    else { status.textContent = 'Save failed (' + r.status + ')'; }
  }).catch(() => { status.textContent = 'Save failed (network error)'; });
}
""" + project_app.STAGE_GROUP_SCRIPT)
    return web_theme.page_shell(
        "/storyboard/", "Storyboard", f"{len(spreads)} spreads &mdash; drag to reorder", body, extra_head, extra_script,
    )


def _spread_thumb_jpeg(conn: sqlite3.Connection, spreads_path: str, spread_number: int) -> bytes | None:
    spreads = _load_spreads(spreads_path)
    spread = next((s for s in spreads if s["spread"] == spread_number), None)
    if spread is None:
        return None
    filename = _rep_filename(spread)
    if filename is None:
        return None
    row = conn.execute("SELECT path FROM photos WHERE filename = ?", (filename,)).fetchone()
    if row is None or not Path(row[0]).exists():
        return None
    img = Image.open(row[0])
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((THUMB_MAX, THUMB_MAX))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def make_handler(db_path: str, spreads_path: str, crops_path: str, mount: str = ""):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            if self.path == "/" or self.path == "":
                body = _render_index(spreads_path, mount) if Path(spreads_path).exists() else _not_ready_page(mount)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/spread_thumb/"):
                try:
                    spread_number = int(self.path.rsplit("/", 1)[-1])
                except ValueError:
                    self.send_response(400)
                    self.end_headers()
                    return
                conn = connect(db_path)
                try:
                    jpeg = _spread_thumb_jpeg(conn, spreads_path, spread_number)
                finally:
                    conn.close()
                if jpeg is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.end_headers()
                self.wfile.write(jpeg)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            if self.path != "/reorder":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            import urllib.parse
            fields = urllib.parse.parse_qs(body)
            order_str = fields.get("order", [""])[0]
            try:
                new_order = [int(x) for x in order_str.split(",") if x]
                apply_reorder(spreads_path, crops_path, new_order, spreads_path, crops_path)
            except (ValueError, FileNotFoundError) as e:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                msg = str(e).encode("utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def run(db_path: str, spreads_path: str, crops_path: str, port: int, mount: str = "") -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(db_path, spreads_path, crops_path, mount))
    print(f"Serving spread reorder UI at http://127.0.0.1:{port}/  (spreads: {spreads_path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local web UI to drag-and-drop reorder spreads")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--spreads", default=SPREADS_PATH)
    parser.add_argument("--crops", default=CROPS_PATH)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    run(args.db, args.spreads, args.crops, args.port)
