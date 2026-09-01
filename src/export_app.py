"""Phase 3 fifth slice: export screen (idea §19) -- print size/style at a glance,
preflight check results, and a PDF export trigger. Wraps `export_pdf.py`'s existing
`run_preflight`/`export_pdf` functions (no new preflight/export logic); this module is
just the browser UI over them, consistent with the other Phase 3 apps.

Print size and design style are chosen at app startup (`--size`/`--style`, same flags
`spread_editor_app.py` already takes for regenerate) rather than picked per-export here --
changing them means re-rendering every spread at the new size/style first (Dashboard's
pipeline or Spread Editor's regenerate-all), so this screen displays the active choice and
explains that, rather than offering a selector that would silently do nothing.
"""

import argparse
import html
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import web_theme
from export_pdf import export_pdf, run_preflight
from layout_geometry import DEFAULT_SIZE
from style_stage import DEFAULT_STYLE

SIZES = ["12x18", "12x24", "12x30", "12x36"]


def _preflight_html(results: list[dict]) -> str:
    if not results:
        return '<p style="color:var(--text-faint);font-size:12.5px;">No rendered spreads yet -- run the pipeline first.</p>'
    rows = []
    for r in results:
        ok = r.get("ok", False)
        name = html.escape(Path(r["path"]).name)
        if ok:
            rows.append(f"""
            <div class="preflight-row">
              <span class="badge-dot done"></span>
              <div>{name}</div>
            </div>""")
        else:
            issues = "; ".join(r.get("issues", []))
            rows.append(f"""
            <div class="preflight-row warn">
              <span class="badge-dot failed"></span>
              <div>{name}<div class="detail">{html.escape(issues)}</div></div>
            </div>""")
    return "".join(rows)


def _render_index(rendered_dir: str, spreads_path: str, dest_dir: str, size: str, style: str,
                   mount: str, message: str | None) -> bytes:
    results = run_preflight(rendered_dir, spreads_path, size) if Path(spreads_path).exists() else []
    passed = sum(1 for r in results if r.get("ok"))
    can_export = bool(results) and bool(dest_dir)
    extra_head = """
<style>
.preflight-row{display:flex;align-items:flex-start;gap:10px;padding:9px 0;border-bottom:1px solid var(--border-soft);font-size:12.5px;}
.preflight-row:last-child{border-bottom:none;}
.preflight-row .badge-dot{margin-top:4px;}
.preflight-row.warn{color:var(--brown);}
.detail{color:var(--text-faint);font-size:11.5px;margin-top:2px;}
.kv{display:flex;gap:8px;font-size:12.5px;padding:4px 0;}
.kv .k{color:var(--text-faint);width:110px;flex-shrink:0;}
#exportStatus{margin-top:10px;font-size:12.5px;color:var(--emerald-strong);font-weight:600;}
form.config{display:flex;flex-wrap:wrap;align-items:center;gap:10px;}
form.config input[type=text]{flex:1;min-width:220px;}
</style>"""
    message_html = f'<p style="color:var(--emerald-strong);font-weight:600;">{html.escape(message)}</p>' if message else ""
    body = f"""
{message_html}
<div class="card" style="padding:18px 20px;">
  <div class="section-title" style="margin-bottom:10px;">Current output settings</div>
  <div class="kv"><div class="k">Print size</div><div>{html.escape(size)}</div></div>
  <div class="kv"><div class="k">Design style</div><div>{html.escape(style)}</div></div>
  <div class="kv"><div class="k">Rendered dir</div><div>{html.escape(rendered_dir)}</div></div>
  <p style="font-size:11.5px;color:var(--text-faint);margin-top:8px;">To change size or style, re-render
  from the Dashboard pipeline or Spread Editor's "regenerate all", then reopen this screen.</p>
</div>

<form class="config card" style="padding:14px 18px;" id="destForm">
  <label style="font-size:12.5px;color:var(--text-muted);">Export destination folder (album.pdf is written here):</label>
  <input type="text" id="destInput" name="dest_dir" value="{html.escape(dest_dir)}" placeholder="D:\\path\\to\\deliver">
  <button type="button" id="chooseDestBtn" class="btn btn-outline">Choose Folder&hellip;</button>
</form>

<div class="card" style="padding:18px 20px;">
  <div class="section-title" style="margin-bottom:10px;">Preflight check ({passed} / {len(results)} passed)</div>
  {_preflight_html(results)}
</div>

<div class="card" style="padding:18px 20px;">
  <p style="font-size:11.5px;color:var(--brown);font-weight:600;">After a successful export, the working
  project data (database + exports folder: spreads, crops, rendered pages) is cleared automatically so you
  can start the next project fresh. Your source photo folder and the exported PDF (already written to the
  destination above) are never touched.</p>
  <button id="exportBtn" class="btn btn-primary" {'disabled' if not can_export else ''}>Export PDF</button>
  <span id="exportStatus"></span>
</div>"""
    extra_script = ("""
document.getElementById('chooseDestBtn').addEventListener('click', () => {
  const btn = document.getElementById('chooseDestBtn');
  btn.disabled = true;
  btn.textContent = 'Waiting for dialog...';
  fetch('""" + mount + """/pick-dest-folder', {method: 'POST'}).then(r => r.json()).then(data => {
    btn.disabled = false;
    btn.textContent = 'Choose Folder\\u2026';
    if (data.path) {
      document.getElementById('destInput').value = data.path;
      document.getElementById('exportBtn').disabled = false;
    }
  }).catch(() => { btn.disabled = false; btn.textContent = 'Choose Folder\\u2026'; });
});
document.getElementById('destInput').addEventListener('change', (e) => {
  fetch('""" + mount + """/set-dest-folder?dest_dir=' + encodeURIComponent(e.target.value), {method: 'POST'});
});
const btn = document.getElementById('exportBtn');
if (btn) {
  btn.addEventListener('click', () => {
    btn.disabled = true;
    document.getElementById('exportStatus').textContent = 'Exporting...';
    fetch('""" + mount + """/export', {method: 'POST'}).then(r => r.json()).then(data => {
      document.getElementById('exportStatus').textContent = data.ok
        ? ('Exported ' + data.pages + ' pages to ' + data.out + '. Project data cleared -- ready for the next project.')
        : ('Failed: ' + data.error);
      btn.disabled = !data.ok ? false : true;
    }).catch(() => {
      document.getElementById('exportStatus').textContent = 'Export failed (network error).';
      btn.disabled = false;
    });
  });
}
""")
    return web_theme.page_shell(
        "/export/", "Export", "Print size, preflight, and PDF export", body, extra_head, extra_script,
    )


