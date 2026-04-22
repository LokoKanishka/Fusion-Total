from __future__ import annotations

import json
import subprocess
import threading
import time
import os
import re
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from pathlib import Path

from .conversation import ConversationCore
from .dialogue import STTProvider, default_stt_provider
from .metrics import VoiceMetric, VoiceMetricsStore
from .notes import ReaderNotesStore
from .reader import Document, ReaderSession
from .tts import AllTalkProvider, AudioArtifact, AudioCache, TTSProvider

LABORATORY_NOTES_DOC_ID = "__laboratory__"
LABORATORY_NOTES_TITLE = "Laboratorio"


@dataclass
class VoiceSettings:
    voice: str = field(default_factory=lambda: os.environ.get("FUSION_READER_VOICE", "female_03.wav"))
    language: str = "es"


class FusionReaderV2:
    def __init__(
        self,
        tts: TTSProvider | None = None,
        cache: AudioCache | None = None,
        voice: VoiceSettings | None = None,
        metrics: VoiceMetricsStore | None = None,
        conversation: ConversationCore | None = None,
        stt: STTProvider | None = None,
        notes: ReaderNotesStore | None = None,
        prefetch_wait_seconds: float = 25.0,
        prefetch_ahead: int | None = None,
        prefetch_workers: int | None = None,
        session_state_path: Path | str | None = "runtime/fusion_reader_v2/session_state.json",
    ) -> None:
        self.session = ReaderSession()
        self.tts = tts or AllTalkProvider()
        self.cache = cache or AudioCache()
        self.voice = voice or VoiceSettings()
        self.metrics = metrics or VoiceMetricsStore()
        self.conversation = conversation or ConversationCore()
        self.stt = stt or default_stt_provider()
        self.notes = notes or ReaderNotesStore()
        self.prefetch_wait_seconds = prefetch_wait_seconds
        self.prefetch_ahead = max(0, int(prefetch_ahead if prefetch_ahead is not None else os.environ.get("FUSION_READER_PREFETCH_AHEAD", "3")))
        self.prefetch_workers = max(1, int(prefetch_workers if prefetch_workers is not None else os.environ.get("FUSION_READER_PREFETCH_WORKERS", "1")))
        self._executor = ThreadPoolExecutor(max_workers=self.prefetch_workers, thread_name_prefix="fusion-reader-v2-tts")
        self._prefetch_lock = threading.Lock()
        self._prefetch_futures: dict[int, Future[AudioArtifact]] = {}
        self._prefetch_started: dict[int, float] = {}
        self._prefetch_future: Future[AudioArtifact] | None = None
        self._prefetch_index: int | None = None
        self._prefetch_started_ts: float | None = None
        self._tts_lock = threading.Lock()
        self._prepare_lock = threading.Lock()
        self._prepare_cancel = threading.Event()
        self._prepare_thread: threading.Thread | None = None
        self._prepare_generation = 0
        self._prepare_status: dict = self._new_prepare_status()
        self._chat_lock = threading.Lock()
        self._chat_history: list[dict] = []
        self._dialogue_lock = threading.Lock()
        self._dialogue_history: list[dict] = []
        self.dialogue_tts_max_chars = int(os.environ.get("FUSION_READER_DIALOGUE_TTS_MAX_CHARS", "520"))
        self.fast_note_ack = os.environ.get("FUSION_READER_FAST_NOTE_ACK", "0").strip().lower() not in {"0", "false", "no"}
        self.fast_dialogue_ack = os.environ.get("FUSION_READER_FAST_DIALOGUE_ACK", "0").strip().lower() not in {"0", "false", "no"}
        self._reference_documents: dict[str, dict] = {}
        self._laboratory_focus: dict = {}
        self._main_source_path = ""
        self._main_source_type = ""
        self.dialogue_allow_supreme = os.environ.get("FUSION_READER_DIALOGUE_ALLOW_SUPREME", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.reasoning_mode = str(getattr(self.conversation, "default_reasoning_mode", "thinking") or "thinking")
        self.laboratory_mode = "document"
        self.session_state_path = Path(session_state_path) if session_state_path else None
        self.dialogue_trace_path = (self.session_state_path.parent / "dialogue_trace.jsonl") if self.session_state_path else None
        self._restore_session_state()

    def _effective_reasoning_mode(self, *, dialogue: bool = False) -> dict:
        requested = str(self.reasoning_mode or "thinking")
        applied = requested
        degraded = False
        reason = ""
        if dialogue and requested == "supreme" and not self.dialogue_allow_supreme:
            applied = "thinking"
            degraded = True
            reason = "dialogue_supreme_degraded_to_thinking"
        return {
            "requested": requested,
            "applied": applied,
            "degraded": degraded,
            "reason": reason,
        }

    def _append_dialogue_trace(self, event: dict) -> None:
        if self.dialogue_trace_path is None:
            return
        try:
            self.dialogue_trace_path.parent.mkdir(parents=True, exist_ok=True)
            with self.dialogue_trace_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=True) + "\n")
        except Exception:
            return

    def _document_record(
        self,
        doc_id: str,
        title: str,
        text: str,
        source_path: str = "",
        source_type: str = "",
    ) -> dict:
        document = Document.from_text(doc_id, title, text)
        preview = " ".join(document.text.split())[:220]
        return {
            "doc_id": document.doc_id,
            "title": document.title,
            "text": document.text,
            "source_path": str(source_path or ""),
            "source_type": str(source_type or "text"),
            "total": len(document.chunks),
            "preview": preview,
        }

    def _public_document_record(self, record: dict) -> dict:
        return {
            "doc_id": str(record.get("doc_id") or ""),
            "title": str(record.get("title") or ""),
            "source_path": str(record.get("source_path") or ""),
            "source_type": str(record.get("source_type") or ""),
            "total": int(record.get("total") or 0),
            "preview": str(record.get("preview") or ""),
        }

    def _snapshot_document_record(self, record: dict) -> dict:
        document = Document.from_text(
            str(record.get("doc_id") or ""),
            str(record.get("title") or ""),
            str(record.get("text") or ""),
        )
        return {
            **self._public_document_record(record),
            "text": document.text,
            "chunks": [
                {
                    "chunk_number": index + 1,
                    "text": chunk,
                }
                for index, chunk in enumerate(document.chunks)
            ],
        }

    def _main_document_record(self) -> dict | None:
        document = self.session.document
        if not document:
            return None
        return self._document_record(
            document.doc_id,
            document.title,
            document.text,
            source_path=self._main_source_path,
            source_type=self._main_source_type,
        )

    def _reference_document_items(self) -> list[dict]:
        return [self._public_document_record(item) for item in self._reference_documents.values()]

    def _all_document_records(self) -> list[dict]:
        records: list[dict] = []
        main_record = self._main_document_record()
        if main_record:
            records.append(
                {
                    **self._snapshot_document_record(main_record),
                    "role": "main",
                    "current": int(self.session.status().get("current") or 0),
                }
            )
        for item in self._reference_documents.values():
            records.append({**self._snapshot_document_record(item), "role": "reference"})
        return records

    def _normalize_search_text(self, text: str) -> str:
        return " ".join(str(text or "").strip().lower().replace("¿", "").replace("¡", "").split())

    def _meaningful_search_terms(self, text: str) -> list[str]:
        stopwords = {
            "a", "al", "alguna", "alguno", "andá", "anda", "andar", "bloque", "busca", "buscá", "buscar",
            "chunk", "con", "consulta", "de", "del", "donde", "dónde", "documento", "el", "en", "esa", "ese",
            "esta", "este", "habla", "ir", "la", "las", "lo", "los", "main", "muestra", "mostrame", "muéstrame",
            "para", "parte", "por", "principal", "quiero", "sección", "seccion", "sobre", "texto", "ver",
            "vamos", "y",
        }
        tokens = re.findall(r"[a-záéíóúñ0-9_./-]{3,}", self._normalize_search_text(text))
        out: list[str] = []
        for token in tokens:
            if token in stopwords or token in out:
                continue
            out.append(token)
        return out

    def _resolve_document_record(self, selector: str = "") -> dict | None:
        clean_selector = self._normalize_search_text(selector)
        records = self._all_document_records()
        if not clean_selector:
            return records[0] if records else None
        for record in records:
            title = self._normalize_search_text(record.get("title") or "")
            doc_id = self._normalize_search_text(record.get("doc_id") or "")
            if clean_selector == title or clean_selector == doc_id:
                return record
        for record in records:
            haystack = " ".join(
                [
                    self._normalize_search_text(record.get("title") or ""),
                    self._normalize_search_text(record.get("doc_id") or ""),
                ]
            ).strip()
            if clean_selector and clean_selector in haystack:
                return record
        selector_terms = self._meaningful_search_terms(clean_selector)
        ranked: list[tuple[int, dict]] = []
        for record in records:
            haystack = " ".join(
                [
                    self._normalize_search_text(record.get("title") or ""),
                    self._normalize_search_text(record.get("doc_id") or ""),
                    self._normalize_search_text(record.get("preview") or ""),
                ]
            ).strip()
            score = sum(1 for term in selector_terms if term in haystack)
            if score > 0:
                ranked.append((score, record))
        if ranked:
            ranked.sort(key=lambda item: item[0], reverse=True)
            return ranked[0][1]
        return None

    def _set_laboratory_focus(self, record: dict, chunk_index: int, query: str = "", reason: str = "") -> dict:
        chunks = record.get("chunks") if isinstance(record.get("chunks"), list) else []
        if chunk_index < 0 or chunk_index >= len(chunks):
            raise IndexError("chunk_out_of_bounds")
        item = chunks[chunk_index]
        text = str(item.get("text") or "").strip()
        focus = {
            "ok": True,
            "doc_id": str(record.get("doc_id") or ""),
            "title": str(record.get("title") or ""),
            "role": str(record.get("role") or "reference"),
            "source_type": str(record.get("source_type") or "text"),
            "total": int(record.get("total") or len(chunks)),
            "chunk_index": int(chunk_index),
            "chunk_number": int(item.get("chunk_number") or chunk_index + 1),
            "text": text,
            "query": str(query or "").strip(),
            "reason": str(reason or "").strip(),
            "updated_ts": time.time(),
        }
        self._laboratory_focus = focus
        return dict(focus)

    def laboratory_focus_status(self) -> dict:
        return dict(self._laboratory_focus) if self._laboratory_focus else {}

    def _focus_record(self) -> dict | None:
        focus = self.laboratory_focus_status()
        if not focus:
            return None
        record = self._resolve_document_record(str(focus.get("doc_id") or focus.get("title") or ""))
        if not record:
            return None
        return record

    def _new_prepare_status(self) -> dict:
        return {
            "ok": True,
            "status": "idle",
            "doc_id": "",
            "title": "",
            "current": 0,
            "total": 0,
            "percent": 0,
            "cached": 0,
            "generated": 0,
            "failed": 0,
            "message": "Sin preparación activa.",
            "started_ts": 0.0,
            "updated_ts": 0.0,
            "done_ts": 0.0,
        }

    def load_text(
        self,
        doc_id: str,
        title: str,
        text: str,
        prefetch: bool = True,
        source_path: str = "",
        source_type: str = "",
    ) -> dict:
        self._reset_prepare_for_new_document()
        self._laboratory_focus = {}
        status = self.session.load(Document.from_text(doc_id, title, text))
        self._main_source_path = str(source_path or "")
        self._main_source_type = str(source_type or "text")
        self._reference_documents.pop(str(status.get("doc_id") or ""), None)
        self._persist_session_state(text=str(text or ""), source_path=source_path, source_type=source_type)
        if prefetch:
            self.prefetch_current()
        return status

    def load_file(self, path: str | Path, prefetch: bool = True) -> dict:
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        return self.load_text(p.stem, p.name, text, prefetch=prefetch, source_path=str(p), source_type="file")

    def add_reference_text(
        self,
        doc_id: str,
        title: str,
        text: str,
        source_path: str = "",
        source_type: str = "",
    ) -> dict:
        main_doc = self.session.document
        clean_doc_id = str(doc_id or "").strip()
        if main_doc and clean_doc_id and clean_doc_id == main_doc.doc_id:
            return {"ok": False, "error": "reference_matches_main_document"}
        record = self._document_record(clean_doc_id or title, title, text, source_path=source_path, source_type=source_type)
        self._reference_documents[record["doc_id"]] = record
        self._persist_session_state()
        return {
            **self.status(),
            "reference_added": self._public_document_record(record),
            "message": f"{record['title']} agregado como documento de consulta.",
        }

    def add_reference_file(self, path: str | Path) -> dict:
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        return self.add_reference_text(p.stem, p.name, text, source_path=str(p), source_type="file")

    def list_reference_documents(self) -> dict:
        return {"ok": True, "items": self._reference_document_items()}

    def remove_reference_document(self, doc_id: str) -> dict:
        removed = self._reference_documents.pop(str(doc_id or ""), None)
        if not removed:
            return {"ok": False, "error": "reference_not_found"}
        if str(self._laboratory_focus.get("doc_id") or "") == str(removed.get("doc_id") or ""):
            self._laboratory_focus = {}
        self._persist_session_state()
        return {"ok": True, "removed": True, "reference": self._public_document_record(removed), "items": self._reference_document_items()}

    def promote_reference_document(self, doc_id: str, prefetch: bool = True) -> dict:
        selected_id = str(doc_id or "")
        record = self._reference_documents.pop(selected_id, None)
        if not record:
            return {"ok": False, "error": "reference_not_found"}
        previous_main = self._main_document_record()
        self._reset_prepare_for_new_document()
        status = self.session.load(Document.from_text(str(record.get("doc_id") or ""), str(record.get("title") or ""), str(record.get("text") or "")))
        self._main_source_path = str(record.get("source_path") or "")
        self._main_source_type = str(record.get("source_type") or "text")
        if previous_main and previous_main["doc_id"] != status.get("doc_id"):
            self._reference_documents[previous_main["doc_id"]] = previous_main
        self._laboratory_focus = {}
        self._persist_session_state()
        if prefetch:
            self.prefetch_current()
        return {
            **self.status(),
            "promoted_reference": self._public_document_record(record),
            "message": f"{record['title']} ahora es el documento principal.",
        }

    def status(self) -> dict:
        out = self.session.status()
        out["voice"] = self.voice.voice
        out["language"] = self.voice.language
        out["tts"] = self.tts.health()
        out["reasoning"] = self.reasoning_status()
        out["laboratory_mode"] = self.laboratory_mode_status()
        main_record = self._main_document_record()
        out["main_document"] = self._public_document_record(main_record) if main_record else {}
        out["reference_documents"] = self._reference_document_items()
        out["laboratory_focus"] = self.laboratory_focus_status()
        with self._prefetch_lock:
            out["prefetch_index"] = self._prefetch_index
            out["prefetch_done"] = bool(self._prefetch_future and self._prefetch_future.done())
            out["prefetch_age_ms"] = int((time.time() - self._prefetch_started_ts) * 1000) if self._prefetch_started_ts else 0
            out["prefetch_indexes"] = sorted(self._prefetch_futures)
            out["prefetch_done_indexes"] = sorted(index for index, future in self._prefetch_futures.items() if future.done())
            out["prefetch_ahead"] = self.prefetch_ahead
        out["prepare"] = self.prepare_status()
        out["notes"] = self.notes_summary()
        return out

    def _synthesize_cached(self, text: str) -> AudioArtifact:
        cached = self.cache.get(text, self.voice.voice, self.voice.language)
        if cached:
            return cached
        with self._tts_lock:
            cached = self.cache.get(text, self.voice.voice, self.voice.language)
            if cached:
                return cached
            artifact = self.tts.synthesize(text, voice=self.voice.voice, language=self.voice.language)
            return self.cache.put(text, self.voice.voice, self.voice.language, artifact)

    def _artifact_for_index(self, index: int, text: str) -> AudioArtifact:
        with self._prefetch_lock:
            future = self._prefetch_futures.get(index)
            if not future and self._prefetch_index == index:
                future = self._prefetch_future
        if future:
            try:
                artifact = future.result(timeout=self.prefetch_wait_seconds)
                self._forget_prefetch(index, future)
                return artifact
            except TimeoutError:
                self._forget_prefetch(index, future)
                self._reset_prefetch_queue(future)
                return AudioArtifact(False, provider=self.tts.name, detail="prefetch_timeout")
        return self._synthesize_cached(text)

    def prefetch_current(self) -> None:
        self.prefetch_window(self.session.cursor)

    def prefetch_next(self) -> None:
        self.prefetch_window(self.session.cursor + 1)

    def prefetch_window(self, start_index: int) -> None:
        for offset in range(self.prefetch_ahead + 1):
            self._prefetch(start_index + offset)

    def _prefetch(self, index: int) -> None:
        document = self.session.document
        if not document or index < 0 or index >= len(document.chunks):
            return
        text = document.chunks[index]
        with self._prefetch_lock:
            existing = self._prefetch_futures.get(index)
            if existing and not existing.done():
                return
            future = self._executor.submit(self._synthesize_cached, text)
            self._prefetch_futures[index] = future
            self._prefetch_started[index] = time.time()
            self._set_primary_prefetch_locked()

    def _forget_prefetch(self, index: int, future: Future[AudioArtifact]) -> None:
        with self._prefetch_lock:
            if self._prefetch_futures.get(index) is future:
                self._prefetch_futures.pop(index, None)
                self._prefetch_started.pop(index, None)
            if self._prefetch_future is future:
                self._set_primary_prefetch_locked()

    def _set_primary_prefetch_locked(self) -> None:
        if not self._prefetch_futures:
            self._prefetch_future = None
            self._prefetch_index = None
            self._prefetch_started_ts = None
            return
        current = self.session.cursor
        index = min(self._prefetch_futures, key=lambda item: (abs(item - current), item))
        self._prefetch_index = index
        self._prefetch_future = self._prefetch_futures[index]
        self._prefetch_started_ts = self._prefetch_started.get(index)

    def _reset_prefetch_queue(self, stale_future: Future[AudioArtifact]) -> None:
        with self._prefetch_lock:
            stale_indexes = [index for index, future in self._prefetch_futures.items() if future is stale_future]
            if self._prefetch_future is not stale_future and not stale_indexes:
                return
            old_executor = self._executor
            self._executor = ThreadPoolExecutor(max_workers=self.prefetch_workers, thread_name_prefix="fusion-reader-v2-tts")
            for index in stale_indexes:
                self._prefetch_futures.pop(index, None)
                self._prefetch_started.pop(index, None)
            self._prefetch_future = None
            self._prefetch_index = None
            self._prefetch_started_ts = None
            self._set_primary_prefetch_locked()
        old_executor.shutdown(wait=False, cancel_futures=True)

    def read_current(self, play: bool = True) -> dict:
        text = self.session.current_chunk()
        if not text:
            return {**self.session.status(), "ok": False, "error": "no_current_chunk"}
        started = time.perf_counter()
        artifact = self._artifact_for_index(self.session.cursor, text)
        ready_ms = int((time.perf_counter() - started) * 1000)
        if play and artifact.ok:
            self._play(artifact.path)
        self.prefetch_next()
        status = self.session.status()
        out = {
            **status,
            "ok": artifact.ok,
            "audio": str(artifact.path or ""),
            "cached": artifact.cached,
            "detail": artifact.detail,
            "provider": artifact.provider,
            "synthesis_ms": artifact.duration_ms,
            "ready_ms": ready_ms,
        }
        self._record_voice_metric("read", out, text)
        return out

    def next(self) -> dict:
        self.session.next_chunk()
        self._persist_session_state()
        self.prefetch_current()
        return self.session.status()

    def previous(self) -> dict:
        self.session.previous_chunk()
        self._persist_session_state()
        self.prefetch_current()
        return self.session.status()

    def jump(self, one_based_index: int) -> dict:
        self.session.jump(one_based_index)
        self._persist_session_state()
        self.prefetch_current()
        return self.session.status()

    def prepare_document(self, start: str = "cursor") -> dict:
        document = self.session.document
        if not document or not document.chunks:
            return {"ok": False, "error": "no_document_loaded"}
        with self._prepare_lock:
            if self._prepare_thread and self._prepare_thread.is_alive():
                return dict(self._prepare_status)
            self._prepare_cancel.clear()
            self._prepare_generation += 1
            generation = self._prepare_generation
            now = time.time()
            self._prepare_status = {
                **self._new_prepare_status(),
                "status": "running",
                "doc_id": document.doc_id,
                "title": document.title,
                "total": len(document.chunks),
                "message": "Preparando audio del documento...",
                "started_ts": now,
                "updated_ts": now,
            }
            self._prepare_thread = threading.Thread(
                target=self._prepare_worker,
                args=(document.doc_id, start, generation),
                name="fusion-reader-v2-prepare",
                daemon=True,
            )
            self._prepare_thread.start()
            return dict(self._prepare_status)

    def cancel_prepare(self) -> dict:
        self._prepare_cancel.set()
        with self._prepare_lock:
            if self._prepare_status.get("status") == "running":
                self._prepare_status["status"] = "canceling"
                self._prepare_status["message"] = "Cancelando preparación..."
                self._prepare_status["updated_ts"] = time.time()
            return dict(self._prepare_status)

    def prepare_status(self) -> dict:
        with self._prepare_lock:
            return dict(self._prepare_status)

    def _prepare_worker(self, doc_id: str, start: str, generation: int) -> None:
        document = self.session.document
        if not document or document.doc_id != doc_id:
            self._finish_prepare("error", "El documento activo cambió antes de preparar audio.", generation=generation)
            return
        total = len(document.chunks)
        start_index = self.session.cursor if start != "beginning" else 0
        order = list(range(start_index, total)) + list(range(0, start_index))
        cached = generated = failed = processed = 0
        for index in order:
            if self._prepare_cancel.is_set():
                self._finish_prepare("canceled", "Preparación cancelada.", processed, total, cached, generated, failed, generation=generation)
                return
            current_document = self.session.document
            if not current_document or current_document.doc_id != doc_id:
                self._finish_prepare("canceled", "Preparación detenida porque cambió el documento.", processed, total, cached, generated, failed, generation=generation)
                return
            self._wait_for_interactive_tts()
            text = current_document.chunks[index]
            if self.cache.get(text, self.voice.voice, self.voice.language):
                cached += 1
            else:
                artifact = self._synthesize_cached(text)
                if artifact.ok:
                    generated += 1
                else:
                    failed += 1
            processed += 1
            self._update_prepare_status(processed, total, cached, generated, failed, generation=generation)
        self._finish_prepare("done", "Documento preparado en cache.", processed, total, cached, generated, failed, generation=generation)

    def _reset_prepare_for_new_document(self) -> None:
        self._prepare_cancel.set()
        with self._prepare_lock:
            self._prepare_generation += 1
            self._prepare_status = self._new_prepare_status()
        self._prepare_cancel.clear()

    def _wait_for_interactive_tts(self) -> None:
        while not self._prepare_cancel.is_set():
            with self._prefetch_lock:
                busy = any(not future.done() for future in self._prefetch_futures.values())
            if not busy:
                return
            time.sleep(0.2)

    def _update_prepare_status(self, current: int, total: int, cached: int, generated: int, failed: int, generation: int) -> None:
        with self._prepare_lock:
            if generation != self._prepare_generation:
                return
            self._prepare_status.update(
                {
                    "current": current,
                    "total": total,
                    "percent": int(((cached + generated + failed) * 100) / total) if total else 0,
                    "cached": cached,
                    "generated": generated,
                    "failed": failed,
                    "message": f"Preparando audio {cached + generated + failed}/{total}.",
                    "updated_ts": time.time(),
                }
            )

    def _finish_prepare(
        self,
        status: str,
        message: str,
        current: int | None = None,
        total: int | None = None,
        cached: int | None = None,
        generated: int | None = None,
        failed: int | None = None,
        generation: int | None = None,
    ) -> None:
        with self._prepare_lock:
            if generation is not None and generation != self._prepare_generation:
                return
            if current is not None:
                self._prepare_status["current"] = current
            if total is not None:
                self._prepare_status["total"] = total
            if cached is not None:
                self._prepare_status["cached"] = cached
            if generated is not None:
                self._prepare_status["generated"] = generated
            if failed is not None:
                self._prepare_status["failed"] = failed
            total_count = int(self._prepare_status.get("total") or 0)
            done_count = int(self._prepare_status.get("cached") or 0) + int(self._prepare_status.get("generated") or 0) + int(self._prepare_status.get("failed") or 0)
            self._prepare_status["status"] = status
            self._prepare_status["percent"] = int(done_count * 100 / total_count) if total_count else 0
            self._prepare_status["message"] = message
            self._prepare_status["updated_ts"] = time.time()
            self._prepare_status["done_ts"] = time.time()

    def test_voice(self, text: str = "Prueba de voz neural del lector conversacional.", play: bool = True) -> dict:
        started = time.perf_counter()
        artifact = self._synthesize_cached(text)
        ready_ms = int((time.perf_counter() - started) * 1000)
        if play and artifact.ok:
            self._play(artifact.path)
        out = {
            "ok": artifact.ok,
            "audio": str(artifact.path or ""),
            "cached": artifact.cached,
            "detail": artifact.detail,
            "provider": artifact.provider,
            "synthesis_ms": artifact.duration_ms,
            "ready_ms": ready_ms,
        }
        self._record_voice_metric("voice_test", out, text)
        return out

    def voices(self) -> dict:
        return {"ok": True, "voices": self.tts.voices(), "current": self.voice.voice}

    def recent_voice_metrics(self, limit: int = 20) -> dict:
        return {"ok": True, "items": self.metrics.recent(limit=limit)}

    def voice_metrics_summary(self, limit: int = 500) -> dict:
        return {"ok": True, "items": self.metrics.summary(limit=limit)}

    def voice_metrics_by_document(self, limit: int = 1000) -> dict:
        return {"ok": True, "items": self.metrics.document_summary(limit=limit)}

    def voice_metrics_by_chunk(self, doc_id: str = "", limit: int = 1000) -> dict:
        return {"ok": True, "items": self.metrics.chunk_summary(doc_id=doc_id, limit=limit)}

    def reader_snapshot(self) -> dict:
        document = self.session.document
        status = self.session.status()
        if not document:
            return {
                **status,
                "current_chunk": "",
                "previous_chunk": "",
                "next_chunk": "",
                "document_text": "",
                "notes": [],
                "main_document": {},
                "document_chunks": [],
                "reference_documents": [self._snapshot_document_record(item) for item in self._reference_documents.values()],
                "laboratory_focus": self.laboratory_focus_status(),
                "laboratory_mode": self.laboratory_mode_status(),
            }
        cursor = self.session.cursor
        chunks = document.chunks
        main_record = self._main_document_record()
        return {
            **status,
            "current_chunk": self.session.current_chunk(),
            "previous_chunk": chunks[cursor - 1] if cursor > 0 else "",
            "next_chunk": chunks[cursor + 1] if cursor + 1 < len(chunks) else "",
            "document_text": document.text,
            "notes": self.list_notes(doc_id=document.doc_id, chunk_index=None).get("items", []),
            "main_document": self._snapshot_document_record(main_record) if main_record else {},
            "document_chunks": [
                {
                    "chunk_number": index + 1,
                    "text": chunk,
                }
                for index, chunk in enumerate(chunks)
            ],
            "reference_documents": [self._snapshot_document_record(item) for item in self._reference_documents.values()],
            "laboratory_focus": self.laboratory_focus_status(),
            "laboratory_mode": self.laboratory_mode_status(),
        }

    def _extract_navigation_plan(self, text: str) -> dict | None:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split())
        if not clean:
            return None
        plan: dict[str, object] = {}
        block_match = re.search(
            r"(?:^|\b)(?:and[aá]|anda|ir|ite|vamos|salt[aá]|salta|mostrame|mu[eé]strame|ll[eé]vame|llevame|abr[ií]|abre|quiero\s+ver|quiero\s+ir\s+a|ver)?"
            r".*?\b(?:bloque|chunk|parte|secci[oó]n)\s+(\d{1,4})(?:\s+(?:de|del|en)\s+(.+?))?(?=\s+y\s+(?:busca|busc[aá]|buscar|encuentra|encontr[aá]|encontrar|ubica|ubic[aá]|d[oó]nde|donde)\b|$)",
            clean,
            flags=re.IGNORECASE,
        )
        if block_match:
            plan["focus_chunk_number"] = int(block_match.group(1))
            plan["focus_selector"] = str(block_match.group(2) or "").strip(" .,:;!?")
        search_match = re.search(
            r"\b(?:busca|busc[aá]|buscar|encuentra|encontr[aá]|encontrar|ubica|ubic[aá])\b\s*(?:d[oó]nde\s+(?:habla|dice)\s+de\s+)?(.+)$",
            clean,
            flags=re.IGNORECASE,
        )
        if search_match:
            tail = str(search_match.group(1) or "").strip(" .,:;!?")
            query, selector = self._split_search_tail(tail)
            if query:
                plan["search_query"] = query
                plan["search_selector"] = selector
        where_match = re.search(
            r"\b(?:d[oó]nde\s+habla\s+de|d[oó]nde\s+dice|donde\s+habla\s+de|donde\s+dice)\s+(.+)$",
            clean,
            flags=re.IGNORECASE,
        )
        if where_match:
            tail = str(where_match.group(1) or "").strip(" .,:;!?")
            query, selector = self._split_search_tail(tail)
            if query:
                plan["search_query"] = query
                plan["search_selector"] = selector
        return plan or None

    def _split_search_tail(self, tail: str) -> tuple[str, str]:
        clean_tail = str(tail or "").strip(" .,:;!?")
        if not clean_tail:
            return "", ""
        quote_match = re.search(r"[\"“”']([^\"“”']{2,})[\"“”']", clean_tail)
        quoted = str(quote_match.group(1) or "").strip() if quote_match else ""
        selector = ""
        split_match = re.match(r"(.+?)\s+\ben\b\s+(.+)$", clean_tail, flags=re.IGNORECASE)
        if split_match:
            query = quoted or str(split_match.group(1) or "").strip(" .,:;!?")
            selector = str(split_match.group(2) or "").strip(" .,:;!?")
            return query, selector
        return quoted or clean_tail, selector

    def _extract_compare_plan(self, text: str) -> dict | None:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split())
        if not clean:
            return None
        if not re.search(r"\bcompar", clean, flags=re.IGNORECASE):
            return None
        lowered = clean.lower()
        if " con " not in lowered:
            return None
        left_raw, right_raw = re.split(r"\bcon\b", clean, maxsplit=1, flags=re.IGNORECASE)
        left_raw = re.sub(r"^.*?\bcompar[aá]\s+", "", left_raw, flags=re.IGNORECASE).strip(" .,:;!?")
        right_raw = str(right_raw or "").strip(" .,:;!?")
        if not left_raw or not right_raw:
            return None
        left = self._parse_compare_target(left_raw, prefer_focus=True)
        right = self._parse_compare_target(right_raw, prefer_focus=False)
        if not left or not right:
            return None
        return {"left": left, "right": right}

    def _parse_compare_target(self, text: str, prefer_focus: bool = False) -> dict | None:
        clean = str(text or "").strip(" .,:;!?")
        if not clean:
            return None
        if re.search(r"\b(?:este|ese|actual)\s+bloque\b", clean, flags=re.IGNORECASE):
            focus = self.laboratory_focus_status() if prefer_focus else {}
            if focus:
                return {
                    "source": "focus",
                    "doc_id": str(focus.get("doc_id") or ""),
                    "title": str(focus.get("title") or ""),
                    "chunk_number": int(focus.get("chunk_number") or 0),
                }
            status = self.session.status()
            if status.get("doc_id"):
                return {
                    "source": "main",
                    "doc_id": str(status.get("doc_id") or ""),
                    "title": str(status.get("title") or ""),
                    "chunk_number": int(status.get("current") or 0),
                }
        match = re.search(
            r"\b(?:bloque|chunk|parte|secci[oó]n)\s+(\d{1,4})(?:\s+(?:de|del|en)\s+(.+))?$",
            clean,
            flags=re.IGNORECASE,
        )
        if match:
            return {
                "source": "explicit",
                "doc_selector": str(match.group(2) or "").strip(" .,:;!?"),
                "chunk_number": int(match.group(1)),
            }
        # fall back to current block of selected document
        record = self._resolve_document_record(clean)
        if record:
            default_chunk = 1
            if record.get("role") == "main":
                default_chunk = int(self.session.status().get("current") or 1)
            return {
                "source": "document_only",
                "doc_selector": clean,
                "chunk_number": default_chunk,
            }
        return None

    def _resolve_compare_target(self, target: dict) -> dict | None:
        chunk_number = int(target.get("chunk_number") or 0)
        if chunk_number <= 0:
            return None
        record = None
        if target.get("source") == "focus":
            record = self._focus_record()
        if record is None and target.get("doc_id"):
            record = self._resolve_document_record(str(target.get("doc_id") or ""))
        if record is None:
            record = self._resolve_document_record(str(target.get("doc_selector") or ""))
        if record is None and self.session.document:
            record = self._resolve_document_record(str(self.session.document.doc_id))
        if not record:
            return None
        chunks = record.get("chunks")
        if not isinstance(chunks, list) or chunk_number > len(chunks):
            return None
        item = chunks[chunk_number - 1]
        return {
            "doc_id": str(record.get("doc_id") or ""),
            "title": str(record.get("title") or ""),
            "role": str(record.get("role") or "reference"),
            "source_type": str(record.get("source_type") or "text"),
            "total": int(record.get("total") or len(chunks)),
            "chunk_number": chunk_number,
            "chunk_index": chunk_number - 1,
            "text": str(item.get("text") or "").strip(),
        }

    def _compare_terms(self, text: str) -> list[str]:
        return self._meaningful_search_terms(text)

    def _compare_summary(self, left: dict, right: dict, dialogue: bool = False) -> str:
        left_terms = set(self._compare_terms(left.get("text") or ""))
        right_terms = set(self._compare_terms(right.get("text") or ""))
        overlap = [term for term in left_terms.intersection(right_terms)]
        overlap = sorted(overlap, key=len, reverse=True)[:6]
        left_only = sorted(left_terms - right_terms, key=len, reverse=True)[:4]
        right_only = sorted(right_terms - left_terms, key=len, reverse=True)[:4]
        left_excerpt = self._navigation_excerpt(str(left.get("text") or ""), max_chars=190 if dialogue else 240)
        right_excerpt = self._navigation_excerpt(str(right.get("text") or ""), max_chars=190 if dialogue else 240)
        if dialogue:
            parts = [
                f"Comparé {left['title']} bloque {left['chunk_number']} con {right['title']} bloque {right['chunk_number']}.",
                f"Coinciden en {', '.join(overlap[:3])}." if overlap else "No comparten vocabulario fuerte en esta muestra.",
                f"El primero dice: {left_excerpt}",
                f"El segundo dice: {right_excerpt}",
            ]
            return " ".join(parts)
        lines = [
            f"Comparación:",
            f"- {left['title']} | bloque {left['chunk_number']} de {left['total']}: {left_excerpt}",
            f"- {right['title']} | bloque {right['chunk_number']} de {right['total']}: {right_excerpt}",
            f"Coincidencias: {', '.join(overlap) if overlap else 'no encontré coincidencias léxicas fuertes en esta muestra.'}",
            f"Rasgos del primero: {', '.join(left_only) if left_only else 'sin rasgos diferenciales claros.'}",
            f"Rasgos del segundo: {', '.join(right_only) if right_only else 'sin rasgos diferenciales claros.'}",
        ]
        return "\n".join(lines)

    def _handle_compare_intent(self, text: str, dialogue: bool = False) -> dict | None:
        plan = self._extract_compare_plan(text)
        if not plan:
            return None
        left = self._resolve_compare_target(plan["left"])
        right = self._resolve_compare_target(plan["right"])
        if not left or not right:
            return {
                "ok": False,
                "answer": "",
                "model": "reader_compare",
                "detail": "compare_target_not_found",
                "error": "compare_target_not_found",
            }
        summary = self._compare_summary(left, right, dialogue=dialogue)
        focus_record = self._resolve_document_record(str(right.get("doc_id") or right.get("title") or ""))
        if not focus_record:
            return {
                "ok": False,
                "answer": "",
                "model": "reader_compare",
                "detail": "document_not_found",
                "error": "document_not_found",
            }
        focus = self._set_laboratory_focus(focus_record, int(right["chunk_index"]), reason="compare")
        return {
            "ok": True,
            "answer": summary,
            "model": "reader_compare",
            "detail": "compare_blocks",
            "doc_id": focus.get("doc_id") or "",
            "title": focus.get("title") or "",
            "current": focus.get("chunk_number") or 0,
            "total": focus.get("total") or 0,
            "comparison": {"left": left, "right": right},
            "laboratory_focus": focus,
        }

    def _search_chunk_matches(self, query: str, selector: str = "", limit: int = 5) -> list[dict]:
        selected_record = self._resolve_document_record(selector) if selector else None
        records = [selected_record] if selected_record else self._all_document_records()
        normalized_query = self._normalize_search_text(query)
        terms = self._meaningful_search_terms(query)
        matches: list[tuple[int, int, dict]] = []
        for record in records:
            if not record:
                continue
            chunks = record.get("chunks")
            if not isinstance(chunks, list):
                continue
            for index, item in enumerate(chunks):
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                haystack = self._normalize_search_text(text)
                score = 0
                if normalized_query and normalized_query in haystack:
                    score += 30 + len(terms)
                score += sum(4 for term in terms if term and term in haystack)
                if score <= 0:
                    continue
                matches.append(
                    (
                        score,
                        -index,
                        {
                            "doc_id": str(record.get("doc_id") or ""),
                            "title": str(record.get("title") or ""),
                            "role": str(record.get("role") or "reference"),
                            "source_type": str(record.get("source_type") or "text"),
                            "total": int(record.get("total") or len(chunks)),
                            "chunk_index": index,
                            "chunk_number": int(item.get("chunk_number") or index + 1),
                            "text": text,
                        },
                    )
                )
        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in matches[: max(1, int(limit or 1))]]

    def _navigation_excerpt(self, text: str, max_chars: int = 280) -> str:
        excerpt = " ".join(str(text or "").split())
        if len(excerpt) <= max_chars:
            return excerpt
        return excerpt[:max_chars].rstrip() + "..."

    def _is_reflective_navigation_request(self, text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if not clean:
            return False
        if re.search(
            r"\b(?:exactamente|literal|textual(?:mente)?|cita|citame|cit[aá]melo|recita|repite|leer|le[eé]me|leelo|le[eé]lo|que\s+dice(?:\s+exactamente)?)\b",
            clean,
            flags=re.IGNORECASE,
        ):
            return False
        return bool(
            re.search(
                r"\b(?:pens(?:a|á|ar|emos)|filos[oó]fic|interpret|analiz|lectura|reflexion|reflexi[oó]n|opina|opin[aá]s|opinas|opin[ií]on|"
                r"qu[eé]\s+ves|qu[eé]\s+interpret|qu[eé]\s+te\s+parece|qu[eé]\s+plantea|qu[eé]\s+implica|qu[eé]\s+significa|"
                r"tension|problematiz|critic|latente|vuelo\s+propio|desarroll|pensamiento\s+cr[ií]tico)\b",
                clean,
                flags=re.IGNORECASE,
            )
        )

    def _handle_navigation_intent(self, text: str, dialogue: bool = False) -> dict | None:
        plan = self._extract_navigation_plan(text)
        if not plan:
            return None
        focused_record: dict | None = None
        if plan.get("focus_chunk_number"):
            selected = self._resolve_document_record(str(plan.get("focus_selector") or ""))
            if not selected:
                return {
                    "ok": False,
                    "answer": "",
                    "model": "reader_navigation",
                    "detail": "document_not_found",
                    "error": "document_not_found",
                }
            chunk_number = int(plan.get("focus_chunk_number") or 0)
            chunks = selected.get("chunks") if isinstance(selected.get("chunks"), list) else []
            if chunk_number < 1 or chunk_number > len(chunks):
                return {
                    "ok": False,
                    "answer": "",
                    "model": "reader_navigation",
                    "detail": "chunk_out_of_bounds",
                    "error": "chunk_out_of_bounds",
                    "doc_id": selected.get("doc_id") or "",
                    "title": selected.get("title") or "",
                    "total": len(chunks),
                }
            if selected.get("role") == "main":
                self.jump(chunk_number)
                selected = self._resolve_document_record(selected.get("doc_id") or "") or selected
            focused_record = selected
            if not plan.get("search_query"):
                focus = self._set_laboratory_focus(selected, chunk_number - 1, reason="focus_block")
                if self._is_reflective_navigation_request(text):
                    return {
                        "ok": True,
                        "answer": "",
                        "model": "reader_navigation",
                        "detail": "focus_block_context",
                        "doc_id": focus.get("doc_id") or "",
                        "title": focus.get("title") or "",
                        "current": focus.get("chunk_number") or 0,
                        "total": focus.get("total") or 0,
                        "laboratory_focus": focus,
                        "continue_with_llm": True,
                    }
                excerpt = self._navigation_excerpt(focus["text"], max_chars=340 if dialogue else 460)
                answer = (
                    f"Quedé en {focus['title']}, bloque {focus['chunk_number']} de {focus['total']}. {excerpt}"
                    if dialogue
                    else f"Foco de laboratorio en {focus['title']}, bloque {focus['chunk_number']} de {focus['total']}.\n\n{excerpt}"
                )
                return {
                    "ok": True,
                    "answer": answer,
                    "model": "reader_navigation",
                    "detail": "focus_block",
                    "doc_id": focus.get("doc_id") or "",
                    "title": focus.get("title") or "",
                    "current": focus.get("chunk_number") or 0,
                    "total": focus.get("total") or 0,
                    "laboratory_focus": focus,
                }
        if plan.get("search_query"):
            selector = str(plan.get("search_selector") or "")
            if not selector and focused_record:
                selector = str(focused_record.get("doc_id") or focused_record.get("title") or "")
            matches = self._search_chunk_matches(str(plan.get("search_query") or ""), selector=selector, limit=5)
            if not matches:
                return {
                    "ok": False,
                    "answer": "",
                    "model": "reader_navigation",
                    "detail": "search_no_matches",
                    "error": "search_no_matches",
                }
            focus_record = self._resolve_document_record(matches[0].get("doc_id") or "") or self._resolve_document_record(matches[0].get("title") or "")
            if not focus_record:
                return {
                    "ok": False,
                    "answer": "",
                    "model": "reader_navigation",
                    "detail": "document_not_found",
                    "error": "document_not_found",
                }
            focus = self._set_laboratory_focus(
                focus_record,
                int(matches[0]["chunk_index"]),
                query=str(plan.get("search_query") or ""),
                reason="search",
            )
            if dialogue:
                answer = (
                    f"Encontré {plan.get('search_query')} en {focus['title']}, bloque {focus['chunk_number']}. "
                    f"{self._navigation_excerpt(focus['text'], max_chars=300)}"
                )
            else:
                lines = [f"Encontré coincidencias para '{plan.get('search_query')}'."]
                for item in matches[:3]:
                    lines.append(
                        f"- {item['title']} | bloque {item['chunk_number']} de {item['total']}: {self._navigation_excerpt(item['text'], max_chars=180)}"
                    )
                answer = "\n".join(lines)
            return {
                "ok": True,
                "answer": answer,
                "model": "reader_navigation",
                "detail": "search_matches",
                "doc_id": focus.get("doc_id") or "",
                "title": focus.get("title") or "",
                "current": focus.get("chunk_number") or 0,
                "total": focus.get("total") or 0,
                "laboratory_focus": focus,
                "matches": matches,
            }
        return None

    def chat(self, message: str, model: str = "", chunk_index: int | None = None) -> dict:
        started = time.perf_counter()
        note_text = self._extract_note_command(message)
        if note_text:
            if self._should_create_laboratory_note(message) or self._should_route_generic_note_to_laboratory(message, note_text):
                created = self.create_laboratory_note(note_text)
            else:
                selected_chunk = self._resolve_note_chunk_index(chunk_index)
                created = self.create_note(note_text, chunk_index=selected_chunk)
            if not created.get("ok"):
                return {
                    "ok": False,
                    "answer": "",
                    "model": "reader_notes",
                    "detail": created.get("error") or "note_failed",
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "doc_id": self.session.status().get("doc_id") or "",
                    "title": self.session.status().get("title") or "",
                    "current": self.session.status().get("current") or 0,
                    "total": self.session.status().get("total") or 0,
                }
            note = created["note"]
            return {
                "ok": True,
                "answer": self._note_saved_answer(note),
                "model": "reader_notes",
                "detail": "",
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "doc_id": note.get("doc_id") or "",
                "title": note.get("title") or "",
                "current": note.get("chunk_number") or 0,
                "total": self.session.status().get("total") or 0,
                "note": note,
            }
        if self._looks_like_note_request(message):
            snapshot = self.session.status()
            return {
                "ok": True,
                "answer": "Sí, puedo guardar notas. Decime: tomá nota de ...",
                "model": "reader_notes",
                "detail": "missing_note_text",
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "doc_id": snapshot.get("doc_id") or "",
                "title": snapshot.get("title") or "",
                "current": snapshot.get("current") or 0,
                "total": snapshot.get("total") or 0,
            }
        comparison = self._handle_compare_intent(message, dialogue=False)
        if comparison is not None:
            comparison["duration_ms"] = int((time.perf_counter() - started) * 1000)
            if comparison.get("ok"):
                self._remember_chat_turn(message, comparison.get("answer") or "")
            return comparison
        navigation = self._handle_navigation_intent(message, dialogue=False)
        if navigation is not None:
            navigation["duration_ms"] = int((time.perf_counter() - started) * 1000)
            if navigation.get("ok"):
                self._remember_chat_turn(message, navigation.get("answer") or "")
            return navigation
        snapshot = self.reader_snapshot()
        with self._chat_lock:
            history = list(self._chat_history)
        result = self.conversation.ask(message, snapshot=snapshot, model=model, history=history, reasoning_mode=self.reasoning_mode)
        if result.ok:
            self._remember_chat_turn(message, result.answer)
        return {
            "ok": result.ok,
            "answer": result.answer,
            "model": result.model,
            "detail": result.detail,
            "duration_ms": result.duration_ms or int((time.perf_counter() - started) * 1000),
            "reasoning_mode": result.reasoning_mode or self.reasoning_mode,
            "reasoning_passes": result.reasoning_passes or 1,
            "doc_id": snapshot.get("doc_id") or "",
            "title": snapshot.get("title") or "",
            "current": snapshot.get("current") or 0,
            "total": snapshot.get("total") or 0,
        }

    def _remember_chat_turn(self, user_message: str, assistant_answer: str) -> None:
        user_message = str(user_message or "").strip()
        assistant_answer = str(assistant_answer or "").strip()
        if not user_message and not assistant_answer:
            return
        with self._chat_lock:
            if user_message:
                self._chat_history.append({"role": "user", "content": user_message})
            if assistant_answer:
                self._chat_history.append({"role": "assistant", "content": assistant_answer})
            self._chat_history = self._chat_history[-20:]

    def clear_laboratory_history(self) -> dict:
        with self._chat_lock:
            chat_turns = len(self._chat_history)
            self._chat_history = []
        with self._dialogue_lock:
            dialogue_turns = len(self._dialogue_history)
            self._dialogue_history = []
        return {
            "ok": True,
            "cleared": True,
            "chat_items": chat_turns,
            "dialogue_items": dialogue_turns,
        }

    def dialogue_status(self) -> dict:
        dialogue_reasoning = self._effective_reasoning_mode(dialogue=True)
        return {
            "ok": True,
            "stt": self.stt.health(),
            "tts": self.tts.health(),
            "turns": len(self._dialogue_history),
            "reasoning": self.reasoning_status(),
            "laboratory_mode": self.laboratory_mode_status(),
            "dialogue_reasoning": {
                **self.conversation.reasoning_status(dialogue_reasoning["applied"]),
                "requested_mode": dialogue_reasoning["requested"],
                "applied_mode": dialogue_reasoning["applied"],
                "degraded": dialogue_reasoning["degraded"],
                "degraded_reason": dialogue_reasoning["reason"],
            },
        }

    def reasoning_status(self) -> dict:
        info = self.conversation.reasoning_status(self.reasoning_mode)
        info["selected"] = info.get("mode") == self.reasoning_mode
        return info

    def laboratory_mode_status(self) -> dict:
        mode = "free" if str(self.laboratory_mode or "").strip().lower() == "free" else "document"
        return {
            "mode": mode,
            "label": "Modo libre" if mode == "free" else "Anclado al texto",
            "description": (
                "Lucy puede conversar libremente aunque el tema no dependa del texto; el documento queda como contexto opcional."
                if mode == "free"
                else "Lucy prioriza lo que ves, el texto activo y los documentos cargados."
            ),
            "selected": True,
        }

    def set_reasoning_mode(self, mode: str) -> dict:
        profile = self.conversation.reasoning_status(mode)
        self.reasoning_mode = str(profile.get("mode") or self.reasoning_mode or "thinking")
        self._persist_session_state()
        self._append_dialogue_trace(
            {
                "ts": time.time(),
                "event": "reasoning_mode_changed",
                "requested_mode": str(mode or ""),
                "selected_mode": self.reasoning_mode,
                "dialogue_allow_supreme": self.dialogue_allow_supreme,
            }
        )
        out = self.reasoning_status()
        dialogue_reasoning = self._effective_reasoning_mode(dialogue=True)
        out["dialogue_reasoning"] = {
            **self.conversation.reasoning_status(dialogue_reasoning["applied"]),
            "requested_mode": dialogue_reasoning["requested"],
            "applied_mode": dialogue_reasoning["applied"],
            "degraded": dialogue_reasoning["degraded"],
            "degraded_reason": dialogue_reasoning["reason"],
        }
        return out

    def set_laboratory_mode(self, mode: str) -> dict:
        self.laboratory_mode = "free" if str(mode or "").strip().lower() == "free" else "document"
        self._persist_session_state()
        self._append_dialogue_trace(
            {
                "ts": time.time(),
                "event": "laboratory_mode_changed",
                "selected_mode": self.laboratory_mode,
            }
        )
        return self.laboratory_mode_status()

    def dialogue_reset(self) -> dict:
        with self._dialogue_lock:
            self._dialogue_history = []
        return self.dialogue_status()

    def dialogue_turn_text(self, text: str, model: str = "", chunk_index: int | None = None) -> dict:
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "empty_dialogue_text"}
        self._prioritize_dialogue()
        started = time.perf_counter()
        reasoning = self._effective_reasoning_mode(dialogue=True)
        trace_event = {
            "ts": time.time(),
            "event": "dialogue_turn_text",
            "requested_mode": reasoning["requested"],
            "applied_mode": reasoning["applied"],
            "reasoning_degraded": reasoning["degraded"],
            "degraded_reason": reasoning["reason"],
            "chunk_index": chunk_index,
            "text_preview": text[:220],
        }
        if self._is_stop_dialogue_command(text):
            out = {
                "ok": True,
                "transcript": text,
                "answer": "",
                "audio": "",
                "cached": False,
                "provider": "text_ack",
                "detail": "dialogue_stopped",
                "model": "reader_control",
                "stt_ms": 0,
                "chat_ms": 0,
                "tts_ms": 0,
                "trace": {"intent_ms": int((time.perf_counter() - started) * 1000), "server_text_total_ms": int((time.perf_counter() - started) * 1000)},
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "voice_ok": True,
                "reasoning_mode_requested": reasoning["requested"],
                "reasoning_mode_applied": reasoning["applied"],
                "reasoning_degraded": reasoning["degraded"],
            }
            self._append_dialogue_trace({**trace_event, "ok": True, "detail": "dialogue_stopped", "duration_ms": out["duration_ms"]})
            return out
        note_text = self._extract_note_command(text)
        intent_ms = int((time.perf_counter() - started) * 1000)
        if note_text:
            note_started = time.perf_counter()
            if self._should_create_laboratory_note(text) or self._should_route_generic_note_to_laboratory(text, note_text):
                created = self.create_laboratory_note(note_text)
            else:
                selected_chunk = self._resolve_note_chunk_index(chunk_index)
                created = self.create_note(note_text, chunk_index=selected_chunk)
            note_ms = int((time.perf_counter() - note_started) * 1000)
            if not created.get("ok"):
                out = {
                    "ok": False,
                    "transcript": text,
                    "answer": "",
                    "model": "reader_notes",
                    "detail": created.get("error") or "note_failed",
                    "chat_ms": 0,
                    "trace": {"intent_ms": intent_ms, "note_ms": note_ms, "server_text_total_ms": int((time.perf_counter() - started) * 1000)},
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "reasoning_mode_requested": reasoning["requested"],
                    "reasoning_mode_applied": reasoning["applied"],
                    "reasoning_degraded": reasoning["degraded"],
                }
                self._append_dialogue_trace({**trace_event, "ok": False, "detail": out["detail"], "duration_ms": out["duration_ms"]})
                return out
            note = created["note"]
            spoken_answer = self._note_saved_answer(note, spoken=True)
            if self.fast_note_ack:
                artifact = AudioArtifact(True, provider="text_ack", detail="fast_note_ack")
                tts_ms = 0
            else:
                tts_started = time.perf_counter()
                artifact = self._synthesize_cached(spoken_answer)
                tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
            with self._dialogue_lock:
                self._dialogue_history.append({"role": "user", "content": text})
                self._dialogue_history.append({"role": "assistant", "content": spoken_answer})
                self._dialogue_history = self._dialogue_history[-16:]
            out = {
                "ok": True,
                "transcript": text,
                "answer": spoken_answer,
                "audio": str(artifact.path or ""),
                "cached": artifact.cached,
                "provider": artifact.provider,
                "detail": artifact.detail,
                "model": "reader_notes",
                "stt_ms": 0,
                "chat_ms": 0,
                "tts_ms": tts_ms,
                "trace": {
                    "intent_ms": intent_ms,
                    "note_ms": note_ms,
                    "tts_ms": tts_ms,
                    "server_text_total_ms": int((time.perf_counter() - started) * 1000),
                },
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "note": note,
                "voice_ok": artifact.ok,
                "reasoning_mode_requested": reasoning["requested"],
                "reasoning_mode_applied": reasoning["applied"],
                "reasoning_degraded": reasoning["degraded"],
            }
            self._append_dialogue_trace(
                {
                    **trace_event,
                    "ok": bool(out["ok"]),
                    "detail": str(out.get("detail") or ""),
                    "note": True,
                    "tts_ok": bool(artifact.ok),
                    "duration_ms": out["duration_ms"],
                }
            )
            return out
        if self._looks_like_note_request(text):
            spoken_answer = "Sí, puedo guardar notas. Decime: tomá nota de, y lo que querés guardar."
            if self.fast_dialogue_ack:
                artifact = AudioArtifact(True, provider="text_ack", detail="fast_dialogue_ack")
                tts_ms = 0
            else:
                tts_started = time.perf_counter()
                artifact = self._synthesize_cached(spoken_answer)
                tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
            out = {
                "ok": True,
                "transcript": text,
                "answer": spoken_answer,
                "audio": str(artifact.path or ""),
                "cached": artifact.cached,
                "provider": artifact.provider,
                "detail": "missing_note_text",
                "model": "reader_notes",
                "stt_ms": 0,
                "chat_ms": 0,
                "tts_ms": tts_ms,
                "trace": {
                    "intent_ms": intent_ms,
                    "tts_ms": tts_ms,
                    "server_text_total_ms": int((time.perf_counter() - started) * 1000),
                },
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "voice_ok": artifact.ok,
                "reasoning_mode_requested": reasoning["requested"],
                "reasoning_mode_applied": reasoning["applied"],
                "reasoning_degraded": reasoning["degraded"],
            }
            self._append_dialogue_trace({**trace_event, "ok": True, "detail": "missing_note_text", "tts_ok": bool(artifact.ok), "duration_ms": out["duration_ms"]})
            return out
        comparison = self._handle_compare_intent(text, dialogue=True)
        if comparison is not None:
            spoken_answer = str(comparison.get("answer") or "").strip()
            if not comparison.get("ok"):
                out = {
                    "ok": False,
                    "transcript": text,
                    "answer": "",
                    "model": comparison.get("model") or "reader_compare",
                    "detail": comparison.get("detail") or comparison.get("error") or "compare_failed",
                    "chat_ms": 0,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "reasoning_mode_requested": reasoning["requested"],
                    "reasoning_mode_applied": reasoning["applied"],
                    "reasoning_degraded": reasoning["degraded"],
                }
                self._append_dialogue_trace({**trace_event, "ok": False, "detail": out["detail"], "duration_ms": out["duration_ms"]})
                return out
            if self.fast_dialogue_ack:
                artifact = AudioArtifact(True, provider="text_ack", detail="fast_dialogue_ack")
                tts_ms = 0
            else:
                tts_started = time.perf_counter()
                artifact = self._synthesize_cached(spoken_answer)
                tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
            with self._dialogue_lock:
                self._dialogue_history.append({"role": "user", "content": text})
                self._dialogue_history.append({"role": "assistant", "content": spoken_answer})
                self._dialogue_history = self._dialogue_history[-16:]
            out = {
                "ok": True,
                "transcript": text,
                "answer": spoken_answer,
                "audio": str(artifact.path or ""),
                "cached": artifact.cached,
                "provider": artifact.provider,
                "detail": comparison.get("detail") or artifact.detail,
                "model": comparison.get("model") or "reader_compare",
                "stt_ms": 0,
                "chat_ms": 0,
                "tts_ms": tts_ms,
                "trace": {
                    "intent_ms": intent_ms,
                    "tts_ms": tts_ms,
                    "server_text_total_ms": int((time.perf_counter() - started) * 1000),
                },
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "voice_ok": artifact.ok,
                "laboratory_focus": comparison.get("laboratory_focus") or {},
                "comparison": comparison.get("comparison") or {},
                "reasoning_mode_requested": reasoning["requested"],
                "reasoning_mode_applied": reasoning["applied"],
                "reasoning_degraded": reasoning["degraded"],
            }
            self._append_dialogue_trace({**trace_event, "ok": True, "detail": str(out.get("detail") or ""), "tts_ok": bool(artifact.ok), "duration_ms": out["duration_ms"]})
            return out
        navigation = self._handle_navigation_intent(text, dialogue=True)
        if navigation is not None:
            if navigation.get("continue_with_llm"):
                trace_event["navigation_detail"] = str(navigation.get("detail") or "")
            else:
                spoken_answer = str(navigation.get("answer") or "").strip()
                if not navigation.get("ok"):
                    out = {
                        "ok": False,
                        "transcript": text,
                        "answer": "",
                        "model": navigation.get("model") or "reader_navigation",
                        "detail": navigation.get("detail") or navigation.get("error") or "navigation_failed",
                        "chat_ms": 0,
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "reasoning_mode_requested": reasoning["requested"],
                        "reasoning_mode_applied": reasoning["applied"],
                        "reasoning_degraded": reasoning["degraded"],
                    }
                    self._append_dialogue_trace({**trace_event, "ok": False, "detail": out["detail"], "duration_ms": out["duration_ms"]})
                    return out
                if self.fast_dialogue_ack:
                    artifact = AudioArtifact(True, provider="text_ack", detail="fast_dialogue_ack")
                    tts_ms = 0
                else:
                    tts_started = time.perf_counter()
                    artifact = self._synthesize_cached(spoken_answer)
                    tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
                with self._dialogue_lock:
                    self._dialogue_history.append({"role": "user", "content": text})
                    self._dialogue_history.append({"role": "assistant", "content": spoken_answer})
                    self._dialogue_history = self._dialogue_history[-16:]
                out = {
                    "ok": True,
                    "transcript": text,
                    "answer": spoken_answer,
                    "audio": str(artifact.path or ""),
                    "cached": artifact.cached,
                    "provider": artifact.provider,
                    "detail": navigation.get("detail") or artifact.detail,
                    "model": navigation.get("model") or "reader_navigation",
                    "stt_ms": 0,
                    "chat_ms": 0,
                    "tts_ms": tts_ms,
                    "trace": {
                        "intent_ms": intent_ms,
                        "tts_ms": tts_ms,
                        "server_text_total_ms": int((time.perf_counter() - started) * 1000),
                    },
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "voice_ok": artifact.ok,
                    "laboratory_focus": navigation.get("laboratory_focus") or {},
                    "matches": navigation.get("matches") or [],
                    "reasoning_mode_requested": reasoning["requested"],
                    "reasoning_mode_applied": reasoning["applied"],
                    "reasoning_degraded": reasoning["degraded"],
                }
                self._append_dialogue_trace({**trace_event, "ok": True, "detail": str(out.get("detail") or ""), "tts_ok": bool(artifact.ok), "duration_ms": out["duration_ms"]})
                return out
        snapshot = self.reader_snapshot()
        with self._chat_lock:
            snapshot["laboratory_history"] = list(self._chat_history)
        with self._dialogue_lock:
            history = list(self._dialogue_history)
        chat_started = time.perf_counter()
        result = self.conversation.ask_dialogue(text, snapshot=snapshot, history=history, model=model, reasoning_mode=reasoning["applied"])
        chat_ms = result.duration_ms or int((time.perf_counter() - chat_started) * 1000)
        if not result.ok:
            out = {
                "ok": False,
                "transcript": text,
                "answer": "",
                "model": result.model,
                "detail": result.detail,
                "chat_ms": chat_ms,
                "reasoning_mode": result.reasoning_mode or reasoning["applied"],
                "reasoning_passes": result.reasoning_passes or 1,
                "reasoning_mode_requested": reasoning["requested"],
                "reasoning_mode_applied": reasoning["applied"],
                "reasoning_degraded": reasoning["degraded"],
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
            self._append_dialogue_trace({**trace_event, "ok": False, "detail": out["detail"], "chat_ms": chat_ms, "duration_ms": out["duration_ms"]})
            return out
        spoken_answer = self._shorten_dialogue_answer(result.answer)
        if self.fast_dialogue_ack:
            artifact = AudioArtifact(True, provider="text_ack", detail="fast_dialogue_ack")
            tts_ms = 0
        else:
            tts_started = time.perf_counter()
            artifact = self._synthesize_cached(spoken_answer)
            tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
        if artifact.ok:
            with self._dialogue_lock:
                self._dialogue_history.append({"role": "user", "content": text})
                self._dialogue_history.append({"role": "assistant", "content": spoken_answer})
                self._dialogue_history = self._dialogue_history[-16:]
        out = {
            "ok": bool(result.ok and artifact.ok),
            "transcript": text,
            "answer": spoken_answer,
            "audio": str(artifact.path or ""),
            "cached": artifact.cached,
            "provider": artifact.provider,
            "detail": artifact.detail or result.detail,
            "model": result.model,
            "stt_ms": 0,
            "chat_ms": chat_ms,
            "tts_ms": tts_ms,
            "reasoning_mode": result.reasoning_mode or reasoning["applied"],
            "reasoning_passes": result.reasoning_passes or 1,
            "reasoning_mode_requested": reasoning["requested"],
            "reasoning_mode_applied": reasoning["applied"],
            "reasoning_degraded": reasoning["degraded"],
            "trace": {
                "intent_ms": intent_ms,
                "chat_ms": chat_ms,
                "tts_ms": tts_ms,
                "server_text_total_ms": int((time.perf_counter() - started) * 1000),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        self._append_dialogue_trace(
            {
                **trace_event,
                "ok": bool(out["ok"]),
                "detail": str(out.get("detail") or ""),
                "chat_ms": chat_ms,
                "tts_ms": tts_ms,
                "reasoning_passes": out["reasoning_passes"],
                "duration_ms": out["duration_ms"],
                "tts_ok": bool(artifact.ok),
            }
        )
        return out

    def dialogue_turn_audio(self, path: str | Path, mime: str = "", model: str = "", chunk_index: int | None = None) -> dict:
        self._prioritize_dialogue()
        started = time.perf_counter()
        transcript = self.stt.transcribe_file(path, mime=mime, language=self.voice.language)
        stt_elapsed_ms = int((time.perf_counter() - started) * 1000)
        if not transcript.ok:
            if transcript.detail == "hallucinated_transcript":
                out = {
                    "ok": True,
                    "ignored": True,
                    "transcript": transcript.text,
                    "answer": "",
                    "audio": "",
                    "cached": False,
                    "provider": "text_ack",
                    "detail": transcript.detail,
                    "model": "reader_stt",
                    "stt_provider": transcript.provider,
                    "stt_ms": transcript.duration_ms,
                    "chat_ms": 0,
                    "tts_ms": 0,
                    "trace": {
                        "stt_ms": transcript.duration_ms,
                        "stt_wall_ms": stt_elapsed_ms,
                        "stt_detail": transcript.detail,
                        "stt_timings": transcript.timings or {},
                        "tts_ms": 0,
                        "server_total_ms": int((time.perf_counter() - started) * 1000),
                    },
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "voice_ok": True,
                }
                self._append_dialogue_trace(
                    {
                        "ts": time.time(),
                        "event": "dialogue_turn_audio",
                        "ok": True,
                        "ignored": True,
                        "detail": transcript.detail,
                        "stt_provider": transcript.provider,
                        "stt_ms": transcript.duration_ms,
                        "duration_ms": out["duration_ms"],
                    }
                )
                return out
            if transcript.detail in {"empty_transcript", "empty_audio"}:
                spoken_answer = "No alcancé a escuchar una frase completa. Repetímela un poco más cerca o un poco más lento."
                if self.fast_dialogue_ack:
                    artifact = AudioArtifact(True, provider="text_ack", detail="fast_dialogue_ack")
                    tts_ms = 0
                else:
                    tts_started = time.perf_counter()
                    artifact = self._synthesize_cached(spoken_answer)
                    tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
                out = {
                    "ok": True,
                    "transcript": transcript.text,
                    "answer": spoken_answer,
                    "audio": str(artifact.path or ""),
                    "cached": artifact.cached,
                    "provider": artifact.provider,
                    "detail": transcript.detail,
                    "model": "reader_stt",
                    "stt_provider": transcript.provider,
                    "stt_ms": transcript.duration_ms,
                    "chat_ms": 0,
                    "tts_ms": tts_ms,
                    "trace": {
                        "stt_ms": transcript.duration_ms,
                        "stt_wall_ms": stt_elapsed_ms,
                        "stt_detail": transcript.detail,
                        "stt_timings": transcript.timings or {},
                        "tts_ms": tts_ms,
                        "server_total_ms": int((time.perf_counter() - started) * 1000),
                    },
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "voice_ok": artifact.ok,
                }
                self._append_dialogue_trace(
                    {
                        "ts": time.time(),
                        "event": "dialogue_turn_audio",
                        "ok": True,
                        "detail": transcript.detail,
                        "stt_provider": transcript.provider,
                        "stt_ms": transcript.duration_ms,
                        "tts_ms": tts_ms,
                        "duration_ms": out["duration_ms"],
                        "tts_ok": bool(artifact.ok),
                    }
                )
                return out
            out = {
                "ok": False,
                "error": "transcription_failed",
                "transcript": transcript.text,
                "detail": transcript.detail,
                "stt_provider": transcript.provider,
                "stt_ms": transcript.duration_ms,
                "trace": {
                    "stt_ms": transcript.duration_ms,
                    "stt_wall_ms": stt_elapsed_ms,
                    "stt_detail": transcript.detail,
                    "stt_timings": transcript.timings or {},
                    "server_total_ms": int((time.perf_counter() - started) * 1000),
                },
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
            self._append_dialogue_trace(
                {
                    "ts": time.time(),
                    "event": "dialogue_turn_audio",
                    "ok": False,
                    "detail": transcript.detail,
                    "error": "transcription_failed",
                    "stt_provider": transcript.provider,
                    "stt_ms": transcript.duration_ms,
                    "duration_ms": out["duration_ms"],
                }
            )
            return out
        after_stt = time.perf_counter()
        out = self.dialogue_turn_text(transcript.text, model=model, chunk_index=chunk_index)
        text_turn_ms = int((time.perf_counter() - after_stt) * 1000)
        out["stt_provider"] = transcript.provider
        out["stt_ms"] = transcript.duration_ms
        out["trace"] = {
            **(out.get("trace") if isinstance(out.get("trace"), dict) else {}),
            "stt_ms": transcript.duration_ms,
            "stt_wall_ms": stt_elapsed_ms,
            "stt_timings": transcript.timings or {},
            "text_turn_ms": text_turn_ms,
            "server_total_ms": int((time.perf_counter() - started) * 1000),
        }
        out["duration_ms"] = int((time.perf_counter() - started) * 1000)
        self._append_dialogue_trace(
            {
                "ts": time.time(),
                "event": "dialogue_turn_audio",
                "ok": bool(out.get("ok")),
                "detail": str(out.get("detail") or ""),
                "stt_provider": transcript.provider,
                "stt_ms": transcript.duration_ms,
                "reasoning_mode_requested": str(out.get("reasoning_mode_requested") or ""),
                "reasoning_mode_applied": str(out.get("reasoning_mode_applied") or out.get("reasoning_mode") or ""),
                "reasoning_degraded": bool(out.get("reasoning_degraded")),
                "duration_ms": out["duration_ms"],
            }
        )
        return out

    def _prioritize_dialogue(self) -> None:
        self._prepare_cancel.set()
        with self._prepare_lock:
            if self._prepare_status.get("status") == "running":
                self._prepare_status["status"] = "canceling"
                self._prepare_status["message"] = "Cancelando preparación para priorizar diálogo..."
                self._prepare_status["updated_ts"] = time.time()
        self._clear_prefetch_queue()

    def _clear_prefetch_queue(self) -> None:
        with self._prefetch_lock:
            if not self._prefetch_futures:
                return
            old_executor = self._executor
            self._executor = ThreadPoolExecutor(max_workers=self.prefetch_workers, thread_name_prefix="fusion-reader-v2-tts")
            self._prefetch_futures = {}
            self._prefetch_started = {}
            self._prefetch_future = None
            self._prefetch_index = None
            self._prefetch_started_ts = None
        old_executor.shutdown(wait=False, cancel_futures=True)

    def _shorten_dialogue_answer(self, answer: str) -> str:
        text = " ".join(str(answer or "").split()).strip()
        limit = max(80, self.dialogue_tts_max_chars)
        if len(text) <= limit:
            return text
        clipped = text[:limit].rstrip()
        sentence_end = max(clipped.rfind("."), clipped.rfind("?"), clipped.rfind("!"))
        if sentence_end >= 80:
            return clipped[: sentence_end + 1].strip()
        word_end = clipped.rfind(" ")
        if word_end >= 80:
            return clipped[:word_end].rstrip().rstrip(",;:") + "."
        return clipped.rstrip(",;:") + "."

    def notes_summary(self) -> dict:
        status = self.session.status()
        doc_id = str(status.get("doc_id") or "")
        if not doc_id:
            return {"ok": True, "count": 0, "current_count": 0}
        notes = self.notes.list(doc_id)
        current_index = max(0, int(status.get("current") or 1) - 1)
        current_count = sum(1 for note in notes if int(note.get("chunk_index") or 0) == current_index)
        return {"ok": True, "count": len(notes), "current_count": current_count}

    def list_notes(self, doc_id: str = "", chunk_index: int | None = None, current_only: bool = False) -> dict:
        status = self.session.status()
        selected_doc = str(doc_id or status.get("doc_id") or "")
        if not selected_doc:
            return {"ok": True, "doc_id": "", "items": []}
        if current_only:
            chunk_index = max(0, int(status.get("current") or 1) - 1)
        return {"ok": True, "doc_id": selected_doc, "items": self.notes.list(selected_doc, chunk_index=chunk_index)}

    def create_note(self, text: str, chunk_index: int | None = None) -> dict:
        document = self.session.document
        if not document:
            return {"ok": False, "error": "no_document_loaded"}
        selected_index = self.session.cursor if chunk_index is None else int(chunk_index)
        if selected_index < 0 or selected_index >= len(document.chunks):
            return {"ok": False, "error": "chunk_out_of_bounds"}
        try:
            note = self.notes.add(
                document.doc_id,
                document.title,
                selected_index,
                text,
                quote=document.chunks[selected_index],
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "note": note, "items": self.notes.list(document.doc_id)}

    def create_laboratory_note(self, text: str) -> dict:
        clean_text = self._resolve_laboratory_note_text(text)
        if not clean_text:
            return {"ok": False, "error": "empty_note"}
        try:
            note = self.notes.add(
                LABORATORY_NOTES_DOC_ID,
                LABORATORY_NOTES_TITLE,
                0,
                clean_text,
                quote=self._recent_laboratory_quote(),
                source_kind="laboratory",
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "note": note, "items": self.notes.list(LABORATORY_NOTES_DOC_ID)}

    def _resolve_note_chunk_index(self, chunk_index: int | None = None) -> int | None:
        document = self.session.document
        if document is None or chunk_index is None:
            return None
        try:
            selected = int(chunk_index)
        except (TypeError, ValueError):
            return None
        if selected < 0 or selected >= len(document.chunks):
            return None
        return selected

    def update_note(self, note_id: str, text: str, doc_id: str = "") -> dict:
        selected_doc = str(doc_id or self.session.status().get("doc_id") or "")
        if not selected_doc:
            return {"ok": False, "error": "no_document_loaded"}
        try:
            note = self.notes.update(selected_doc, str(note_id or ""), text)
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "note": note, "items": self.notes.list(selected_doc)}

    def rename_note(self, note_id: str, label: str, doc_id: str = "") -> dict:
        selected_doc = str(doc_id or self.session.status().get("doc_id") or "")
        if not selected_doc:
            return {"ok": False, "error": "no_document_loaded"}
        try:
            note = self.notes.update_label(selected_doc, str(note_id or ""), label)
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "note": note, "items": self.notes.list(selected_doc)}

    def delete_note(self, note_id: str, doc_id: str = "") -> dict:
        selected_doc = str(doc_id or self.session.status().get("doc_id") or "")
        if not selected_doc:
            return {"ok": False, "error": "no_document_loaded"}
        try:
            out = self.notes.delete(selected_doc, str(note_id or ""))
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}
        return {**out, "items": self.notes.list(selected_doc)}

    def _note_reference(self, note: dict) -> str:
        kind = str(note.get("source_kind") or "document").strip().lower()
        if kind == "laboratory":
            return f"L{int(note.get('anchor_number') or 1)}"
        return f"B{int(note.get('chunk_number') or note.get('anchor_number') or 1)}"

    def _note_saved_answer(self, note: dict, spoken: bool = False) -> str:
        ref = self._note_reference(note)
        if str(note.get("source_kind") or "").strip().lower() == "laboratory":
            return f"Listo, guardé esa nota como {ref}." if spoken else f"Nota guardada como {ref}."
        return f"Listo, guardé esa nota en el bloque {note.get('chunk_number') or 1}." if spoken else f"Nota guardada en el bloque {note.get('chunk_number') or 1}."

    def _recent_laboratory_quote(self) -> str:
        with self._dialogue_lock:
            dialogue_history = list(self._dialogue_history[-4:])
        with self._chat_lock:
            chat_history = list(self._chat_history[-4:])
        history = dialogue_history or chat_history
        lines: list[str] = []
        for item in history:
            role = str(item.get("role") or "").strip().lower()
            content = " ".join(str(item.get("content") or "").split())
            if not content:
                continue
            prefix = "Vos" if role == "user" else "Laboratorio" if role == "assistant" else "Sistema"
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines[:4]).strip()

    def _resolve_laboratory_note_text(self, text: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""
        if self._is_generic_laboratory_note_text(clean):
            resolved = self._recent_laboratory_note_target()
            if resolved:
                return resolved
        return clean

    def _recent_laboratory_note_target(self) -> str:
        with self._dialogue_lock:
            dialogue_history = list(self._dialogue_history)
        with self._chat_lock:
            chat_history = list(self._chat_history)
        for history in (dialogue_history, chat_history):
            if not history:
                continue
            for item in reversed(history):
                role = str(item.get("role") or "").strip().lower()
                content = " ".join(str(item.get("content") or "").split()).strip()
                if role != "assistant" or not content:
                    continue
                if self._looks_like_note_request(content):
                    continue
                return content
            for item in reversed(history):
                role = str(item.get("role") or "").strip().lower()
                content = " ".join(str(item.get("content") or "").split()).strip()
                if role != "user" or not content:
                    continue
                if self._looks_like_note_request(content):
                    continue
                return content
        return ""

    def _is_generic_laboratory_note_text(self, text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if not clean:
            return True
        if len(clean) <= 2:
            return True
        return bool(
            re.fullmatch(
                r"(?:d|de|del|eso|esto|eso\s+mismo|esto\s+mismo|todo\s+eso|todo\s+esto|lo\s+anterior|la\s+anterior|esa\s+frase|esta\s+frase|esa\s+idea|esta\s+idea|lo\s+que\s+acabo\s+de\s+decir|esto\s+que\s+acabo\s+de\s+decir|eso\s+que\s+acabo\s+de\s+decir|lo\s+que\s+acab(?:a|á)s?\s+de\s+decir|esto\s+que\s+acab(?:a|á)s?\s+de\s+decir|eso\s+que\s+acab(?:a|á)s?\s+de\s+decir|lo\s+[úu]ltimo\s+que\s+dijiste)",
                clean,
                flags=re.IGNORECASE,
            )
        )

    def _should_create_laboratory_note(self, text: str) -> bool:
        if self.session.document is None:
            return True
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if not clean:
            return False
        if not self._looks_like_recent_speech_reference(clean):
            return False
        with self._dialogue_lock:
            if self._dialogue_history:
                return True
        with self._chat_lock:
            return bool(self._chat_history)

    def _should_route_generic_note_to_laboratory(self, text: str, note_text: str) -> bool:
        if self.session.document is None:
            return True
        if not self._is_generic_note_pointer(note_text):
            return False
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if re.search(r"\b(?:documento|texto|pantalla|bloque|p[aá]rrafo|cap[ií]tulo|fragmento)\b", clean, flags=re.IGNORECASE):
            return False
        with self._dialogue_lock:
            if self._dialogue_history:
                return True
        with self._chat_lock:
            return bool(self._chat_history)

    def _looks_like_recent_speech_reference(self, text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if not clean:
            return False
        return bool(
            re.search(
                r"\b(?:laboratorio|chat|conversaci[oó]n|charla|saludo|mensajes?|lo\s+que\s+dijimos|lo\s+que\s+dije|lo\s+que\s+dijiste|lo\s+que\s+hablamos|nuestro\s+saludo|esta\s+charla|esta\s+conversaci[oó]n|mensaje\s+anterior|esto\s+que\s+acabo\s+de\s+decir|eso\s+que\s+acabo\s+de\s+decir|lo\s+que\s+acabo\s+de\s+decir|esto\s+que\s+acab(?:a|á)s?\s+de\s+decir|eso\s+que\s+acab(?:a|á)s?\s+de\s+decir|lo\s+que\s+acab(?:a|á)s?\s+de\s+decir|esto\s+que\s+dijiste|eso\s+que\s+dijiste|lo\s+[úu]ltimo\s+que\s+dijiste)\b",
                clean,
                flags=re.IGNORECASE,
            )
        )

    def _looks_like_immediate_speech_reference(self, text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if not clean:
            return False
        return bool(
            re.search(
                r"\b(?:esto\s+que\s+acabo\s+de\s+decir|eso\s+que\s+acabo\s+de\s+decir|lo\s+que\s+acabo\s+de\s+decir|esto\s+que\s+acab(?:a|á)s?\s+de\s+decir|eso\s+que\s+acab(?:a|á)s?\s+de\s+decir|lo\s+que\s+acab(?:a|á)s?\s+de\s+decir|esto\s+que\s+dijiste|eso\s+que\s+dijiste|lo\s+[úu]ltimo\s+que\s+dijiste|lo\s+que\s+dijiste|lo\s+que\s+dije)\b",
                clean,
                flags=re.IGNORECASE,
            )
        )

    def _is_generic_note_pointer(self, text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if not clean:
            return False
        if len(clean) <= 2:
            return True
        return bool(
            re.fullmatch(
                r"(?:d|de|del|eso|esto|eso\s+mismo|esto\s+mismo|todo\s+eso|todo\s+esto|lo\s+anterior|la\s+anterior|esa\s+frase|esta\s+frase|esa\s+idea|esta\s+idea)",
                clean,
                flags=re.IGNORECASE,
            )
        )

    def _persist_session_state(self, text: str | None = None, source_path: str = "", source_type: str = "") -> None:
        if self.session_state_path is None:
            return
        status = self.session.status()
        payload = {
            "doc_id": str(status.get("doc_id") or ""),
            "title": str(status.get("title") or ""),
            "cursor": int(status.get("cursor") or 0),
            "current": int(status.get("current") or 0),
            "total": int(status.get("total") or 0),
            "updated_ts": time.time(),
            "reasoning_mode": self.reasoning_mode,
            "laboratory_mode": self.laboratory_mode,
            "reference_documents": [
                {
                    "doc_id": str(item.get("doc_id") or ""),
                    "title": str(item.get("title") or ""),
                    "text": str(item.get("text") or ""),
                    "source_path": str(item.get("source_path") or ""),
                    "source_type": str(item.get("source_type") or ""),
                }
                for item in self._reference_documents.values()
            ],
        }
        selected_source_path = str(source_path or self._main_source_path or "")
        selected_source_type = str(source_type or self._main_source_type or "")
        if selected_source_path:
            payload["source_path"] = selected_source_path
        if selected_source_type:
            payload["source_type"] = selected_source_type
        if text is not None:
            payload["text"] = str(text)
        else:
            previous = self._read_session_state()
            if previous:
                if previous.get("source_path"):
                    payload["source_path"] = str(previous.get("source_path") or "")
                if previous.get("source_type"):
                    payload["source_type"] = str(previous.get("source_type") or "")
                if previous.get("text"):
                    payload["text"] = str(previous.get("text") or "")
        self.session_state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.session_state_path.with_suffix(f"{self.session_state_path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.session_state_path)

    def _read_session_state(self) -> dict:
        if self.session_state_path is None or not self.session_state_path.exists():
            return {}
        try:
            raw = json.loads(self.session_state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return raw if isinstance(raw, dict) else {}

    def _restore_session_state(self) -> None:
        raw = self._read_session_state()
        self.reasoning_mode = str(raw.get("reasoning_mode") or self.reasoning_mode or "thinking")
        self.reasoning_mode = str(self.conversation.reasoning_status(self.reasoning_mode).get("mode") or "thinking")
        self.laboratory_mode = "free" if str(raw.get("laboratory_mode") or "").strip().lower() == "free" else "document"
        doc_id = str(raw.get("doc_id") or "")
        title = str(raw.get("title") or "")
        self._reference_documents = {}
        if not doc_id:
            for item in raw.get("reference_documents") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    record = self._document_record(
                        str(item.get("doc_id") or item.get("title") or "consulta"),
                        str(item.get("title") or "Consulta"),
                        str(item.get("text") or ""),
                        source_path=str(item.get("source_path") or ""),
                        source_type=str(item.get("source_type") or ""),
                    )
                except Exception:
                    continue
                if record["text"].strip():
                    self._reference_documents[record["doc_id"]] = record
            return
        source_path = str(raw.get("source_path") or "")
        text = ""
        if source_path:
            path = Path(source_path)
            if path.exists() and path.is_file():
                try:
                    text = path.read_text(encoding="utf-8")
                except Exception:
                    text = ""
        if not text:
            text = str(raw.get("text") or "")
        if not text.strip():
            return
        self._reset_prepare_for_new_document()
        self.session.load(Document.from_text(doc_id, title or doc_id, text))
        self._main_source_path = source_path
        self._main_source_type = str(raw.get("source_type") or "")
        try:
            cursor = int(raw.get("cursor") or 0)
        except (TypeError, ValueError):
            cursor = 0
        total = len(self.session.document.chunks) if self.session.document else 0
        self.session.cursor = max(0, min(cursor, max(0, total - 1)))
        for item in raw.get("reference_documents") or []:
            if not isinstance(item, dict):
                continue
            try:
                record = self._document_record(
                    str(item.get("doc_id") or item.get("title") or "consulta"),
                    str(item.get("title") or "Consulta"),
                    str(item.get("text") or ""),
                    source_path=str(item.get("source_path") or ""),
                    source_type=str(item.get("source_type") or ""),
                )
            except Exception:
                continue
            if record["text"].strip() and record["doc_id"] != doc_id:
                self._reference_documents[record["doc_id"]] = record

    def _extract_note_command(self, text: str) -> str:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split())
        if not clean:
            return ""
        recent_speech_note = self._extract_recent_speech_note(clean)
        if recent_speech_note:
            return recent_speech_note
        prefix = (
            r"(?:(?:hola|por\s+favor|che|ok|okay|bueno|bien|s[ií]|y|adem[áa]s|tambi[ée]n|est[áa]\s+bien)[,.]?\s+)*"
            r"(?:(?:necesito|necesitar[ií]a|quiero|quisiera|me\s+gustar[ií]a|te\s+pido)\s+que\s+)?"
            r"(?:(?:me\s+)?(?:pod[eé]s|podr[ií]as|puedes|puede|podrias|podr[ií]a)\s+)?"
        )
        save_verbs = r"(?:guarda|guard[áa]|guardar|guarde|guardes|gu[áa]rdame|guardame|gu[áa]rdalo|guardalo|gu[áa]rdala|guardala)"
        make_note_verbs = r"(?:hac[eé]|hac[ée]me|hace|hacer|haga|hagas|haz|hazme|crea|cre[áa]|crear|agrega|agreg[áa]|agregar|sum[áa]|suma|sumar|deja|dej[áa]|dejar|dejame|d[eé]jame)"
        note_noun = r"(?:(?:una|la|esta|esa)\s+)?notas?"
        suffix_target = r"(?:eso|esto|lo\s+anterior|esta\s+frase|esta\s+idea|lo\s+que\s+dije|lo\s+que\s+te\s+dije)"
        suffix_patterns = [
            rf"^(.{{8,}}?)\s+(?:{save_verbs}|anota|anot[áa]|anotar|anotame|an[óo]tame)\s+{suffix_target}\s+(?:en|como)\s+(?:una\s+)?notas?\s*[.!?]*$",
            rf"^(.{{8,}}?)\s+(?:gu[áa]rdalo|guardalo|gu[áa]rdala|guardala|an[óo]talo|anotalo|an[óo]tala|anotala)\s+(?:en|como)\s+(?:una\s+)?notas?\s*[.!?]*$",
            rf"^(.{{8,}}?)\s+(?:{save_verbs}|anota|anot[áa]|anotar)\s+{suffix_target}\s*[.!?]*$",
        ]
        for pattern in suffix_patterns:
            match = re.search(pattern, clean, flags=re.IGNORECASE)
            if match:
                return self._clean_note_text(match.group(1))
        patterns = [
            rf"^{prefix}(?:lo\s+que\s+)?(?:quiero|necesito|quisiera|me\s+gustar[ií]a)\s+que\s+guardes\s+(?:es\s+)?(?:lo\s+siguiente\s*)?[:.,-]?\s*(.+)$",
            rf"^{prefix}{make_note_verbs}\s+(?:me\s+)?{note_noun}\s+(?:de\s+|del\s+|sobre\s+|con\s+)?(.+)$",
            rf"^{prefix}{make_note_verbs}\s+(?:otra\s+|una\s+)?notas?\s*[:,-]\s*(.+)$",
            rf"^{prefix}{save_verbs}\s+(?:esto\s+|eso\s+)?como\s+notas?\s*[:,-]?\s*(.+)$",
            rf"^{prefix}{save_verbs}\s+{note_noun}\s+(?:de\s+|del\s+|sobre\s+|con\s+)?(.+)$",
            rf"^{prefix}{save_verbs}\s+(?:esto|eso|esto\s+de|eso\s+de|la|lo|este|esta)\s+(?:de\s+|del\s+|sobre\s+|con\s+)?(.+)$",
            rf"^{prefix}{save_verbs}\s+(?:de\s+|del\s+|sobre\s+|con\s+)(.+)$",
            rf"^{prefix}{save_verbs}\s+(?:tambi[ée]n\s+|adem[áa]s\s+)?(.{{6,}})$",
            rf"^{prefix}(?:pon|pon[eé]|poneme|ponm[eé])\s+(?:esto\s+|eso\s+)?(?:en|como)\s+(?:una\s+)?notas?\s*[:,-]?\s*(.+)$",
            rf"^{prefix}(?:toma|tom[áa]|tomad|tomar|tome|tomes|tomame|t[óo]mame)\s+(?:una\s+)?notas?\s*(?:de\s+|del\s+|sobre\s+|acerca\s+de\s+|con\s+|en\s+notas?\s+del\s+documento\s+(?:que\s+)?(?:vamos\s+a\s+hablar\s+de\s+)?|[:,-]\s*)?(.+)$",
            rf"^{prefix}(?:anota|anot[áa]|anotar|anotame|an[óo]tame)\s*(?:esto\s+|eso\s+)?(?:de\s+|sobre\s+|[:,-]\s*)?(.+)$",
            rf"^{prefix}(?:notas?|apunte|apuntes)\s*[:,-]\s*(.+)$",
        ]
        for pattern in patterns:
            match = re.match(pattern, clean, flags=re.IGNORECASE)
            if match:
                return self._clean_note_text(match.group(1))
        inline_patterns = [
            r"(?:^|[,.]\s*|\bas[ií]\s+que\s+)(?:toma|tom[áa]|tomad|tomar|tome|tomes|tomame|t[óo]mame)\s+(?:una\s+)?notas?\s*(?:de\s+|del\s+|sobre\s+|acerca\s+de\s+|con\s+|[:,-]\s*)?(.+)$",
            rf"(?:^|[,.]\s*|\bas[ií]\s+que\s+){save_verbs}\s+{note_noun}\s+(?:de\s+|del\s+|sobre\s+|con\s+)?(.+)$",
            rf"(?:^|[,.]\s*|\bas[ií]\s+que\s+){make_note_verbs}\s+(?:me\s+)?{note_noun}\s+(?:de\s+|del\s+|sobre\s+|con\s+)?(.+)$",
            rf"(?:^|[,.]\s*|\bas[ií]\s+que\s+){save_verbs}\s+(?:esto|eso|esto\s+de|eso\s+de|la|lo|este|esta)\s+(?:de\s+|del\s+|sobre\s+|con\s+)?(.+)$",
            r"(?:^|[,.]\s*|\bas[ií]\s+que\s+)(?:anota|anot[áa]|anotar|anotame|an[óo]tame)\s*(?:esto\s+|eso\s+)?(?:de\s+|sobre\s+|[:,-]\s*)?(.+)$",
        ]
        for pattern in inline_patterns:
            match = re.search(pattern, clean, flags=re.IGNORECASE)
            if match:
                return self._clean_note_text(match.group(1))
        if self._looks_like_note_request(clean):
            for pattern in (
                r"(?:vamos\s+a\s+hablar|hablemos|estamos\s+hablando)\s+de\s+(.+)$",
                r"notas?\s+(?:del?\s+|sobre\s+|acerca\s+de\s+|con\s+)(.+)$",
            ):
                match = re.search(pattern, clean, flags=re.IGNORECASE)
                if match:
                    return self._clean_note_text(match.group(1))
        return ""

    def _extract_recent_speech_note(self, text: str) -> str:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split())
        if not clean:
            return ""
        for pattern in (
            r"^\s*tomando\s+a\s+(.+)$",
            r"^\s*tom[ée]\s+nota\s+de\s+(.+)$",
            r"^\s*toma\s+de\s+(.+)$",
        ):
            match = re.match(pattern, clean, flags=re.IGNORECASE)
            if not match:
                continue
            candidate = str(match.group(1) or "").strip(" .,:;-¿?¡!")
            candidate = re.split(r"\.\s+", candidate)[0].strip(" .,:;-¿?¡!")
            if self._looks_like_immediate_speech_reference(candidate):
                return self._clean_note_text(candidate)
        if self._looks_like_immediate_speech_reference(clean):
            return self._clean_note_text(clean)
        return ""

    def _looks_like_note_request(self, text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split())
        if not clean:
            return False
        has_note_word = re.search(r"\bnotas?\b|\bapuntes?\b", clean, flags=re.IGNORECASE)
        has_note_action = re.search(r"\b(?:tomar|toma|tom[áa]|tomad|tome|tomes|guardar|guarda|guard[áa]|guarde|guardes|guardalo|guard[áa]lo|guardala|guard[áa]la|anotar|anota|anot[áa]|pon|pon[eé]|poneme|hac[eé]|haceme|haz|hazme|crea|cre[áa]|agrega|agreg[áa]|suma|sum[áa]|deja|dej[áa]|dejar|dejame|d[eé]jame)\b", clean, flags=re.IGNORECASE)
        has_save_clause = re.search(r"\b(?:quiero|necesito|quisiera|me\s+gustar[ií]a)\s+que\s+guardes\b", clean, flags=re.IGNORECASE)
        has_suffix_reference = re.search(r"\b(?:guarda|guard[áa]|guardar|guarde|guardes|anota|anot[áa]|anotar)\s+(?:eso|esto|lo\s+anterior|esta\s+frase|esta\s+idea)\s+(?:en|como)\s+(?:una\s+)?notas?\b", clean, flags=re.IGNORECASE)
        has_followup_save = re.search(r"^\s*(?:y\s+|adem[áa]s\s+|tambi[ée]n\s+)*(?:guarda|guard[áa]|guardar|guarde|guardes|gu[áa]rdame|guardame)\s+(?:tambi[ée]n\s+|adem[áa]s\s+)?.{6,}$", clean, flags=re.IGNORECASE)
        return bool((has_note_word and (has_note_action or has_save_clause)) or has_suffix_reference or has_followup_save)

    def _is_stop_dialogue_command(self, text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).strip(" .,:;-!?").lower()
        if not clean:
            return False
        return bool(re.fullmatch(r"(?:det[eé]nte|detente|par[áa]|para|stop|basta|callate|c[áa]llate|silencio|no\s+hables|esper[áa]|espera)(?:\s+por\s+favor)?", clean, flags=re.IGNORECASE))

    def _clean_note_text(self, text: str) -> str:
        note = str(text or "").strip(" .,:;-¿?¡!")
        cleanup_patterns = [
            r"^(?:en\s+)?notas?\s+del\s+documento\s+(?:que\s+)?(?:vamos\s+a\s+hablar\s+de\s+)?",
            r"^(?:que\s+)?(?:vamos\s+a\s+hablar|hablemos|estamos\s+hablando)\s+de\s+",
            r"^(?:de\s+)?que\s+",
            r"^(?:de\s+)?(?:lo\s+que\s+)?(?:vamos\s+a\s+hablar|hablemos|estamos\s+hablando)\s+",
            r"^(?:acerca|sobre)\s+de\s+",
            r"^(?:por\s+ejemplo|ejemplo)\s*[:,.-]?\s*",
        ]
        for pattern in cleanup_patterns:
            note = re.sub(pattern, "", note, flags=re.IGNORECASE).strip(" .,:;-¿?¡!")
        note = re.sub(r"\s+(?:en|del|para)\s+el\s+bloque\s+\d+\s*$", "", note, flags=re.IGNORECASE).strip(" .,:;-¿?¡!")
        note = re.sub(r"\s+", " ", note).strip()
        return note

    def _record_voice_metric(self, event: str, payload: dict, text: str) -> None:
        try:
            self.metrics.record(
                VoiceMetric(
                    event=event,
                    ok=bool(payload.get("ok")),
                    provider=str(payload.get("provider") or ""),
                    cached=bool(payload.get("cached")),
                    voice=self.voice.voice,
                    language=self.voice.language,
                    ready_ms=int(payload.get("ready_ms") or 0),
                    synthesis_ms=int(payload.get("synthesis_ms") or 0),
                    text_chars=len(text or ""),
                    doc_id=str(payload.get("doc_id") or ""),
                    title=str(payload.get("title") or ""),
                    current=int(payload.get("current") or 0),
                    total=int(payload.get("total") or 0),
                    detail=str(payload.get("detail") or ""),
                )
            )
        except Exception:
            return

    def _play(self, path: Path | None) -> None:
        if not path:
            return
        for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)], ["paplay", str(path)], ["aplay", str(path)]):
            try:
                subprocess.run(cmd, check=False, timeout=300)
                return
            except FileNotFoundError:
                continue
            except Exception:
                continue
