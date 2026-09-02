"""Phase 3 (project-plan.md) third slice: project management -- run the Phase 1/2 batch
pipeline (import -> burst -> quality -> Qwen -> shortlist -> spread -> face -> people-
cluster -> crop -> render -> PDF export) from a browser UI instead of hand-typed CLI
commands.

Stdlib-only (http.server), same pattern as `label_people_app.py` /
`reorder_spreads_app.py`. Each stage is a long-running batch script (`import_stage.py`
imports thousands of photos, `qwen_stage.py` runs local LLM inference, etc.) -- these run
as a subprocess in a background thread so the HTTP request that starts a stage returns
immediately; screens poll `/status` to show a badge + live log output per stage.

Per-stage controls (rebuilt 2026-09-02, user request): there is no more single "Dashboard"
screen or auto-advancing pipeline chain -- each pipeline stage gets its own Start/Pause
button, and stages are distributed across the step screens they actually belong to
(`stage_group_html()`/`STAGE_GROUP_SCRIPT` below are the reusable widget every screen
embeds for its own stages; see SETUP_STAGES/STORYBOARD_STAGES/PEOPLE_STAGES/EDITOR_STAGES).
Only one stage subprocess can run at a time (`_current_proc`); Start is rejected with 409
while another stage is running. Stop remains whole-project: it terminates whatever is
running and wipes the DB + exports dir back to empty -- per explicit user decision
(2026-09-01), because undoing only "what this run added" can't be done precisely (e.g.
import_stage adds photos incrementally with no per-run marker). This is destructive and
irreversible -- the UI requires a JS confirmation before calling it, and doubles as the
"New Project" action when nothing is running.
"""

import argparse
import html
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import web_theme
from db import connect
from layout_geometry import ORIENTATIONS, PRINT_SIZE_LABELS, PRINT_SIZES

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"

STAGES = [
    {"key": "import", "label": "Import photos", "script": "import_stage.py",
     "needs_source_dir": True, "extra_args": []},
    {"key": "burst", "label": "Burst / duplicate detection", "script": "burst_stage.py"},
    {"key": "quality", "label": "Quality scoring", "script": "quality_stage.py"},
    {"key": "qwen", "label": "Qwen3-VL understanding", "script": "qwen_stage.py"},
    {"key": "shortlist", "label": "Shortlist", "script": "shortlist_stage.py"},
    {"key": "spread", "label": "Spread layout planning", "script": "spread_stage.py",
     "extra_args": ["--out", "{exports}/spreads.json"]},
    {"key": "face", "label": "Face detection", "script": "face_stage.py",
     "extra_args": ["--spreads", "{exports}/spreads.json"]},
    {"key": "person_cluster", "label": "People clustering", "script": "person_cluster_stage.py"},
    {"key": "crop", "label": "Intelligent cropping", "script": "crop_stage.py",
     "extra_args": ["--spreads", "{exports}/spreads.json", "--out", "{exports}/crops.json",
                     "--size", "{size}", "--orientation", "{orientation}"]},
    {"key": "render", "label": "Render spreads", "script": "render_stage.py",
     "extra_args": ["--spreads", "{exports}/spreads.json", "--crops", "{exports}/crops.json",
                     "--out-dir", "{exports}/rendered_spreads", "--size", "{size}", "--orientation", "{orientation}"]},
]
STAGE_BY_KEY = {s["key"]: s for s in STAGES}
STAGE_KEYS = [s["key"] for s in STAGES]
MAX_LOG_LINES = 300

# Which screen each stage's controls live on, per user request (2026-09-02): "move the
# pipeline stages to the relevant step page" instead of one Dashboard listing all ten.
# This mirrors the checkpoint boundaries the pipeline already had (spread -> review in
# Storyboard; face/person_cluster -> review in People; crop/render -> review in Editor).
SETUP_STAGES = ["import", "burst", "quality", "qwen", "shortlist"]
STORYBOARD_STAGES = ["spread"]
PEOPLE_STAGES = ["face", "person_cluster"]
EDITOR_STAGES = ["crop", "render"]
SIZE_REQUIRED_STAGES = {"crop", "render"}

