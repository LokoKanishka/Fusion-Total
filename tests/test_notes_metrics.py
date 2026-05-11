import unittest
import tempfile
import json
import os
from pathlib import Path
from fusion_reader_v2 import ConversationCore, NullChatProvider
from tests.helpers import (
    test_app,
    NullTTSProvider,
)

class NotesMetricsTests(unittest.TestCase):
    def test_notes_are_persisted_to_json_file(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        app.load_text("doc1", "Doc 1", "Contenido", prefetch=False)
        app.create_note("Nota de prueba", 0)
        notes_file = root / "notes" / "doc1.json"
        self.assertTrue(notes_file.exists())
        
    def test_notes_can_be_filtered_by_document(self):
        app = test_app()
        app.load_text("doc1", "D1", "C1", prefetch=False)
        app.create_note("N1", 0)
        app.load_text("doc2", "D2", "C2", prefetch=False)
        app.create_note("N2", 0)
        self.assertEqual(len(app.list_notes(doc_id="doc1")["items"]), 1)
        self.assertEqual(app.list_notes(doc_id="doc1")["items"][0]["text"], "N1")


    def test_notes_include_timestamp(self):
        app = test_app()
        app.load_text("doc1", "D1", "C1", prefetch=False)
        app.create_note("N", 0)
        note = app.list_notes()["items"][0]
        self.assertIn("created_ts", note)

    def test_notes_can_be_deleted(self):
        app = test_app()
        app.load_text("doc1", "D1", "C1", prefetch=False)
        note = app.create_note("N", 0)
        note_id = note["note"]["note_id"]
        self.assertEqual(len(app.list_notes()["items"]), 1)
        app.delete_note(note_id)
        self.assertEqual(len(app.list_notes()["items"]), 0)

    def test_voice_metrics_track_usage_by_voice_id(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(tts=NullTTSProvider(), root=root)
        app.set_voice("v1.wav")
        app.test_voice("Hola")
        app.set_voice("v2.wav")
        app.test_voice("Hola")
        app.test_voice("Mundo")
        metrics = app.voice_metrics_summary()
        v1 = next(m for m in metrics["items"] if m["voice"] == "v1.wav")
        v2 = next(m for m in metrics["items"] if m["voice"] == "v2.wav")
        self.assertEqual(v1["count"], 1)
        self.assertEqual(v2["count"], 2)

    def test_voice_metrics_are_persisted_across_sessions(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(tts=NullTTSProvider(), root=root)
        app.set_voice("v1.wav")
        app.test_voice("Hola")
        reopened = test_app(tts=NullTTSProvider(), root=root)
        metrics = reopened.voice_metrics_summary()
        v1 = next(m for m in metrics["items"] if m["voice"] == "v1.wav")
        self.assertEqual(v1["count"], 1)

    def test_clear_laboratory_history_removes_chat_and_dialogue_context(self):
        chat_provider = NullChatProvider("L.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.chat("Pasted")
        app.dialogue_turn_text("¿?")
        cleared = app.clear_laboratory_history()
        self.assertTrue(cleared["ok"])
        app.dialogue_turn_text("¿Leés?")
        # Context should be empty or just the system prompt + new turn
        prompt = str(chat_provider.calls[-1][0])
        self.assertNotIn("Pasted", prompt)

    def test_server_exposes_laboratory_history_reset_button_and_endpoint(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("clearLabHistoryBtn", server)
        self.assertIn("/api/laboratory/reset", server)

from tests.helpers import attach_legacy_tests

attach_legacy_tests(NotesMetricsTests, (
    "test_note_command_understands_natural_document_notes_phrase",
    "test_note_command_understands_take_note_language",
    "test_note_request_without_content_does_not_reach_llm",
    "test_notes_get_compact_labels_and_can_be_renamed",
    "test_notes_persist_by_document_and_chunk",
    "test_notes_update_delete_and_chat_command",
    "test_voice_metrics_are_persisted",
    "test_voice_metrics_group_by_document_and_chunk",
    "test_voice_metrics_summary_groups_by_provider",
))
