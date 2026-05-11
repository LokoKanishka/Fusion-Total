import json
import os
import tempfile
import time
import wave
from pathlib import Path
from fusion_reader_v2 import (
    AudioArtifact,
    AudioCache,
    ConversationCore,
    ExternalResearchResult,
    FusionReaderV2,
    NullChatProvider,
    NullExternalResearchBridge,
    NullSTTProvider,
    NullTTSProvider,
    ReaderNotesStore,
    STTProvider,
    TranscriptResult,
    VoiceMetricsStore,
)

def test_app(tts=None, stt=None, root: Path | None = None, external_research=None) -> FusionReaderV2:
    root = root or Path(tempfile.mkdtemp())
    return FusionReaderV2(
        tts=tts or NullTTSProvider(),
        stt=stt or NullSTTProvider(),
        cache=AudioCache(root / "audio_cache"),
        metrics=VoiceMetricsStore(root / "voice_metrics.jsonl"),
        notes=ReaderNotesStore(root / "notes"),
        conversation=ConversationCore(NullChatProvider("Entendido.")),
        external_research=external_research or NullExternalResearchBridge(ExternalResearchResult(False, detail="bridge_unused")),
        session_state_path=root / "session_state.json",
    )

class FailingTTSProvider(NullTTSProvider):
    name = "failing_tts"

    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        self.calls.append((text, voice, language))
        return AudioArtifact(False, provider=self.name, detail="tts_down")

class SyntheticWavTTSProvider(NullTTSProvider):
    name = "synthetic_wav_tts"

    def __init__(self, delay_seconds: float = 0.0) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds

    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        self.calls.append((text, voice, language))
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        fd, name = tempfile.mkstemp(prefix="fusion_reader_v2_synthetic_", suffix=".wav")
        os.close(fd)
        path = Path(name)
        sample_rate = 16000
        frames = (max(1, len(text)) * 160)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"\0\0" * frames)
        return AudioArtifact(True, path=path, provider=self.name, duration_ms=max(1, len(text)))

class LengthLimitedSyntheticWavTTSProvider(SyntheticWavTTSProvider):
    def __init__(self, max_chars: int, delay_seconds: float = 0.0) -> None:
        super().__init__(delay_seconds=delay_seconds)
        self.max_chars = max_chars

    def synthesize(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        if len(text) > self.max_chars:
            self.calls.append((text, voice, language))
            return AudioArtifact(False, provider=self.name, detail="http_400")
        return super().synthesize(text, voice=voice, language=language)

class EmptyTranscriptSTTProvider(STTProvider):
    name = "empty_stt"

    def transcribe_file(self, path: str | Path, mime: str = "", language: str = "es") -> TranscriptResult:
        return TranscriptResult(False, provider=self.name, detail="empty_transcript")

class HallucinatedTranscriptSTTProvider(STTProvider):
    name = "hallucinated_stt"

    def health(self) -> dict:
        return {"ok": True, "provider": self.name}

    def transcribe_file(self, path: str | Path, mime: str = "", language: str = "es") -> TranscriptResult:
        return TranscriptResult(False, text="¡Suscríbete!", provider=self.name, detail="hallucinated_transcript", duration_ms=12)

class BrokenSTTProvider(STTProvider):
    name = "broken_stt"

    def health(self) -> dict:
        return {"ok": False, "provider": self.name, "detail": "connection_refused"}

    def transcribe_file(self, path: str | Path, mime: str = "", language: str = "es") -> TranscriptResult:
        return TranscriptResult(False, provider=self.name, detail="connection_refused", duration_ms=33)

class FailingChatProvider:
    name = "failing_chat"

    def __init__(self, detail: str = "connection_refused") -> None:
        self.detail = detail
        self.calls: list[tuple[list[dict], str, dict]] = []

    def health(self) -> dict:
        return {"ok": False, "provider": self.name, "model": "broken-local", "detail": self.detail}

    def chat(self, messages: list[dict], model: str = "", think: bool | None = None, num_predict: int | None = None):
        self.calls.append((messages, model, {"think": think, "num_predict": num_predict}))
        from fusion_reader_v2.conversation import ChatResult
        return ChatResult(False, model=model or "broken-local", detail=self.detail, duration_ms=41)

def attach_legacy_tests(target_class, names: tuple[str, ...]) -> None:
    from tests.test_fusion_reader_v2 import FusionReaderV2Tests

    for name in names:
        legacy_name = "legacy_" + name.removeprefix("test_")
        setattr(target_class, name, getattr(FusionReaderV2Tests, legacy_name))

class NullResearchProvider:
    def __init__(self, results=None) -> None:
        self.results = results or []
        self.calls = []

    def search(self, query: str) -> list:
        self.calls.append(query)
        return self.results

class FailingResearchProvider:
    def search(self, query: str) -> list:
        raise RuntimeError("failed_to_research")

class FakeExternalResearchBridge:
    def __init__(self, result: ExternalResearchResult, *, available: bool = True) -> None:
        self.result = result
        self.available_value = available
        self.calls: list[tuple[str, dict]] = []

    def available(self) -> bool:
        return self.available_value

    def research(self, request: str, snapshot: dict | None = None) -> ExternalResearchResult:
        self.calls.append((str(request or ""), dict(snapshot or {})))
        return self.result

def make_simple_pdf_bytes(lines: list[str]) -> bytes:
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content_lines = ["BT", "/F1 18 Tf"]
    y = 760
    for line in lines:
        content_lines.append(f"1 0 0 1 72 {y} Tm ({esc(line)}) Tj")
        y -= 28
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1", errors="replace")
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(f"<< /Length {len(content)} >>\nstream\n".encode("latin-1") + content + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode("latin-1"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("latin-1")
    )
    return bytes(out)

READING_FILLER = (
    "La lectura continua necesita suficiente contexto para sostener una pagina mental coherente "
    "sin fragmentarse en unidades diminutas que vuelvan torpe la navegacion del lector."
)

def make_reading_paragraph(label: str, extra: str = "") -> str:
    parts = [str(label or "").strip(), READING_FILLER]
    if extra:
        parts.append(str(extra).strip())
    return " ".join(part for part in parts if part).strip()

def make_reading_document(label: str, paragraphs: int, extra: str = "") -> str:
    return "\n\n".join(make_reading_paragraph(f"{label} {index}.", extra=extra) for index in range(1, paragraphs + 1))

def make_reading_sections(*sections: tuple[str, str], paragraphs_per_section: int = 10) -> str:
    paragraphs: list[str] = []
    for label, marker in sections:
        for index in range(1, paragraphs_per_section + 1):
            extra = marker if index == 1 else ""
            paragraphs.append(make_reading_paragraph(f"{label} {index}.", extra=extra))
    return "\n\n".join(paragraphs)

def manual_document(doc_id: str, title: str, chunks: list[str]):
    from fusion_reader_v2 import Document
    return Document(doc_id=doc_id, title=title, text="\n\n".join(chunks), chunks=list(chunks))

class FakeUrlOpenResponse:
    def __init__(self, payload: str, status: int = 200) -> None:
        self.payload = payload.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False
