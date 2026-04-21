"""Fusion Reader v2: voice-first conversational reader core."""

from .documents import ImportedDocument, import_document_bytes, import_document_path
from .conversation import ChatResult, ConversationCore, NullChatProvider, OllamaChatProvider
from .dialogue import AutoSTTProvider, FasterWhisperServerSTTProvider, NullSTTProvider, STTProvider, TranscriptResult, WhisperCliSTTProvider, default_stt_provider
from .notes import ReaderNote, ReaderNotesStore
from .reader import Document, ReaderSession, split_text
from .service import FusionReaderV2
from .tts import AllTalkProvider, AudioArtifact, AudioCache, NullTTSProvider
from .metrics import VoiceMetric, VoiceMetricsStore

__all__ = [
    "AllTalkProvider",
    "AudioArtifact",
    "AudioCache",
    "AutoSTTProvider",
    "ChatResult",
    "ConversationCore",
    "Document",
    "FusionReaderV2",
    "FasterWhisperServerSTTProvider",
    "ImportedDocument",
    "NullTTSProvider",
    "NullChatProvider",
    "NullSTTProvider",
    "OllamaChatProvider",
    "ReaderNote",
    "ReaderNotesStore",
    "ReaderSession",
    "STTProvider",
    "TranscriptResult",
    "VoiceMetric",
    "VoiceMetricsStore",
    "WhisperCliSTTProvider",
    "default_stt_provider",
    "import_document_bytes",
    "import_document_path",
    "split_text",
]
