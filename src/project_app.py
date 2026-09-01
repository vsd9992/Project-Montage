"""Phase 3 (project-plan.md) third slice: project management -- run the Phase 1/2 batch
pipeline (import -> burst -> quality -> Qwen -> shortlist -> spread -> face -> people-
cluster -> crop -> render -> PDF export) from a browser UI instead of hand-typed CLI
commands, and see at a glance how far a project has gotten.

Stdlib-only (http.server), same pattern as `label_people_app.py` /
`reorder_spreads_app.py`. Each stage is a long-running batch script (`import_stage.py`
imports thousands of photos, `qwen_stage.py` runs local LLM inference, etc.) -- these run
as a subprocess in a background thread so the HTTP request that starts a stage returns
immediately; the page polls `/status` to show a prominent "running" banner, per-stage
badges, and live log output, rather than holding the browser connection open for a
multi-minute (or longer) run.

Chain controls (added after user feedback, 2026-09-01): a single Start button runs every
stage in order, auto-advancing on success. Pause terminates the currently running
subprocess and halts auto-advance -- resuming later re-enters at the same stage, which is
safe because every stage is already designed to be resumable/idempotent (skips
already-processed rows/files). Stop terminates the current subprocess AND wipes the whole
project (DB file + exports dir) back to empty, per explicit user decision (2026-09-01):
"wipe the whole project" was chosen over partial/no clearing, specifically because trying
to undo only "what this run added" can't be done precisely (e.g. import_stage adds photos
incrementally with no per-run marker). This is destructive and irreversible -- the UI
requires a JS confirmation before calling it.
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
     "extra_args": ["--spreads", "{exports}/spreads.json", "--out", "{exports}/crops.json"]},
    {"key": "render", "label": "Render spreads", "script": "render_stage.py",
     "extra_args": ["--spreads", "{exports}/spreads.json", "--crops", "{exports}/crops.json",
                     "--out-dir", "{exports}/rendered_spreads"]},
    {"key": "export_pdf", "label": "Export PDF", "script": "export_pdf.py",
     "extra_args": ["--rendered-dir", "{exports}/rendered_spreads", "--spreads", "{exports}/spreads.json",
                     "--out", "{exports}/album.pdf"]},
]
STAGE_BY_KEY = {s["key"]: s for s in STAGES}
STAGE_KEYS = [s["key"] for s in STAGES]
MAX_LOG_LINES = 300

_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}  # key -> {"running", "returncode", "lines", "started_at", "interrupted"}

_proc_lock = threading.Lock()
_current_proc = {"proc": None, "key": None}

_chain_lock = threading.Lock()
_chain_state = {
    "running": False,
    "current_index": 0,       # next stage index to (re)run
    "finished": False,
    "failed_key": None,
    "pause_requested": False,
    "stop_requested": False,
}


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


def _build_args(stage: dict, db_path: str, exports_dir: str, source_dir: str) -> list[str]:
    args = []
    if stage.get("needs_source_dir"):
        args.append(source_dir)
    args += ["--db", db_path]
    for a in stage.get("extra_args", []):
        args.append(a.replace("{exports}", exports_dir))
    return args


def _run_stage_blocking(key: str, db_path: str, exports_dir: str, source_dir: str) -> int:
    """Runs one stage to completion (or until terminated by pause/stop), updating _jobs
    and _current_proc live. Returns the process return code (negative if terminated)."""
    stage = STAGE_BY_KEY[key]
    Path(exports_dir).mkdir(parents=True, exist_ok=True)
    script_path = SRC_DIR / stage["script"]
    args = _build_args(stage, db_path, exports_dir, source_dir)
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


def _run_chain(db_path: str, exports_dir: str, source_dir: str) -> None:
    with _chain_lock:
        _chain_state["running"] = True
        _chain_state["finished"] = False
        _chain_state["failed_key"] = None
        start_index = _chain_state["current_index"]

    idx = start_index
    while idx < len(STAGE_KEYS):
        with _chain_lock:
            if _chain_state["stop_requested"] or _chain_state["pause_requested"]:
                break
            _chain_state["current_index"] = idx
        key = STAGE_KEYS[idx]
        rc = _run_stage_blocking(key, db_path, exports_dir, source_dir)

        with _chain_lock:
            stop = _chain_state["stop_requested"]
            pause = _chain_state["pause_requested"]
        if stop or pause:
            break
        if rc != 0:
            with _chain_lock:
                _chain_state["failed_key"] = key
            break
        idx += 1

    with _chain_lock:
        stop = _chain_state["stop_requested"]
        pause = _chain_state["pause_requested"]
        _chain_state["running"] = False
        if not stop and not pause and _chain_state["failed_key"] is None:
            _chain_state["current_index"] = len(STAGE_KEYS)
            _chain_state["finished"] = True
        _chain_state["pause_requested"] = False

    if stop:
        _wipe_project(db_path, exports_dir)
        with _chain_lock:
            _chain_state["current_index"] = 0
            _chain_state["finished"] = False
            _chain_state["failed_key"] = None
            _chain_state["stop_requested"] = False


def _render_index(db_path: str, exports_dir: str, source_dir: str) -> bytes:
    status = _project_status(db_path, exports_dir)
    with _chain_lock:
        chain = dict(_chain_state)
    rows = []
    for i, stage in enumerate(STAGES):
        with _jobs_lock:
            job = _jobs.get(stage["key"])
        running = job["running"] if job else False
        rc = job["returncode"] if job else None
        interrupted = job["interrupted"] if job else False
        if running:
            badge = "running"
        elif interrupted:
            badge = "paused"
        elif rc == 0:
            badge = "ok"
        elif rc not in (None, 0):
            badge = "failed"
        else:
            badge = "idle"
        rows.append(f"""
        <div class="stage" data-key="{stage['key']}">
          <div class="stage-head">
            <span class="badge {badge}" data-badge>{badge}</span>
            <strong>{i + 1}. {html.escape(stage['label'])}</strong>
            <code>{html.escape(stage['script'])}</code>
            <button data-run="{stage['key']}" {'disabled' if running else ''}>Run</button>
          </div>
          <pre class="log" data-log></pre>
        </div>""")
    extra_head = """
