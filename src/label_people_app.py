"""Phase 3 (project-plan.md) first slice: people-labeling UI (idea §7).

`src/person_cluster_stage.py` groups detected faces into `people` clusters but leaves
`people.label` NULL -- naming them ("Person 01" -> "Bride") is a user-facing UI concern,
done here. Stdlib-only (http.server), no new dependency: FastAPI/uvicorn/Flask are not
installed in this project's venv and this app's needs (list + a few form posts + image
crops) don't warrant adding one.

Two features beyond basic labeling, added after user feedback (2026-09-01):
- Ignore/restore: events have lots of non-priority faces (random guests). `people.ignored`
  marks a cluster as not a priority person; ignored clusters move to a separate collapsed
  section instead of being deleted (data stays, just deprioritized -- future selection-
  scoring work can filter on it).
- Same-name merge: the face-clustering stage sometimes splits one real person into several
  clusters. Rather than a separate merge UI, saving a label that matches another cluster's
  existing label merges the two: all `faces.person_id` from the other cluster move to the
  lower `person_id`, and the now-empty `people` row is deleted.
"""

import argparse
import html
import io
import sqlite3
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageOps

import project_app
import web_theme
from db import connect

DB_PATH = "cache/project_full.db"
FACE_PAD = 0.4  # fraction of bbox size to pad on each side for context


def _people_rows(conn: sqlite3.Connection, ignored: bool) -> list[dict]:
    rows = conn.execute(
        "SELECT p.person_id, p.label, COUNT(f.id) AS face_count, MIN(f.id) AS rep_face_id "
        "FROM people p JOIN faces f ON f.person_id = p.person_id "
        "WHERE p.ignored = ? "
        "GROUP BY p.person_id ORDER BY face_count DESC",
        (1 if ignored else 0,),
    ).fetchall()
    return [
        {"person_id": r[0], "label": r[1], "face_count": r[2], "rep_face_id": r[3]}
        for r in rows
    ]


def _person_card(p: dict, ignored: bool, mount: str) -> str:
    label = html.escape(p["label"] or "")
    action = "restore" if ignored else "ignore"
    action_label = "Restore" if ignored else "Remove"
    action_class = "btn-outline" if ignored else "btn-danger"
    label_form = "" if ignored else f"""
            <form method="post" action="{mount}/label/{p['person_id']}" style="display:flex;gap:8px;margin-top:8px;">
              <input type="text" name="label" value="{label}" placeholder="Name this person">
              <button type="submit" class="btn btn-outline">Save</button>
            </form>"""
    label_display = f'<div style="font-weight:600;">{label}</div>' if ignored and label else ""
    return f"""
        <div class="card person-card">
          <div class="person-head">
            <img class="avatar" src="{mount}/face_thumb/{p['rep_face_id']}" width="52" height="52">
            <div>
              <div class="person-count">Person {p['person_id']} &mdash; {p['face_count']} faces</div>
              {label_display}
            </div>
          </div>
          {label_form}
          <form method="post" action="{mount}/{action}/{p['person_id']}">
            <button type="submit" class="btn {action_class}" style="width:100%;">{action_label}</button>
          </form>
        </div>"""


def _render_index(conn: sqlite3.Connection, mount: str) -> bytes:
    active = _people_rows(conn, ignored=False)
    ignored = _people_rows(conn, ignored=True)
    active_html = "".join(_person_card(p, False, mount) for p in active)
    ignored_html = "".join(_person_card(p, True, mount) for p in ignored)
    extra_head = f"""
<style>
.person-card{{padding:14px;display:flex;flex-direction:column;gap:6px;}}
.person-head{{display:flex;align-items:center;gap:12px;}}
.avatar{{border-radius:50%;object-fit:cover;border:1px solid var(--border);}}
.person-count{{font-size:11.5px;color:var(--text-faint);}}
details{{margin-top:8px;}}
details summary{{cursor:pointer;font-weight:600;color:var(--text-muted);}}
{project_app.STAGE_GROUP_CSS}
</style>"""
    body = f"""
{project_app.stage_group_html(project_app.PEOPLE_STAGES, "People stages")}
<p style="font-size:12.5px;color:var(--text-muted);">Type the same name on two different clusters to merge
them. "Remove" moves a cluster to the list below instead of deleting it &mdash; restore any time.</p>
<div class="grid-fill">
{active_html}
</div>
<details><summary>Removed ({len(ignored)})</summary>
<div class="grid-fill" style="margin-top:12px;">
{ignored_html}
</div>
</details>"""
    return web_theme.page_shell(
        "/people/", "People", f"{len(active)} active clusters, {len(ignored)} removed",
        body, extra_head, project_app.STAGE_GROUP_SCRIPT,
    )


