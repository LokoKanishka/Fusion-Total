#!/usr/bin/env python3
import os
import sys
import json
import time
import threading
import socket
import logging
import re
import queue
import shutil
import subprocess
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

# Add parent dir to sys.path to allow importing from app package if needed
sys.path.append(str(Path(__file__).resolve().parent.parent))

# Import modular components
from app.reader import _READER_STORE, _READER_LIBRARY, _safe_session_id
from app.voice import _STT_MANAGER, _load_voice_state, _save_voice_state
from app.chat import _CHAT_EVENTS, _load_history, _save_history
from app.models import _model_catalog, _build_system_prompt
from molbot_direct_chat.reader_ui_html import READER_HTML

# Globals & Config (Extracted/Normalized)
PORT = int(os.environ.get("DIRECT_CHAT_HTTP_PORT", 8000))
TOKEN = os.environ.get("OPENCLAW_VERIFY_TOKEN", "")

class Handler(BaseHTTPRequestHandler):
    server_version = "MolbotDirectChat/2.0-Modular"

    def _json(self, status: int, data: dict) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _parse_payload(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0: return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            raw = READER_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if path == "/api/reader/books":
            self._json(200, _READER_LIBRARY.list_books())
            return

        if path == "/api/reader/session":
            sid = _safe_session_id(str(query.get("session_id", ["default"])[0]))
            include_chunks = str(query.get("include_chunks", ["0"])[0]).lower() in ("1", "true")
            self._json(200, _READER_STORE.get_session(sid, include_chunks=include_chunks))
            return

        if path == "/api/reader/session/next":
            sid = _safe_session_id(str(query.get("session_id", ["default"])[0]))
            self._json(200, _READER_STORE.next_chunk(sid))
            return

        # ... Fallback 404 ...
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        payload = self._parse_payload()
        sid = _safe_session_id(str(payload.get("session_id", "default")))

        if path == "/api/reader/session/start":
            self._json(200, _READER_STORE.start_session(sid, **payload))
            return

        if path == "/api/reader/session/commit":
            self._json(200, _READER_STORE.commit(sid, **payload))
            return

        if path == "/api/reader/progress":
            self._json(200, _READER_STORE.update_progress(sid, **payload))
            return

        # ... Fallback 404 ...
        self.send_response(404)
        self.end_headers()

def run():
    print(f"Starting Modular Backend on port {PORT}...")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()

if __name__ == "__main__":
    run()
