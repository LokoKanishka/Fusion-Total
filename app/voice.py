import threading
import queue
import time
import os
import re
import json
import subprocess
import collections
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Paths and Defaults for compatibility
VOICE_STATE_PATH = Path(os.environ.get("DIRECT_CHAT_VOICE_STATE_PATH", "~/.openclaw/voice_state.json")).expanduser()
_default_voice_state = {
    "enabled": False,
    "speaker": os.environ.get("DIRECT_CHAT_ALLTALK_VOICE", "female_01.wav"),
    "provider": "alltalk",
    "alltalk_url": os.environ.get("DIRECT_CHAT_ALLTALK_URL", "http://localhost:7851"),
    "tts_backend": "alltalk",
    "tts_language": os.environ.get("DIRECT_CHAT_TTS_LANGUAGE", "es"),
    "tts_fallback_backend": os.environ.get("DIRECT_CHAT_TTS_FALLBACK_BACKEND", "spd-say"),
    "tts_fallback_language": os.environ.get("DIRECT_CHAT_TTS_FALLBACK_LANGUAGE", "es-419"),
    "tts_fallback_rate": int(os.environ.get("DIRECT_CHAT_TTS_FALLBACK_RATE", "-25")),
    "tts_fallback_pitch": int(os.environ.get("DIRECT_CHAT_TTS_FALLBACK_PITCH", "-5")),
    "tts_fallback_voice_type": os.environ.get("DIRECT_CHAT_TTS_FALLBACK_VOICE_TYPE", "female1"),
    "tts_health_url": f"{os.environ.get('DIRECT_CHAT_ALLTALK_URL', 'http://localhost:7851').rstrip('/')}/api/ready",
    "tts_health_timeout_sec": 1.0,
    "tts_available": False,
    "voice_owner": "reader",
    "reader_mode_active": False,
    "reader_owner_token": "",
    "stt_no_speech_detected": False,
    "stt_vad_true_ratio": 0.0,
}

@dataclass
class TTSResult:
    ok: bool
    detail: str = ""
    provider: str = ""
    audio_path: Path | None = None
    audio_url: str = ""

class TTSProvider:
    name = "base"

    def __init__(self, state: dict) -> None:
        self.state = state

    def health(self) -> dict:
        return {"ok": False, "provider": self.name, "detail": "not_implemented"}

    def voices(self) -> list[str]:
        return []

    def synthesize(self, text: str, blocking: bool = True) -> TTSResult:
        return TTSResult(False, "not_implemented", provider=self.name)

