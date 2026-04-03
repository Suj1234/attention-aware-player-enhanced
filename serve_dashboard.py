#!/usr/bin/env python3
"""Serve dashboard.html with injected session data at http://localhost:8765."""

import http.server
import json
import os
import threading
import webbrowser
from pathlib import Path

PORT = 8765
BASE_DIR = Path(__file__).parent
SESSIONS_DIR = BASE_DIR / "head" / "sessions"
DASHBOARD_HTML = BASE_DIR / "dashboard.html"

_shutdown_timer = None
_server = None
IDLE_TIMEOUT = 60  # seconds


def load_sessions():
    sessions = []
    if SESSIONS_DIR.is_dir():
        for path in sorted(SESSIONS_DIR.glob("*.json")):
            try:
                with open(path) as f:
                    sessions.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass
    return sessions


def build_html():
    with open(DASHBOARD_HTML) as f:
        html = f.read()
    sessions = load_sessions()
    injection = f"<script>window.__SESSIONS = {json.dumps(sessions, indent=2)};</script>"
    return html.replace("</head>", f"{injection}\n</head>", 1)


def reset_idle_timer():
    global _shutdown_timer
    if _shutdown_timer is not None:
        _shutdown_timer.cancel()
    _shutdown_timer = threading.Timer(IDLE_TIMEOUT, shutdown)
    _shutdown_timer.daemon = True
    _shutdown_timer.start()


def shutdown():
    print(f"\nNo browser activity for {IDLE_TIMEOUT}s — shutting down.")
    if _server:
        threading.Thread(target=_server.shutdown, daemon=True).start()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        reset_idle_timer()
        if self.path in ("/", "/index.html", "/dashboard.html"):
            try:
                body = build_html().encode()
            except OSError as e:
                self.send_error(500, str(e))
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            # Serve other static assets (CSS, JS, images) from project root
            file_path = BASE_DIR / self.path.lstrip("/")
            if file_path.is_file():
                with open(file_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)

    def log_message(self, fmt, *args):
        pass  # suppress per-request logging


def main():
    global _server
    _server = http.server.HTTPServer(("localhost", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Dashboard running at {url} — press Ctrl+C to stop")
    webbrowser.open(url)
    reset_idle_timer()
    try:
        _server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if _shutdown_timer:
            _shutdown_timer.cancel()


if __name__ == "__main__":
    main()
