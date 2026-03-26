#!/usr/bin/env python3
import os
import json
import time
import fcntl
import re
import hashlib
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
        self.lock = threading.Lock()
        
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
            with self.lock_path.open("a+", encoding="utf-8") as lockf:
                fcntl.flock(lockf.fileno(), fcntl.LOCK_EX if write else fcntl.LOCK_SH)
                state = self._load_state_unlocked()
                out = func(state)
                if write:
                    self._save_state_unlocked(state)
                return out

    def start_session(self, session_id: str, id: str | None = None, book_id: str | None = None, **kwargs) -> dict:
        actual_id = id or book_id or kwargs.get("id") or kwargs.get("book_id")
        if not actual_id: 
            return {"ok": False, "error": "missing_id"}
        
        from app.reader import _READER_LIBRARY
        book_data = _READER_LIBRARY.get_book_text(actual_id)
        if not book_data.get("ok"):
            return {"ok": False, "error": "reader_book_not_found", "requested": actual_id}
        
        text = book_data.get("text", "")
        chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
        
        def _write(state: dict) -> dict:
            now = time.time()
            sess = {
                "session_id": session_id,
                "book_id": actual_id,
                "total_chunks": len(chunks),
                "cursor": 0,
                "done": False,
                "chunks": chunks,
                "pending": None,
                "barge_in_count": 0,
                "reader_state": "reading",
                "created_ts": now,
                "updated_ts": now,
                "metadata": kwargs.get("metadata", {})
            }
            state[session_id] = sess
            return {"ok": True, "started": True, "session_id": session_id}
        return self._with_state(True, _write)

    def get_session(self, session_id: str, include_chunks: bool = False) -> dict:
        def _read(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "exists": False, "session_id": session_id}
            out = dict(sess)
            if not include_chunks: out.pop("chunks", None)
            out["exists"] = True
            out["ok"] = True
            return out
        return self._with_state(False, _read)

    def next_chunk(self, session_id: str, autocommit: bool = False) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}
            
            now = time.time()
            if sess.get("pending") and not autocommit:
                return {"ok": True, "replayed": True, "chunk": sess["pending"], "session_id": session_id}
                
            cursor = sess.get("cursor", 0)
            chunks = sess.get("chunks", [])
            if cursor >= len(chunks):
                sess["done"] = True
                return {"ok": True, "done": True, "chunk": None, "session_id": session_id}
            
            chunk_data = {
                "chunk_index": cursor,
                "chunk_id": f"chunk_{cursor+1:03d}",
                "text": chunks[cursor],
                "last_delivery_ts": now
            }
            
            res = {"ok": True, "chunk": chunk_data, "session_id": session_id}
            
            if autocommit:
                sess["cursor"] += 1
                sess["pending"] = None
                sess["last_commit_ts"] = now
                if sess["cursor"] >= len(chunks):
                    sess["done"] = True
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
            
            now = time.time()
            sess["cursor"] += 1
            sess["pending"] = None
            sess["last_commit_ts"] = now
            sess["updated_ts"] = now
            # Auto-advance for continuous mode
            sess["reader_state"] = "reading" 
            if sess["cursor"] >= sess["total_chunks"]:
                sess["done"] = True
                sess["reader_state"] = "paused"
            
            out = dict(sess)
            out.pop("chunks", None)
            out["committed"] = True
            out["ok"] = True
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
            sess["pending"] = None
            sess["updated_ts"] = now
            sess["reader_state"] = "commenting"
            
            out = dict(sess)
            out.pop("chunks", None)
            out["ok"] = True
            return out
        return self._with_state(True, _write)

    # Methods for legacy unittest compatibility
    def seek_phrase(self, session_id: str, phrase: str) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}
            chunks = sess.get("chunks", [])
            for i, c in enumerate(chunks):
                if phrase.lower() in c.lower():
                    sess["cursor"] = i
                    sess["pending"] = None
                    sess["updated_ts"] = time.time()
                    return {"ok": True, "found": True, "cursor": i}
            return {"ok": False, "error": "phrase_not_found"}
        return self._with_state(True, _write)

    def set_manual_mode(self, session_id: str, manual: bool, **kwargs) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}
            sess["manual_mode"] = manual
            sess["updated_ts"] = time.time()
            return {"ok": True, "manual": manual}
        return self._with_state(True, _write)

    def update_progress(self, session_id: str, **kwargs) -> dict:
        def _write(state: dict) -> dict:
            sess = state.get(session_id)
            if not sess: return {"ok": False, "error": "reader_session_not_found"}
            if "offset_chars" in kwargs:
                # Test expect this for bookmarking
                sess["last_offset"] = kwargs["offset_chars"]
            sess["updated_ts"] = time.time()
            return {"ok": True}
        return self._with_state(True, _write)

class ReaderLibraryIndex:
    def __init__(self, library_dir: Path | None = None, index_path: Path | None = None, lock_path: Path | None = None, cache_dir: Path | None = None):
        runtime_dir = Path(os.environ.get("OPENCLAW_RUNTIME_DIR", "."))
        self.library_dir = library_dir or (runtime_dir / "library")
        self.library_dir.mkdir(parents=True, exist_ok=True)
        
        state_dir = Path(os.environ.get("OPENCLAW_STATE_DIR", runtime_dir / "state"))
        self.index_path = index_path or (state_dir / "reader_library_index.json")
        self.lock_path = lock_path or (state_dir / ".reader_library_index.lock")
        self.cache_dir = cache_dir or (state_dir / "reader_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

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
            with self.lock_path.open("a+", encoding="utf-8") as lockf:
                fcntl.flock(lockf.fileno(), fcntl.LOCK_EX if write else fcntl.LOCK_SH)
                state = self._load_state_unlocked()
                out = func(state)
                if write:
                    self._save_state_unlocked(state)
                return out

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
            for p in self.library_dir.glob("*.txt"):
                bid = p.stem
                if bid not in books:
                    books[bid] = {
                        "id": bid,
                        "book_id": bid,
                        "title": p.stem,
                        "path": str(p),
                        "cached_text_path": str(p),
                        "added_ts": float(time.time()),
                    }
                    found += 1
            state["books"] = books
            return {"ok": True, "count": len(books), "found_new": found}
        return self._with_state(True, _write)

# Global instances
_READER_STORE = ReaderSessionStore()
_READER_LIBRARY = ReaderLibraryIndex()