class AllTalkTTSProvider(TTSProvider):
    name = "alltalk"

    def _base_url(self) -> str:
        return str(
            self.state.get("alltalk_url")
            or os.environ.get("DIRECT_CHAT_ALLTALK_URL")
            or "http://localhost:7851"
        ).rstrip("/")

    def _speaker(self) -> str:
        speaker = str(
            self.state.get("speaker")
            or os.environ.get("DIRECT_CHAT_ALLTALK_VOICE")
            or "female_01.wav"
        )
        return "female_01.wav" if speaker == "default" else speaker

    def _language(self) -> str:
        return str(
            self.state.get("tts_language")
            or os.environ.get("DIRECT_CHAT_TTS_LANGUAGE")
            or "es"
        )

    def _requests(self):
        try:
            import requests
            return requests, ""
        except Exception as e:
            return None, f"requests_unavailable:{e}"

    def health(self) -> dict:
        requests, err = self._requests()
        if requests is None:
            return {"ok": False, "provider": self.name, "detail": err}
        base = self._base_url()
        timeout = min(3.0, max(0.1, float(self.state.get("tts_health_timeout_sec", 1.0) or 1.0)))
        try:
            resp = requests.get(f"{base}/api/ready", timeout=timeout)
            return {
                "ok": resp.status_code < 500,
                "provider": self.name,
                "status_code": resp.status_code,
                "url": f"{base}/api/ready",
            }
        except Exception as e:
            return {"ok": False, "provider": self.name, "detail": f"alltalk_unreachable:{e}", "url": f"{base}/api/ready"}

    def voices(self) -> list[str]:
        requests, _err = self._requests()
        if requests is None:
            return []
        try:
            resp = requests.get(f"{self._base_url()}/api/voices", timeout=5)
            if resp.status_code >= 400:
                return []
            data = resp.json()
        except Exception:
            return []
        if isinstance(data, list):
            return [str(v) for v in data if str(v).strip()]
        if isinstance(data, dict):
            candidates = data.get("voices") or data.get("voice_files") or data.get("data") or []
            if isinstance(candidates, dict):
                candidates = list(candidates.keys())
            if isinstance(candidates, list):
                out = []
                for item in candidates:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("voice") or item.get("filename") or item.get("id")
                    else:
                        name = item
                    if str(name or "").strip():
                        out.append(str(name))
                return out
        return []

    def synthesize(self, text: str, blocking: bool = True) -> TTSResult:
        requests, err = self._requests()
        if requests is None:
            return TTSResult(False, err, provider=self.name)
        base = self._base_url()
        speaker = self._speaker()
        payload = {
            "text_input": text,
            "text_filtering": "standard",
            "character_voice_gen": speaker,
            "narrator_enabled": "false",
            "narrator_voice_gen": speaker,
            "text_not_inside": "character",
            "language": self._language(),
            "output_file_name": f"reader_{int(time.time())}",
            "output_file_timestamp": "true",
            "autoplay": "false",
            "autoplay_volume": "0.8",
        }
        try:
            resp = requests.post(f"{base}/api/tts-generate", data=payload, timeout=180)
            if resp.status_code >= 400:
                return TTSResult(False, f"alltalk_http_{resp.status_code}", provider=self.name)
            data = resp.json() if "json" in resp.headers.get("content-type", "").lower() else {}
            audio_ref = (
                data.get("output_file_url")
                or data.get("audio_url")
                or data.get("file_url")
                or data.get("output_file_path")
                or data.get("file_path")
            )
            if not audio_ref:
                return TTSResult(False, "alltalk_no_audio_reference", provider=self.name)
            audio_url = str(audio_ref) if str(audio_ref).startswith("http") else f"{base}/{str(audio_ref).lstrip('/')}"
            audio_resp = requests.get(audio_url, timeout=180)
            if audio_resp.status_code >= 400 or not audio_resp.content:
                return TTSResult(False, f"alltalk_audio_http_{audio_resp.status_code}", provider=self.name, audio_url=audio_url)
            suffix = ".wav" if "wav" in audio_resp.headers.get("content-type", "").lower() else ".mp3"
            fd, name = tempfile.mkstemp(prefix="reader_tts_", suffix=suffix)
            os.close(fd)
            path = Path(name)
            path.write_bytes(audio_resp.content)
            return TTSResult(True, "ok", provider=self.name, audio_path=path, audio_url=audio_url)
        except Exception as e:
            return TTSResult(False, f"alltalk_error:{e}", provider=self.name)

class SpeechDispatcherTTSProvider(TTSProvider):
    name = "spd-say"

    def health(self) -> dict:
        try:
            subprocess.run(["spd-say", "--version"], check=False, timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "provider": self.name}
        except Exception as e:
            return {"ok": False, "provider": self.name, "detail": f"spd_say_unavailable:{e}"}

    def synthesize(self, text: str, blocking: bool = True) -> TTSResult:
        language = str(
            self.state.get("tts_fallback_language")
            or os.environ.get("DIRECT_CHAT_TTS_FALLBACK_LANGUAGE")
            or "es-419"
        )
        rate = str(
            self.state.get("tts_fallback_rate")
            or os.environ.get("DIRECT_CHAT_TTS_FALLBACK_RATE")
            or "-25"
        )
        pitch = str(
            self.state.get("tts_fallback_pitch")
            or os.environ.get("DIRECT_CHAT_TTS_FALLBACK_PITCH")
            or "-5"
        )
        voice_type = str(
            self.state.get("tts_fallback_voice_type")
            or os.environ.get("DIRECT_CHAT_TTS_FALLBACK_VOICE_TYPE")
            or "female1"
        )
        cmd = [
            "spd-say",
            "-o", "espeak-ng",
            "-l", language,
            "-r", rate,
            "-p", pitch,
            "-t", voice_type,
            "-m", "none",
        ]
        if blocking:
            cmd.append("-w")
        cmd.append(text)
        subprocess.run(cmd, check=False)
        return TTSResult(True, "ok", provider=self.name)