_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}  # key -> {"running", "returncode", "lines", "started_at", "interrupted"}

_proc_lock = threading.Lock()
_current_proc = {"proc": None, "key": None}


def _any_running() -> bool:
    with _proc_lock:
        return _current_proc["proc"] is not None


def _project_status(db_path: str, exports_dir: str) -> dict:
    exports = Path(exports_dir)
    status = {"db": db_path, "exports": exports_dir}
    if not Path(db_path).exists():
        status["photos"] = 0
        return status
    conn = connect(db_path)
    try:
        status["photos"] = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        status["with_quality"] = conn.execute("SELECT COUNT(*) FROM photos WHERE quality_score IS NOT NULL").fetchone()[0]
        status["qwen_described"] = conn.execute("SELECT COUNT(*) FROM photos WHERE ai_description IS NOT NULL").fetchone()[0]
        status["shortlisted"] = conn.execute("SELECT COUNT(*) FROM photos WHERE selection_score IS NOT NULL").fetchone()[0]
        status["faces"] = conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
        status["people"] = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    finally:
        conn.close()
    status["spreads_json"] = (exports / "spreads.json").exists()
    status["crops_json"] = (exports / "crops.json").exists()
    rendered_dir = exports / "rendered_spreads"
    status["rendered_count"] = len(list(rendered_dir.glob("*.jpg"))) if rendered_dir.exists() else 0
    status["album_pdf"] = (exports / "album.pdf").exists()
    return status


# --- UI state persistence (2026-09-02 fix) --------------------------------------------
# Size/orientation previously lived only in the in-memory `state` dict, reset to the CLI
# default on every app restart. That caused two user-reported bugs: (1) export's preflight
# comparing against a fresh-default geometry that didn't match spreads actually rendered at
# a different size/orientation before a restart, and (2) no way to tell "this is a resumed
# project" from "this is a fresh one". Persisting the last-chosen size/orientation next to
# the project's own exports dir (and clearing it on wipe) fixes both: a resumed project
# keeps rendering at the size it was rendered at, and a wiped/fresh project has nothing to
# resume, so it defaults cleanly.
def _ui_state_path(exports_dir: str) -> Path:
    return Path(exports_dir) / "ui_state.json"


def _load_ui_state(exports_dir: str) -> dict:
    p = _ui_state_path(exports_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_ui_state(exports_dir: str, state: dict) -> None:
    p = _ui_state_path(exports_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"size": state.get("size", ""), "orientation": state.get("orientation", "landscape")}),
                 encoding="utf-8")


def _build_args(stage: dict, db_path: str, exports_dir: str, source_dir: str, size: str, orientation: str) -> list[str]:
    args = []
    if stage.get("needs_source_dir"):
        args.append(source_dir)
    args += ["--db", db_path]
    for a in stage.get("extra_args", []):
        args.append(a.replace("{exports}", exports_dir).replace("{size}", size).replace("{orientation}", orientation))
    return args


