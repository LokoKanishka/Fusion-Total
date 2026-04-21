from __future__ import annotations

import re
import time
import os
from dataclasses import dataclass, field


DEFAULT_CHUNK_MAX_CHARS = int(os.environ.get("FUSION_READER_CHUNK_MAX_CHARS", "420"))


def split_text(text: str, max_chars: int | None = None) -> list[str]:
    """Split text into natural reading chunks without requiring NLP deps."""
    max_chars = max_chars or DEFAULT_CHUNK_MAX_CHARS
    clean = re.sub(r"\r\n?", "\n", str(text or "")).strip()
    if not clean:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", clean) if p.strip() and not is_noise_paragraph(p)]
    chunks: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
            continue

        sentences = re.split(r"(?<=[.!?;:])\s+", paragraph)
        current = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(split_long_sentence(sentence, max_chars=max_chars))
                continue
            if current and len(current) + 1 + len(sentence) > max_chars:
                chunks.append(current)
                current = sentence
            elif current:
                current = f"{current} {sentence}"
            else:
                current = sentence
        if current:
            chunks.append(current)

    return chunks


def is_noise_paragraph(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    return compact in {"0", "O"}


def split_long_sentence(sentence: str, max_chars: int | None = None) -> list[str]:
    max_chars = max_chars or DEFAULT_CHUNK_MAX_CHARS
    words = str(sentence or "").split()
    chunks: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > max_chars:
            chunks.append(current)
            current = word
        elif current:
            current = f"{current} {word}"
        else:
            current = word
    if current:
        chunks.append(current)
    return chunks


@dataclass
class Document:
    doc_id: str
    title: str
    text: str
    chunks: list[str] = field(default_factory=list)

    @classmethod
    def from_text(cls, doc_id: str, title: str, text: str, max_chars: int | None = None) -> "Document":
        return cls(doc_id=doc_id, title=title, text=text, chunks=split_text(text, max_chars=max_chars))


@dataclass
class ReaderSession:
    document: Document | None = None
    cursor: int = 0
    state: str = "idle"
    updated_ts: float = field(default_factory=time.time)

    def load(self, document: Document) -> dict:
        self.document = document
        self.cursor = 0
        self.state = "loaded" if document.chunks else "empty"
        self.updated_ts = time.time()
        return self.status()

    def current_chunk(self) -> str:
        if not self.document or not self.document.chunks:
            return ""
        return self.document.chunks[self.cursor]

    def next_chunk(self) -> str:
        if not self.document or not self.document.chunks:
            self.state = "empty"
            return ""
        if self.cursor < len(self.document.chunks) - 1:
            self.cursor += 1
            self.state = "loaded"
        else:
            self.state = "eof"
        self.updated_ts = time.time()
        return self.current_chunk()

    def previous_chunk(self) -> str:
        if not self.document or not self.document.chunks:
            self.state = "empty"
            return ""
        self.cursor = max(0, self.cursor - 1)
        self.state = "loaded"
        self.updated_ts = time.time()
        return self.current_chunk()

    def jump(self, one_based_index: int) -> str:
        if not self.document or not self.document.chunks:
            self.state = "empty"
            return ""
        idx = int(one_based_index) - 1
        if idx < 0 or idx >= len(self.document.chunks):
            raise IndexError("chunk_out_of_bounds")
        self.cursor = idx
        self.state = "loaded"
        self.updated_ts = time.time()
        return self.current_chunk()

    def status(self) -> dict:
        total = len(self.document.chunks) if self.document else 0
        return {
            "ok": True,
            "state": self.state,
            "doc_id": self.document.doc_id if self.document else "",
            "title": self.document.title if self.document else "",
            "cursor": self.cursor,
            "current": self.cursor + 1 if total else 0,
            "total": total,
            "text": self.current_chunk(),
            "updated_ts": self.updated_ts,
        }