<style>
#banner { padding:14px 18px; border-radius:12px; background:var(--royal-soft); border:1px solid var(--royal);
          font-weight:600; display:none; align-items:center; gap:0.9em; color:var(--text); }
#banner.show { display:flex; }
.spinner { width:14px; height:14px; border:2px solid var(--royal); border-top-color:transparent;
           border-radius:50%; animation:spin 0.8s linear infinite; }
@keyframes spin { to { transform:rotate(360deg); } }
.stat-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;}
.stat-tile{padding:16px 18px;}
.stat-value{font-family:'Newsreader',Georgia,serif;font-size:24px;font-weight:600;}
.stat-label{font-size:12px;color:var(--text-muted);margin-top:4px;}
form.config{display:flex;flex-wrap:wrap;align-items:center;gap:10px;}
form.config input[type=text]{flex:1;min-width:220px;}
.chain-controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;}
.stage{border:1px solid var(--border);border-radius:10px;padding:12px 16px;margin-bottom:8px;background:var(--bg-elev);}
.stage-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.stage-head code{color:var(--text-faint);font-size:11.5px;}
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
</style>"""
    body = f"""
<div id="banner"><span class="spinner"></span><span id="bannerText">Working...</span></div>

<div class="stat-row">
<div class="card stat-tile"><div class="stat-value">{status.get('photos', 0)}</div><div class="stat-label">Photos imported</div></div>
<div class="card stat-tile"><div class="stat-value">{'&#10003;' if status.get('spreads_json') else '&mdash;'}</div><div class="stat-label">Spreads planned</div></div>
<div class="card stat-tile"><div class="stat-value">{status.get('people', 0)}</div><div class="stat-label">People found</div></div>
<div class="card stat-tile"><div class="stat-value">{status.get('rendered_count', 0)}</div><div class="stat-label">Rendered spreads</div></div>
<div class="card stat-tile"><div class="stat-value">{'&#10003;' if status.get('album_pdf') else '&mdash;'}</div><div class="stat-label">Album PDF</div></div>
</div>

<form class="config card" style="padding:14px 18px;" method="get" action="/">
  <label style="font-size:12.5px;color:var(--text-muted);">Source photo directory (used by Import):</label>
  <input type="text" name="source_dir" value="{html.escape(source_dir)}" placeholder="D:\\path\\to\\event\\photos">
  <button type="submit" class="btn btn-outline">Choose Folder</button>
</form>

<div class="chain-controls">
  <button id="startBtn" class="btn btn-primary">Start / Resume Pipeline</button>
  <button id="pauseBtn" class="btn btn-outline">Pause</button>
  <button id="stopBtn" class="btn btn-danger">Stop &amp; Clear Project</button>
  <span id="chainText" style="font-size:12.5px;color:var(--text-muted);"></span>
</div>

<div>
<div class="section-title" style="margin-bottom:10px;">Pipeline stages</div>
{''.join(rows)}
</div>
"""
    extra_script = ("""