def _run_stage_blocking(key: str, db_path: str, exports_dir: str, source_dir: str, size: str, orientation: str) -> int:
    """Runs one stage to completion (or until terminated by pause/stop), updating _jobs
    and _current_proc live. Returns the process return code (negative if terminated)."""
    stage = STAGE_BY_KEY[key]
    Path(exports_dir).mkdir(parents=True, exist_ok=True)
    script_path = SRC_DIR / stage["script"]
    args = _build_args(stage, db_path, exports_dir, source_dir, size, orientation)
    cmd = [sys.executable, str(script_path), *args]

    with _jobs_lock:
        _jobs[key] = {"running": True, "returncode": None, "interrupted": False,
                       "lines": [f"$ {' '.join(cmd)}"], "started_at": time.time()}

    try:
        proc = subprocess.Popen(
            cmd, cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except Exception as e:
        with _jobs_lock:
            _jobs[key]["running"] = False
            _jobs[key]["returncode"] = -1
            _jobs[key]["lines"].append(f"ERROR launching stage: {e}")
        return -1

    with _proc_lock:
        _current_proc["proc"] = proc
        _current_proc["key"] = key

    for line in proc.stdout:
        with _jobs_lock:
            job = _jobs[key]
            job["lines"].append(line.rstrip("\n"))
            if len(job["lines"]) > MAX_LOG_LINES:
                job["lines"] = job["lines"][-MAX_LOG_LINES:]
    returncode = proc.wait()

    with _proc_lock:
        _current_proc["proc"] = None
        _current_proc["key"] = None

    with _jobs_lock:
        _jobs[key]["running"] = False
        _jobs[key]["returncode"] = returncode
    return returncode


def _terminate_current() -> None:
    with _proc_lock:
        proc = _current_proc["proc"]
        key = _current_proc["key"]
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if key:
            with _jobs_lock:
                if key in _jobs:
                    _jobs[key]["interrupted"] = True


def _wipe_project(db_path: str, exports_dir: str) -> None:
    p = Path(db_path)
    if p.exists():
        p.unlink()
    exports = Path(exports_dir)
    if exports.exists():
        shutil.rmtree(exports)
    exports.mkdir(parents=True, exist_ok=True)
    with _jobs_lock:
        _jobs.clear()


# --- Reusable per-screen stage-controls widget -----------------------------------------
STAGE_GROUP_CSS = """
.stage{border:1px solid var(--border);border-radius:10px;padding:12px 16px;margin-bottom:8px;background:var(--bg-elev);}
.stage-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.stage-head strong{flex:1;min-width:120px;font-size:13.5px;}
.badge{font-size:11px;padding:3px 9px;border-radius:999px;text-transform:uppercase;font-weight:700;letter-spacing:.03em;}
.badge.idle{background:var(--bg-elev-2);color:var(--text-faint);}
.badge.running{background:var(--royal-soft);color:var(--royal);}
.badge.ok{background:var(--emerald-soft);color:var(--emerald-strong);}
.badge.failed{background:var(--danger-soft);color:var(--danger);}
.badge.paused{background:var(--brown-soft);color:var(--brown);}
.log{max-height:160px;overflow-y:auto;background:oklch(22% 0.01 255);color:oklch(88% 0.005 255);font-size:11.5px;
     padding:8px 10px;margin-top:8px;display:none;white-space:pre-wrap;border-radius:8px;}
.log.show{display:block;}
.btn-sm{padding:5px 11px;font-size:12px;}
.stage-group-title{font-size:12px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--text-faint);margin-bottom:2px;}
"""

STAGE_GROUP_SCRIPT = """
function pollStageGroup() {
  fetch('/status').then(r => r.json()).then(data => {
    const jobs = data.jobs || {};
    document.querySelectorAll('.stage[data-key]').forEach(el => {
      const key = el.dataset.key;
      const job = jobs[key];
      const badgeEl = el.querySelector('[data-badge]');
      const logEl = el.querySelector('[data-log]');
      const startBtn = el.querySelector('[data-start]');
      const pauseBtn = el.querySelector('[data-pause]');
      let badge = 'idle', running = false;
      if (job) {
        running = !!job.running;
        if (job.running) badge = 'running';
        else if (job.interrupted) badge = 'paused';
        else if (job.returncode === 0) badge = 'ok';
        else if (job.returncode !== null) badge = 'failed';
        if (job.lines && job.lines.length) {
          logEl.textContent = job.lines.join('\\n');
          if (el.dataset.detailsOpen === '1') { logEl.classList.add('show'); logEl.scrollTop = logEl.scrollHeight; }
        }
      }
      badgeEl.textContent = badge;
      badgeEl.className = 'badge ' + badge;
      if (startBtn) {
        startBtn.style.display = running ? 'none' : 'inline-block';
        startBtn.disabled = !running && data.any_running;
      }
      if (pauseBtn) pauseBtn.style.display = running ? 'inline-block' : 'none';
    });
  });
}
document.addEventListener('click', (e) => {
  const details = e.target.closest('[data-details-toggle]');
  if (details) {
    e.preventDefault();
    const stageEl = details.closest('.stage');
    const open = stageEl.dataset.detailsOpen === '1';
    stageEl.dataset.detailsOpen = open ? '0' : '1';
    stageEl.querySelector('[data-log]').classList.toggle('show', !open);
    details.textContent = open ? 'Details' : 'Hide details';
    return;
  }
  const startBtn = e.target.closest('[data-start]');
  if (startBtn) {
    const key = startBtn.closest('.stage').dataset.key;
    startBtn.disabled = true;
    fetch('/run/' + key, {method: 'POST'}).then(r => {
      if (!r.ok) return r.json().then(d => alert(d.error || 'Could not start this stage.')).catch(() => alert('Could not start this stage (another stage may already be running).'));
    }).catch(() => {}).finally(() => { startBtn.disabled = false; });
    return;
  }
  const pauseBtn = e.target.closest('[data-pause]');
  if (pauseBtn) {
    pauseBtn.disabled = true;
    fetch('/pause', {method: 'POST'}).finally(() => { pauseBtn.disabled = false; });
    return;
  }
});
setInterval(pollStageGroup, 1500);
pollStageGroup();
"""


def stage_group_html(keys: list[str], title: str = "Pipeline stages") -> str:
    rows = []
    for key in keys:
        stage = STAGE_BY_KEY[key]
        rows.append(f"""
        <div class="stage" data-key="{stage['key']}">
          <div class="stage-head">
            <span class="badge idle" data-badge>idle</span>
            <strong>{html.escape(stage['label'])}</strong>
            <button type="button" class="btn btn-outline btn-sm" data-start>Start</button>
            <button type="button" class="btn btn-outline btn-sm" data-pause style="display:none;">Pause</button>
            <a href="#" class="details-toggle" data-details-toggle style="font-size:11.5px;color:var(--text-faint);">Details</a>
          </div>
          <pre class="log" data-log></pre>
        </div>""")
    return f'<div><div class="stage-group-title">{html.escape(title)}</div>{"".join(rows)}</div>'


def _render_index(db_path: str, exports_dir: str, source_dir: str, size: str, orientation: str) -> bytes:
    status = _project_status(db_path, exports_dir)
    any_running = _any_running()
    extra_head = f"""
<style>
.stat-row{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;}}
.stat-tile{{padding:16px 18px;}}
.stat-value{{font-family:'Newsreader',Georgia,serif;font-size:24px;font-weight:600;}}
.stat-label{{font-size:12px;color:var(--text-muted);margin-top:4px;}}
form.config{{display:flex;flex-wrap:wrap;align-items:center;gap:10px;}}
form.config input[type=text]{{flex:1;min-width:220px;}}
{STAGE_GROUP_CSS}
</style>"""
    body = f"""
<div class="stat-row">
<div class="card stat-tile"><div class="stat-value">{status.get('photos', 0)}</div><div class="stat-label">Photos imported</div></div>
<div class="card stat-tile"><div class="stat-value">{'&#10003;' if status.get('spreads_json') else '&mdash;'}</div><div class="stat-label">Spreads planned</div></div>
<div class="card stat-tile"><div class="stat-value">{status.get('people', 0)}</div><div class="stat-label">People found</div></div>
<div class="card stat-tile"><div class="stat-value">{status.get('rendered_count', 0)}</div><div class="stat-label">Rendered spreads</div></div>
<div class="card stat-tile"><div class="stat-value">{'&#10003;' if status.get('album_pdf') else '&mdash;'}</div><div class="stat-label">Album PDF</div></div>
</div>

<form class="config card" style="padding:14px 18px;" id="sourceForm">
  <label style="font-size:12.5px;color:var(--text-muted);">Source photo directory (used by Import):</label>
  <input type="text" id="sourceInput" name="source_dir" value="{html.escape(source_dir)}" placeholder="D:\\path\\to\\event\\photos">
  <button type="button" id="chooseFolderBtn" class="btn btn-outline">Choose Folder&hellip;</button>
</form>

<form class="config card" style="padding:14px 18px;display:flex;flex-wrap:wrap;gap:16px;">
  <div>
    <label style="font-size:12.5px;color:var(--text-muted);display:block;margin-bottom:6px;">Print size (page ratio drives layout &mdash; choose before starting):</label>
    <select id="sizeSelect">
      <option value="" {'selected' if not size else ''} disabled>-- choose a size --</option>
      {''.join(f'<option value="{s}" {"selected" if s == size else ""}>{html.escape(PRINT_SIZE_LABELS.get(s, s))}</option>' for s in PRINT_SIZES)}
    </select>
  </div>
  <div>
    <label style="font-size:12.5px;color:var(--text-muted);display:block;margin-bottom:6px;">Orientation:</label>
    <select id="orientationSelect">
      <option value="landscape" {"selected" if orientation != "portrait" else ""}>Landscape (spread wider than tall)</option>
      <option value="portrait" {"selected" if orientation == "portrait" else ""}>Portrait (spread taller than wide)</option>
    </select>
  </div>
</form>

<div class="card" style="padding:14px 18px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
  <button id="newProjectBtn" class="btn btn-danger">{'Stop &amp; Clear Project' if any_running else 'New Project (Clear)'}</button>
  <span style="font-size:11.5px;color:var(--text-faint);">Permanently deletes the project database and all exports so far
  (photos on disk in your source folder are never touched).</span>
</div>

{stage_group_html(SETUP_STAGES, "Setup stages")}
"""
    extra_script = ("""
document.getElementById('sourceInput').addEventListener('change', () => {
  fetch('/set-source-dir?source_dir=' + encodeURIComponent(document.getElementById('sourceInput').value), {method: 'POST'});
});
document.getElementById('chooseFolderBtn').addEventListener('click', () => {
  const btn = document.getElementById('chooseFolderBtn');
  btn.disabled = true;
  btn.textContent = 'Waiting for dialog...';
  fetch('/pick-folder', {method: 'POST'}).then(r => r.json()).then(data => {
    btn.disabled = false;
    btn.textContent = 'Choose Folder\\u2026';
    if (data.path) document.getElementById('sourceInput').value = data.path;
  }).catch(() => { btn.disabled = false; btn.textContent = 'Choose Folder\\u2026'; });
});
document.getElementById('sizeSelect').addEventListener('change', (e) => {
  fetch('/set-size?size=' + encodeURIComponent(e.target.value), {method: 'POST'});
});
document.getElementById('orientationSelect').addEventListener('change', (e) => {
  fetch('/set-orientation?orientation=' + encodeURIComponent(e.target.value), {method: 'POST'});
});
document.getElementById('newProjectBtn').addEventListener('click', () => {
  if (!confirm('This will permanently DELETE the project database and all exports (photos on disk are untouched). Continue?')) return;
  fetch('/stop-project', {method: 'POST'}).then(() => location.reload());
});
""" + STAGE_GROUP_SCRIPT)
    return web_theme.page_shell("/", "Setup", "Choose photos & print size, then run the early pipeline stages",
                                 body, extra_head, extra_script)


def make_handler(db_path: str, exports_dir: str, state: dict):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                qs = urllib.parse.parse_qs(parsed.query)
                if "source_dir" in qs:
                    state["source_dir"] = qs["source_dir"][0]
                body = _render_index(db_path, exports_dir, state["source_dir"], state.get("size", ""),
                                      state.get("orientation", "landscape"))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/status":
                with _jobs_lock:
                    jobs = dict(_jobs)
                proj = _project_status(db_path, exports_dir)
                ready = {
                    "/people/": proj.get("people", 0) > 0,
                    "/storyboard/": proj.get("spreads_json", False),
                    "/editor/": proj.get("rendered_count", 0) > 0,
                    "/export/": proj.get("rendered_count", 0) > 0,
                }
                body = json.dumps({"jobs": jobs, "ready": ready, "any_running": _any_running()}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def _accept(self):
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send_json(self, body: bytes, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path.startswith("/run/"):
                key = path.rsplit("/", 1)[-1]
                if key not in STAGE_BY_KEY:
                    self.send_response(404)
                    self.end_headers()
                    return
                if key in SIZE_REQUIRED_STAGES and not state.get("size"):
                    self._send_json(json.dumps({"error": "Choose a print size on Setup before running this stage."}).encode("utf-8"), 400)
                    return
                if _any_running():
                    self._send_json(json.dumps({"error": "Another stage is already running -- pause it first."}).encode("utf-8"), 409)
                    return
                threading.Thread(
                    target=_run_stage_blocking,
                    args=(key, db_path, exports_dir, state["source_dir"], state.get("size", ""),
                          state.get("orientation", "landscape")),
                    daemon=True,
                ).start()
                self._accept()
                return

            if path == "/pause":
                _terminate_current()
                self._accept()
                return

            if path == "/stop-project":
                _terminate_current()
                _wipe_project(db_path, exports_dir)
                state["size"] = ""
                state["orientation"] = "landscape"
                _save_ui_state(exports_dir, state)
                self._accept()
                return

            if path == "/set-source-dir":
                qs = urllib.parse.parse_qs(parsed.query)
                state["source_dir"] = qs.get("source_dir", [""])[0]
                self._accept()
                return

            if path == "/set-size":
                qs = urllib.parse.parse_qs(parsed.query)
                value = qs.get("size", [""])[0]
                if value and value not in PRINT_SIZES:
                    self.send_response(400); self.end_headers(); return
                state["size"] = value
                _save_ui_state(exports_dir, state)
                self._accept()
                return

            if path == "/set-orientation":
                qs = urllib.parse.parse_qs(parsed.query)
                value = qs.get("orientation", ["landscape"])[0]
                if value not in ORIENTATIONS:
                    self.send_response(400); self.end_headers(); return
                state["orientation"] = value
                _save_ui_state(exports_dir, state)
                self._accept()
                return

            if path == "/pick-folder":
                # Native OS folder-picker dialog, since this is a local single-user desktop
                # tool -- a browser <input type=file webkitdirectory> can't return a real
                # absolute path, so we ask the OS directly via Tk's file dialog instead.
                try:
                    import tkinter
                    from tkinter import filedialog
                    root = tkinter.Tk()
                    root.withdraw()
                    root.attributes("-topmost", True)
                    chosen = filedialog.askdirectory(
                        initialdir=state.get("source_dir") or None, title="Choose source photo folder",
                    )
                    root.destroy()
                except Exception:
                    chosen = ""
                if chosen:
                    state["source_dir"] = chosen
                body = json.dumps({"path": chosen or ""}).encode("utf-8")
                self._send_json(body)
                return

            self.send_response(404)
            self.end_headers()

    return Handler


def run(db_path: str, exports_dir: str, source_dir: str, port: int) -> None:
    state = {"source_dir": source_dir, "size": "", "orientation": "landscape"}
    state.update(_load_ui_state(exports_dir))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(db_path, exports_dir, state))
    print(f"Serving project pipeline UI at http://127.0.0.1:{port}/  (db: {db_path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local web UI to run the project pipeline")
    parser.add_argument("--db", default="cache/project.db")
    parser.add_argument("--exports", default="exports")
    parser.add_argument("--source-dir", default="")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    run(args.db, args.exports, args.source_dir, args.port)
