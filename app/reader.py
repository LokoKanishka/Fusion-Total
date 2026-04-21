#!/usr/bin/env python3
import os
import json
import time
import fcntl
import threading
from pathlib import Path
from datetime import datetime, timezone

def _safe_session_id(sid: str) -> str:
    return "".join(c for c in str(sid) if c.isalnum() or c in ("-", "_")).strip() or "default"

class ReaderSessionStore:
    def __init__(self, state_path: Path | None = None, lock_path: Path | None = None):
        runtime_dir = Path(os.environ.get("OPENCLAW_RUNTIME_DIR", "."))
        state_dir = Path(os.environ.get("OPENCLAW_STATE_DIR", runtime_dir / "state"))
        state_dir.mkdir(parents=True, exist_ok=True)

        self.state_path = state_path or (state_dir / "reading_sessions.json")
        self.lock_path = lock_path or (state_dir / ".reading_sessions.lock")
        self.lock = threading.RLock()

        if not self.state_path.exists():
            self._save_state_unlocked({})

    def _now_iso(self, p: float | None = None) -> str:
        return datetime.fromtimestamp(p or time.time(), tz=timezone.utc).isoformat()

    def _save_state_unlocked(self, state: dict):
        temp = self.state_path.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        temp.replace(self.state_path)

    def _load_state_unlocked(self) -> dict:
        if not self.state_path.exists(): return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _with_state(self, write: bool, func):
        with self.lock:
            start = time.time()
            with self.lock_path.open("a+", encoding="utf-8") as lockf:
                while True:
                    try:
                        fcntl.flock(lockf.fileno(), (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.time() - start > 5.0:
                            raise TimeoutError(f"Could not acquire lock on {self.lock_path} after 5s")
                        time.sleep(0.1)

                try:
                    state = self._load_state_unlocked()
                    out = func(state)
                    if write:
                        self._save_state_unlocked(state)
                    return out
                finally:
                    fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

    def _chunk_text(self, text: str) -> list[str]:
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        chunks = [c.strip() for c in raw.split("\n\n") if c.strip()]
        if len(chunks) <= 1:
            lines = [c.strip() for c in raw.split("\n") if c.strip()]
            if len(lines) > 1:
                chunks = lines
        return chunks or ([raw.strip()] if raw.strip() else [])

    def _chunk_payload(self, sess: dict, index: int, *, text: str | None = None) -> dict:
        chunks = sess.get("chunks", [])
        chunk_text = chunks[index] if text is None and 0 <= index < len(chunks) else (text or "")
        return {
            "chunk_index": index,
            "chunk_id": f"chunk_{index+1:03d}",
            "text": chunk_text,
            "offset_chars": int(sess.get("bookmark", {}).get("offset_chars", 0) or 0),
            "last_delivery_ts": time.time(),
        }

    def _public_session(self, sess: dict, include_chunks: bool = False) -> dict:
        out = dict(sess)
        if not include_chunks:
            out.pop("chunks", None)
        out["exists"] = True
        out["ok"] = True
        out["has_pending"] = bool(sess.get("pending"))
        out["continuous_active"] = bool(sess.get("continuous_enabled") and sess.get("reader_state") == "reading")
        return out

    def start_session(
        self,
        session_id: str,
        id: str | None = None,
        book_id: str | None = None,
        chunks: list[str] | None = None,
        reset: bool = False,
        **kwargs,
    ) -> dict:
        actual_id = id or book_id or kwargs.get("id") or kwargs.get("book_id")
        metadata = dict(kwargs.get("metadata") or {})
        if chunks is None:
            if not actual_id:
                return {"ok": False, "error": "missing_id"}
            book_data = _READER_LIBRARY.get_book_text(actual_id)
            if not book_data.get("ok"):
                return {"ok": False, "error": "reader_book_not_found", "requested": actual_id}
            chunks = self._chunk_text(book_data.get("text", ""))
            metadata.update(book_data.get("book") or {})
        else:
            chunks = [str(c).strip() for c in chunks if str(c).strip()]
            actual_id = actual_id or metadata.get("book_id") or metadata.get("id") or session_id

        def _write(state: dict) -> dict:
            now = time.time()
            sess = {
                "session_id": session_id,
                "book_id": actual_id,
                "title": metadata.get("title") or actual_id,
                "total_chunks": len(chunks),
                "cursor": 0,
                "done": False,
                "chunks": chunks,
                "pending": None,
                "last_active_chunk": None,
                "bookmark": {"chunk_index": 0, "offset_chars": 0, "quality": "session_start"},
                "barge_in_count": 0,
                "reader_state": "reading",
                "manual_mode": False,
                "continuous_enabled": False,
                "continuous_reason": "session_start",
                "created_ts": now,
                "updated_ts": now,
                "metadata": metadata
            }
            state[session_id] = sess
            return {"ok": True, "started": True, "session_id": session_id, "book_id": actual_id, "total_chunks": len(chunks)}
        return self._with_state(True, _write)

    def get_session(self, session_id: str, include_chunks: bool = False) -> dict:
        def _read(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "exists": False, "session_id": session_id}
            return self._public_session(sess, include_chunks=include_chunks)
        return self._with_state(False, _read)

    def next_chunk(self, session_id: str, autocommit: bool = False) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}

            now = time.time()
            if sess.get("pending") and not autocommit:
                return {"ok": True, "replayed": True, "cursor": sess.get("cursor", 0), "chunk": sess["pending"], "session_id": session_id}

            cursor = sess.get("cursor", 0)
            chunks = sess.get("chunks", [])
            if cursor >= len(chunks):
                sess["done"] = True
                return {"ok": True, "done": True, "chunk": None, "session_id": session_id}

            chunk_data = self._chunk_payload(sess, cursor)

            # Preserve for contextual queries post barge-in
            sess["last_active_chunk"] = dict(chunk_data)

            res = {"ok": True, "replayed": False, "cursor": cursor, "chunk": chunk_data, "session_id": session_id}

            if autocommit:
                sess["cursor"] += 1
                sess["pending"] = None
                sess["last_commit_ts"] = now
                sess["bookmark"] = {"chunk_index": sess["cursor"], "offset_chars": 0, "quality": "autocommit"}
                if sess["cursor"] >= len(chunks):
                    sess["done"] = True
                    sess["continuous_enabled"] = False
                    sess["continuous_reason"] = "eof"
                # Required by verify_reader_library.sh
                res["autocommit_registered"] = True
                res["autocommitted"] = True
                res["autocommit"] = True
            else:
                sess["pending"] = chunk_data

            sess["reader_state"] = "reading"
            sess["updated_ts"] = now
            return res
        return self._with_state(True, _write)

    def commit(self, session_id: str, chunk_id: str | None = None, **kwargs) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}

            pending = sess.get("pending")
            if not pending: return {"ok": True, "committed": False, "detail": "nothing_to_commit"}

            if chunk_id and pending.get("chunk_id") != chunk_id:
                return {"ok": False, "error": "reader_commit_chunk_mismatch"}
            if kwargs.get("chunk_index") is not None and int(kwargs.get("chunk_index")) != int(pending.get("chunk_index", -1)):
                return {"ok": False, "error": "reader_commit_chunk_mismatch"}

            now = time.time()
            sess["cursor"] += 1
            sess["pending"] = None
            sess["last_commit_ts"] = now
            sess["bookmark"] = {"chunk_index": sess["cursor"], "offset_chars": 0, "quality": kwargs.get("reason", "commit")}
            sess["updated_ts"] = now
            # Auto-advance for continuous mode
            sess["reader_state"] = "reading"
            if sess["cursor"] >= sess["total_chunks"]:
                sess["done"] = True
                sess["reader_state"] = "paused"
                sess["continuous_enabled"] = False
                sess["continuous_reason"] = "eof"

            out = self._public_session(sess)
            out["committed"] = True
            return out
        return self._with_state(True, _write)

    def mark_barge_in(self, session_id: str, **kwargs) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}

            now = time.time()
            sess["barge_in_count"] += 1
            sess["last_barge_in_ts"] = now
            sess["last_barge_in_detail"] = kwargs.get("detail", "barge_in")
            sess["reader_state"] = "commenting"
            sess["continuous_enabled"] = False
            sess["continuous_reason"] = kwargs.get("detail", "barge_in")
            active = sess.get("pending") or sess.get("last_active_chunk") or self._chunk_payload(sess, min(sess.get("cursor", 0), max(0, sess.get("total_chunks", 1) - 1)))
            text_len = len(str(active.get("text", "")))
            playback_ms = max(0.0, float(kwargs.get("playback_ms", 0.0) or 0.0))
            estimated = min(text_len, int(playback_ms * 0.012)) if playback_ms else int(active.get("offset_chars", 0) or 0)
            bookmark = {
                "chunk_index": int(active.get("chunk_index", sess.get("cursor", 0)) or 0),
                "chunk_id": active.get("chunk_id"),
                "offset_chars": max(0, estimated),
                "quality": kwargs.get("detail", "barge_in"),
            }
            sess["bookmark"] = bookmark
            if sess.get("pending"):
                sess["pending"]["offset_chars"] = bookmark["offset_chars"]
            sess["updated_ts"] = now

            out = self._public_session(sess)
            out["interrupted"] = True
            return out
        return self._with_state(True, _write)

    def resume_session(self, session_id: str) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}
            sess["reader_state"] = "reading"
            sess["updated_ts"] = time.time()
            return {"ok": True, "state": "reading", "reader_state": "reading"}
        return self._with_state(True, _write)

    # Methods for legacy unittest compatibility
    def seek_phrase(self, session_id: str, phrase: str) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}
            chunks = sess.get("chunks", [])
            cursor = int(sess.get("cursor", 0) or 0)
            order = list(range(cursor, len(chunks))) + list(range(0, cursor))
            for i in order:
                c = chunks[i]
                if phrase.lower() in c.lower():
                    pos = c.lower().find(phrase.lower())
                    seek_text = c[pos:].strip() if pos >= 0 else c
                    sess["cursor"] = i
                    sess["pending"] = self._chunk_payload(sess, i, text=seek_text)
                    sess["last_active_chunk"] = dict(sess["pending"])
                    sess["bookmark"] = {"chunk_index": i, "chunk_id": sess["pending"]["chunk_id"], "offset_chars": pos if pos >= 0 else 0, "quality": "phrase"}
                    sess["reader_state"] = "reading"
                    sess["updated_ts"] = time.time()
                    return {"ok": True, "found": True, "seeked": True, "seek_wrapped": i < cursor, "cursor": i, "chunk": dict(sess["pending"])}
            return {"ok": False, "error": "phrase_not_found"}
        return self._with_state(True, _write)

    def rewind(self, session_id: str, unit: str = "sentence", **kwargs) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}
            cursor = max(0, int(sess.get("cursor", 0) or 0) - (1 if unit in ("paragraph", "parrafo", "párrafo") else 0))
            sess["cursor"] = cursor
            sess["pending"] = self._chunk_payload(sess, cursor)
            sess["reader_state"] = "reading"
            sess["updated_ts"] = time.time()
            return {"ok": True, "rewound": True, "rewind_unit": unit, "cursor": cursor, "chunk": dict(sess["pending"])}
        return self._with_state(True, _write)

    def set_reader_state(self, session_id: str, reader_state: str, **kwargs) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}
            sess["reader_state"] = str(reader_state or "idle")
            sess["updated_ts"] = time.time()
            return self._public_session(sess)
        return self._with_state(True, _write)

    def set_continuous(self, session_id: str, enabled: bool, **kwargs) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}
            sess["continuous_enabled"] = bool(enabled)
            sess["continuous_reason"] = kwargs.get("reason", "manual")
            if enabled:
                sess["manual_mode"] = False
                sess["reader_state"] = "reading"
            sess["updated_ts"] = time.time()
            out = self._public_session(sess)
            out["continuous_enabled"] = bool(sess.get("continuous_enabled"))
            out["continuous_active"] = bool(sess.get("continuous_enabled") and sess.get("reader_state") == "reading")
            return out
        return self._with_state(True, _write)

    def set_manual_mode(self, session_id: str, manual: bool, **kwargs) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}
            sess["manual_mode"] = bool(manual)
            if manual:
                sess["continuous_enabled"] = False
                sess["continuous_reason"] = kwargs.get("reason", "manual_mode")
            sess["updated_ts"] = time.time()
            out = self._public_session(sess)
            out["manual"] = bool(manual)
            return out
        return self._with_state(True, _write)

    def update_progress(self, session_id: str, **kwargs) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}
            pending = sess.get("pending")
            if kwargs.get("chunk_id") and pending and pending.get("chunk_id") != kwargs.get("chunk_id"):
                return {"ok": False, "error": "reader_progress_chunk_mismatch"}
            offset = max(0, int(kwargs.get("offset_chars", 0) or 0))
            if pending:
                pending["offset_chars"] = offset
            chunk_index = int((pending or sess.get("last_active_chunk") or {}).get("chunk_index", sess.get("cursor", 0)) or 0)
            bookmark = {
                "chunk_index": chunk_index,
                "chunk_id": (pending or {}).get("chunk_id"),
                "offset_chars": offset,
                "quality": kwargs.get("quality", "progress"),
            }
            sess["bookmark"] = bookmark
            sess["last_offset"] = offset
            sess["updated_ts"] = time.time()
            return {"ok": True, "progress_updated": True, "chunk": dict(pending or {}), "bookmark": bookmark}
        return self._with_state(True, _write)