def get_tts_provider(state: dict) -> TTSProvider:
    provider = str(state.get("provider") or state.get("tts_backend") or "alltalk").lower()
    if provider == "alltalk":
        return AllTalkTTSProvider(state)
    return SpeechDispatcherTTSProvider(state)

def enrich_tts_state(state: dict, include_voices: bool = False) -> dict:
    out = dict(_default_voice_state)
    out.update(state or {})
    provider = get_tts_provider(out)
    health = provider.health()
    out["tts_backend"] = provider.name
    out["tts_provider"] = provider.name
    out["tts_available"] = bool(health.get("ok"))
    out["tts_health"] = health
    if include_voices:
        out["tts_voices"] = provider.voices()
    return out

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
        self._queue: queue.Queue = queue.Queue()
        self._items = collections.deque(maxlen=100)
        self._status = {}
        self._enabled = False
        self._owner_session_id = ""
        self._rms_current = 0.0
        self._rms_threshold = 0.02
        self._vad_true_ratio = 0.0
        self._last_segment_ms = 0

    def enable(self, session_id: str = "") -> None:
        with self._lock:
            self._enabled = True
            self._owner_session_id = session_id or self._owner_session_id or "default"

    def disable(self) -> None:
        with self._lock:
            self._enabled = False
            self._owner_session_id = ""
            self._worker = None

    def claim_owner(self, session_id: str) -> None:
        with self._lock:
            self._owner_session_id = session_id or "default"

    def _clear_queue_locked(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def inject(self, text: str, session_id: str = "") -> None:
        with self._lock:
            if session_id:
                self._enabled = True
                self._owner_session_id = session_id
            self._queue.put({"text": text, "ts": time.time()})

    def start(self, session_id: str = "") -> None:
        self.enable(session_id)

    def stop(self) -> None:
        self.disable()

    def poll(self, session_id: str, limit: int = 5) -> list:
        with self._lock:
            if self._enabled and self._owner_session_id and session_id != self._owner_session_id:
                return []
            out = []
            for _ in range(max(1, int(limit or 5))):
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                text = str(item.get("text") or item.get("cmd") or "").strip()
                low = text.lower()
                if any(w in low for w in ("pausa", "para", "pará", "deten", "stop", "pauza", "posa", "poza")):
                    out.append({"kind": "voice_cmd", "source": "voice_cmd", "cmd": "pause", "text": text, "ts": item.get("ts", time.time())})
                elif any(w in low for w in ("continuar", "segui", "seguí", "sigue", "dale")):
                    out.append({"kind": "voice_cmd", "source": "voice_cmd", "cmd": "continue", "text": text, "ts": item.get("ts", time.time())})
                elif any(w in low for w in ("repetir", "repeti", "repetí")):
                    out.append({"kind": "voice_cmd", "source": "voice_cmd", "cmd": "repeat", "text": text, "ts": item.get("ts", time.time())})
                elif text:
                    out.append({"kind": "chat_text", "source": "voice_chat", "text": text, "ts": item.get("ts", time.time())})
            return out

    def status(self) -> dict:
        with self._lock:
            out = dict(self._status)
            out.update({
                "stt_enabled": self._enabled,
                "stt_running": bool(self._worker),
                "stt_owner_session_id": self._owner_session_id,
                "rms": self._rms_current,
                "threshold": self._rms_threshold,
                "vad_true_ratio": self._vad_true_ratio,
                "last_segment_ms": self._last_segment_ms,
            })
            return out

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

def stop_speech() -> None:
    """Cancels all current speech by sending the stop command to spd-say."""
    if os.environ.get("DIRECT_CHAT_TTS_DRY_RUN") == "1":
        print("[TTS DRY-RUN]: stop_speech triggered")
        return

    try:
        subprocess.run(["spd-say", "-S"], check=False, timeout=2.0)
    except Exception:
        pass

def _play_audio_file(path: Path, blocking: bool = True) -> bool:
    players = [
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
        ["paplay", str(path)],
        ["aplay", str(path)],
    ]
    for cmd in players:
        try:
            proc = subprocess.Popen(cmd)
            if blocking:
                proc.wait(timeout=180)
            return True
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return False

def _tts_speak_alltalk(text: str, state: dict) -> tuple[Path | None, str]:
    try:
        import requests
    except Exception as e:
        return None, f"requests_unavailable:{e}"

    base = str(state.get("alltalk_url") or os.environ.get("DIRECT_CHAT_ALLTALK_URL") or "http://localhost:7851").rstrip("/")
    speaker = str(state.get("speaker") or os.environ.get("DIRECT_CHAT_ALLTALK_VOICE") or "female_01.wav")
    if speaker == "default":
        speaker = "female_01.wav"
    payload = {
        "text_input": text,
        "text_filtering": "standard",
        "character_voice_gen": speaker,
        "narrator_enabled": "false",
        "narrator_voice_gen": speaker,
        "text_not_inside": "character",
        "language": "es",
        "output_file_name": f"reader_{int(time.time())}",
        "output_file_timestamp": "true",
        "autoplay": "false",
        "autoplay_volume": "0.8",
    }
    try:
        resp = requests.post(f"{base}/api/tts-generate", data=payload, timeout=180)
        if resp.status_code >= 400:
            return None, f"alltalk_http_{resp.status_code}"
        data = resp.json() if "json" in resp.headers.get("content-type", "").lower() else {}
        audio_ref = (
            data.get("output_file_url")
            or data.get("audio_url")
            or data.get("file_url")
            or data.get("output_file_path")
            or data.get("file_path")
        )
        if not audio_ref:
            return None, "alltalk_no_audio_reference"
        if str(audio_ref).startswith("http"):
            audio_resp = requests.get(str(audio_ref), timeout=180)
        else:
            audio_resp = requests.get(f"{base}/{str(audio_ref).lstrip('/')}", timeout=180)
        if audio_resp.status_code >= 400 or not audio_resp.content:
            return None, f"alltalk_audio_http_{audio_resp.status_code}"
        suffix = ".wav" if "wav" in audio_resp.headers.get("content-type", "").lower() else ".mp3"
        fd, name = tempfile.mkstemp(prefix="reader_tts_", suffix=suffix)
        os.close(fd)
        path = Path(name)
        path.write_bytes(audio_resp.content)
        return path, "ok"
    except Exception as e:
        return None, f"alltalk_error:{e}"

def _tts_speak_spd_say(text: str, state: dict, blocking: bool = True) -> bool:
    language = str(
        state.get("tts_fallback_language")
        or os.environ.get("DIRECT_CHAT_TTS_FALLBACK_LANGUAGE")
        or "es-419"
    )
    rate = str(
        state.get("tts_fallback_rate")
        or os.environ.get("DIRECT_CHAT_TTS_FALLBACK_RATE")
        or "-25"
    )
    pitch = str(
        state.get("tts_fallback_pitch")
        or os.environ.get("DIRECT_CHAT_TTS_FALLBACK_PITCH")
        or "-5"
    )
    voice_type = str(
        state.get("tts_fallback_voice_type")
        or os.environ.get("DIRECT_CHAT_TTS_FALLBACK_VOICE_TYPE")
        or "female1"
    )
    cmd = [
        "spd-say",
        "-o", "espeak-ng",
        "-l", language,
        "-r", rate,
        "-p", pitch,
        "-t", voice_type,
        "-m", "none",
    ]
    if blocking:
        cmd.append("-w")
    cmd.append(text)
    subprocess.run(cmd, check=False)
    return True

def perform_tts(text: str, blocking: bool = True) -> bool:
    """
    Performs TTS using AllTalk when available, then a Spanish speech-dispatcher fallback.
    - blocking: If True, waits for the speech to finish before returning.
    """
    if not text.strip():
        return False

    if os.environ.get("DIRECT_CHAT_TTS_DRY_RUN") == "1":
        print(f"[TTS DRY-RUN]: {text}")
        return True

    state = enrich_tts_state(_load_voice_state())
    provider = get_tts_provider(state)
    if provider.name == "alltalk":
        result = provider.synthesize(text, blocking=blocking)
        if result.audio_path:
            try:
                return _play_audio_file(result.audio_path, blocking=blocking)
            finally:
                try:
                    result.audio_path.unlink()
                except Exception:
                    pass
        if result.ok:
            return True
        print(f"AllTalk TTS fallback: {result.detail}")

    try:
        fallback = SpeechDispatcherTTSProvider(state).synthesize(text, blocking=blocking)
        return bool(fallback.ok)
    except Exception as e:
        print(f"TTS Error: {e}")
        return False
