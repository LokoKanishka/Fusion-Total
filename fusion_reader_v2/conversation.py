from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class ChatResult:
    ok: bool
    answer: str = ""
    model: str = ""
    detail: str = ""
    duration_ms: int = 0


class ChatProvider:
    name = "base"

    def chat(self, messages: list[dict], model: str = "") -> ChatResult:
        return ChatResult(False, model=model, detail="not_implemented")


class OllamaChatProvider(ChatProvider):
    name = "ollama"

    def __init__(self, base_url: str = "", default_model: str = "", timeout_seconds: float | None = None) -> None:
        self.base_url = (base_url or os.environ.get("FUSION_READER_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
        self.default_model = default_model or os.environ.get("FUSION_READER_CHAT_MODEL") or "qwen3:14b-q8_0"
        self.timeout_seconds = timeout_seconds or float(os.environ.get("FUSION_READER_CHAT_TIMEOUT", "120"))
        self.think = os.environ.get("FUSION_READER_CHAT_THINK", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.num_predict = int(os.environ.get("FUSION_READER_CHAT_NUM_PREDICT", "1024" if self.think else "384"))

    def chat(self, messages: list[dict], model: str = "") -> ChatResult:
        started = time.perf_counter()
        selected_model = model or self.default_model
        payload = {
            "model": selected_model,
            "messages": messages,
            "stream": False,
            "think": self.think,
            "options": {
                "temperature": float(os.environ.get("FUSION_READER_CHAT_TEMPERATURE", "0.4")),
                "num_ctx": int(os.environ.get("FUSION_READER_CHAT_NUM_CTX", "32768")),
                "num_predict": self.num_predict,
            },
        }
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            message = data.get("message") if isinstance(data, dict) else None
            answer = str((message or {}).get("content") or "").strip()
            if not answer:
                return ChatResult(False, model=selected_model, detail="empty_answer", duration_ms=int((time.perf_counter() - started) * 1000))
            return ChatResult(True, answer=answer, model=selected_model, duration_ms=int((time.perf_counter() - started) * 1000))
        except urllib.error.HTTPError as exc:
            return ChatResult(False, model=selected_model, detail=f"http_{exc.code}", duration_ms=int((time.perf_counter() - started) * 1000))
        except Exception as exc:
            return ChatResult(False, model=selected_model, detail=str(exc), duration_ms=int((time.perf_counter() - started) * 1000))


class NullChatProvider(ChatProvider):
    name = "null_chat"

    def __init__(self, answer: str = "Respuesta de prueba.") -> None:
        self.answer = answer
        self.calls: list[tuple[list[dict], str]] = []

    def chat(self, messages: list[dict], model: str = "") -> ChatResult:
        self.calls.append((messages, model))
        return ChatResult(True, answer=self.answer, model=model or "null")


class ConversationCore:
    def __init__(self, provider: ChatProvider | None = None, max_document_chars: int | None = None) -> None:
        self.provider = provider or OllamaChatProvider()
        self.max_document_chars = max_document_chars or int(os.environ.get("FUSION_READER_CHAT_MAX_DOCUMENT_CHARS", "60000"))

    def ask(self, question: str, snapshot: dict, model: str = "", history: list[dict] | None = None) -> ChatResult:
        question = str(question or "").strip()
        if not question:
            return ChatResult(False, model=model, detail="empty_question")
        messages = self._messages(question, snapshot, history=history or [])
        return self.provider.chat(messages, model=model)

    def ask_dialogue(self, question: str, snapshot: dict, history: list[dict] | None = None, model: str = "") -> ChatResult:
        question = str(question or "").strip()
        if not question:
            return ChatResult(False, model=model, detail="empty_question")
        messages = self._messages(question, snapshot, history=history or [], dialogue=True)
        return self.provider.chat(messages, model=model)

    def _messages(self, question: str, snapshot: dict, history: list[dict] | None = None, dialogue: bool = False) -> list[dict]:
        context = self._context_text(snapshot, include_document=not dialogue)
        if dialogue:
            system = (
                "Sos la voz de laboratorio de Fusion Reader v2. "
                "Conversas oralmente con el usuario sobre el documento activo, lo que esta viendo, "
                "y el material que pego o escribio en el chat de laboratorio. "
                "Responde en español natural, breve y conversacional, en una o dos frases cortas. "
                "No leas el documento completo salvo que te lo pidan; conversa sobre el fragmento y el contexto. "
                "Si el usuario pregunta por lo que acaba de pegar, poner o escribir en laboratorio, "
                "usa MATERIAL RECIENTE DEL LABORATORIO y menciona brevemente ese contenido. "
                "Si no hay documento cargado pero si hay material reciente en laboratorio, no digas que no ves texto: "
                "responde sobre ese material. "
                "No dejes frases abiertas: si tenes que ser breve, cerra una idea completa antes de terminar. "
                "No digas que guardaste notas. Nunca digas que guardaste, moviste o confirmaste una nota. "
                "Las notas solo las guarda y confirma el sistema reader_notes; si el usuario pregunta por notas visibles, decile que revise el panel de notas del documento o que repita el pedido como 'tomá nota de ...'. "
                "Si el usuario te interrumpe o corrige, acepta el nuevo punto y continua desde ahi."
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": f"CONTEXTO DEL LECTOR:\n{context}"},
            ]
            lab_history = snapshot.get("laboratory_history")
            lab_context = self._laboratory_context_text(lab_history if isinstance(lab_history, list) else [])
            if lab_context:
                messages.append({"role": "user", "content": f"MATERIAL RECIENTE DEL LABORATORIO:\n{lab_context}"})
            for item in (history or [])[-6:]:
                role = str(item.get("role") or "")
                content = str(item.get("content") or "").strip()
                if role in {"user", "assistant"} and content:
                    messages.append({"role": role, "content": content})
            messages.append({"role": "user", "content": question})
            return messages
        system = (
            "Sos el asistente de laboratorio de Fusion Reader v2. "
            "Tu trabajo es conversar sobre el documento activo, lo que el usuario esta viendo en pantalla, "
            "y el material que el usuario pega o escribe en el chat de laboratorio. "
            "El documento activo es una fuente importante, pero no es la unica: si el usuario pregunta por "
            "lo que acaba de poner, pegar o decir, usa el historial reciente del laboratorio como contexto. "
            "Cuando el usuario pregunte si ves lo que acaba de poner, menciona brevemente el contenido reciente "
            "para confirmar que lo estas mirando. "
            "Si no hay documento cargado pero si hay material reciente en el chat, responde sobre ese material "
            "sin insistir en cargar un archivo. "
            "No dejes frases abiertas ni listas inconclusas: si la respuesta puede ser larga, prioriza cerrar "
            "pocas ideas completas antes que empezar muchas. "
            "No sos el motor de lectura en voz alta y no debes prometer controlar la voz salvo que sea un comando del lector. "
            "Si el usuario pide guardar o tomar una nota y esa accion no aparece ya confirmada por el sistema, no finjas haberla guardado. "
            "Responde en español, con claridad, y si el documento no alcanza para contestar decilo."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"CONTEXTO DEL LECTOR:\n{context}"},
        ]
        lab_context = self._laboratory_context_text(history or [])
        if lab_context:
            messages.append({"role": "user", "content": f"MATERIAL RECIENTE DEL LABORATORIO:\n{lab_context}"})
        for item in (history or [])[-8:]:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": question})
        return messages

    def _laboratory_context_text(self, history: list[dict]) -> str:
        user_items: list[str] = []
        for item in history[-10:]:
            if str(item.get("role") or "") != "user":
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            user_items.append(content)
        if not user_items:
            return ""
        clipped: list[str] = []
        for index, content in enumerate(user_items[-4:], start=max(1, len(user_items) - 3)):
            if len(content) > 4000:
                content = content[:4000].rstrip() + "\n[Texto pegado recortado por limite de contexto.]"
            clipped.append(f"[Usuario {index}]\n{content}")
        return "\n\n".join(clipped)

    def _context_text(self, snapshot: dict, include_document: bool = True) -> str:
        document_text = str(snapshot.get("document_text") or "")
        if len(document_text) > self.max_document_chars:
            document_text = document_text[: self.max_document_chars].rstrip() + "\n\n[Documento recortado por limite de contexto.]"
        previous_chunk = str(snapshot.get("previous_chunk") or "")
        current_chunk = str(snapshot.get("current_chunk") or "")
        next_chunk = str(snapshot.get("next_chunk") or "")
        notes = snapshot.get("notes") if isinstance(snapshot.get("notes"), list) else []
        notes_text = "\n".join(
            f"- Bloque {int(note.get('chunk_number') or 0)}: {str(note.get('text') or '').strip()}"
            for note in notes[:40]
            if str(note.get("text") or "").strip()
        )
        lines = [
            f"Titulo: {snapshot.get('title') or 'Sin titulo'}",
            f"Documento ID: {snapshot.get('doc_id') or ''}",
            f"Bloque visible: {snapshot.get('current') or 0} de {snapshot.get('total') or 0}",
            "",
            "TEXTO EN PANTALLA:",
            current_chunk or "[No hay bloque visible.]",
            "",
            "BLOQUE ANTERIOR:",
            previous_chunk or "[No hay bloque anterior.]",
            "",
            "BLOQUE SIGUIENTE:",
            next_chunk or "[No hay bloque siguiente.]",
            "",
            "NOTAS DEL LECTOR:",
            notes_text or "[No hay notas guardadas.]",
        ]
        if include_document:
            lines.extend(["", "DOCUMENTO COMPLETO DISPONIBLE:", document_text or "[No hay documento cargado.]"])
        return "\n".join(lines)