function poll() {
  fetch('/status').then(r => r.json()).then(data => {
    const jobs = data.jobs, chain = data.chain;
    for (const key in jobs) {
      const el = document.querySelector('.stage[data-key="' + key + '"]');
      if (!el) continue;
      const badgeEl = el.querySelector('[data-badge]');
      const logEl = el.querySelector('[data-log]');
      const btn = el.querySelector('[data-run]');
      const job = jobs[key];
      let badge = 'idle';
      if (job.running) badge = 'running';
      else if (job.interrupted) badge = 'paused';
      else if (job.returncode === 0) badge = 'ok';
      else if (job.returncode !== null) badge = 'failed';
      badgeEl.textContent = badge;
      badgeEl.className = 'badge ' + badge;
      btn.disabled = job.running;
      if (job.lines && job.lines.length) {
        logEl.textContent = job.lines.join('\\n');
        logEl.classList.add('show');
        logEl.scrollTop = logEl.scrollHeight;
      }
    }

    const banner = document.getElementById('banner');
    const bannerText = document.getElementById('bannerText');
    const anyRunning = Object.values(jobs).some(j => j.running);
    if (chain.running) {
      const stageKey = Object.keys(jobs).find(k => jobs[k].running);
      const label = stageKey ? stageKey : ('stage ' + (chain.current_index + 1));
      bannerText.textContent = 'Pipeline running -- ' + label + ' (' + (chain.current_index + 1) + '/""" + str(len(STAGES)) + """)...';
      banner.classList.add('show');
    } else if (anyRunning) {
      const stageKey = Object.keys(jobs).find(k => jobs[k].running);
      bannerText.textContent = 'Running: ' + stageKey + '...';
      banner.classList.add('show');
    } else {
      banner.classList.remove('show');
    }

    const chainText = document.getElementById('chainText');
    if (chain.finished) chainText.textContent = 'All stages complete.';
    else if (chain.failed_key) chainText.textContent = 'Stopped: ' + chain.failed_key + ' failed.';
    else if (chain.running) chainText.textContent = 'Running...';
    else if (chain.current_index > 0) chainText.textContent = 'Paused at stage ' + (chain.current_index + 1) + '.';
    else chainText.textContent = '';

    document.getElementById('startBtn').disabled = chain.running;
    document.getElementById('pauseBtn').disabled = !chain.running;
  });
}
document.querySelectorAll('[data-run]').forEach(btn => {
  btn.addEventListener('click', () => {
    btn.disabled = true;
    fetch('/run/' + btn.dataset.run, {method: 'POST'});
  });
});
document.getElementById('startBtn').addEventListener('click', () => {
  fetch('/chain/start', {method: 'POST'});
});
document.getElementById('pauseBtn').addEventListener('click', () => {
  fetch('/chain/pause', {method: 'POST'});
});
document.getElementById('stopBtn').addEventListener('click', () => {
  if (!confirm('Stop will permanently DELETE the project database and all exports (photos on disk are untouched). Continue?')) return;
  fetch('/chain/stop', {method: 'POST'}).then(() => location.reload());
});
setInterval(poll, 1500);
poll();
""")
    return web_theme.page_shell("/", "Dashboard", "Pipeline status and controls", body, extra_head, extra_script)


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
                body = _render_index(db_path, exports_dir, state["source_dir"])
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/status":
                with _jobs_lock:
                    jobs = dict(_jobs)
                with _chain_lock:
                    chain = dict(_chain_state)
                body = json.dumps({"jobs": jobs, "chain": chain}).encode("utf-8")
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

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path.startswith("/run/"):
                key = path.rsplit("/", 1)[-1]
                if key not in STAGE_BY_KEY:
                    self.send_response(404)
                    self.end_headers()
                    return
                with _jobs_lock:
                    already_running = _jobs.get(key, {}).get("running", False)
                if already_running:
                    self.send_response(409)
                    self.end_headers()
                    return
                threading.Thread(
                    target=_run_stage_blocking, args=(key, db_path, exports_dir, state["source_dir"]),
                    daemon=True,
                ).start()
                self._accept()
                return

            if path == "/chain/start":
                with _chain_lock:
                    if _chain_state["running"]:
                        self.send_response(409)
                        self.end_headers()
                        return
                    _chain_state["stop_requested"] = False
                    _chain_state["pause_requested"] = False
                    if _chain_state["finished"]:
                        _chain_state["current_index"] = 0
                        _chain_state["finished"] = False
                threading.Thread(
                    target=_run_chain, args=(db_path, exports_dir, state["source_dir"]), daemon=True,
                ).start()
                self._accept()
                return

            if path == "/chain/pause":
                with _chain_lock:
                    _chain_state["pause_requested"] = True
                _terminate_current()
                self._accept()
                return

            if path == "/chain/stop":
                with _chain_lock:
                    _chain_state["stop_requested"] = True
                _terminate_current()
                # if no chain was running, wipe happens inline here; if a chain thread is
                # running it observes stop_requested and wipes itself after unwinding.
                with _chain_lock:
                    chain_running = _chain_state["running"]
                if not chain_running:
                    _wipe_project(db_path, exports_dir)
                    with _chain_lock:
                        _chain_state["current_index"] = 0
                        _chain_state["finished"] = False
                        _chain_state["failed_key"] = None
                        _chain_state["stop_requested"] = False
                self._accept()
                return

            self.send_response(404)
            self.end_headers()

    return Handler


def run(db_path: str, exports_dir: str, source_dir: str, port: int) -> None:
    state = {"source_dir": source_dir}
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