def make_handler(rendered_dir: str, spreads_path: str, db_path: str, exports_dir: str, size, style: str,
                  mount: str = ""):
    # `size` is either a plain print-size string or the project-wide state dict shared
    # with project_app's Dashboard (app.py wires it that way), resolved fresh per request
    # so a size chosen there after this handler was constructed is picked up here too.
    def _size() -> str:
        return (size.get("size") or DEFAULT_SIZE) if isinstance(size, dict) else size

    # Chosen on this screen via native folder dialog, per user request (2026-09-01): export
    # asks where to deliver the PDF rather than always writing into the (about-to-be-wiped)
    # working exports/ folder.
    dest_state = {"dest_dir": ""}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send_json(self, body: bytes, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/" or self.path == "":
                body = _render_index(rendered_dir, spreads_path, dest_state["dest_dir"], _size(), style, mount, None)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/set-dest-folder":
                qs = urllib.parse.parse_qs(parsed.query)
                dest_state["dest_dir"] = qs.get("dest_dir", [""])[0]
                self._send_json(b"{}")
                return

            if path == "/pick-dest-folder":
                try:
                    import tkinter
                    from tkinter import filedialog
                    root = tkinter.Tk()
                    root.withdraw()
                    root.attributes("-topmost", True)
                    chosen = filedialog.askdirectory(
                        initialdir=dest_state["dest_dir"] or None, title="Choose export destination folder",
                    )
                    root.destroy()
                except Exception:
                    chosen = ""
                if chosen:
                    dest_state["dest_dir"] = chosen
                self._send_json(json.dumps({"path": chosen or ""}).encode("utf-8"))
                return

            if path != "/export":
                self.send_response(404)
                self.end_headers()
                return

            with lock:
                try:
                    dest_dir = dest_state["dest_dir"]
                    if not dest_dir:
                        raise RuntimeError("choose a destination folder first")
                    os.makedirs(dest_dir, exist_ok=True)
                    out_pdf = os.path.join(dest_dir, "album.pdf")
                    current_size = _size()
                    results = run_preflight(rendered_dir, spreads_path, current_size)
                    failed = [r for r in results if not r.get("ok")]
                    if failed:
                        raise RuntimeError(f"{len(failed)} spread(s) failed preflight")
                    export_pdf(rendered_dir, spreads_path, out_pdf, skip_failed=False, size=current_size)
                    # Export succeeded and the PDF is safely outside exports_dir now -- clear
                    # the working project data (DB + exports/ folder: spreads.json,
                    # crops.json, rendered_spreads/) so the next project starts fresh, per
                    # user request (2026-09-01). Source photos are never touched -- this only
                    # ever deletes db_path and exports_dir.
                    from project_app import _chain_lock, _chain_state, _wipe_project
                    _wipe_project(db_path, exports_dir)
                    with _chain_lock:
                        _chain_state["current_index"] = 0
                        _chain_state["finished"] = False
                        _chain_state["failed_key"] = None
                        _chain_state["checkpoint_key"] = None
                    resp = {"ok": True, "pages": len(results), "out": out_pdf}
                except Exception as e:
                    resp = {"ok": False, "error": str(e)}
            self._send_json(json.dumps(resp).encode("utf-8"))

    return Handler


def run(rendered_dir: str, spreads_path: str, db_path: str, exports_dir: str, size: str, style: str,
        port: int, mount: str = "") -> None:
    server = ThreadingHTTPServer(
        ("127.0.0.1", port), make_handler(rendered_dir, spreads_path, db_path, exports_dir, size, style, mount),
    )
    print(f"Serving export UI at http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local web UI for preflight + PDF export")
    parser.add_argument("--rendered-dir", default="exports/rendered_spreads")
    parser.add_argument("--spreads", default="exports/spreads.json")
    parser.add_argument("--db", default="cache/project_full.db")
    parser.add_argument("--exports", default="exports")
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument("--style", default=DEFAULT_STYLE)
    parser.add_argument("--port", type=int, default=8004)
    args = parser.parse_args()
    run(args.rendered_dir, args.spreads, args.db, args.exports, args.size, args.style, args.port)