class ReaderLibraryIndex:
    def __init__(self, library_dir: Path | None = None, index_path: Path | None = None, lock_path: Path | None = None, cache_dir: Path | None = None):
        runtime_dir = Path(os.environ.get("OPENCLAW_RUNTIME_DIR", "."))
        env_library = os.environ.get("LUCY_LIBRARY_DIR")
        self.library_dir = library_dir or (Path(env_library) if env_library else (runtime_dir / "library"))
        self.library_dir.mkdir(parents=True, exist_ok=True)

        state_dir = Path(os.environ.get("OPENCLAW_STATE_DIR", runtime_dir / "state"))
        self.index_path = index_path or (state_dir / "reader_library_index.json")
        self.lock_path = lock_path or (state_dir / ".reader_library_index.lock")
        self.cache_dir = cache_dir or (state_dir / "reader_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def _save_state_unlocked(self, state: dict):
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def _load_state_unlocked(self) -> dict:
        if not self.index_path.exists(): return {"books": {}}
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"books": {}}

    def _with_state(self, write: bool, func):
        with self.lock:
            start = time.time()
            with self.lock_path.open("a+", encoding="utf-8") as lockf:
                while True:
                    try:
                        fcntl.flock(lockf.fileno(), (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.time() - start > 5.0:
                            raise TimeoutError(f"Could not acquire lock on {self.lock_path} after 5s")
                        time.sleep(0.1)

                try:
                    state = self._load_state_unlocked()
                    out = func(state)
                    if write:
                        self._save_state_unlocked(state)
                    return out
                finally:
                    fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

    def list_books(self) -> dict:
        def _read(state: dict) -> dict:
            books = list(state.get("books", {}).values())
            books.sort(key=lambda b: str(b.get("title", "")).lower())
            return {"ok": True, "count": len(books), "books": books}
        return self._with_state(False, _read)

    def get_book_text(self, book_id: str) -> dict:
        def _read(state: dict) -> dict:
            item = state.get("books", {}).get(book_id)
            if not item: return {"ok": False, "error": "reader_book_not_found"}
            p = Path(item.get("cached_text_path", ""))
            if not p.exists(): return {"ok": False, "error": "reader_book_cache_missing"}
            return {"ok": True, "text": p.read_text(encoding="utf-8"), "book": item}
        return self._with_state(False, _read)

    def rescan(self) -> dict:
        def _write(state: dict) -> dict:
            books = state.get("books", {})
            found = 0
            for p in list(self.library_dir.glob("*.txt")) + list(self.library_dir.glob("*.md")):
                bid = p.stem
                if bid not in books:
                    books[bid] = {
                        "id": bid,
                        "book_id": bid,
                        "title": p.stem,
                        "path": str(p),
                        "cached_text_path": str(p),
                        "format": p.suffix.lower().lstrip(".") or "txt",
                        "added_ts": float(time.time()),
                    }
                    found += 1
            for p in self.library_dir.glob("*.pdf"):
                bid = p.stem
                if bid in books:
                    continue
                try:
                    from app.uploads import _extract_pdf_text
                    text, extractor = _extract_pdf_text(p)
                except Exception:
                    text, extractor = "", "unavailable"
                if not text.strip():
                    continue
                cache_path = self.cache_dir / f"{bid}.txt"
                cache_path.write_text(text, encoding="utf-8")
                books[bid] = {
                    "id": bid,
                    "book_id": bid,
                    "title": p.stem,
                    "path": str(p),
                    "cached_text_path": str(cache_path),
                    "format": "pdf",
                    "extractor": extractor,
                    "added_ts": float(time.time()),
                }
                found += 1
            state["books"] = books
            return {"ok": True, "count": len(books), "found_new": found}
        return self._with_state(True, _write)

# Global instances
_READER_STORE = ReaderSessionStore()
_READER_LIBRARY = ReaderLibraryIndex()
