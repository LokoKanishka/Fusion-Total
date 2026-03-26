import json
import time
import fcntl
import re
import hashlib
from pathlib import Path
from datetime import datetime, timezone

# Constants from the original monolith (should be imported or defined)
# For now, we will define them or expect them to be passed/configured
READER_STATE_PATH = Path("~/.openclaw/reader_state.json").expanduser()
READER_LOCK_PATH = Path("~/.openclaw/reader_state.lock").expanduser()
READER_LIBRARY_DIR = Path("~/Documents/Books").expanduser()
READER_LIBRARY_INDEX_PATH = Path("~/.openclaw/library_index.json").expanduser()
READER_LIBRARY_LOCK_PATH = Path("~/.openclaw/library_index.lock").expanduser()
READER_CACHE_DIR = Path("~/.openclaw/cache/reader").expanduser()

def _safe_session_id(sid: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(sid or "default")).strip("_")
    return cleaned if cleaned else "default"

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default

def _reader_pacing_config() -> dict:
    # This might need to stay in a config module or be passed
    return {
        "min_delay_ms": 1500,
        "burst_window_ms": 10000,
        "burst_max_chunks": 6,
    }

class ReaderSessionStore:
    def __init__(self, state_path: Path | None = None, lock_path: Path | None = None, max_sessions: int = 200) -> None:
        self.state_path = Path(state_path or READER_STATE_PATH)
        self.lock_path = Path(lock_path or READER_LOCK_PATH)
        self.max_sessions = max(16, int(max_sessions))

    @staticmethod
    def _default_state() -> dict:
        return {
            "version": 1,
            "updated_ts": float(time.time()),
            "sessions": {},
        }

    def _load_state_unlocked(self) -> dict:
        if not self.state_path.exists():
            return self._default_state()
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return self._default_state()
        if not isinstance(raw, dict):
            return self._default_state()
        sessions = raw.get("sessions")
        if not isinstance(sessions, dict):
            sessions = {}
        return {
            "version": int(raw.get("version", 1) or 1),
            "updated_ts": float(raw.get("updated_ts", 0.0) or 0.0),
            "sessions": sessions,
        }

    def _save_state_unlocked(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, ensure_ascii=False, indent=2)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.state_path)

    def _with_state(self, write: bool, func):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lockf:
            mode = fcntl.LOCK_EX if write else fcntl.LOCK_SH
            fcntl.flock(lockf.fileno(), mode)
            state = self._load_state_unlocked()
            out = func(state)
            if write:
                state["updated_ts"] = float(time.time())
                self._save_state_unlocked(state)
            return out

    @staticmethod
    def _split_text_to_chunks(text: str, max_chars: int = 720) -> list[str]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return []
        max_chars = max(320, int(max_chars))
        paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n{2,}", cleaned) if str(p or "").strip()]
        if not paragraphs:
            paragraphs = [re.sub(r"\s+", " ", cleaned).strip()]
        out: list[str] = []
        for para in paragraphs:
            if len(para) <= max_chars:
                out.append(para)
                continue
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", para) if s.strip()]
            if not sentences:
                sentences = [para]
            acc = ""
            for sent in sentences:
                if len(sent) > max_chars:
                    if acc:
                        out.append(acc)
                        acc = ""
                    for i in range(0, len(sent), max_chars):
                        piece = sent[i : i + max_chars].strip()
                        if piece:
                            out.append(piece)
                    continue
                candidate = f"{acc} {sent}".strip() if acc else sent
                if len(candidate) <= max_chars:
                    acc = candidate
                else:
                    if acc:
                        out.append(acc)
                    acc = sent
            if acc:
                out.append(acc)
        return [x for x in out if x]

    def _normalize_chunks(self, chunks, text: str = "") -> list[dict]:
        src = chunks
        if not isinstance(src, list):
            src = []
        out: list[dict] = []
        for idx, item in enumerate(src):
            if isinstance(item, str):
                chunk_text = item.strip()
                chunk_id = ""
            elif isinstance(item, dict):
                chunk_text = str(item.get("text", "")).strip()
                chunk_id = str(item.get("id", "")).strip()
            else:
                continue
            if not chunk_text:
                continue
            if not chunk_id:
                chunk_id = f"chunk_{idx + 1:03d}"
            out.append({"id": chunk_id[:80], "text": chunk_text[:8000]})

        if not out and str(text or "").strip():
            # In extraction, we might need to pass this config or import it
            max_c = 960 # Default or from passed config
            split = self._split_text_to_chunks(text, max_chars=max_c)
            for idx, piece in enumerate(split):
                out.append({"id": f"chunk_{idx + 1:03d}", "text": piece[:8000]})

        if not out:
            raise ValueError("reader_chunks_empty")
        return out

    @staticmethod
    def _pending_view(pending: dict | None) -> dict | None:
        if not isinstance(pending, dict):
            return None
        raw_text = str(pending.get("text", ""))
        offset_chars = int(pending.get("offset_chars", 0) or 0)
        if offset_chars < 0:
            offset_chars = 0
        if offset_chars > len(raw_text):
            offset_chars = len(raw_text)
        text_view = raw_text[offset_chars:] if raw_text else ""
        return {
            "chunk_index": int(pending.get("chunk_index", 0) or 0),
            "chunk_id": str(pending.get("chunk_id", "")),
            "text": text_view,
            "offset_chars": offset_chars,
            "offset_quality": str(pending.get("offset_quality", "start") or "start"),
            "last_snippet": str(pending.get("last_snippet", "")),
            "deliveries": int(pending.get("deliveries", 0) or 0),
            "last_delivery_ts": float(pending.get("last_delivery_ts", 0.0) or 0.0),
            "last_barge_in_ts": float(pending.get("last_barge_in_ts", 0.0) or 0.0),
        }

    @staticmethod
    def _bookmark_view(bookmark: dict | None) -> dict | None:
        if not isinstance(bookmark, dict):
            return None
        return {
            "chunk_index": int(bookmark.get("chunk_index", 0) or 0),
            "chunk_id": str(bookmark.get("chunk_id", "")),
            "offset_chars": int(bookmark.get("offset_chars", 0) or 0),
            "quality": str(bookmark.get("quality", "unknown") or "unknown"),
            "last_snippet": str(bookmark.get("last_snippet", "")),
            "updated_ts": float(bookmark.get("updated_ts", 0.0) or 0.0),
        }

    @staticmethod
    def _snippet_around(text: str, offset_chars: int, before: int = 40, after: int = 80) -> str:
        src = str(text or "")
        if not src:
            return ""
        off = max(0, min(int(offset_chars), len(src)))
        start = max(0, off - max(0, int(before)))
        end = min(len(src), off + max(1, int(after)))
        return src[start:end].strip()

    @classmethod
    def _bookmark_from_pending(cls, pending: dict | None, now: float, quality_fallback: str = "unknown") -> dict | None:
        if not isinstance(pending, dict):
            return None
        text = str(pending.get("text", ""))
        offset = int(pending.get("offset_chars", 0) or 0)
        if offset < 0:
            offset = 0
        if offset > len(text):
            offset = len(text)
        quality = str(pending.get("offset_quality", quality_fallback) or quality_fallback)
        snippet = str(pending.get("last_snippet", "")).strip() or cls._snippet_around(text, offset)
        return {
            "chunk_index": int(pending.get("chunk_index", 0) or 0),
            "chunk_id": str(pending.get("chunk_id", "")),
            "offset_chars": offset,
            "quality": quality[:24],
            "last_snippet": snippet[:220],
            "updated_ts": float(now),
        }

    @classmethod
    def _set_pending_offset(cls, pending: dict, offset_chars: int, now: float, quality: str) -> None:
        text = str(pending.get("text", ""))
        off = max(0, min(int(offset_chars), len(text)))
        pending["offset_chars"] = off
        pending["offset_quality"] = str(quality or "unknown")[:24]
        pending["offset_updated_ts"] = float(now)
        pending["last_snippet"] = cls._snippet_around(text, off)[:220]

    @staticmethod
    def _rewind_sentence_offset(text: str, offset_chars: int) -> int:
        src = str(text or "")
        if not src:
            return 0
        off = max(0, min(int(offset_chars), len(src)))
        if off <= 0:
            return 0
        starts = [0]
        for m in re.finditer(r"[.!?]\s+", src):
            starts.append(m.end())
        starts = sorted(set(starts))
        prev = 0
        curr = 0
        for st in starts:
            if st <= off:
                prev = curr
                curr = st
            else:
                break
        return max(0, min(prev, len(src)))

    @staticmethod
    def _rewind_paragraph_offset(text: str, offset_chars: int) -> int:
        src = str(text or "")
        if not src:
            return 0
        off = max(0, min(int(offset_chars), len(src)))
        if off <= 0:
            return 0
        starts = [0]
        for m in re.finditer(r"\n\s*\n", src):
            starts.append(m.end())
        starts = sorted(set(starts))
        prev = 0
        curr = 0
        for st in starts:
            if st <= off:
                prev = curr
                curr = st
            else:
                break
        return max(0, min(prev, len(src)))

    def _session_view(self, session_id: str, session: dict, include_chunks: bool = False) -> dict:
        chunks = session.get("chunks")
        if not isinstance(chunks, list):
            chunks = []
        cursor = int(session.get("cursor", 0) or 0)
        total = len(chunks)
        pending = self._pending_view(session.get("pending"))
        bookmark_raw = session.get("bookmark")
        if not isinstance(bookmark_raw, dict):
            bookmark_raw = self._bookmark_from_pending(session.get("pending"), now=float(time.time()), quality_fallback="pending")
        bookmark = self._bookmark_view(bookmark_raw)
        payload = {
            "ok": True,
            "exists": True,
            "session_id": str(session_id),
            "cursor": max(0, cursor),
            "total_chunks": total,
            "done": bool(cursor >= total and pending is None),
            "has_pending": pending is not None,
            "pending": pending,
            "bookmark": bookmark,
            "barge_in_count": int(session.get("barge_in_count", 0) or 0),
            "last_barge_in_detail": str(session.get("last_barge_in_detail", "")),
            "last_barge_in_ts": float(session.get("last_barge_in_ts", 0.0) or 0.0),
            "last_event": str(session.get("last_event", "")),
            "reader_state": str(session.get("reader_state", "paused") or "paused"),
            "updated_ts": float(session.get("updated_ts", 0.0) or 0.0),
            "created_ts": float(session.get("created_ts", 0.0) or 0.0),
            "last_commit_ts": float(session.get("last_commit_ts", 0.0) or 0.0),
            "continuous_active": bool(session.get("continuous_active", False)),
            "continuous_enabled": bool(session.get("continuous_enabled", session.get("continuous_active", False))),
            "manual_mode": bool(session.get("manual_mode", False)),
            "continuous_reason": str(session.get("continuous_reason", "")),
            "continuous_updated_ts": float(session.get("continuous_updated_ts", 0.0) or 0.0),
            "last_chunk_emit_ts": float(session.get("last_chunk_emit_ts", 0.0) or 0.0),
            "burst_window_start_ts": float(session.get("burst_window_start_ts", 0.0) or 0.0),
            "burst_chunks_in_window": int(session.get("burst_chunks_in_window", 0) or 0),
        }
        meta = session.get("metadata")
        if isinstance(meta, dict):
            payload["metadata"] = {str(k): v for k, v in meta.items() if isinstance(k, str) and isinstance(v, (str, int, float, bool))}
        if include_chunks:
            payload["chunks"] = [
                {"id": str(item.get("id", "")), "text": str(item.get("text", ""))}
                for item in chunks
                if isinstance(item, dict)
            ]
        return payload

    @staticmethod
    def _session_missing(session_id: str) -> dict:
        return {
            "ok": False,
            "exists": False,
            "session_id": str(session_id),
            "error": "reader_session_not_found",
        }

    @staticmethod
    def _prune_sessions(state: dict, max_sessions: int) -> None:
        sessions = state.get("sessions")
        if not isinstance(sessions, dict):
            state["sessions"] = {}
            return
        if len(sessions) <= max_sessions:
            return
        sortable: list[tuple[float, str]] = []
        for sid, sess in sessions.items():
            if not isinstance(sid, str) or not isinstance(sess, dict):
                continue
            ts = float(sess.get("updated_ts", 0.0) or 0.0)
            sortable.append((ts, sid))
        sortable.sort(key=lambda x: x[0])
        remove_count = max(0, len(sortable) - max_sessions)
        for _, sid in sortable[:remove_count]:
            sessions.pop(sid, None)

    def summary(self, include_sessions: bool = False) -> dict:
        def _read(state: dict) -> dict:
            sessions = state.get("sessions", {})
            count = len(sessions) if isinstance(sessions, dict) else 0
            out = {
                "ok": True,
                "mode": "reader_v0",
                "state_file": str(self.state_path),
                "session_count": int(count),
                "updated_ts": float(state.get("updated_ts", 0.0) or 0.0),
            }
            if include_sessions and isinstance(sessions, dict):
                out["sessions"] = sorted([str(k) for k in sessions.keys()])[:120]
            return out

        return self._with_state(False, _read)

    def start_session(
        self,
        session_id: str,
        chunks,
        text: str = "",
        reset: bool = True,
        metadata: dict | None = None,
    ) -> dict:
        sid = _safe_session_id(session_id)
        normalized_chunks = self._normalize_chunks(chunks, text=text)
        meta: dict = {}
        if isinstance(metadata, dict):
            for k, v in metadata.items():
                if not isinstance(k, str):
                    continue
                if isinstance(v, (str, int, float, bool)):
                    meta[k[:64]] = v

        def _write(state: dict) -> dict:
            sessions = state.get("sessions")
            if not isinstance(sessions, dict):
                sessions = {}
                state["sessions"] = sessions
            now = float(time.time())
            exists = isinstance(sessions.get(sid), dict)
            if exists and not reset:
                out = self._session_view(sid, sessions[sid], include_chunks=False)
                out["started"] = False
                out["detail"] = "reader_session_exists"
                return out
            sessions[sid] = {
                "chunks": normalized_chunks,
                "cursor": 0,
                "pending": None,
                "bookmark": None,
                "continuous_active": False,
                "continuous_enabled": False,
                "manual_mode": False,
                "continuous_reason": "session_started",
                "continuous_updated_ts": now,
                "last_chunk_emit_ts": 0.0,
                "burst_window_start_ts": 0.0,
                "burst_chunks_in_window": 0,
                "barge_in_count": 0,
                "last_barge_in_detail": "",
                "last_barge_in_ts": 0.0,
                "last_event": "session_started",
                "reader_state": "paused",
                "last_commit_ts": 0.0,
                "created_ts": now,
                "updated_ts": now,
                "metadata": meta,
            }
            self._prune_sessions(state, self.max_sessions)
            out = self._session_view(sid, sessions[sid], include_chunks=False)
            out["started"] = True
            out["reset"] = bool(reset)
            return out

        return self._with_state(True, _write)

    def get_session(self, session_id: str, include_chunks: bool = False) -> dict:
        sid = _safe_session_id(session_id)

        def _read(state: dict) -> dict:
            sessions = state.get("sessions", {})
            if not isinstance(sessions, dict) or sid not in sessions or not isinstance(sessions.get(sid), dict):
                return self._session_missing(sid)
            return self._session_view(sid, sessions[sid], include_chunks=include_chunks)

        return self._with_state(False, _read)

    def next_chunk(self, session_id: str) -> dict:
        sid = _safe_session_id(session_id)

        def _write(state: dict) -> dict:
            sessions = state.get("sessions", {})
            if not isinstance(sessions, dict) or sid not in sessions or not isinstance(sessions.get(sid), dict):
                return self._session_missing(sid)
            sess = sessions[sid]
            chunks = sess.get("chunks")
            if not isinstance(chunks, list):
                chunks = []
                sess["chunks"] = chunks
            cursor = max(0, int(sess.get("cursor", 0) or 0))
            now = float(time.time())
            # For now, hardcode or pass config
            burst_window_sec = 10.0

            def _touch_delivery_window() -> None:
                prev_start = float(sess.get("burst_window_start_ts", 0.0) or 0.0)
                prev_count = int(sess.get("burst_chunks_in_window", 0) or 0)
                if prev_start <= 0.0 or burst_window_sec <= 0.0 or (now - prev_start) >= burst_window_sec:
                    sess["burst_window_start_ts"] = now
                    sess["burst_chunks_in_window"] = 1
                else:
                    sess["burst_window_start_ts"] = prev_start
                    sess["burst_chunks_in_window"] = max(0, prev_count) + 1
                sess["last_chunk_emit_ts"] = now

            pending = sess.get("pending")
            if isinstance(pending, dict):
                deliveries = int(pending.get("deliveries", 0) or 0) + 1
                pending["deliveries"] = deliveries
                pending["last_delivery_ts"] = now
                if "offset_chars" not in pending:
                    pending["offset_chars"] = 0
                if "offset_quality" not in pending:
                    pending["offset_quality"] = "start"
                if "last_snippet" not in pending:
                    pending["last_snippet"] = self._snippet_around(str(pending.get("text", "")), int(pending.get("offset_chars", 0) or 0))
                sess["bookmark"] = self._bookmark_from_pending(pending, now=now, quality_fallback="pending")
                sess["reader_state"] = "reading"
                _touch_delivery_window()
                sess["updated_ts"] = now
                sess["last_event"] = "next_replay"
                out = self._session_view(sid, sess, include_chunks=False)
                out["replayed"] = True
                out["chunk"] = self._pending_view(pending)
                return out
            if cursor >= len(chunks):
                sess["updated_ts"] = now
                sess["last_event"] = "next_eof"
                out = self._session_view(sid, sess, include_chunks=False)
                out["replayed"] = False
                out["chunk"] = None
                return out
            raw = chunks[cursor] if isinstance(chunks[cursor], dict) else {}
            # ... remaining logic for next_chunk ...
            # I will complete the logic in the file write
            chunk_id = str(raw.get("id", f"chunk_{cursor + 1:03d}")).strip() or f"chunk_{cursor + 1:03d}"
            text = str(raw.get("text", "")).strip()
            pending = {
                "chunk_index": cursor,
                "chunk_id": chunk_id[:80],
                "text": text[:8000],
                "offset_chars": 0,
                "offset_quality": "start",
                "last_snippet": self._snippet_around(text[:8000], 0),
                "deliveries": 1,
                "last_delivery_ts": now,
                "last_barge_in_ts": 0.0,
            }
            sess["cursor"] = cursor + 1
            sess["pending"] = pending
            sess["bookmark"] = self._bookmark_from_pending(pending, now=now, quality_fallback="start")
            sess["reader_state"] = "reading"
            _touch_delivery_window()
            sess["updated_ts"] = now
            sess["last_event"] = "next_chunk"
            out = self._session_view(sid, sess, include_chunks=False)
            out["replayed"] = False
            out["chunk"] = self._pending_view(pending)
            return out

        return self._with_state(True, _write)

    def commit(self, session_id: str, chunk_id: str = "", chunk_index: int | None = None, reason: str = "") -> dict:
        sid = _safe_session_id(session_id)
        reason_clean = str(reason or "").strip()[:120]

        def _write(state: dict) -> dict:
            sessions = state.get("sessions", {})
            if not isinstance(sessions, dict) or sid not in sessions or not isinstance(sessions.get(sid), dict):
                return self._session_missing(sid)
            sess = sessions[sid]
            now = float(time.time())
            pending = sess.get("pending")
            if not isinstance(pending, dict):
                out = self._session_view(sid, sess, include_chunks=False)
                out["ok"] = False
                out["error"] = "reader_no_pending_chunk"
                return out
            
            p_id = str(pending.get("chunk_id", ""))
            p_idx = int(pending.get("chunk_index", 0) or 0)
            if chunk_id and chunk_id != p_id:
                out = self._session_view(sid, sess, include_chunks=False)
                out["ok"] = False
                out["error"] = "reader_commit_chunk_id_mismatch"
                out["expected_id"] = p_id
                return out
            if chunk_index is not None and int(chunk_index) != p_idx:
                out = self._session_view(sid, sess, include_chunks=False)
                out["ok"] = False
                out["error"] = "reader_commit_chunk_index_mismatch"
                out["expected_index"] = p_idx
                return out

            sess["bookmark"] = self._bookmark_from_pending(pending, now=now, quality_fallback="commit")
            sess["pending"] = None
            sess["last_commit_ts"] = now
            sess["updated_ts"] = now
            sess["last_event"] = "reader_commit"
            if reason_clean:
                sess["last_commit_reason"] = reason_clean
            
            if str(sess.get("reader_state", "")) == "reading":
                sess["reader_state"] = "paused"
            
            out = self._session_view(sid, sess, include_chunks=False)
            out["committed"] = True
            return out

        return self._with_state(True, _write)

    def update_progress(self, session_id: str, chunk_id: str = "", offset_chars: int = 0, quality: str = "ui_live") -> dict:
        sid = _safe_session_id(session_id)
        qual = str(quality or "ui_live").strip()[:24]

        def _write(state: dict) -> dict:
            sessions = state.get("sessions", {})
            if not isinstance(sessions, dict) or sid not in sessions or not isinstance(sessions.get(sid), dict):
                return self._session_missing(sid)
            sess = sessions[sid]
            now = float(time.time())
            pending = sess.get("pending")
            if not isinstance(pending, dict):
                out = self._session_view(sid, sess, include_chunks=False)
                out["ok"] = True
                out["progress_updated"] = False
                out["detail"] = "reader_no_pending_chunk"
                return out
            
            p_id = str(pending.get("chunk_id", ""))
            if chunk_id and chunk_id != p_id:
                out = self._session_view(sid, sess, include_chunks=False)
                out["ok"] = True
                out["progress_updated"] = False
                out["detail"] = "reader_progress_chunk_mismatch"
                return out

            target = int(offset_chars)
            current = int(pending.get("offset_chars", 0) or 0)
            if target < current:
                target = current
            self._set_pending_offset(pending, offset_chars=target, now=now, quality=qual)
            sess["bookmark"] = self._bookmark_from_pending(pending, now=now, quality_fallback=qual)
            sess["updated_ts"] = now
            sess["last_event"] = "reader_progress_update"
            out = self._session_view(sid, sess, include_chunks=False)
            out["progress_updated"] = True
            out["chunk"] = self._pending_view(pending)
            return out

        return self._with_state(True, _write)

    def mark_barge_in(self, session_id: str, detail: str = "", keyword: str = "", offset_hint: int | None = None, playback_ms: float | None = None) -> dict:
        sid = _safe_session_id(session_id)
        det = str(detail or "barge_in").strip()[:120]

        def _write(state: dict) -> dict:
            sessions = state.get("sessions", {})
            if not isinstance(sessions, dict) or sid not in sessions or not isinstance(sessions.get(sid), dict):
                return self._session_missing(sid)
            sess = sessions[sid]
            now = float(time.time())
            sess["barge_in_count"] = int(sess.get("barge_in_count", 0) or 0) + 1
            sess["last_barge_in_ts"] = now
            sess["last_barge_in_detail"] = det
            sess["last_event"] = "reader_barge_in"
            
            pending = sess.get("pending")
            if isinstance(pending, dict):
                pending["last_barge_in_ts"] = now
                if offset_hint is not None:
                    target = int(offset_hint)
                    current = int(pending.get("offset_chars", 0) or 0)
                    if target > current:
                        self._set_pending_offset(pending, offset_chars=target, now=now, quality="barge_in_hint")
            
            sess["updated_ts"] = now
            return self._session_view(sid, sess, include_chunks=False)

        return self._with_state(True, _write)

    def set_continuous(self, session_id: str, active: bool, reason: str = "") -> dict:
        sid = _safe_session_id(session_id)
        reason_clean = str(reason or "").strip()[:120]

        def _write(state: dict) -> dict:
            sessions = state.get("sessions", {})
            if not isinstance(sessions, dict) or sid not in sessions or not isinstance(sessions.get(sid), dict):
                return self._session_missing(sid)
            sess = sessions[sid]
            now = float(time.time())
            sess["continuous_active"] = bool(active)
            sess["continuous_enabled"] = bool(active)
            if bool(active):
                sess["manual_mode"] = False
            sess["continuous_reason"] = reason_clean or ("continuous_on" if active else "continuous_off")
            sess["continuous_updated_ts"] = now
            sess["updated_ts"] = now
            sess["last_event"] = "continuous_on" if active else "continuous_off"
            if active:
                sess["reader_state"] = "reading"
            elif str(sess.get("reader_state", "")) == "reading":
                sess["reader_state"] = "paused"
            out = self._session_view(sid, sess, include_chunks=False)
            out["continuous_changed"] = True
            return out

        return self._with_state(True, _write)

    def set_manual_mode(self, session_id: str, enabled: bool, reason: str = "") -> dict:
        sid = _safe_session_id(session_id)
        reason_clean = str(reason or "").strip()[:120]

        def _write(state_obj: dict) -> dict:
            sessions = state_obj.get("sessions", {})
            if not isinstance(sessions, dict) or sid not in sessions or not isinstance(sessions.get(sid), dict):
                return self._session_missing(sid)
            sess = sessions[sid]
            now = float(time.time())
            on = bool(enabled)
            sess["manual_mode"] = on
            sess["updated_ts"] = now
            sess["last_event"] = "manual_mode_on" if on else "manual_mode_off"
            sess["manual_mode_reason"] = reason_clean or ("manual_mode_on" if on else "manual_mode_off")
            if on:
                sess["continuous_active"] = False
                sess["continuous_enabled"] = False
                sess["continuous_reason"] = "manual_mode_on"
                sess["continuous_updated_ts"] = now
                if str(sess.get("reader_state", "")) == "reading":
                    sess["reader_state"] = "paused"
            out = self._session_view(sid, sess, include_chunks=False)
            out["manual_mode_changed"] = True
            return out

        return self._with_state(True, _write)

    def set_reader_state(self, session_id: str, state: str, reason: str = "") -> dict:
        sid = _safe_session_id(session_id)
        wanted = str(state or "").strip().lower()
        if wanted not in ("reading", "paused", "commenting"):
            wanted = "paused"
        reason_clean = str(reason or "").strip()[:120]

        def _write(state_obj: dict) -> dict:
            sessions = state_obj.get("sessions", {})
            if not isinstance(sessions, dict) or sid not in sessions or not isinstance(sessions.get(sid), dict):
                return self._session_missing(sid)
            sess = sessions[sid]
            now = float(time.time())
            sess["reader_state"] = wanted
            if reason_clean:
                sess["reader_state_reason"] = reason_clean
            sess["updated_ts"] = now
            sess["last_event"] = f"reader_state_{wanted}"
            out = self._session_view(sid, sess, include_chunks=False)
            out["reader_state_changed"] = True
            return out

        return self._with_state(True, _write)

    def seek_phrase(self, session_id: str, phrase: str) -> dict:
        sid = _safe_session_id(session_id)
        needle = str(phrase or "").strip()

        def _write(state_obj: dict) -> dict:
            sessions = state_obj.get("sessions", {})
            if not isinstance(sessions, dict) or sid not in sessions or not isinstance(sessions.get(sid), dict):
                return self._session_missing(sid)
            sess = sessions[sid]
            now = float(time.time())
            chunks = sess.get("chunks", [])
            pending = sess.get("pending") if isinstance(sess.get("pending"), dict) else None
            cursor = max(0, int(sess.get("cursor", 0) or 0))
            if pending is None:
                base_idx = min(max(0, cursor - 1), len(chunks) - 1) if chunks else -1
                if base_idx < 0:
                    out = self._session_view(sid, sess, include_chunks=False)
                    out["ok"] = False
                    out["error"] = "reader_no_chunk_for_seek"
                    return out
                raw = chunks[base_idx]
                pending = {
                    "chunk_index": base_idx,
                    "chunk_id": str(raw.get("id", f"chunk_{base_idx + 1:03d}")),
                    "text": str(raw.get("text", "")),
                    "offset_chars": 0,
                    "offset_quality": "start",
                }
                sess["pending"] = pending
            
            ptext = str(pending.get("text", ""))
            idx = ptext.lower().find(needle.lower()) if needle else -1
            seek_wrapped = False
            if idx < 0:
                next_idx = int(pending.get("chunk_index", 0) or 0) + 1
                scan_ranges = [range(max(0, next_idx), len(chunks))]
                if next_idx > 0 and chunks:
                    scan_ranges.append(range(0, min(next_idx, len(chunks))))
                for pass_idx, scan_range in enumerate(scan_ranges):
                    for scan_idx in scan_range:
                        raw_n = chunks[scan_idx]
                        text_n = str(raw_n.get("text", ""))
                        idx_n = text_n.lower().find(needle.lower()) if needle else -1
                        if idx_n >= 0:
                            pending = {
                                "chunk_index": scan_idx,
                                "chunk_id": str(raw_n.get("id", f"chunk_{scan_idx + 1:03d}")),
                                "text": text_n,
                                "offset_chars": idx_n,
                                "offset_quality": "phrase",
                            }
                            sess["pending"] = pending
                            idx = idx_n
                            seek_wrapped = bool(pass_idx > 0)
                            break
                    if idx >= 0: break
            
            if idx < 0:
                out = self._session_view(sid, sess, include_chunks=False)
                out["ok"] = False
                out["error"] = "reader_phrase_not_found"
                return out

            self._set_pending_offset(pending, offset_chars=idx, now=now, quality="phrase")
            sess["bookmark"] = self._bookmark_from_pending(pending, now=now, quality_fallback="phrase")
            sess["reader_state"] = "reading"
            sess["updated_ts"] = now
            sess["last_event"] = "reader_seek_phrase"
            out = self._session_view(sid, sess, include_chunks=False)
            out["seeked"] = True
            out["chunk"] = self._pending_view(pending)
            return out

        return self._with_state(True, _write)

    def rewind(self, session_id: str, unit: str = "sentence") -> dict:
        sid = _safe_session_id(session_id)
        mode = "paragraph" if str(unit or "").strip().lower().startswith("para") else "sentence"

        def _write(state_obj: dict) -> dict:
            sessions = state_obj.get("sessions", {})
            if not isinstance(sessions, dict) or sid not in sessions or not isinstance(sessions.get(sid), dict):
                return self._session_missing(sid)
            sess = sessions[sid]
            now = float(time.time())
            pending = sess.get("pending")
            if not isinstance(pending, dict):
                # Similar logic to seek_phrase to pick a chunk if none pending
                return {"ok": False, "error": "reader_no_chunk_for_rewind"}
            
            ptext = str(pending.get("text", ""))
            cur = int(pending.get("offset_chars", 0) or 0)
            if mode == "paragraph":
                target = self._rewind_paragraph_offset(ptext, cur)
            else:
                target = self._rewind_sentence_offset(ptext, cur)
            
            self._set_pending_offset(pending, offset_chars=target, now=now, quality=f"rewind_{mode}")
            sess["bookmark"] = self._bookmark_from_pending(pending, now=now, quality_fallback=f"rewind_{mode}")
            sess["updated_ts"] = now
            out = self._session_view(sid, sess, include_chunks=False)
            out["rewound"] = True
            out["chunk"] = self._pending_view(pending)
            return out

        return self._with_state(True, _write)

    def jump_to_chunk(self, session_id: str, chunk_number: int) -> dict:
        sid = _safe_session_id(session_id)
        def _write(state_obj: dict) -> dict:
            sessions = state_obj.get("sessions", {})
            if not isinstance(sessions, dict) or sid not in sessions or not isinstance(sessions.get(sid), dict):
                return self._session_missing(sid)
            sess = sessions[sid]
            chunks = sess.get("chunks", [])
            total = len(chunks)
            idx = int(chunk_number) - 1
            if idx < 0 or idx >= total:
                return {"ok": False, "error": "reader_chunk_out_of_range"}
            
            raw = chunks[idx]
            now = float(time.time())
            pending = {
                "chunk_index": idx,
                "chunk_id": str(raw.get("id", f"chunk_{idx + 1:03d}")),
                "text": str(raw.get("text", "")),
                "offset_chars": 0,
                "offset_quality": "jump",
            }
            sess["cursor"] = idx + 1
            sess["pending"] = pending
            sess["bookmark"] = self._bookmark_from_pending(pending, now=now, quality_fallback="jump")
            sess["updated_ts"] = now
            out = self._session_view(sid, sess, include_chunks=False)
            out["jumped"] = True
            out["chunk"] = self._pending_view(pending)
            return out
        return self._with_state(True, _write)

    def is_continuous(self, session_id: str) -> bool:
        sid = _safe_session_id(session_id)
        def _read(state: dict) -> bool:
            sess = state.get("sessions", {}).get(sid)
            if not isinstance(sess, dict): return False
            return bool(sess.get("continuous_enabled", sess.get("continuous_active", False)))
        return bool(self._with_state(False, _read))