def _face_thumb_jpeg(conn: sqlite3.Connection, face_id: int) -> bytes | None:
    row = conn.execute(
        "SELECT f.bbox_x1, f.bbox_y1, f.bbox_x2, f.bbox_y2, ph.path "
        "FROM faces f JOIN photos ph ON ph.file_hash = f.file_hash WHERE f.id = ?",
        (face_id,),
    ).fetchone()
    if row is None:
        return None
    x1, y1, x2, y2, path = row
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)  # match face_stage.py's cv2.imread EXIF-corrected space
    w, h = img.size
    bw, bh = x2 - x1, y2 - y1
    pad_x, pad_y = bw * FACE_PAD, bh * FACE_PAD
    box = (
        max(0, int(x1 - pad_x)),
        max(0, int(y1 - pad_y)),
        min(w, int(x2 + pad_x)),
        min(h, int(y2 + pad_y)),
    )
    crop = img.crop(box).convert("RGB")
    crop.thumbnail((240, 240))
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _save_label(conn: sqlite3.Connection, person_id: int, label: str | None) -> None:
    """Set person_id's label; if another cluster already has the same label (case-
    insensitive), merge them into the lower person_id instead of leaving duplicates."""
    if label is None:
        conn.execute("UPDATE people SET label = NULL WHERE person_id = ?", (person_id,))
        conn.commit()
        return

    others = conn.execute(
        "SELECT person_id FROM people WHERE person_id != ? AND label = ? COLLATE NOCASE",
        (person_id, label),
    ).fetchall()
    other_ids = [r[0] for r in others]

    if not other_ids:
        conn.execute("UPDATE people SET label = ? WHERE person_id = ?", (label, person_id))
        conn.commit()
        return

    target = min([person_id, *other_ids])
    losers = [pid for pid in [person_id, *other_ids] if pid != target]
    for loser in losers:
        conn.execute("UPDATE faces SET person_id = ? WHERE person_id = ?", (target, loser))
        conn.execute("DELETE FROM people WHERE person_id = ?", (loser,))
    conn.execute("UPDATE people SET label = ? WHERE person_id = ?", (label, target))
    conn.commit()


def make_handler(db_path: str, mount: str = ""):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            conn = connect(db_path)
            try:
                if self.path == "/" or self.path == "":
                    body = _render_index(conn, mount)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/face_thumb/"):
                    face_id = int(self.path.rsplit("/", 1)[-1])
                    jpeg = _face_thumb_jpeg(conn, face_id)
                    if jpeg is None:
                        self.send_response(404)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpeg)))
                    self.end_headers()
                    self.wfile.write(jpeg)
                else:
                    self.send_response(404)
                    self.end_headers()
            finally:
                conn.close()

        def do_POST(self):
            parts = self.path.strip("/").split("/")
            if len(parts) != 2 or parts[0] not in ("label", "ignore", "restore"):
                self.send_response(404)
                self.end_headers()
                return
            action, person_id_str = parts
            try:
                person_id = int(person_id_str)
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return

            conn = connect(db_path)
            try:
                if action == "label":
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length).decode("utf-8")
                    fields = urllib.parse.parse_qs(body)
                    label = fields.get("label", [""])[0].strip() or None
                    _save_label(conn, person_id, label)
                elif action == "ignore":
                    conn.execute("UPDATE people SET ignored = 1 WHERE person_id = ?", (person_id,))
                    conn.commit()
                elif action == "restore":
                    conn.execute("UPDATE people SET ignored = 0 WHERE person_id = ?", (person_id,))
                    conn.commit()
            finally:
                conn.close()

            self.send_response(303)
            self.send_header("Location", mount + "/")
            self.end_headers()

    return Handler


def run(db_path: str, port: int, mount: str = "") -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(db_path, mount))
    print(f"Serving people-labeling UI at http://127.0.0.1:{port}/  (db: {db_path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local web UI to label person clusters")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run(args.db, args.port)
