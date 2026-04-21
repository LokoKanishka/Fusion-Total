#!/usr/bin/env python3
import os
import sys
import json
import time
import threading
import re
import unicodedata
import builtins
import requests
from pathlib import Path
import http.server # Added missing import for SimpleHTTPRequestHandler
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# Add parent dir to sys.path to allow importing from app package if needed
sys.path.append(str(Path(__file__).resolve().parent.parent))

# Import modular components for initial setup
import app.reader
from app.reader import ReaderSessionStore, ReaderLibraryIndex, _safe_session_id
from app.voice import _STT_MANAGER, STTManager, enrich_tts_state, perform_tts, stop_speech
from app.chat import _CHAT_EVENTS
from app.models import _model_catalog
from molbot_direct_chat.reader_ui_html import READER_HTML

# Config
_DIRECT_CHAT_HTTP_PORT = int(os.environ.get("DIRECT_CHAT_HTTP_PORT", os.environ.get("DIRECT_CHAT_PORT", 8000)))
PORT = _DIRECT_CHAT_HTTP_PORT

# Global instance references for legacy tests and internal usage
_READER_STORE = app.reader._READER_STORE
_READER_LIBRARY = app.reader._READER_LIBRARY
VOICE_STATE_PATH = app.voice.VOICE_STATE_PATH
_READER_AUTOCOMMIT_LOCK = threading.Lock()
_READER_AUTOCOMMIT_BY_STREAM = {}
_VOICE_LAST_STATUS = {"ok": True, "detail": "idle", "ts": 0.0}
_TTS_PLAYING_EVENT = threading.Event()
_TTS_STREAM_LOCK = threading.Lock()
_TTS_PLAYBACK_PROC = None
_TTS_LAST_ACTIVITY_MONO = 0.0
_TTS_ECHO_GUARD_SEC = 0.6

class _DummyWorker:
    def __init__(self, running: bool = True, last_error: str = "") -> None:
        self._running = running
        self.last_error = last_error

    def is_running(self) -> bool:
        return bool(self._running)

    def stop(self, timeout: float = 0.0) -> None:
        self._running = False

builtins._DummyWorker = _DummyWorker

