import threading
import queue
import time
import os
import re
import json
import subprocess
import collections
from pathlib import Path

# Paths and Defaults for compatibility
VOICE_STATE_PATH = Path("~/.openclaw/voice_state.json").expanduser()
_default_voice_state = {"enabled": False, "speaker": "default", "provider": "alltalk"}

class STTWorker:
    def __init__(self, config: dict, item_queue: queue.Queue, status_queue: queue.Queue) -> None:
        self.config = config
        self.item_queue = item_queue
        self.status_queue = status_queue
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        pass

class BargeInMonitor:
    def __init__(self, stream_id: int, stop_event: threading.Event) -> None:
        self.stream_id = stream_id
        self.stop_event = stop_event
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        pass

class STTManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._worker: STTWorker | None = None
        self._items = collections.deque(maxlen=100)
        self._status = {}

    def start(self, session_id: str = "") -> None:
        pass

    def stop(self) -> None:
        pass

    def poll(self, session_id: str, limit: int = 5) -> list:
        return []

    def status(self) -> dict:
        return self._status

# Global instance
_STT_MANAGER = STTManager()

def _load_voice_state() -> dict:
    if not VOICE_STATE_PATH.exists():
        return dict(_default_voice_state)
    try:
        return json.loads(VOICE_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": False}

def _save_voice_state(state: dict) -> None:
    VOICE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    VOICE_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def _tts_speak_alltalk(text: str, state: dict) -> tuple[Path | None, str]:
    return None, "not_implemented_in_modular"
