"""Single entry point for Album Studio: one process, one public port, one browser tab.

Replaces running `project_app.py` / `label_people_app.py` / `reorder_spreads_app.py` /
`spread_editor_app.py` / `export_app.py` as four-to-five separate processes on separate
ports. Each of those keeps its own internal `ThreadingHTTPServer` (unchanged logic, just
given a `mount` prefix so the HTML it generates links correctly -- see `web_theme.py`),
run on a private localhost-only port in a background thread; this module's own server is
the only one bound to a port the user actually navigates to, and reverse-proxies each
request to the right internal server by path prefix.

The Qwen3-VL model itself is never started here -- it only ever starts on demand, either
when the pipeline reaches the Qwen stage (`project_app`'s existing subprocess-per-stage
design) or when the spread editor's chat is first used (`spread_editor_app`'s lazy
`start_server()` call) -- launching this app does not load any AI model.
"""

import argparse
import json
import threading
import webbrowser
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import export_app
import label_people_app
import project_app
import reorder_spreads_app
import spread_editor_app
from layout_geometry import DEFAULT_SIZE
from style_stage import DEFAULT_STYLE

# internal-only ports, not the one the user opens in a browser
_PORT_DASHBOARD = 8101
_PORT_PEOPLE = 8102
_PORT_STORYBOARD = 8103
_PORT_EDITOR = 8104
_PORT_EXPORT = 8105

HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "content-length", "server", "date"}


def _start_background(server: ThreadingHTTPServer) -> None:
    threading.Thread(target=server.serve_forever, daemon=True).start()


def _proxy(handler: BaseHTTPRequestHandler, port: int, forward_path: str) -> None:
    length = int(handler.headers.get("Content-Length", 0))
    body = handler.rfile.read(length) if length else None
    conn = HTTPConnection("127.0.0.1", port, timeout=600)
    headers = {k: v for k, v in handler.headers.items() if k.lower() not in HOP_BY_HOP and k.lower() != "host"}
    try:
        conn.request(handler.command, forward_path or "/", body=body, headers=headers)
        resp = conn.getresponse()
        resp_body = resp.read()
        handler.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in HOP_BY_HOP:
                handler.send_header(k, v)
        handler.send_header("Content-Length", str(len(resp_body)))
        handler.end_headers()
        handler.wfile.write(resp_body)
    except (ConnectionRefusedError, OSError):
        handler.send_response(502)
        handler.end_headers()
        handler.wfile.write(b"Section not ready yet -- try again in a moment.")
    finally:
        conn.close()


# (prefix, internal_port) -- checked in order, longest/most-specific first
ROUTES = [
    ("/people", _PORT_PEOPLE),
    ("/storyboard", _PORT_STORYBOARD),
    ("/editor", _PORT_EDITOR),
    ("/export", _PORT_EXPORT),
]


def make_router(engine_state: dict):
    class Router(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _dispatch(self):
            path = self.path
            if path == "/api/engine-status":
                self._engine_status()
                return
            for prefix, port in ROUTES:
                if path == prefix or path.startswith(prefix + "/"):
                    suffix = path[len(prefix):]
                    _proxy(self, port, suffix or "/")
                    return
            _proxy(self, _PORT_DASHBOARD, path)

        def _engine_status(self):
            with project_app._jobs_lock:
                qwen_running = project_app._jobs.get("qwen", {}).get("running", False)
            editor_state = engine_state.get("state", "idle")
            if qwen_running or editor_state == "loading":
                state = "loading"
            elif editor_state == "ready":
                state = "ready"
            else:
                state = "idle"
            body = json.dumps({"state": state}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._dispatch()

        def do_POST(self):
            self._dispatch()

    return Router


def run(db_path: str, exports_dir: str, source_dir: str, spreads_path: str, crops_path: str,
        rendered_dir: str, out_pdf: str, size: str, style: str, port: int, open_browser: bool = True) -> None:
    engine_state: dict = {"state": "idle"}

    dashboard = ThreadingHTTPServer(
        ("127.0.0.1", _PORT_DASHBOARD),
        project_app.make_handler(db_path, exports_dir, {"source_dir": source_dir}),
    )
    people = ThreadingHTTPServer(
        ("127.0.0.1", _PORT_PEOPLE), label_people_app.make_handler(db_path, "/people"),
    )
    storyboard = ThreadingHTTPServer(
        ("127.0.0.1", _PORT_STORYBOARD),
        reorder_spreads_app.make_handler(db_path, spreads_path, crops_path, "/storyboard"),
    )
    editor = ThreadingHTTPServer(
        ("127.0.0.1", _PORT_EDITOR),
        spread_editor_app.make_handler(db_path, spreads_path, crops_path, rendered_dir, size, style,
                                        "/editor", engine_state),
    )
    export = ThreadingHTTPServer(
        ("127.0.0.1", _PORT_EXPORT),
        export_app.make_handler(rendered_dir, spreads_path, out_pdf, size, style, "/export"),
    )

    for s in (dashboard, people, storyboard, editor, export):
        _start_background(s)

    router = ThreadingHTTPServer(("127.0.0.1", port), make_router(engine_state))
    url = f"http://127.0.0.1:{port}/"
    print(f"Album Studio running at {url}")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        router.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for s in (router, dashboard, people, storyboard, editor, export):
            s.server_close()
        editor_handler_llama_proc = editor.RequestHandlerClass.llama_proc["proc"] \
            if hasattr(editor.RequestHandlerClass, "llama_proc") else None
        if editor_handler_llama_proc is not None:
            from qwen_stage import stop_server
            print("Stopping AI engine...")
            stop_server(editor_handler_llama_proc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Album Studio -- single-process local web app")
    parser.add_argument("--db", default="cache/project_full.db")
    parser.add_argument("--exports", default="exports")
    parser.add_argument("--source-dir", default="")
    parser.add_argument("--spreads", default="exports/spreads.json")
    parser.add_argument("--crops", default="exports/crops.json")
    parser.add_argument("--rendered-dir", default="exports/rendered_spreads")
    parser.add_argument("--out-pdf", default="exports/album.pdf")
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument("--style", default=DEFAULT_STYLE)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    run(args.db, args.exports, args.source_dir, args.spreads, args.crops, args.rendered_dir,
        args.out_pdf, args.size, args.style, args.port, open_browser=not args.no_browser)