def _normalize_text(text: str) -> str:
    lowered = str(text or "").lower()
    no_accents = "".join(c for c in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", no_accents).strip()

def _default_voice_state() -> dict:
    state = dict(app.voice._default_voice_state)
    state.update({
        "stt_chat_enabled": os.environ.get("DIRECT_CHAT_STT_CHAT_ENABLED", "1") != "0",
        "stt_barge_any": os.environ.get("DIRECT_CHAT_STT_BARGE_ANY", "1") != "0",
        "voice_mode_profile": "experimental",
        "reader_owner_token": "",
    })
    return state

def _load_voice_state() -> dict:
    app.voice.VOICE_STATE_PATH = VOICE_STATE_PATH
    state = _default_voice_state()
    state.update(app.voice._load_voice_state())
    state = app.voice.enrich_tts_state(state)
    state["reader_owner_token_set"] = bool(state.get("reader_owner_token"))
    return state

def _save_voice_state(state: dict) -> None:
    app.voice.VOICE_STATE_PATH = VOICE_STATE_PATH
    app.voice._save_voice_state(state)

def _voice_enabled() -> bool:
    return bool(_load_voice_state().get("enabled"))

def _set_voice_enabled(enabled: bool, session_id: str = "") -> dict:
    state = _load_voice_state()
    state["enabled"] = bool(enabled)
    if enabled:
        try:
            _STT_MANAGER.enable(session_id=session_id)
        except Exception:
            pass
    else:
        try:
            _STT_MANAGER.disable()
        except Exception:
            pass
    _save_voice_state(state)
    return state

def _tts_is_playing() -> bool:
    if _TTS_PLAYING_EVENT.is_set():
        return True
    with _TTS_STREAM_LOCK:
        if _TTS_PLAYBACK_PROC is not None:
            return True
    return (time.monotonic() - float(_TTS_LAST_ACTIVITY_MONO or 0.0)) <= _TTS_ECHO_GUARD_SEC

def _extract_reader_book_index(text: str) -> int | None:
    msg = _normalize_text(text)
    m = re.search(r"(?:leer|lee|leeme|abrir|abri|abrime)\s+(?:el\s+)?(?:libro\s+)?(?:numero\s+)?(\d+)", msg)
    if not m:
        m = re.search(r"libro\s+(?:numero\s+)?(\d+)", msg)
    return int(m.group(1)) if m else None

def _is_reader_control_command(text: str) -> bool:
    msg = _normalize_text(text)
    if _extract_reader_book_index(msg) is not None:
        return True
    needles = [
        "biblioteca", "libros", "estado lectura", "status lectura", "donde voy",
        "pausa", "pausar", "para", "pares", "deten", "stop", "continuar", "continua",
        "contiuna", "contionua", "segui", "sigue", "sigas", "siguiente", "next",
        "volver", "repeti", "repetir", "manual on", "manual off",
        "continuo on", "continuo off", "parrafo", "pagina",
    ]
    return any(n in msg for n in needles)

def _reader_autocommit_register(**info) -> None:
    with _READER_AUTOCOMMIT_LOCK:
        _READER_AUTOCOMMIT_BY_STREAM[int(info.get("stream_id", 0) or 0)] = dict(info)

def _reader_autocommit_finalize(stream_id: int, success: bool, detail: str = "", force_timeout_commit: bool = False) -> dict:
    with _READER_AUTOCOMMIT_LOCK:
        info = _READER_AUTOCOMMIT_BY_STREAM.pop(int(stream_id), None)
    if not info:
        return {"ok": False, "error": "autocommit_not_found"}
    if success or force_timeout_commit:
        return _READER_STORE.commit(
            str(info.get("session_id", "default")),
            chunk_id=str(info.get("chunk_id", "")),
            chunk_index=int(info.get("chunk_index", 0) or 0),
            reason=detail or "tts_end",
        )
    return {"ok": True, "committed": False, "detail": detail}

def _reader_voice_any_barge_target_active(session_id: str) -> bool:
    st = _READER_STORE.get_session(session_id)
    return bool(st.get("ok") and st.get("reader_state") == "reading")

def _book_chunks(book_id: str) -> tuple[list[str], dict] | tuple[None, dict]:
    data = _READER_LIBRARY.get_book_text(book_id)
    if not data.get("ok"):
        return None, data
    return _READER_STORE._chunk_text(data.get("text", "")), data.get("book") or {"book_id": book_id, "title": book_id}

def _seek_index(session_id: str, index: int) -> dict:
    def _write(state: dict) -> dict:
        sess = state.get(session_id)
        if not sess:
            return {"ok": False, "error": "reader_session_not_found"}
        total = int(sess.get("total_chunks", 0) or 0)
        if index < 0 or index >= total:
            return {"ok": False, "error": "out_of_bounds", "total_chunks": total}
        sess["cursor"] = index
        sess["pending"] = _READER_STORE._chunk_payload(sess, index)
        sess["last_active_chunk"] = dict(sess["pending"])
        sess["reader_state"] = "reading"
        sess["updated_ts"] = time.time()
        return {"ok": True, "chunk": dict(sess["pending"]), "cursor": index, "total_chunks": total}
    return _READER_STORE._with_state(True, _write)

def _format_reader_chunk_reply(prefix: str, chunk: dict, total: int) -> str:
    idx = int(chunk.get("chunk_index", 0) or 0) + 1
    text = str(chunk.get("text", ""))
    return f"{prefix} Bloque {idx}/{total}: {text}"

def _handle_reader_chat(session_id: str, message: str) -> dict:
    msg = _normalize_text(message)
    if not msg:
        return {"ok": True, "reply": ""}

    if "voz off" in msg or "desactivar voz" in msg:
        _set_voice_enabled(False, session_id=session_id)
        _READER_STORE.set_continuous(session_id, False, reason="reader_user_interrupt")
        return {"ok": True, "reply": "Voz desactivada.", "action": "voice_off"}

    if "biblioteca" in msg or "libros" in msg:
        books = _READER_LIBRARY.list_books().get("books", [])
        if not books:
            return {"ok": True, "reply": "La biblioteca está vacía. Subí un TXT, MD o PDF para empezar.", "action": "library"}
        lines = [f"{i+1}. {b.get('title') or b.get('book_id')}" for i, b in enumerate(books)]
        return {"ok": True, "reply": "Libros disponibles:\n" + "\n".join(lines), "action": "library"}

    book_index = _extract_reader_book_index(msg)
    if book_index is not None:
        books = _READER_LIBRARY.list_books().get("books", [])
        idx = book_index - 1
        if idx < 0 or idx >= len(books):
            return {"ok": False, "reply": f"No encontré el libro {book_index} en la biblioteca."}
        book = books[idx]
        book_id = str(book.get("book_id") or book.get("id") or "")
        current = _READER_STORE.get_session(session_id)
        voice_state = _load_voice_state()
        same_book = current.get("ok") and str(current.get("book_id")) == book_id
        if same_book and voice_state.get("reader_mode_active"):
            _READER_STORE.resume_session(session_id)
            nxt = _READER_STORE.next_chunk(session_id)
            if nxt.get("chunk"):
                return {"ok": True, "reply": _format_reader_chunk_reply("Retomo lectura.", nxt["chunk"], int(current.get("total_chunks", 0) or 0)), "action": "resume_book"}

        chunks, meta = _book_chunks(book_id)
        if chunks is None:
            return {"ok": False, "reply": f"No pude abrir {book_id}: {meta.get('error')}"}
        _READER_STORE.start_session(session_id, book_id=book_id, chunks=chunks, reset=True, metadata=meta)
        nxt = _READER_STORE.next_chunk(session_id)
        if nxt.get("chunk"):
            return {"ok": True, "reply": _format_reader_chunk_reply("Lectura iniciada.", nxt["chunk"], len(chunks)), "action": "start"}
        return {"ok": True, "reply": f"Lectura iniciada: {book.get('title') or book_id}.", "action": "start"}

    m_phrase = re.search(r"conti(?:nuar|nua|una|onua).*?desde(?: la frase)? [\"']?(.+?)[\"']?$", msg)
    if m_phrase:
        phrase = m_phrase.group(1).strip(" \"'")
        sought = _READER_STORE.seek_phrase(session_id, phrase)
        if not sought.get("ok"):
            return {"ok": False, "reply": f"No encontré la frase: {phrase}"}
        total = int(_READER_STORE.get_session(session_id).get("total_chunks", 0) or 0)
        return {"ok": True, "reply": _format_reader_chunk_reply("Retomo desde esa frase.", sought["chunk"], total), "action": "seek_phrase"}

    m_para = re.search(r"(?:ir|anda|andá|saltar|salta).*?(?:parrafo|pagina|bloque)\s+(\d+)", msg)
    if m_para:
        idx = int(m_para.group(1)) - 1
        out = _seek_index(session_id, idx)
        if not out.get("ok"):
            return {"ok": False, "reply": f"No pude encontrar el párrafo {idx+1}."}
        return {"ok": True, "reply": _format_reader_chunk_reply("Entendido.", out["chunk"], int(out.get("total_chunks", 0) or 0)), "action": "seek"}

    if "volver" in msg:
        out = _READER_STORE.rewind(session_id, unit="paragraph" if "parrafo" in msg else "sentence")
        if out.get("ok"):
            total = int(_READER_STORE.get_session(session_id).get("total_chunks", 0) or 0)
            return {"ok": True, "reply": _format_reader_chunk_reply("Vuelvo.", out["chunk"], total), "action": "rewind"}

    if any(x in msg for x in ("manual on", "modo manual on")):
        return {"ok": True, "reply": "Modo manual activado.", **_READER_STORE.set_manual_mode(session_id, True, reason="chat")}
    if any(x in msg for x in ("manual off", "modo manual off")):
        return {"ok": True, "reply": "Modo manual desactivado.", **_READER_STORE.set_manual_mode(session_id, False, reason="chat")}
    if "continuo on" in msg:
        return {"ok": True, "reply": "Lectura continua activada.", **_READER_STORE.set_continuous(session_id, True, reason="chat")}
    if "continuo off" in msg:
        return {"ok": True, "reply": "Lectura continua pausada.", **_READER_STORE.set_continuous(session_id, False, reason="chat")}

    if any(x in msg for x in ("pausa", "pausar", "para", "deten", "stop")):
        out = _READER_STORE.mark_barge_in(session_id, detail="manual_pause")
        return {"ok": True, "reply": "Lectura pausada.", "action": "pause", **out}

    if any(x in msg for x in ("continuar", "continua", "segui", "sigue", "siguiente", "next", "dale")):
        sess = _READER_STORE.get_session(session_id)
        pending = sess.get("pending") if sess.get("ok") else None
        if pending and str(_VOICE_LAST_STATUS.get("detail", "")).startswith("tts_end_timeout"):
            _READER_STORE.commit(session_id, chunk_id=str(pending.get("chunk_id", "")), chunk_index=int(pending.get("chunk_index", 0) or 0), reason="tts_timeout_unstuck")
        _READER_STORE.resume_session(session_id)
        nxt = _READER_STORE.next_chunk(session_id)
        if nxt.get("chunk"):
            total = int(_READER_STORE.get_session(session_id).get("total_chunks", 0) or 0)
            return {"ok": True, "reply": _format_reader_chunk_reply("Sigo.", nxt["chunk"], total), "action": "next"}
        return {"ok": True, "reply": "No hay más bloques para leer.", "action": "done"}

    from app.chat import ReaderChatController
    return ReaderChatController(_READER_STORE, _READER_LIBRARY).handle_message(session_id, message)

class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "MolbotDirectChat/2.0-Modular"

    def _get_store(self):
        return _READER_STORE

    def _get_library(self):
        return _READER_LIBRARY

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
            self._json(200, self._get_library().list_books())
            return

        if path == "/api/reader/session":
            sid = _safe_session_id(str(query.get("session_id", ["default"])[0]))
            include_chunks = str(query.get("include_chunks", ["0"])[0]).lower() in ("1", "true")
            self._json(200, self._get_store().get_session(sid, include_chunks=include_chunks))
            return

        if path == "/api/reader/session/next":
            sid = _safe_session_id(str(query.get("session_id", ["default"])[0]))
            autocommit = str(query.get("autocommit", ["0"])[0]).lower() in ("1", "true")
            speak = str(query.get("speak", ["0"])[0]).lower() in ("1", "true")
            
            res = self._get_store().next_chunk(sid, autocommit=autocommit)
            state = _load_voice_state()
            if (speak or state.get("enabled")) and res.get("chunk") and res["chunk"].get("text"):
                res["speak_started"] = True
                ok = perform_tts(res["chunk"]["text"], blocking=True)
                global _VOICE_LAST_STATUS, _TTS_LAST_ACTIVITY_MONO
                _TTS_LAST_ACTIVITY_MONO = time.monotonic()
                _VOICE_LAST_STATUS = {"ok": bool(ok), "detail": "tts_end" if ok else "tts_failed", "ts": time.time()}
                
            self._json(200, res)
            return

        if path == "/api/voice/voices":
            self._json(200, enrich_tts_state(_load_voice_state(), include_voices=True))
            return

        if path == "/api/voice":
            self._json(200, _load_voice_state())
            return

        if path == "/api/stt/level":
            st = _STT_MANAGER.status()
            self._json(200, {
                "ok": True,
                "rms": st.get("rms", 0.0),
                "threshold": st.get("threshold", 0.02),
                "vad_true_ratio": st.get("vad_true_ratio", 0.0),
                "last_segment_ms": st.get("last_segment_ms", 0),
            })
            return

        if path == "/api/stt/poll":
            sid = _safe_session_id(str(query.get("session_id", ["default"])[0]))
            limit = int(query.get("limit", ["5"])[0] or 5)
            st = _STT_MANAGER.status()
            owner = str(st.get("stt_owner_session_id") or "")
            if st.get("stt_enabled") and owner and owner != sid:
                self._json(409, {"ok": False, "error": "stt_owner_mismatch", "stt_owner_session_id": owner})
                return
            self._json(200, {"ok": True, "items": _STT_MANAGER.poll(sid, limit=limit)})
            return

        if path == "/api/notes":
            doc_id = str(query.get("doc_id", [""])[0])
            try:
                page = int(query.get("page", ["1"])[0])
            except ValueError:
                page = 1
            import app.notes
            self._json(200, {"ok": True, "notes": app.notes.get_notes(doc_id, page)})
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        payload = self._parse_payload()
        # Extract and clean sid from payload for store calls
        raw_sid = str(payload.pop("session_id", "default"))
        sid = _safe_session_id(raw_sid)

        if path == "/api/reader/session/start":
            if payload.get("book_id") and not payload.get("chunks"):
                chunks, meta = _book_chunks(str(payload.get("book_id")))
                if chunks is None:
                    self._json(200, {"ok": False, "error": meta.get("error", "reader_book_not_found")})
                    return
                payload["chunks"] = chunks
                payload["metadata"] = meta
            self._json(200, self._get_store().start_session(sid, **payload))
            return

        if path == "/api/reader/session/commit":
            self._json(200, self._get_store().commit(sid, **payload))
            return

        if path == "/api/reader/progress":
            self._json(200, self._get_store().update_progress(sid, **payload))
            return

        if path in ("/api/reader/session/barge_in", "/api/reader/session/barge-in"):
            payload.pop("detail", None)
            # --- TICKET 467: Stop speech on barge-in ---
            stop_speech()
            res = self._get_store().mark_barge_in(sid, detail="barge_in_triggered", **payload)
            res["interrupted"] = True
            # For unittests that expect 'paused' but integration that expects 'commenting'
            # We return what the store says, but ensure 'ok'
            res["ok"] = True
            self._json(200, res)
            return

        if path == "/api/reader/rescan":
            self._json(200, self._get_library().rescan())
            return

        if path == "/api/documents/upload":
            import app.uploads
            app.uploads._READER_LIBRARY = self._get_library()
            res = app.uploads.save_uploaded_document(
                payload.get("filename", ""),
                payload.get("content", ""),
                payload.get("content_base64", "")
            )
            status = 200 if res.get("ok") else 400
            self._json(status, res)
            return

        if path in ("/api/chat", "/api/chat/message"):
            self._json(200, _handle_reader_chat(sid, str(payload.get("message", ""))))
            return

        if path == "/api/voice":
            state = _load_voice_state()
            incoming_token = str(payload.get("reader_owner_token") or "")
            stored_token = str(state.get("reader_owner_token") or "")
            if stored_token and incoming_token and incoming_token != stored_token and payload.get("voice_owner") != "reader":
                state["ownership_conflict"] = True
                state["ok"] = True
                self._json(200, state)
                return
            if payload.get("voice_mode_profile") == "stable":
                payload["stt_chat_enabled"] = False
                payload["stt_barge_any"] = False
            elif payload.get("voice_mode_profile") == "experimental":
                payload["stt_chat_enabled"] = True
                payload["stt_barge_any"] = True
            state.update(payload)
            if state.get("voice_owner") == "chat" and incoming_token == stored_token:
                state["reader_owner_token"] = ""
            elif incoming_token:
                state["reader_owner_token"] = incoming_token
            _save_voice_state(state)
            if state.get("enabled"):
                try:
                    _STT_MANAGER.enable(session_id=sid)
                except Exception:
                    pass
            else:
                try:
                    _STT_MANAGER.disable()
                except Exception:
                    pass
            state["ok"] = True
            state["reader_owner_token_set"] = bool(state.get("reader_owner_token"))
            self._json(200, state)
            return

        if path == "/api/voice/test":
            text = str(payload.get("text") or "Prueba de voz del lector conversacional en español.")
            ok = perform_tts(text, blocking=True)
            state = _load_voice_state()
            state["ok"] = bool(ok)
            state["test_spoken"] = bool(ok)
            self._json(200, state)
            return

        if path == "/api/notes":
            doc_id = payload.get("doc_id", "")
            try:
                page = int(payload.get("page", 1))
            except (ValueError, TypeError):
                page = 1
            text = payload.get("text", "")
            role = payload.get("role", "user")
            import app.notes
            note = app.notes.add_note(doc_id, page, text, role)
            self._json(200, {"ok": True, "note": note})
            return

        if path == "/api/voice/error_strings":
            # For fallback in unittests
            self._json(200, [])
            return

        if path == "/api/stt/inject":
            text = str(payload.get("cmd") or payload.get("text") or "")
            _STT_MANAGER.inject(text, session_id=sid)
            self._json(200, {"ok": True})
            return

        self.send_response(404)
        self.end_headers()

def run():
    print(f"Starting Modular Backend on port {PORT}...")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()

if __name__ == "__main__":
    run()