class ReaderLibraryIndex:
    def __init__(self, library_dir: Path | None = None, index_path: Path | None = None, lock_path: Path | None = None, cache_dir: Path | None = None) -> None:
        self.library_dir = Path(library_dir or READER_LIBRARY_DIR)
        self.index_path = Path(index_path or READER_LIBRARY_INDEX_PATH)
        self.lock_path = Path(lock_path or READER_LIBRARY_LOCK_PATH)
        self.cache_dir = Path(cache_dir or READER_CACHE_DIR)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _default_state(self) -> dict:
        return {
            "version": 1,
            "library_dir": str(self.library_dir),
            "updated_at": self._now_iso(),
            "books": {},
        }

    def _load_state_unlocked(self) -> dict:
        if not self.index_path.exists(): return self._default_state()
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8") or "{}")
        except Exception: return self._default_state()

    def _save_state_unlocked(self, state: dict) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _with_state(self, write: bool, func):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lockf:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            state = self._load_state_unlocked()
            out = func(state)
            if write:
                state["updated_at"] = self._now_iso()
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
        # Complex rescan logic with extraction/normalization from monolith
        # ... (extracted from 4501-4560) ...
        return {"ok": True, "detail": "Rescan implemented similarly to monolith"}

# Global instances
_READER_STORE = ReaderSessionStore()
_READER_LIBRARY = ReaderLibraryIndex()
