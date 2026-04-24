import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
import zipfile
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from fusion_reader_v2 import (
    AudioArtifact,
    AudioCache,
    AllTalkProvider,
    AutoExternalResearchBridge,
    AutoSTTProvider,
    ConversationCore,
    ExternalResearchResult,
    FasterWhisperServerSTTProvider,
    FusionReaderV2,
    NullChatProvider,
    NullExternalResearchBridge,
    NullSTTProvider,
    NullTTSProvider,
    OllamaChatProvider,
    OpenClawResearchBridge,
    SearxngResearchBridge,
    STTProvider,
    TranscriptResult,
    VoiceMetricsStore,
    WhisperCliSTTProvider,
    ReaderNotesStore,
    import_document_bytes,
    split_text,
)
from fusion_reader_v2.dialogue import is_hallucinated_transcript
from fusion_reader_v2.documents import clean_heading, repair_ocr_spacing, structured_plain_ocr_text


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


class FusionReaderV2Tests(unittest.TestCase):
    def test_openclaw_bridge_defaults_to_fusion_research_agent(self):
        bridge = OpenClawResearchBridge(command="/bin/echo")
        self.assertEqual(bridge.agent, "fusion-research")

    def test_searxng_bridge_parses_results(self):
        bridge = SearxngResearchBridge(base_url="http://127.0.0.1:8080", timeout_seconds=2)
        payload = {
            "results": [
                {
                    "title": "Plato on Friendship and Eros - Stanford Encyclopedia of Philosophy",
                    "url": "https://plato.stanford.edu/entries/plato-friendship/",
                    "content": "Plato discusses love and friendship primarily in the Symposium and the Lysis.",
                }
            ]
        }
        with mock.patch("fusion_reader_v2.local_web_bridge.urlopen", return_value=FakeUrlOpenResponse(json.dumps(payload))):
            result = bridge.research("busca en internet eros en platon")
        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "searxng")
        self.assertEqual(result.model, "searxng-local")
        self.assertTrue(result.sources)
        self.assertEqual(result.sources[0]["url"], "https://plato.stanford.edu/entries/plato-friendship/")
        self.assertIn("Fuentes:", result.answer)
        self.assertIn("https://plato.stanford.edu/entries/plato-friendship/", result.answer)
        self.assertNotIn("https://", result.spoken_answer)

    def test_searxng_bridge_handles_no_results(self):
        bridge = SearxngResearchBridge(base_url="http://127.0.0.1:8080", timeout_seconds=2)
        with mock.patch("fusion_reader_v2.local_web_bridge.urlopen", return_value=FakeUrlOpenResponse(json.dumps({"results": []}))):
            result = bridge.research("busca papers imposibles")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "searxng_no_results")
        self.assertIn("No encontré resultados útiles", result.answer)

    def test_searxng_bridge_handles_timeout(self):
        bridge = SearxngResearchBridge(base_url="http://127.0.0.1:8080", timeout_seconds=2)
        with mock.patch("fusion_reader_v2.local_web_bridge.urlopen", side_effect=socket.timeout("timeout")):
            result = bridge.research("busca tesis sobre diotima")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "searxng_timeout")
        self.assertIn("SearXNG local", result.answer)

    def test_auto_external_research_prefers_searxng_without_calling_openclaw(self):
        searxng = FakeExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="SearXNG respondió.",
                spoken_answer="SearXNG respondió.",
                detail="external_research_ok",
                provider="searxng",
                model="searxng-local",
            )
        )
        openclaw = FakeExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="OpenClaw respondió.",
                spoken_answer="OpenClaw respondió.",
                detail="external_research_ok",
                provider="openclaw_bridge",
                model="gemini-2.5-flash",
            )
        )
        bridge = AutoExternalResearchBridge(searxng=searxng, openclaw=openclaw)
        result = bridge.research("busca en internet una fuente sobre eros")
        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "searxng")
        self.assertEqual(len(searxng.calls), 1)
        self.assertEqual(len(openclaw.calls), 0)

    def test_auto_external_research_falls_back_to_openclaw_when_searxng_is_unavailable(self):
        searxng = FakeExternalResearchBridge(
            ExternalResearchResult(
                False,
                answer="SearXNG caído.",
                spoken_answer="SearXNG caído.",
                detail="searxng_unavailable",
                provider="searxng",
                model="searxng-local",
            )
        )
        openclaw = FakeExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="OpenClaw respondió.",
                spoken_answer="OpenClaw respondió.",
                detail="external_research_ok",
                provider="openclaw_bridge",
                model="gemini-2.5-flash",
            )
        )
        bridge = AutoExternalResearchBridge(searxng=searxng, openclaw=openclaw)
        result = bridge.research("busca papers sobre Diotima")
        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "openclaw_bridge")
        self.assertEqual(len(searxng.calls), 1)
        self.assertEqual(len(openclaw.calls), 1)

    def test_openclaw_bridge_humanizes_rate_limit_failures(self):
        bridge = OpenClawResearchBridge(command="/bin/echo", timeout_seconds=3)
        payload = {
            "status": "ok",
            "result": {
                "stopReason": "error",
                "meta": {"agentMeta": {"provider": "google", "model": "gemini-2.5-flash"}},
                "payloads": [{"text": "⚠️ API rate limit reached. Please try again later. (429 quota)"}],
            },
        }
        with mock.patch("fusion_reader_v2.openclaw_bridge.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(["openclaw"], 0, stdout=json.dumps(payload), stderr="")
            result = bridge.research("Buscá tesis sobre Fedro.")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "bridge_rate_limit")
        self.assertIn("OpenClaw/Gemini", result.answer)
        self.assertIn("rate limit", result.answer.lower())
        self.assertIn("--agent", run.call_args.args[0])
        self.assertIn("fusion-research", run.call_args.args[0])

    def test_openclaw_bridge_retries_after_gateway_restart(self):
        bridge = OpenClawResearchBridge(command="/bin/echo", timeout_seconds=3)
        restart_payload = {
            "status": "ok",
            "result": {
                "stopReason": "error",
                "meta": {"agentMeta": {"provider": "google", "model": "gemini-2.5-flash"}},
                "payloads": [{"text": "gateway closed (1012): service restart"}],
            },
        }
        success_payload = {
            "status": "ok",
            "result": {
                "stopReason": "completed",
                "meta": {"agentMeta": {"provider": "google", "model": "gemini-2.5-flash"}},
                "payloads": [
                    {
                        "text": json.dumps(
                            {
                                "ok": True,
                                "query": "Fedro en El banquete",
                                "summary": "Encontré una tesis relevante.",
                                "findings": ["Una tesis doctoral lo presenta como apertura elogiosa del eros."],
                                "sources": [{"title": "Universidad X", "url": "https://ejemplo.test/tesis", "note": "tesis doctoral"}],
                                "suggested_followup": "",
                                "error": "",
                            }
                        )
                    }
                ],
            },
        }
        with mock.patch("fusion_reader_v2.openclaw_bridge.subprocess.run") as run, mock.patch("fusion_reader_v2.openclaw_bridge.time.sleep") as sleep:
            run.side_effect = [
                subprocess.CompletedProcess(["openclaw"], 0, stdout=json.dumps(restart_payload), stderr=""),
                subprocess.CompletedProcess(["openclaw"], 0, stdout=json.dumps(success_payload), stderr=""),
            ]
            result = bridge.research("Buscá tesis sobre Fedro.")
        self.assertTrue(result.ok)
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once()
        self.assertIn("Encontré una tesis relevante.", result.answer)

    def test_split_text_keeps_short_paragraphs(self):
        chunks = split_text("Uno.\n\nDos.")
        self.assertEqual(chunks, ["Uno.", "Dos."])

    def test_split_text_breaks_long_sentence_for_faster_tts(self):
        text = " ".join(f"palabra{i}" for i in range(120))
        chunks = split_text(text, max_chars=120)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))

    def test_default_chunks_keep_a_natural_voice_size(self):
        chunks = split_text(" ".join(["texto"] * 100))
        self.assertTrue(all(len(chunk) <= 420 for chunk in chunks))

    def test_split_text_skips_pdf_zero_noise(self):
        chunks = split_text("Uno.\n\n0\n\nDos.")
        self.assertEqual(chunks, ["Uno.", "Dos."])

    def test_reader_load_and_navigation(self):
        app = test_app()
        app.load_text("doc", "Doc", "Uno.\n\nDos.\n\nTres.")
        self.assertEqual(app.status()["current"], 1)
        self.assertEqual(app.next()["text"], "Dos.")
        self.assertEqual(app.previous()["text"], "Uno.")
        self.assertEqual(app.jump(3)["text"], "Tres.")

    def test_voice_cache_reuses_audio(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        first = app.test_voice("Hola mundo")
        second = app.test_voice("Hola mundo")
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(provider.calls), 1)

    def test_default_voice_is_lucy_female_03_and_can_be_overridden(self):
        previous = os.environ.get("FUSION_READER_VOICE")
        try:
            os.environ.pop("FUSION_READER_VOICE", None)
            self.assertEqual(test_app().voice.voice, "female_03.wav")
            self.assertEqual(AllTalkProvider(base_url="http://example.invalid").default_voice, "female_03.wav")
            os.environ["FUSION_READER_VOICE"] = "female_06.wav"
            self.assertEqual(test_app().voice.voice, "female_06.wav")
            self.assertEqual(AllTalkProvider(base_url="http://example.invalid").default_voice, "female_06.wav")
        finally:
            if previous is None:
                os.environ.pop("FUSION_READER_VOICE", None)
            else:
                os.environ["FUSION_READER_VOICE"] = previous

    def test_alltalk_audio_url_uses_configured_local_port(self):
        provider = AllTalkProvider(base_url="http://127.0.0.1:7899")
        self.assertEqual(
            provider._audio_url("http://127.0.0.1:7851/outputs/fusion.wav"),
            "http://127.0.0.1:7899/outputs/fusion.wav",
        )

    def test_alltalk_default_uses_fusion_gpu_port_and_requires_owner(self):
        keys = [
            "FUSION_READER_ALLTALK_URL",
            "FUSION_READER_GPU_TTS_PORT",
            "FUSION_READER_REQUIRE_TTS_OWNER",
            "FUSION_READER_TTS_OWNER_FILE",
        ]
        previous = {key: os.environ.get(key) for key in keys}
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.environ.pop("FUSION_READER_ALLTALK_URL", None)
                os.environ["FUSION_READER_GPU_TTS_PORT"] = "7853"
                os.environ["FUSION_READER_REQUIRE_TTS_OWNER"] = "1"
                os.environ["FUSION_READER_TTS_OWNER_FILE"] = str(Path(tmp) / "missing_owner.json")
                provider = AllTalkProvider()
                self.assertEqual(provider.base_url, "http://127.0.0.1:7853")
                health = provider.health()
                self.assertFalse(health["ok"])
                self.assertIn("tts_owner_missing", health["detail"])
                artifact = provider.synthesize("Hola")
                self.assertFalse(artifact.ok)
                self.assertIn("tts_owner_missing", artifact.detail)
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_alltalk_rejects_doctora_and_historic_ports_even_when_configured(self):
        keys = ["FUSION_READER_REQUIRE_TTS_OWNER", "LUCY_TTS_PORT"]
        previous = {key: os.environ.get(key) for key in keys}
        try:
            os.environ["FUSION_READER_REQUIRE_TTS_OWNER"] = "0"
            os.environ["LUCY_TTS_PORT"] = "7854"
            cases = [
                ("http://127.0.0.1:7854", "tts_foreign_doctora_lucy_port"),
                ("http://127.0.0.1:7852", "tts_historic_unassigned_port"),
            ]
            for url, detail in cases:
                provider = AllTalkProvider(base_url=url)
                health = provider.health()
                self.assertFalse(health["ok"])
                self.assertIn(detail, health["detail"])
                self.assertEqual(provider.voices(), [])
                artifact = provider.synthesize("Hola")
                self.assertFalse(artifact.ok)
                self.assertIn(detail, artifact.detail)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_fusion_launchers_do_not_auto_claim_antigravity_tts_port(self):
        root = Path(__file__).resolve().parents[1]
        launchers = [
            root / "scripts" / "start_fusion_reader_v2.sh",
            root / "scripts" / "open_fusion_reader.sh",
            root / "scripts" / "start_reader_neural_tts_gpu_5090.sh",
        ]
        for launcher in launchers:
            text = launcher.read_text(encoding="utf-8")
            self.assertNotIn("DIRECT_CHAT_ALLTALK_GPU_PORT", text)
            self.assertNotIn("127.0.0.1:7852", text)
        self.assertIn("FUSION_READER_GPU_TTS_PORT:-7853", launchers[0].read_text(encoding="utf-8"))

    def test_fusion_launchers_require_owned_gpu_tts(self):
        root = Path(__file__).resolve().parents[1]
        for rel in (
            "scripts/start_reader_neural_tts_gpu_5090.sh",
            "scripts/start_fusion_reader_v2.sh",
            "scripts/open_fusion_reader.sh",
        ):
            text = (root / rel).read_text(encoding="utf-8")
            self.assertIn("FUSION_READER_TTS_OWNER_FILE", text)
            self.assertIn('"owner"[[:space:]]*:[[:space:]]*"fusion_reader_v2"', text)
            self.assertIn("owner_pid", text)
            self.assertNotIn('if [[ -z "$owner_pid" ]]; then\n    return 0', text)

    def test_fusion_launcher_waits_for_owned_gpu_tts_before_cpu_fallback(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "start_fusion_reader_v2.sh").read_text(encoding="utf-8")
        self.assertIn('FUSION_READER_GPU_TTS_WAIT_SECONDS', text)
        self.assertIn('fusion_gpu_ready()', text)
        self.assertIn('while (( $(date +%s) < gpu_wait_deadline )); do', text)
        self.assertIn('sleep 1', text)
        self.assertIn('owner valido', text)
        self.assertNotIn("127.0.0.1:7852", text)

    def test_voice_port_isolation_verifier_covers_doctora_memory_sources(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "verify_voice_port_isolation.sh").read_text(encoding="utf-8")
        self.assertIn("n8n_data/boveda_lucy.sqlite", text)
        self.assertIn("data/lucy_bunker_log.jsonl", text)
        self.assertIn("Taverna-legacy/alltalk_tts", text)
        self.assertIn("VOICE_RELEVANT_PATTERN", text)
        self.assertIn("latest_relevant_doctora_boveda", text)
        self.assertIn("latest_relevant_doctora_bunker", text)
        self.assertIn("no relevant Doctora voice/TTS entry found", text)
        self.assertIn('"puerto_fusion":7852', text)
        self.assertIn('"alltalk_port":7851', text)
        self.assertIn("--port $HISTORIC_PORT", text)
        self.assertIn("latest relevant Doctora boveda entry", text)
        self.assertIn("latest relevant Doctora bunker entry", text)

    def test_read_current_prefetches_next(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", "Uno.\n\nDos.")
        out = app.read_current(play=False)
        self.assertTrue(out["ok"])
        self.assertIn("ready_ms", out)
        self.assertIn("synthesis_ms", out)
        status = app.status()
        self.assertEqual(status["prefetch_index"], 1)

    def test_read_current_reuses_current_prefetch(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", "Uno.")
        out = app.read_current(play=False)
        self.assertTrue(out["ok"])
        self.assertEqual(len(provider.calls), 1)

    def test_prepare_document_caches_all_chunks_in_background(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", "Uno.\n\nDos.\n\nTres.", prefetch=False)
        started = app.prepare_document()
        self.assertEqual(started["status"], "running")
        for _ in range(50):
            status = app.prepare_status()
            if status["status"] == "done":
                break
            time.sleep(0.01)
        self.assertEqual(app.prepare_status()["status"], "done")
        self.assertEqual(app.prepare_status()["generated"], 3)
        before = len(provider.calls)
        out = app.read_current(play=False)
        self.assertTrue(out["ok"])
        self.assertTrue(out["cached"])
        self.assertEqual(len(provider.calls), before)

    def test_load_text_resets_previous_prepare_status(self):
        app = test_app()
        app.load_text("doc", "Doc", "Uno.", prefetch=False)
        app.prepare_document()
        for _ in range(50):
            if app.prepare_status()["status"] == "done":
                break
            time.sleep(0.01)
        self.assertEqual(app.prepare_status()["status"], "done")
        app.load_text("new", "Nuevo", "Dos.", prefetch=False)
        self.assertEqual(app.prepare_status()["status"], "idle")
        self.assertEqual(app.prepare_status()["doc_id"], "")

    def test_reference_documents_can_be_added_without_replacing_main(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.\n\nSegundo bloque.", prefetch=False)
        out = app.add_reference_text("ref", "Consulta", "Texto de consulta.\n\nOtro dato.")
        self.assertTrue(out["ok"])
        status = app.status()
        self.assertEqual(status["doc_id"], "doc")
        self.assertEqual(status["main_document"]["title"], "Principal")
        self.assertEqual(len(status["reference_documents"]), 1)
        self.assertEqual(status["reference_documents"][0]["doc_id"], "ref")

    def test_promote_reference_swaps_main_and_previous_main_becomes_reference(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.add_reference_text("ref", "Consulta", "Texto de consulta.")
        out = app.promote_reference_document("ref", prefetch=False)
        self.assertTrue(out["ok"])
        status = app.status()
        self.assertEqual(status["doc_id"], "ref")
        self.assertEqual(status["title"], "Consulta")
        refs = status["reference_documents"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["doc_id"], "doc")

    def test_restart_restores_reference_documents(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.add_reference_text("ref", "Consulta", "Texto de consulta.")
        reopened = test_app(root=root)
        status = reopened.status()
        self.assertEqual(status["doc_id"], "doc")
        self.assertEqual(len(status["reference_documents"]), 1)
        self.assertEqual(status["reference_documents"][0]["title"], "Consulta")

    def test_reasoning_mode_defaults_to_thinking_when_env_is_not_forcing_normal(self):
        previous_mode = os.environ.get("FUSION_READER_REASONING_MODE")
        previous_think = os.environ.get("FUSION_READER_CHAT_THINK")
        try:
            os.environ.pop("FUSION_READER_REASONING_MODE", None)
            os.environ.pop("FUSION_READER_CHAT_THINK", None)
            app = test_app()
            self.assertEqual(app.reasoning_status()["mode"], "thinking")
        finally:
            if previous_mode is None:
                os.environ.pop("FUSION_READER_REASONING_MODE", None)
            else:
                os.environ["FUSION_READER_REASONING_MODE"] = previous_mode
            if previous_think is None:
                os.environ.pop("FUSION_READER_CHAT_THINK", None)
            else:
                os.environ["FUSION_READER_CHAT_THINK"] = previous_think

    def test_reasoning_mode_switch_persists_across_restart(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        changed = app.set_reasoning_mode("supreme")
        self.assertEqual(changed["mode"], "supreme")
        reopened = test_app(root=root)
        self.assertEqual(reopened.reasoning_status()["mode"], "supreme")

    def test_chat_uses_selected_reasoning_mode_settings(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("normal")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.chat("¿Qué ves?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["reasoning_mode"], "normal")
        self.assertEqual(out["reasoning_passes"], 1)
        self.assertEqual(len(chat_provider.calls), 1)
        self.assertFalse(chat_provider.calls[0][2]["think"])

    def test_normal_mode_chat_prompt_includes_lucy_persona(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("normal")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("¿Qué ves?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Lucy Cunningham", prompt)
        self.assertIn("companera humana de lectura", prompt)
        self.assertIn("problematizar", prompt)
        self.assertIn("No digas que te llamas Fusion", prompt)
        self.assertIn("identidad tiene prioridad", prompt)

    def test_normal_mode_dialogue_prompt_includes_lucy_persona(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("normal")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.dialogue_turn_text("¿Qué opinás del bloque?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Lucy Cunningham", prompt)
        self.assertIn("pensando juntos", prompt)
        self.assertIn("Borges", prompt)
        self.assertIn("No digas que te llamas Fusion", prompt)

    def test_thinking_mode_chat_prompt_includes_lucy_persona(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("thinking")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("¿Qué ves?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Lucy Cunningham", prompt)
        self.assertIn("filosofico-tecnica", prompt)
        self.assertIn("genealogia conceptual", prompt)
        self.assertIn("Priorizas validez", prompt)
        self.assertIn("No digas que te llamas Fusion", prompt)

    def test_thinking_mode_dialogue_prompt_includes_lucy_persona(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("thinking")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.dialogue_turn_text("¿Qué opinás del bloque?")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Lucy Cunningham", prompt)
        self.assertIn("sobria, exigente y filosofico-tecnica", prompt)
        self.assertIn("validez", prompt)
        self.assertIn("Borges", prompt)

    def test_free_laboratory_mode_chat_prompt_is_not_forced_back_to_text(self):
        chat_provider = NullChatProvider("Entendido.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_laboratory_mode("free")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("Hablemos de física cuántica.")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Estas en modo libre", prompt)
        self.assertIn("No estas obligada a responder solo sobre el texto", prompt)
        self.assertEqual(app.laboratory_mode_status()["mode"], "free")

    def test_supreme_mode_chat_prompt_reuses_thinking_lucy_persona(self):
        chat_provider = NullChatProvider("Respuesta final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        app.chat("¿Qué ves?")
        draft_prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        final_prompt = "\n".join(item["content"] for item in chat_provider.calls[-1][0])
        self.assertIn("Lucy Cunningham", draft_prompt)
        self.assertIn("Pensamiento supremo", draft_prompt)
        self.assertIn("filosofico-tecnica", draft_prompt)
        self.assertIn("No digas que te llamas Fusion", draft_prompt)
        self.assertIn("Lucy Cunningham", final_prompt)
        self.assertIn("Pensamiento supremo", final_prompt)
        self.assertIn("filosofico-tecnica", final_prompt)

    def test_supreme_reasoning_runs_three_passes(self):
        chat_provider = NullChatProvider("Respuesta final.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.load_text("doc", "Doc", "Pantalla actual.\n\nOtro contexto.", prefetch=False)
        out = app.chat("Pensá este fragmento con profundidad.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["reasoning_mode"], "supreme")
        self.assertEqual(out["reasoning_passes"], 3)
        self.assertEqual(len(chat_provider.calls), 3)
        self.assertTrue(all(call[2]["think"] for call in chat_provider.calls))
        self.assertIn("REVISION INTERNA", chat_provider.calls[-1][0][1]["content"])

    def test_dialogue_degrades_supreme_to_thinking_by_default(self):
        chat_provider = NullChatProvider("Entendido.")
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        app.conversation = ConversationCore(chat_provider)
        app.set_reasoning_mode("supreme")
        app.load_text("doc", "Doc", "Pantalla actual.\n\nOtro contexto.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué opinás del bloque?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["reasoning_mode_requested"], "supreme")
        self.assertEqual(out["reasoning_mode_applied"], "thinking")
        self.assertTrue(out["reasoning_degraded"])
        self.assertEqual(out["reasoning_mode"], "thinking")
        self.assertEqual(out["reasoning_passes"], 1)
        self.assertEqual(len(chat_provider.calls), 1)
        self.assertTrue(chat_provider.calls[0][2]["think"])
        trace_path = root / "dialogue_trace.jsonl"
        self.assertTrue(trace_path.exists())
        logged = trace_path.read_text(encoding="utf-8")
        self.assertIn('"event": "dialogue_turn_text"', logged)
        self.assertIn('"requested_mode": "supreme"', logged)
        self.assertIn('"applied_mode": "thinking"', logged)

    def test_dialogue_turn_text_answers_with_audio_without_touching_reader_tts_path(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.fast_dialogue_ack = False
        app.load_text("doc", "Doc", "Pantalla actual.\n\nOtro contexto.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué opinás del bloque?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["transcript"], "¿Qué opinás del bloque?")
        self.assertEqual(out["answer"], "Entendido.")
        self.assertTrue(out["audio"])
        self.assertEqual(out["stt_ms"], 0)
        self.assertEqual(app.dialogue_status()["turns"], 2)

    def test_dialogue_turn_text_fast_ack_skips_tts(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.fast_dialogue_ack = True
        app.load_text("doc", "Doc", "Pantalla actual.\n\nOtro contexto.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué opinás del bloque?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["answer"], "Entendido.")
        self.assertEqual(out["provider"], "text_ack")
        self.assertEqual(out["tts_ms"], 0)
        self.assertEqual(out["audio"], "")
        self.assertEqual(provider.calls, [])

    def test_dialogue_stop_command_does_not_answer_again(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("detente")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_control")
        self.assertEqual(out["answer"], "")
        self.assertEqual(chat_provider.calls, [])

    def test_dialogue_turn_audio_uses_stt_provider(self):
        provider = NullTTSProvider()
        stt = NullSTTProvider("Hola laboratorio.")
        app = test_app(tts=provider, stt=stt)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        out = app.dialogue_turn_audio(audio, mime="audio/webm")
        self.assertTrue(out["ok"])
        self.assertEqual(out["transcript"], "Hola laboratorio.")
        self.assertEqual(out["stt_provider"], "null_stt")
        self.assertIn("trace", out)
        self.assertIn("stt_wall_ms", out["trace"])
        self.assertIn("server_total_ms", out["trace"])

    def test_dialogue_empty_transcript_is_recoverable(self):
        app = test_app(stt=EmptyTranscriptSTTProvider())
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        out = app.dialogue_turn_audio(audio, mime="audio/webm")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_stt")
        self.assertEqual(out["detail"], "empty_transcript")
        self.assertIn("No alcancé", out["answer"])
        self.assertEqual(out["provider"], "null")
        self.assertTrue(out["audio"])

    def test_dialogue_stt_failure_returns_human_answer_instead_of_silence(self):
        app = test_app(stt=BrokenSTTProvider())
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        out = app.dialogue_turn_audio(audio, mime="audio/webm")
        self.assertTrue(out["ok"])
        self.assertEqual(out["error"], "transcription_failed")
        self.assertEqual(out["failed_stage"], "stt")
        self.assertEqual(out["stt_provider"], "broken_stt")
        self.assertIn("No pude entender bien el audio", out["answer"])
        self.assertTrue("audio" in out)

    def test_dialogue_chat_failure_returns_human_answer_and_trace(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        app.conversation = ConversationCore(FailingChatProvider())
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué opinás del bloque?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["failed_stage"], "chat")
        self.assertIn("Se cayó el diálogo local", out["answer"])
        self.assertIn("chat_ms", out["trace"])
        logged = (root / "dialogue_trace.jsonl").read_text(encoding="utf-8")
        self.assertIn('"human_error": "Se cay', logged)
        self.assertIn('"chat_provider": "ollama"', logged)

    def test_dialogue_turn_text_keeps_text_when_tts_fails(self):
        app = test_app(tts=FailingTTSProvider())
        app.load_text("doc", "Doc", "Pantalla actual.\n\nOtro contexto.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué opinás del bloque?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["answer"], "Entendido.")
        self.assertFalse(out["voice_ok"])
        self.assertEqual(out["audio"], "")
        self.assertFalse(out["audio_available"])

    def test_dialogue_turn_text_defaults_to_neural_voice_not_browser_ack(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", "Pantalla actual.\n\nOtro contexto.", prefetch=False)
        out = app.dialogue_turn_text("¿Qué opinás del bloque?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["provider"], "null")
        self.assertTrue(out["audio"])
        self.assertEqual(len(provider.calls), 1)

    def test_dialogue_note_command_defaults_to_neural_voice_not_browser_ack(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("tomá nota de detalle hablado")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["provider"], "null")
        self.assertTrue(out["audio"])
        self.assertEqual(len(provider.calls), 1)

    def test_stt_filters_common_outro_hallucinations(self):
        self.assertTrue(is_hallucinated_transcript("¡Suscríbete!"))
        self.assertTrue(is_hallucinated_transcript("Suscríbete al canal"))
        self.assertTrue(is_hallucinated_transcript("Subtítulos realizados por la comunidad de Amara.org"))
        self.assertTrue(is_hallucinated_transcript("¡Giraff!"))
        self.assertFalse(is_hallucinated_transcript("quiero hacer una nota sobre la palabra suscríbete en el texto"))

    def test_dialogue_hallucinated_transcript_is_ignored_before_chat(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app(stt=HallucinatedTranscriptSTTProvider())
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        out = app.dialogue_turn_audio(audio, mime="audio/webm")
        self.assertTrue(out["ok"])
        self.assertTrue(out["ignored"])
        self.assertEqual(out["model"], "reader_stt")
        self.assertEqual(out["detail"], "hallucinated_transcript")
        self.assertEqual(out["answer"], "")
        self.assertEqual(out["tts_ms"], 0)
        self.assertEqual(chat_provider.calls, [])

    def test_notes_persist_by_document_and_chunk(self):
        root = Path(tempfile.mkdtemp())
        app = test_app(root=root)
        app.load_text("doc", "Doc", "Uno.\n\nDos.", prefetch=False)
        created = app.create_note("Primera nota")
        self.assertTrue(created["ok"])
        self.assertEqual(created["note"]["chunk_number"], 1)
        app.next()
        app.create_note("Segunda nota")
        reopened = test_app(root=root)
        self.assertEqual([item["text"] for item in reopened.list_notes(doc_id="doc")["items"]], ["Primera nota", "Segunda nota"])
        current_notes = app.list_notes(current_only=True)["items"]
        self.assertEqual(len(current_notes), 1)
        self.assertEqual(current_notes[0]["text"], "Segunda nota")

    def test_restart_restores_last_document_cursor_and_notes(self):
        root = Path(tempfile.mkdtemp())
        imported = root / "imported.txt"
        imported.write_text("Uno.\n\nDos.\n\nTres.", encoding="utf-8")
        app = test_app(root=root)
        app.load_file(imported, prefetch=False)
        app.next()
        created = app.create_note("Nota persistente")
        self.assertTrue(created["ok"])
        reopened = test_app(root=root)
        restored = reopened.status()
        self.assertEqual(restored["doc_id"], "imported")
        self.assertEqual(restored["current"], 2)
        notes = reopened.list_notes(current_only=True)["items"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["text"], "Nota persistente")

    def test_notes_update_delete_and_chat_command(self):
        app = test_app()
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.chat("guardá esto como nota: revisar esta idea")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        note = out["note"]
        self.assertEqual(note["text"], "revisar esta idea")
        updated = app.update_note(note["note_id"], "idea editada")
        self.assertTrue(updated["ok"])
        self.assertEqual(updated["note"]["text"], "idea editada")
        deleted = app.delete_note(note["note_id"])
        self.assertTrue(deleted["deleted"])
        self.assertEqual(app.list_notes()["items"], [])

    def test_chat_note_without_document_becomes_laboratory_note(self):
        app = test_app()
        self.assertTrue(app.chat("hola")["ok"])
        out = app.chat("estamos haciendo pruebas, guarda una nota de nuestro saludo")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["answer"], "Nota guardada como L1.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")
        self.assertEqual(out["note"]["anchor_number"], 1)
        self.assertEqual(out["note"]["doc_id"], "__laboratory__")
        notes = app.list_notes(doc_id="__laboratory__")["items"]
        self.assertEqual(len(notes), 1)
        self.assertIn("hola", notes[0]["quote"].lower())

    def test_chat_laboratory_reference_uses_l_note_even_with_document_loaded(self):
        app = test_app()
        app.load_text("doc", "Doc", "Texto del documento.", prefetch=False)
        self.assertTrue(app.chat("hola")["ok"])
        out = app.chat("estamos haciendo pruebas, guarda una nota de nuestro saludo")
        self.assertTrue(out["ok"])
        self.assertEqual(out["answer"], "Nota guardada como L1.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")
        self.assertEqual(app.list_notes()["items"], [])
        self.assertEqual(len(app.list_notes(doc_id="__laboratory__")["items"]), 1)

    def test_dialogue_reference_to_recent_reply_becomes_laboratory_note(self):
        app = test_app()
        app.load_text("doc", "Doc", "Texto del documento.", prefetch=False)
        first = app.dialogue_turn_text("¿Me escuchás?")
        self.assertTrue(first["ok"])
        out = app.dialogue_turn_text("Tomá nota de esto que acabás de decir.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["answer"], "Listo, guardé esa nota como L1.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")
        self.assertIn("Entendido", out["note"]["quote"])
        self.assertEqual(app.list_notes()["items"], [])
        self.assertEqual(len(app.list_notes(doc_id="__laboratory__")["items"]), 1)

    def test_dialogue_stt_like_recent_speech_note_becomes_laboratory_note(self):
        app = test_app()
        app.load_text("doc", "Doc", "Texto del documento.", prefetch=False)
        first = app.dialogue_turn_text("¿Me he escuchado?")
        self.assertTrue(first["ok"])
        out = app.dialogue_turn_text("Tomando a esto que acabo de decir. Tomando a lo que acabo de decir.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["answer"], "Listo, guardé esa nota como L1.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")
        self.assertEqual(out["note"]["text"], "Entendido.")
        self.assertEqual(app.list_notes()["items"], [])
        self.assertEqual(len(app.list_notes(doc_id="__laboratory__")["items"]), 1)

    def test_dialogue_generic_eso_note_routes_to_laboratory(self):
        app = test_app()
        app.load_text("doc", "Doc", "Texto del documento.", prefetch=False)
        first = app.dialogue_turn_text("¿Me escuchás?")
        self.assertTrue(first["ok"])
        out = app.dialogue_turn_text("sí, tome nota de eso")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["answer"], "Listo, guardé esa nota como L1.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")
        self.assertEqual(out["note"]["text"], "Entendido.")
        self.assertEqual(app.list_notes()["items"], [])
        self.assertEqual(len(app.list_notes(doc_id="__laboratory__")["items"]), 1)

    def test_dialogue_short_stt_artifact_note_uses_recent_laboratory_content(self):
        app = test_app()
        app.load_text("doc", "Doc", "Texto del documento.", prefetch=False)
        first = app.dialogue_turn_text("¿Me escuchás?")
        self.assertTrue(first["ok"])
        out = app.dialogue_turn_text("Toma nota D.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["answer"], "Listo, guardé esa nota como L1.")
        self.assertEqual(out["note"]["source_kind"], "laboratory")
        self.assertEqual(out["note"]["text"], "Entendido.")
        self.assertEqual(app.list_notes()["items"], [])
        self.assertEqual(len(app.list_notes(doc_id="__laboratory__")["items"]), 1)

    def test_notes_get_compact_labels_and_can_be_renamed(self):
        app = test_app()
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        created = app.create_note("la transformación del lenguaje humano")
        self.assertTrue(created["ok"])
        note = created["note"]
        self.assertEqual(note["label"], "transformación lenguaje humano")
        renamed = app.rename_note(note["note_id"], "lenguaje IA")
        self.assertTrue(renamed["ok"])
        self.assertEqual(renamed["note"]["label"], "lenguaje IA")
        updated = app.update_note(note["note_id"], "otro texto diferente")
        self.assertTrue(updated["ok"])
        self.assertEqual(updated["note"]["label"], "lenguaje IA")

    def test_note_command_understands_take_note_language(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.chat("necesitaría que tomes nota del giro estadístico del logos")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "giro estadístico del logos")
        self.assertEqual(chat_provider.calls, [])

    def test_note_command_understands_natural_document_notes_phrase(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.chat("Hola, ¿puedes tomar notas en notas del documento que vamos a hablar de la transformación del lenguaje humano en el bloque 3?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "la transformación del lenguaje humano")
        self.assertEqual(chat_provider.calls, [])

    def test_note_request_without_content_does_not_reach_llm(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.chat("¿puedes tomar notas?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["detail"], "missing_note_text")
        self.assertEqual(chat_provider.calls, [])

    def test_dialogue_note_command_answers_with_audio(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.fast_note_ack = False
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("guardá esto como nota: detalle hablado")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "detalle hablado")
        self.assertIn("guardé", out["answer"])
        self.assertTrue(out["audio"])

    def test_dialogue_note_command_succeeds_even_when_tts_fails(self):
        app = test_app(tts=FailingTTSProvider())
        app.fast_note_ack = False
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("tomá nota de detalle sin voz")
        self.assertTrue(out["ok"])
        self.assertFalse(out["voice_ok"])
        self.assertEqual(out["note"]["text"], "detalle sin voz")

    def test_dialogue_note_command_fast_ack_skips_tts(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.fast_note_ack = True
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("tomá nota de detalle rápido")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["provider"], "text_ack")
        self.assertEqual(out["tts_ms"], 0)
        self.assertEqual(provider.calls, [])
        self.assertIn("trace", out)
        self.assertIn("note_ms", out["trace"])

    def test_dialogue_note_command_allows_intro_and_stt_variant(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("Estamos en una prueba, así que tomad nota de esta defensa reconoce la inquietud filosófica para hablarlo después.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "esta defensa reconoce la inquietud filosófica para hablarlo después")
        self.assertEqual(chat_provider.calls, [])

    def test_dialogue_note_command_understands_save_the_note_phrase(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Idea 2: Ontología del lenguaje como cálculo probabilístico.", prefetch=False)
        out = app.dialogue_turn_text("me puedes guardar la nota de ontología del lenguaje como cálculo probabilístico para después")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["chunk_number"], 1)
        self.assertEqual(out["note"]["text"], "ontología del lenguaje como cálculo probabilístico para después")
        self.assertEqual(chat_provider.calls, [])

    def test_dialogue_note_command_understands_followup_save_without_note_word(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        app.dialogue_turn_text("guardá esto como nota: primera idea")
        out = app.dialogue_turn_text("y guarda también la segunda idea de este mismo bloque")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "la segunda idea de este mismo bloque")
        self.assertEqual(len(app.list_notes()["items"]), 2)
        self.assertEqual(chat_provider.calls, [])

    def test_dialogue_note_command_understands_make_me_a_note(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.dialogue_turn_text("haceme una nota de adaptación al interlocutor")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "adaptación al interlocutor")
        self.assertEqual(chat_provider.calls, [])

    def test_dialogue_note_command_understands_leave_a_note(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.dialogue_turn_text("Deja una nota de que tenemos que volver a este bloque")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertEqual(out["note"]["text"], "tenemos que volver a este bloque")
        self.assertEqual(len(app.list_notes(current_only=True)["items"]), 1)
        self.assertEqual(chat_provider.calls, [])

    def test_dialogue_note_command_saves_previous_long_phrase(self):
        chat_provider = NullChatProvider("No deberia llegar al LLM.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque visible.", prefetch=False)
        out = app.dialogue_turn_text(
            "es ampliamente aceptada, pero un crítico podría materializar. "
            "No ocurre algo similar con los humanos en ciertas interacciones rutinarias o performáticas. "
            "Guarda eso en una nota."
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_notes")
        self.assertIn("No ocurre algo similar con los humanos", out["note"]["text"])
        self.assertEqual(chat_provider.calls, [])

    def test_dialogue_note_uses_visible_chunk_index_from_client(self):
        app = test_app()
        app.load_text("doc", "Doc", "Uno.\n\nDos.\n\nTres.", prefetch=False)
        app.jump(3)
        out = app.dialogue_turn_text("tomá nota de esto corresponde al bloque dos", chunk_index=1)
        self.assertTrue(out["ok"])
        self.assertEqual(out["note"]["chunk_number"], 2)
        self.assertEqual(app.session.status()["current"], 3)

    def test_auto_stt_falls_back_when_primary_is_unavailable(self):
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        stt = AutoSTTProvider(
            primary=FasterWhisperServerSTTProvider(base_url="http://127.0.0.1:9", timeout_seconds=0.01),
            fallback=NullSTTProvider("Fallback listo."),
        )
        out = stt.transcribe_file(audio, mime="audio/webm")
        self.assertTrue(out.ok)
        self.assertEqual(out.text, "Fallback listo.")
        self.assertEqual(out.provider, "null_stt")

    def test_auto_stt_falls_back_when_primary_returns_empty_transcript(self):
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        stt = AutoSTTProvider(primary=EmptyTranscriptSTTProvider(), fallback=NullSTTProvider("Recuperado por fallback."))
        out = stt.transcribe_file(audio, mime="audio/webm")
        self.assertTrue(out.ok)
        self.assertEqual(out.text, "Recuperado por fallback.")
        self.assertEqual(out.provider, "null_stt")

    def test_whisper_cli_fallback_uses_known_homebrew_path(self):
        command = Path("/home/linuxbrew/.linuxbrew/bin/whisper")
        if not command.exists():
            self.skipTest("Homebrew whisper command is not installed on this host")
        previous = os.environ.get("FUSION_READER_STT_COMMAND")
        try:
            os.environ.pop("FUSION_READER_STT_COMMAND", None)
            provider = WhisperCliSTTProvider()
            self.assertEqual(provider.command, str(command))
            self.assertTrue(provider.health()["ok"])
        finally:
            if previous is None:
                os.environ.pop("FUSION_READER_STT_COMMAND", None)
            else:
                os.environ["FUSION_READER_STT_COMMAND"] = previous

    def test_auto_stt_does_not_fallback_for_hallucinated_primary(self):
        root = Path(tempfile.mkdtemp())
        audio = root / "audio.webm"
        audio.write_bytes(b"fake audio")
        fallback = NullSTTProvider("Fallback no debe usarse.")
        stt = AutoSTTProvider(primary=HallucinatedTranscriptSTTProvider(), fallback=fallback)
        out = stt.transcribe_file(audio, mime="audio/webm")
        self.assertFalse(out.ok)
        self.assertEqual(out.detail, "hallucinated_transcript")
        self.assertEqual(out.provider, "hallucinated_stt")
        self.assertEqual(fallback.calls, [])

    def test_read_current_times_out_stale_prefetch(self):
        app = test_app()
        app.prefetch_wait_seconds = 0.001
        app.load_text("doc", "Doc", "Uno.", prefetch=False)
        stale = Future()
        with app._prefetch_lock:
            app._prefetch_index = 0
            app._prefetch_future = stale
        out = app.read_current(play=False)
        self.assertFalse(out["ok"])
        self.assertEqual(out["detail"], "prefetch_timeout")

    def test_voice_metrics_are_persisted(self):
        root = Path(tempfile.mkdtemp())
        metrics = VoiceMetricsStore(root / "voice_metrics.jsonl")
        app = FusionReaderV2(tts=NullTTSProvider(), cache=AudioCache(root / "audio_cache"), metrics=metrics)
        app.load_text("doc", "Doc", "Uno.")
        app.read_current(play=False)
        recent = app.recent_voice_metrics()["items"]
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["event"], "read")
        self.assertEqual(recent[0]["doc_id"], "doc")
        self.assertIn("ready_ms", recent[0])

    def test_voice_metrics_summary_groups_by_provider(self):
        root = Path(tempfile.mkdtemp())
        app = FusionReaderV2(
            tts=NullTTSProvider(),
            cache=AudioCache(root / "audio_cache"),
            metrics=VoiceMetricsStore(root / "voice_metrics.jsonl"),
        )
        app.load_text("doc", "Doc", "Uno.")
        app.read_current(play=False)
        summary = app.voice_metrics_summary()["items"]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["event"], "read")
        self.assertEqual(summary[0]["provider"], "null")
        self.assertEqual(summary[0]["count"], 1)
        self.assertIn("ready_ms_avg", summary[0])

    def test_voice_metrics_group_by_document_and_chunk(self):
        root = Path(tempfile.mkdtemp())
        app = FusionReaderV2(
            tts=NullTTSProvider(),
            cache=AudioCache(root / "audio_cache"),
            metrics=VoiceMetricsStore(root / "voice_metrics.jsonl"),
        )
        app.load_text("doc", "Doc", "Uno.\n\nDos.", prefetch=False)
        app.read_current(play=False)
        app.next()
        app.read_current(play=False)
        docs = app.voice_metrics_by_document()["items"]
        chunks = app.voice_metrics_by_chunk(doc_id="doc")["items"]
        self.assertEqual(docs[0]["doc_id"], "doc")
        self.assertEqual(docs[0]["count"], 2)
        self.assertEqual({item["current"] for item in chunks}, {1, 2})

    def test_chat_gets_visible_chunk_and_full_document_without_tts(self):
        provider = NullTTSProvider()
        chat_provider = NullChatProvider("Veo el texto actual.")
        root = Path(tempfile.mkdtemp())
        app = FusionReaderV2(
            tts=provider,
            cache=AudioCache(root / "audio_cache"),
            metrics=VoiceMetricsStore(root / "voice_metrics.jsonl"),
            conversation=ConversationCore(chat_provider),
        )
        app.load_text("doc", "Doc", "Pantalla actual.\n\nContexto posterior del documento.", prefetch=False)
        out = app.chat("¿Qué ves en pantalla?")
        self.assertTrue(out["ok"])
        self.assertEqual(out["answer"], "Veo el texto actual.")
        self.assertEqual(len(provider.calls), 0)
        messages = chat_provider.calls[0][0]
        prompt = "\n".join(item["content"] for item in messages)
        self.assertIn("TEXTO EN PANTALLA:", prompt)
        self.assertIn("Pantalla actual.", prompt)
        self.assertIn("DOCUMENTO COMPLETO DISPONIBLE:", prompt)
        self.assertIn("Contexto posterior del documento.", prompt)

    def test_chat_context_includes_reference_documents(self):
        chat_provider = NullChatProvider("Veo el apoyo.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Texto principal.\n\nContexto del principal.", prefetch=False)
        app.add_reference_text("ref", "Consulta", "Texto de consulta sobre el mismo tema.\n\nComparación complementaria.")
        out = app.chat("Compará el principal con la consulta.")
        self.assertTrue(out["ok"])
        messages = chat_provider.calls[0][0]
        prompt = "\n".join(item["content"] for item in messages)
        self.assertIn("DOCUMENTOS DE CONSULTA:", prompt)
        self.assertIn("Consulta", prompt)
        self.assertIn("Comparación complementaria", prompt)

    def test_chat_lists_all_reference_documents_even_if_first_is_long(self):
        chat_provider = NullChatProvider("Los veo.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        long_text = " ".join(["analisis"] * 600)
        app.add_reference_text("ref-1", "Análisis Filosófico", long_text)
        app.add_reference_text("ref-2", "desgrabaciones.docx", "Primera desgrabación.\n\nSegunda desgrabación.")
        out = app.chat("¿Ves los documentos de consulta?")
        self.assertTrue(out["ok"])
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("Análisis Filosófico", prompt)
        self.assertIn("desgrabaciones.docx", prompt)

    def test_dialogue_context_includes_reference_document_intro_chunks(self):
        chat_provider = NullChatProvider("Sí, lo veo.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Bloque principal uno.\n\nBloque principal dos.", prefetch=False)
        app.add_reference_text(
            "desgrabaciones",
            "desgrabaciones.docx",
            "Primera línea de desgrabaciones.\n\nSegunda línea importante del documento.\n\nTercera línea.",
        )
        out = app.dialogue_turn_text("Dame un contexto general de desgrabaciones.docx.")
        self.assertTrue(out["ok"])
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("desgrabaciones.docx", prompt)
        self.assertIn("Primera línea de desgrabaciones.", prompt)
        self.assertIn("Segunda línea importante del documento.", prompt)

    def test_chat_navigation_focuses_reference_block_without_replacing_main(self):
        app = test_app()
        app.load_text("doc", "Principal", "Uno principal.\n\nDos principal.", prefetch=False)
        app.add_reference_text("ref", "Desgrabaciones.docx", "Uno consulta.\n\nDos consulta.\n\nTres consulta.")
        out = app.chat("andá al bloque 2 de Desgrabaciones.docx")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_navigation")
        self.assertEqual(out["detail"], "focus_block")
        self.assertEqual(out["doc_id"], "ref")
        self.assertEqual(app.status()["doc_id"], "doc")
        self.assertEqual(app.laboratory_focus_status()["chunk_number"], 2)
        self.assertIn("Dos consulta.", out["answer"])

    def test_chat_search_sets_laboratory_focus_on_match(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.add_reference_text(
            "ref",
            "Desgrabaciones.docx",
            "Primera parte.\n\nAcá aparece YouTube como ejemplo pedagógico.\n\nCierre.",
        )
        out = app.chat("buscá dónde habla de YouTube en Desgrabaciones.docx")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_navigation")
        self.assertEqual(out["detail"], "search_matches")
        self.assertEqual(out["current"], 2)
        self.assertIn("YouTube", out["answer"])
        self.assertEqual(app.laboratory_focus_status()["query"], "YouTube")

    def test_chat_combined_focus_and_search_prefers_search_result_when_both_are_requested(self):
        app = test_app()
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.add_reference_text(
            "ref",
            "Desgrabaciones.docx",
            "Speaker 1.\n\nNada de YouTube acá.\n\nMás texto.\n\nYouTube aparece fuerte en este bloque.",
        )
        out = app.chat("Andá al bloque 1 de Desgrabaciones.docx y buscá dónde habla de YouTube y ese bloque qué dice exactamente.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["detail"], "search_matches")
        self.assertEqual(out["current"], 2)
        self.assertIn("YouTube", out["answer"])
        self.assertEqual(app.laboratory_focus_status()["chunk_number"], 2)

    def test_chat_explicit_external_research_uses_openclaw_bridge(self):
        chat_provider = NullChatProvider("No deberia usarse el LLM local.")
        bridge = NullExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="Sali a investigar afuera sobre Fedro.\nHallazgos:\n- Tesis A.\nFuentes:\n- Universidad X | https://ejemplo.test/tesis-a",
                spoken_answer="Sali a investigar afuera sobre Fedro. Encontre una tesis relevante de la Universidad X.",
                detail="external_research_ok",
                provider="openclaw_bridge",
                model="gemini-2.5-flash",
                query="Fedro en El banquete",
                summary="Encontre una tesis relevante de la Universidad X.",
                findings=["Tesis A."],
                sources=[{"title": "Universidad X", "url": "https://ejemplo.test/tesis-a", "note": "tesis doctoral"}],
            )
        )
        app = test_app(external_research=bridge)
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        out = app.chat("Buscá en internet algunas tesis de doctorado sobre la postura de Fedro en El banquete.")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "gemini-2.5-flash")
        self.assertTrue(out["external_research"])
        self.assertEqual(len(bridge.calls), 1)
        self.assertEqual(chat_provider.calls, [])
        self.assertIn("Fedro", out["answer"])
        self.assertEqual(out["external_sources"][0]["title"], "Universidad X")

    def test_chat_document_search_stays_local_even_when_bridge_exists(self):
        bridge = NullExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="No deberia activarse.",
                provider="openclaw_bridge",
                model="gemini-2.5-flash",
            )
        )
        app = test_app(external_research=bridge)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        app.add_reference_text(
            "ref",
            "Desgrabaciones.docx",
            "Primera parte.\n\nAcá aparece YouTube como ejemplo pedagógico.\n\nCierre.",
        )
        out = app.chat("buscá dónde habla de YouTube en Desgrabaciones.docx")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_navigation")
        self.assertEqual(out["detail"], "search_matches")
        self.assertEqual(len(bridge.calls), 0)

    def test_chat_normal_question_does_not_activate_external_research(self):
        bridge = NullExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="No deberia activarse.",
                provider="searxng",
                model="searxng-local",
            )
        )
        chat_provider = NullChatProvider("Recuerdo el contexto actual del laboratorio.")
        app = test_app(external_research=bridge)
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        out = app.chat("¿Qué recordás del contexto actual del laboratorio?")
        self.assertTrue(out["ok"])
        self.assertFalse(out.get("external_research", False))
        self.assertEqual(len(bridge.calls), 0)
        self.assertTrue(chat_provider.calls)

    def test_chat_explicit_academic_search_activates_external_research(self):
        bridge = NullExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="Sali a investigar afuera sobre Diotima.\nFuentes:\n- Stanford | https://ejemplo.test/diotima",
                spoken_answer="Sali a investigar afuera sobre Diotima. Encontré una fuente relevante.",
                detail="external_research_ok",
                provider="searxng",
                model="searxng-local",
                query="Diotima y la escalera del amor",
                summary="Encontré una fuente relevante sobre Diotima.",
                sources=[{"title": "Stanford", "url": "https://ejemplo.test/diotima", "note": "entrada académica"}],
            )
        )
        app = test_app(external_research=bridge)
        app.load_text("doc", "Principal", "Texto principal.", prefetch=False)
        out = app.chat("busca tesis o papers sobre Diotima y la escalera del amor")
        self.assertTrue(out["external_research"])
        self.assertEqual(out["provider"], "searxng")
        self.assertEqual(len(bridge.calls), 1)

    def test_chat_search_is_accent_insensitive(self):
        app = test_app()
        app.load_text("doc", "Principal", "Fedro habla con Socrates sobre eros.", prefetch=False)
        out = app.chat("buscá dónde aparece Sócrates en el documento")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_navigation")
        self.assertEqual(out["detail"], "search_matches")
        self.assertIn("Socrates", out["answer"])

    def test_dialogue_search_no_matches_is_not_a_hard_failure(self):
        provider = NullTTSProvider()
        app = test_app(tts=provider)
        app.load_text("doc", "Principal", "Fedro habla con Agatón.", prefetch=False)
        out = app.dialogue_turn_text("buscá dónde aparece Sócrates en el documento completo")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_navigation")
        self.assertEqual(out["detail"], "search_no_matches")
        self.assertIn("No encontré coincidencias", out["answer"])
        self.assertTrue(provider.calls)

    def test_followup_chat_gets_laboratory_focus_in_context(self):
        chat_provider = NullChatProvider("Sí, sigo ese foco.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Bloque principal uno.\n\nBloque principal dos.", prefetch=False)
        app.add_reference_text("ref", "Desgrabaciones.docx", "Uno consulta.\n\nDos consulta con YouTube.\n\nTres consulta.")
        nav = app.chat("buscá YouTube en Desgrabaciones.docx")
        self.assertTrue(nav["ok"])
        followup = app.chat("¿y ese bloque qué plantea?")
        self.assertTrue(followup["ok"])
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("FOCO ACTUAL DEL LABORATORIO:", prompt)
        self.assertIn("Desgrabaciones.docx", prompt)
        self.assertIn("Dos consulta con YouTube.", prompt)

    def test_dialogue_external_research_uses_bridge_and_keeps_urls_out_of_spoken_tts(self):
        provider = NullTTSProvider()
        bridge = NullExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="Sali a investigar afuera sobre Fedro.\nFuentes:\n- Universidad X | https://ejemplo.test/tesis-a",
                spoken_answer="Sali a investigar afuera sobre Fedro. Encontre una tesis relevante de la Universidad X.",
                detail="external_research_ok",
                provider="openclaw_bridge",
                model="gemini-2.5-flash",
                query="Fedro en El banquete",
                summary="Encontre una tesis relevante de la Universidad X.",
                findings=["La tesis ve a Fedro como una entrada elogiosa al eros."],
                sources=[{"title": "Universidad X", "url": "https://ejemplo.test/tesis-a", "note": "tesis doctoral"}],
            )
        )
        app = test_app(tts=provider, external_research=bridge)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("Buscá en la red tesis sobre Fedro en El banquete.")
        self.assertTrue(out["ok"])
        self.assertTrue(out["external_research"])
        self.assertEqual(out["model"], "gemini-2.5-flash")
        self.assertEqual(len(bridge.calls), 1)
        self.assertTrue(provider.calls)
        self.assertNotIn("https://", provider.calls[-1][0])

    def test_dialogue_external_research_keeps_text_when_tts_fails(self):
        bridge = NullExternalResearchBridge(
            ExternalResearchResult(
                True,
                answer="Encontré estas fuentes sobre Diotima.\nFuentes:\n- Fuente A | https://ejemplo.test/a",
                spoken_answer="Encontré estas fuentes sobre Diotima.",
                detail="external_research_ok",
                provider="searxng",
                model="searxng-local",
                sources=[{"title": "Fuente A", "url": "https://ejemplo.test/a", "note": "nota"}],
            )
        )
        app = test_app(tts=FailingTTSProvider(), external_research=bridge)
        app.load_text("doc", "Doc", "Pantalla actual.", prefetch=False)
        out = app.dialogue_turn_text("busca afuera fuentes sobre Diotima y la escalera del amor")
        self.assertTrue(out["ok"])
        self.assertTrue(out["external_research"])
        self.assertEqual(out["model"], "searxng-local")
        self.assertIn("Fuentes:", out["answer"])
        self.assertFalse(out["voice_ok"])
        self.assertEqual(out["audio"], "")

    def test_status_reports_runtime_services_without_ambiguous_ok(self):
        app = test_app(stt=BrokenSTTProvider())
        app.conversation = ConversationCore(FailingChatProvider())
        app.set_reasoning_mode("supreme")
        status = app.status()
        dialogue = app.dialogue_status()
        self.assertIn("services", status)
        self.assertFalse(status["services"]["stt"]["ready"])
        self.assertFalse(status["services"]["chat"]["ready"])
        self.assertEqual(status["services"]["dialogue_reasoning"]["applied_mode"], "thinking")
        self.assertTrue(dialogue["dialogue_reasoning"]["degraded"])
        self.assertIn("external_research", dialogue)

    def test_chat_compare_uses_focus_and_explicit_target(self):
        app = test_app()
        app.load_text("doc", "Principal", "Bloque principal uno.\n\nBloque principal dos importante.", prefetch=False)
        app.add_reference_text(
            "ref",
            "Análisis Filosófico.docx",
            "Bloque uno consulta.\n\nBloque dos consulta importante.\n\nBloque tres consulta.",
        )
        nav = app.chat("andá al bloque 2 de Análisis Filosófico.docx")
        self.assertTrue(nav["ok"])
        out = app.chat("compará este bloque con el bloque 2 del principal")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_compare")
        self.assertEqual(out["detail"], "compare_blocks")
        self.assertIn("Comparación:", out["answer"])
        self.assertIn("Análisis Filosófico.docx", out["answer"])
        self.assertIn("Principal", out["answer"])

    def test_dialogue_compare_returns_reader_compare_without_llm(self):
        app = test_app()
        app.load_text("doc", "Principal", "Idea central del principal.\n\nSegunda idea del principal.", prefetch=False)
        app.add_reference_text("ref", "ideas.docx", "Idea central de consulta.\n\nSegunda idea de consulta.")
        app.chat("andá al bloque 1 de ideas.docx")
        out = app.dialogue_turn_text("compará este bloque con el bloque 1 del principal")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "reader_compare")
        self.assertEqual(out["detail"], "compare_blocks")
        self.assertIn("Comparé", out["answer"])

    def test_dialogue_reflective_block_request_sets_focus_and_continues_with_llm(self):
        chat_provider = NullChatProvider("Lucy piensa el bloque con vuelo propio.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Principal", "Principal uno.\n\nPrincipal dos.", prefetch=False)
        app.add_reference_text(
            "ref",
            "ideas.docx",
            "Primer bloque de consulta.\n\nBloque 2: la estadística del lenguaje revela un régimen de inteligibilidad.\n\nTercer bloque.",
        )
        out = app.dialogue_turn_text("Quiero que pensemos filosóficamente sobre el bloque 2 de ideas.docx")
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "null")
        self.assertNotEqual(out["detail"], "focus_block")
        self.assertEqual(app.laboratory_focus_status()["chunk_number"], 2)
        self.assertEqual(app.laboratory_focus_status()["title"], "ideas.docx")
        prompt = "\n".join(item["content"] for item in chat_provider.calls[0][0])
        self.assertIn("FOCO ACTUAL DEL LABORATORIO:", prompt)
        self.assertIn("ideas.docx", prompt)
        self.assertIn("la estadística del lenguaje revela un régimen de inteligibilidad", prompt)

    def test_chat_uses_recent_laboratory_text_without_document(self):
        chat_provider = NullChatProvider("Sí, lo veo.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        pasted = "Texto pegado en laboratorio: la estadística reemplaza al logos como régimen de verdad."
        first = app.chat(pasted)
        second = app.chat("¿Ves lo que acabo de poner?")
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(len(chat_provider.calls), 2)
        messages = chat_provider.calls[1][0]
        joined = "\n".join(item["content"] for item in messages)
        self.assertIn("MATERIAL RECIENTE DEL LABORATORIO:", joined)
        self.assertIn("la estadística reemplaza al logos", joined)
        self.assertIn("usa el historial reciente del laboratorio", messages[0]["content"])
        self.assertIn("menciona brevemente el contenido reciente", messages[0]["content"])

    def test_dialogue_context_does_not_send_full_document(self):
        chat_provider = NullChatProvider("Respuesta breve.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        app.load_text("doc", "Doc", "Bloque uno.\n\nBloque dos visible.\n\nBloque tres final.", prefetch=False)
        app.jump(2)
        out = app.dialogue_turn_text("¿Qué te parece?")
        self.assertTrue(out["ok"])
        messages = chat_provider.calls[0][0]
        joined = "\n".join(item["content"] for item in messages)
        self.assertIn("Bloque dos visible", joined)
        self.assertNotIn("DOCUMENTO COMPLETO DISPONIBLE", joined)
        self.assertIn("No digas que guardaste notas", messages[0]["content"])

    def test_dialogue_uses_recent_text_chat_laboratory_material(self):
        chat_provider = NullChatProvider("Lo tengo presente.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        pasted = "Programa por problemas: verdad, lenguaje, poder y cuerpo para sexto año."
        chat_out = app.chat(pasted)
        dialogue_out = app.dialogue_turn_text("¿Estás leyendo lo que pegué en el laboratorio?")
        self.assertTrue(chat_out["ok"])
        self.assertTrue(dialogue_out["ok"])
        self.assertEqual(len(chat_provider.calls), 2)
        messages = chat_provider.calls[1][0]
        joined = "\n".join(item["content"] for item in messages)
        self.assertIn("MATERIAL RECIENTE DEL LABORATORIO:", joined)
        self.assertIn("Programa por problemas", joined)
        self.assertIn("no digas que no ves texto", messages[0]["content"])

    def test_clear_laboratory_history_removes_chat_and_dialogue_context(self):
        chat_provider = NullChatProvider("Lo tengo presente.")
        app = test_app()
        app.conversation = ConversationCore(chat_provider)
        pasted = "Programa por problemas: verdad, lenguaje, poder y cuerpo para sexto año."
        self.assertTrue(app.chat(pasted)["ok"])
        self.assertTrue(app.dialogue_turn_text("¿Leés lo que pegué?")["ok"])
        cleared = app.clear_laboratory_history()
        self.assertTrue(cleared["ok"])
        self.assertGreaterEqual(cleared["chat_items"], 2)
        self.assertGreaterEqual(cleared["dialogue_items"], 2)
        self.assertTrue(app.dialogue_turn_text("¿Leés lo que pegué?")["ok"])
        messages = chat_provider.calls[-1][0]
        joined = "\n".join(item["content"] for item in messages)
        self.assertNotIn("MATERIAL RECIENTE DEL LABORATORIO:", joined)
        self.assertNotIn("Programa por problemas", joined)

    def test_server_exposes_laboratory_history_reset_button_and_endpoint(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("clearLabHistoryBtn", server)
        self.assertIn("/api/laboratory/reset", server)
        self.assertIn("Historial de laboratorio borrado", server)

    def test_server_exposes_reference_documents_ui_and_endpoints(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("referenceModeToggle", server)
        self.assertIn("Documentos de consulta", server)
        self.assertIn("/api/reference/promote", server)
        self.assertIn("/api/reference/remove", server)
        self.assertIn("Agregar como consulta", server)
        self.assertIn("labFocus", server)
        self.assertIn("Foco del laboratorio", server)
        self.assertNotIn("refreshStatus(", server)

    def test_server_distinguishes_laboratory_notes_with_l_prefix(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("const LAB_NOTES_DOC_ID = '__laboratory__';", server)
        self.assertIn("return `L${Number(note && note.anchor_number || 1)}`;", server)
        self.assertIn("Notas del laboratorio", server)
        self.assertIn("Promise.all([", server)

    def test_manual_chat_uses_dialogue_voice_when_dialogue_is_active(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("async function sendTypedDialogue(message)", server)
        self.assertIn("if (dialogue.active)", server)
        self.assertIn("api('/api/dialogue/turn', { text: message", server)
        self.assertIn("await playDialogueAnswer(data)", server)

    def test_reasoning_tabs_and_endpoint_exist_in_server_ui(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("Pensamiento supremo", server)
        self.assertIn("reasoningNormalBtn", server)
        self.assertIn("api('/api/reasoning/mode', { mode: targetMode })", server)
        self.assertIn("Supremo pedido; diálogo usa Pensamiento para cuidar latencia.", server)

    def test_dialogue_low_latency_defaults_are_configured(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        stt_server = Path("scripts/fusion_reader_v2_stt_server.py").read_text(encoding="utf-8")
        self.assertIn("silenceStopMs: 1250", server)
        self.assertIn("speechStartMs: 35", server)
        self.assertIn("createScriptProcessor(4096, 1, 1)", server)
        self.assertIn("encodeDialogueWav(dialogue.pcmChunks, dialogue.sampleRate)", server)
        self.assertIn("filename: 'dialogue.wav'", server)
        self.assertNotIn("new MediaRecorder", server)
        self.assertNotIn("requestData()", server)
        self.assertIn('FUSION_READER_STT_BEAM_SIZE", "1"', stt_server)
        self.assertIn('FUSION_READER_STT_RECOVERY_BEAM_SIZE', stt_server)
        self.assertIn("STT convert_failed", stt_server)

    def test_server_exposes_free_laboratory_mode_button_and_endpoint(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("freeModeBtn", server)
        self.assertIn("/api/laboratory/mode", server)
        self.assertIn("Modo libre", server)

    def test_dialogue_barge_in_keeps_pre_roll_for_short_commands(self):
        server = Path("scripts/fusion_reader_v2_server.py").read_text(encoding="utf-8")
        self.assertIn("const interruptedWhileSpeech = dialogue.bargeInSpeechMs > 0;", server)
        self.assertIn("dialogue.speechMs = Math.max(dialogue.speechMs, dialogue.speechStartMs);", server)
        self.assertIn("dialogue.suppressUntil = performance.now() + 40;", server)

    def test_academic_profile_uses_larger_token_budget(self):
        academic = Path("scripts/start_fusion_reader_v2_academic.sh").read_text(encoding="utf-8")
        launcher = Path("/home/lucy-ubuntu/.local/bin/fusion-reader-launcher").read_text(encoding="utf-8")
        self.assertIn('FUSION_READER_CHAT_NUM_PREDICT:-1536', academic)
        self.assertIn('FUSION_READER_CHAT_NUM_PREDICT:-1536', launcher)

    def test_ollama_thinking_default_token_budget_is_not_tiny(self):
        previous_think = os.environ.get("FUSION_READER_CHAT_THINK")
        previous_predict = os.environ.get("FUSION_READER_CHAT_NUM_PREDICT")
        try:
            os.environ["FUSION_READER_CHAT_THINK"] = "1"
            os.environ.pop("FUSION_READER_CHAT_NUM_PREDICT", None)
            provider = OllamaChatProvider(base_url="http://example.invalid")
            self.assertGreaterEqual(provider.num_predict, 1024)
        finally:
            if previous_think is None:
                os.environ.pop("FUSION_READER_CHAT_THINK", None)
            else:
                os.environ["FUSION_READER_CHAT_THINK"] = previous_think
            if previous_predict is None:
                os.environ.pop("FUSION_READER_CHAT_NUM_PREDICT", None)
            else:
                os.environ["FUSION_READER_CHAT_NUM_PREDICT"] = previous_predict

    def test_dialogue_shortener_closes_cut_text_without_ellipsis(self):
        app = test_app()
        app.dialogue_tts_max_chars = 90
        text = " ".join(["esta respuesta larga necesita cerrar sin quedarse colgada"] * 8)
        shortened = app._shorten_dialogue_answer(text)
        self.assertLessEqual(len(shortened), 91)
        self.assertTrue(shortened.endswith("."))
        self.assertNotIn("...", shortened)

    def test_import_plain_text_document(self):
        doc = import_document_bytes("cuento.txt", "Uno.\n\nDos.".encode("utf-8"))
        self.assertEqual(doc.doc_id, "cuento")
        self.assertEqual(doc.source_type, "text")
        self.assertIn("Dos.", doc.text)

    def test_import_docx_document(self):
        root = Path(tempfile.mkdtemp())
        path = root / "cuento.docx"
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Primer parrafo.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Segundo parrafo.</w:t></w:r></w:p>
  </w:body>
</w:document>"""
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("word/document.xml", xml)
        doc = import_document_bytes("cuento.docx", path.read_bytes())
        self.assertEqual(doc.source_type, "docx")
        self.assertIn("Primer parrafo.", doc.text)
        self.assertIn("Segundo parrafo.", doc.text)

    def test_import_odt_document(self):
        root = Path(tempfile.mkdtemp())
        path = root / "cuento.odt"
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
  <office:body><office:text>
    <text:p>Linea uno.</text:p>
    <text:p>Linea dos.</text:p>
  </office:text></office:body>
</office:document-content>"""
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("content.xml", xml)
        doc = import_document_bytes("cuento.odt", path.read_bytes())
        self.assertEqual(doc.source_type, "odt")
        self.assertIn("Linea uno.", doc.text)
        self.assertIn("Linea dos.", doc.text)

    def test_ocr_text_keeps_headings_and_paragraphs(self):
        raw = """Capítulo 1
Introducción

Este es un párrafo de prueba con suficiente texto normal para ser conservado.
Sigue en otra línea y mantiene la misma idea.
"""
        text = structured_plain_ocr_text(raw)
        self.assertIn("# Capítulo 1", text)
        self.assertIn("## Introducción", text)
        self.assertIn("Este es un párrafo", text)

    def test_clean_heading_preserves_chapter_number(self):
        self.assertEqual(clean_heading("Capítulo 1"), "Capítulo 1")
        self.assertEqual(clean_heading("## = Introducción >"), "Introducción")

    def test_repair_ocr_spacing_fixes_common_scan_merges(self):
        text = repair_ocr_spacing("Elabad llegó en elaño nuevo y miró alanciano delazul.")
        self.assertIn("El abad", text)
        self.assertIn("el año", text)
        self.assertIn("al anciano", text)
        self.assertIn("del azul", text)


if __name__ == "__main__":
    unittest.main()
