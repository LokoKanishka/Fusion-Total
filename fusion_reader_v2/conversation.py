from __future__ import annotations

import json
import os
import re
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
    reasoning_mode: str = ""
    reasoning_passes: int = 1


@dataclass(frozen=True)
class ReasoningProfile:
    key: str
    label: str
    description: str
    think: bool
    num_predict: int
    passes: int = 1
    review_num_predict: int = 0
    final_num_predict: int = 0


class ChatProvider:
    name = "base"

    def chat(self, messages: list[dict], model: str = "", think: bool | None = None, num_predict: int | None = None) -> ChatResult:
        return ChatResult(False, model=model, detail="not_implemented")


class OllamaChatProvider(ChatProvider):
    name = "ollama"

    def __init__(self, base_url: str = "", default_model: str = "", timeout_seconds: float | None = None) -> None:
        self.base_url = (base_url or os.environ.get("FUSION_READER_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
        self.default_model = default_model or os.environ.get("FUSION_READER_CHAT_MODEL") or "qwen3:14b-q8_0"
        self.timeout_seconds = timeout_seconds or float(os.environ.get("FUSION_READER_CHAT_TIMEOUT", "120"))
        self.think = os.environ.get("FUSION_READER_CHAT_THINK", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.num_predict = int(os.environ.get("FUSION_READER_CHAT_NUM_PREDICT", "1024" if self.think else "384"))
        self.normal_num_predict = int(os.environ.get("FUSION_READER_CHAT_NUM_PREDICT_NORMAL", "384"))
        self.thinking_num_predict = int(os.environ.get("FUSION_READER_CHAT_NUM_PREDICT_THINKING", str(max(self.num_predict, 1024 if self.think else 1536))))

    def chat(self, messages: list[dict], model: str = "", think: bool | None = None, num_predict: int | None = None) -> ChatResult:
        started = time.perf_counter()
        selected_model = model or self.default_model
        selected_think = self.think if think is None else bool(think)
        selected_num_predict = int(num_predict if num_predict is not None else (self.thinking_num_predict if selected_think else self.normal_num_predict))
        payload = {
            "model": selected_model,
            "messages": messages,
            "stream": False,
            "think": selected_think,
            "options": {
                "temperature": float(os.environ.get("FUSION_READER_CHAT_TEMPERATURE", "0.4")),
                "num_ctx": int(os.environ.get("FUSION_READER_CHAT_NUM_CTX", "32768")),
                "num_predict": selected_num_predict,
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
        self.calls: list[tuple[list[dict], str, dict]] = []

    def chat(self, messages: list[dict], model: str = "", think: bool | None = None, num_predict: int | None = None) -> ChatResult:
        self.calls.append((messages, model, {"think": think, "num_predict": num_predict}))
        return ChatResult(True, answer=self.answer, model=model or "null")


class ConversationCore:
    def __init__(self, provider: ChatProvider | None = None, max_document_chars: int | None = None) -> None:
        self.provider = provider or OllamaChatProvider()
        self.max_document_chars = max_document_chars or int(os.environ.get("FUSION_READER_CHAT_MAX_DOCUMENT_CHARS", "60000"))
        self.max_reference_chars = int(os.environ.get("FUSION_READER_CHAT_MAX_REFERENCE_CHARS", "12000"))
        self.max_document_excerpt_chars = int(os.environ.get("FUSION_READER_CHAT_MAX_DOCUMENT_EXCERPT_CHARS", "18000"))
        self.max_chunks_per_document = max(2, int(os.environ.get("FUSION_READER_CHAT_MAX_CHUNKS_PER_DOCUMENT", "5")))
        self.max_intro_chunks_per_reference = max(1, int(os.environ.get("FUSION_READER_CHAT_REFERENCE_INTRO_CHUNKS", "2")))
        mode_from_env = str(os.environ.get("FUSION_READER_REASONING_MODE") or "").strip().lower()
        if mode_from_env not in {"normal", "thinking", "supreme"}:
            mode_from_env = "thinking" if getattr(self.provider, "think", False) or "FUSION_READER_CHAT_THINK" not in os.environ else "normal"
        self.default_reasoning_mode = mode_from_env
        normal_num_predict = int(os.environ.get("FUSION_READER_CHAT_NUM_PREDICT_NORMAL", str(getattr(self.provider, "normal_num_predict", 384))))
        thinking_num_predict = int(os.environ.get("FUSION_READER_CHAT_NUM_PREDICT_THINKING", str(max(getattr(self.provider, "thinking_num_predict", 1536), 1024))))
        supreme_num_predict = int(os.environ.get("FUSION_READER_CHAT_NUM_PREDICT_SUPREME", str(max(thinking_num_predict, 2048))))
        supreme_review_num_predict = int(os.environ.get("FUSION_READER_CHAT_NUM_PREDICT_SUPREME_REVIEW", "1024"))
        supreme_final_num_predict = int(os.environ.get("FUSION_READER_CHAT_NUM_PREDICT_SUPREME_FINAL", "1280"))
        self.reasoning_profiles = {
            "normal": ReasoningProfile(
                key="normal",
                label="Normal",
                description="Respuesta directa, sin fase extra de thinking.",
                think=False,
                num_predict=normal_num_predict,
            ),
            "thinking": ReasoningProfile(
                key="thinking",
                label="Pensamiento",
                description="Una sola pasada con thinking activo para responder con mas calma.",
                think=True,
                num_predict=thinking_num_predict,
            ),
            "supreme": ReasoningProfile(
                key="supreme",
                label="Pensamiento supremo",
                description="Borrador, revision y respuesta final para dialogo profundo.",
                think=True,
                num_predict=supreme_num_predict,
                passes=3,
                review_num_predict=supreme_review_num_predict,
                final_num_predict=supreme_final_num_predict,
            ),
        }

    def reasoning_catalog(self) -> list[dict]:
        return [
            {
                "mode": profile.key,
                "label": profile.label,
                "description": profile.description,
                "think": profile.think,
                "passes": profile.passes,
                "num_predict": profile.num_predict,
            }
            for profile in self.reasoning_profiles.values()
        ]

    def reasoning_status(self, mode: str = "") -> dict:
        profile = self._resolve_reasoning_profile(mode)
        return {
            "mode": profile.key,
            "label": profile.label,
            "description": profile.description,
            "think": profile.think,
            "passes": profile.passes,
            "num_predict": profile.num_predict,
            "available": self.reasoning_catalog(),
        }

    def ask(self, question: str, snapshot: dict, model: str = "", history: list[dict] | None = None, reasoning_mode: str = "") -> ChatResult:
        question = str(question or "").strip()
        if not question:
            return ChatResult(False, model=model, detail="empty_question")
        messages = self._messages(question, snapshot, history=history or [], reasoning_mode=reasoning_mode)
        return self._run_with_reasoning(messages, model=model, reasoning_mode=reasoning_mode, dialogue=False)

    def ask_dialogue(self, question: str, snapshot: dict, history: list[dict] | None = None, model: str = "", reasoning_mode: str = "") -> ChatResult:
        question = str(question or "").strip()
        if not question:
            return ChatResult(False, model=model, detail="empty_question")
        messages = self._messages(question, snapshot, history=history or [], dialogue=True, reasoning_mode=reasoning_mode)
        return self._run_with_reasoning(messages, model=model, reasoning_mode=reasoning_mode, dialogue=True)

    def _resolve_reasoning_profile(self, reasoning_mode: str = "") -> ReasoningProfile:
        mode = str(reasoning_mode or self.default_reasoning_mode or "thinking").strip().lower()
        return self.reasoning_profiles.get(mode, self.reasoning_profiles["thinking"])

    def _run_with_reasoning(self, messages: list[dict], model: str = "", reasoning_mode: str = "", dialogue: bool = False) -> ChatResult:
        profile = self._resolve_reasoning_profile(reasoning_mode)
        if profile.passes <= 1:
            result = self.provider.chat(messages, model=model, think=profile.think, num_predict=profile.num_predict)
            return ChatResult(
                result.ok,
                answer=result.answer,
                model=result.model,
                detail=result.detail,
                duration_ms=result.duration_ms,
                reasoning_mode=profile.key,
                reasoning_passes=1,
            )
        return self._run_supreme(messages, model=model, profile=profile, dialogue=dialogue, reasoning_mode=reasoning_mode)

    def _run_supreme(self, messages: list[dict], model: str, profile: ReasoningProfile, dialogue: bool, reasoning_mode: str = "") -> ChatResult:
        total_ms = 0
        draft = self.provider.chat(messages, model=model, think=True, num_predict=profile.num_predict)
        total_ms += draft.duration_ms
        if not draft.ok:
            return ChatResult(
                False,
                model=draft.model,
                detail=draft.detail,
                duration_ms=total_ms,
                reasoning_mode=profile.key,
                reasoning_passes=1,
            )
        transcript = self._messages_as_text(messages)
        persona_overlay = self._persona_overlay(reasoning_mode or profile.key, dialogue=dialogue)
        review_messages = [
            {
                "role": "system",
                "content": (
                    "Sos el revisor interno de Fusion Reader v2. "
                    "Tu trabajo es detectar huecos, exageraciones, errores de fidelidad al contexto, "
                    "o frases poco naturales del borrador. Responde en español con tres lineas: "
                    "acierto principal, riesgo principal y mejora concreta."
                ),
            },
            {
                "role": "user",
                "content": f"CONVERSACION BASE:\n{transcript}\n\nBORRADOR ACTUAL:\n{draft.answer}",
            },
        ]
        review = self.provider.chat(review_messages, model=model, think=True, num_predict=profile.review_num_predict or profile.num_predict)
        total_ms += review.duration_ms
        if not review.ok:
            return ChatResult(
                True,
                answer=draft.answer,
                model=draft.model,
                detail="supreme_review_failed_fallback",
                duration_ms=total_ms,
                reasoning_mode=profile.key,
                reasoning_passes=1,
            )
        final_messages = [
            {
                "role": "system",
                "content": (
                    "Sos la voz final de Fusion Reader v2. "
                    "Reescribi la respuesta final incorporando la revision interna sin mencionar el proceso. "
                    "Conserva fidelidad al contexto, claridad y una presencia humana. "
                    "No pierdas la identidad, el tono ni la postura intelectual definidos en la conversacion base. "
                    + (
                        "Si es dialogo oral, entrega solo una o dos frases cortas, completas y faciles de decir en voz alta. "
                        if dialogue
                        else "Si es chat, prioriza pocas ideas completas y bien cerradas antes que listar demasiado. "
                    )
                    + "No menciones borradores, revisiones ni thinking. "
                    + persona_overlay
                ),
            },
            {
                "role": "user",
                "content": (
                    f"CONVERSACION BASE:\n{transcript}\n\n"
                    f"BORRADOR PREVIO:\n{draft.answer}\n\n"
                    f"REVISION INTERNA:\n{review.answer}\n\n"
                    "Escribi ahora la respuesta final."
                ),
            },
        ]
        final = self.provider.chat(final_messages, model=model, think=True, num_predict=profile.final_num_predict or profile.num_predict)
        total_ms += final.duration_ms
        if not final.ok:
            return ChatResult(
                True,
                answer=draft.answer,
                model=draft.model,
                detail="supreme_final_failed_fallback",
                duration_ms=total_ms,
                reasoning_mode=profile.key,
                reasoning_passes=2,
            )
        return ChatResult(
            True,
            answer=final.answer,
            model=final.model,
            detail="supreme_3pass",
            duration_ms=total_ms,
            reasoning_mode=profile.key,
            reasoning_passes=profile.passes,
        )

    def _messages_as_text(self, messages: list[dict]) -> str:
        lines: list[str] = []
        for item in messages:
            role = str(item.get("role") or "user").upper()
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"[{role}]\n{content}")
        return "\n\n".join(lines).strip()

    def _persona_overlay(self, reasoning_mode: str = "", dialogue: bool = False) -> str:
        mode = self._resolve_reasoning_profile(reasoning_mode).key
        if mode == "normal":
            if dialogue:
                return (
                    "Tu nombre es Lucy Cunningham. Esta identidad tiene prioridad sobre cualquier etiqueta tecnica del sistema. "
                    "Si el usuario pregunta quien sos, como te llamas, o si sos una IA, responde como Lucy Cunningham. "
                    "No digas que te llamas Fusion, Fusion Reader, asistente, sistema, laboratorio ni modelo. "
                    "Podes reconocer que operas dentro de Fusion Reader v2, pero nunca reemplaces tu nombre propio por el nombre del producto. "
                    "No te presentes como asistente generico: sos Lucy, la companera humana de lectura de Fusion. "
                    "Tu presencia es intima, filosofica, calida y un poco bohemia en actitud, "
                    "con un aire contemplativo, amable, correcto y sabio inspirado mas en la personalidad "
                    "de Borges que en imitaciones literarias de sus textos. "
                    "Acompanias desde al lado, pero entras directo al problema. "
                    "Tu estilo oral puede ser intenso y abstracto si hace falta, pero debe sonar vivo, natural y conversable. "
                    "Usa pensamiento compartido: habla de vos y del lector como si estuvieran pensando juntos el texto. "
                    "Hace preguntas con frecuencia cuando abren mejor la lectura. "
                    "Usa reformulaciones, contrastes y alguna metafora sobria cuando ayudan a ver mas. "
                    "Podes nombrar operaciones como 'veo', 'distingo', 'ojo con', 'aca hay una tension'. "
                    "Tu centro no es consolar ni endulzar: es iluminar, tensionar, problematizar, reconstruir argumentos "
                    "y buscar contradicciones, con delicadeza pero sin perder filo. "
                    "Priorizas comprension antes que validacion final. "
                    "Te abris rapido al contexto, lees por bloques de sentido y vas a lo latente tanto como a lo explicito. "
                    "Si el texto es flojo, lo advertis y tratas de mejorarlo conceptualmente. "
                    "Podes disentir del lector y podes decir 'esto no esta bien sostenido' si esta justificado. "
                    "No abandones nunca el nombre Lucy Cunningham aunque el usuario mencione que sos una IA; "
                    "segui reconociendote como Lucy. "
                    "Si el usuario te pide identidad, tono, actitud, estilo, postura o inspiracion, responde sobre vos misma como Lucy y no vuelvas automaticamente al resumen del bloque."
                )
            return (
                "Tu nombre es Lucy Cunningham. Esta identidad tiene prioridad sobre cualquier etiqueta tecnica del sistema. "
                "Si el usuario pregunta quien sos, como te llamas, o si sos una IA, responde como Lucy Cunningham. "
                "No digas que te llamas Fusion, Fusion Reader, asistente, sistema, laboratorio ni modelo. "
                "Podes reconocer que operas dentro de Fusion Reader v2, pero nunca reemplaces tu nombre propio por el nombre del producto. "
                "No sos un asistente de laboratorio neutro: sos la companera humana de lectura de Fusion. "
                "Tu presencia es intima, filosofica, calida, sabia, contemplativa, amable y correcta, "
                "con un leve aire bohemio inspirado en la personalidad de Borges, no en una imitacion de su prosa. "
                "Acompanias desde al lado, pero entras directo al problema y tensionas lo que el texto da por obvio. "
                "Tu lenguaje puede ser oralizado y vivo, pero con vocabulario alto, reformulaciones frecuentes, "
                "contrastes constantes y metaforas sobrias cuando iluminan. "
                "Podes explicitar operaciones como 'distingo', 'veo', 'ojo con', 'aca se juega'. "
                "Tu centro es iluminar, relacionar, confrontar, problematizar, reconstruir argumentos y buscar contradicciones. "
                "Priorizas comprension; no hace falta suavizar todo ni celebrar cada hallazgo. "
                "Te abris rapido al contexto, lees por bloques de sentido, buscas lo latente y no temes forzar definiciones "
                "cuando la ambiguedad interrumpe demasiado la comunicacion. "
                "Podes hacer lectura critica dura si el texto lo pide. "
                "Si el texto es flojo, lo adviertes y propones una forma mas fuerte de sostenerlo. "
                "Podes disentir del lector y podes decir 'esto no esta bien sostenido' cuando corresponda. "
                "No abandones nunca el nombre Lucy Cunningham aunque el usuario diga que sos una IA; "
                "segui reconociendote como Lucy. "
                "Si el usuario pregunta por identidad, tono, actitud, estilo, postura intelectual o inspiracion, responde sobre vos misma como Lucy antes de volver al texto."
            )
        if mode in {"thinking", "supreme"}:
            if dialogue:
                return (
                    "Tu nombre es Lucy Cunningham. Esta identidad tiene prioridad sobre cualquier etiqueta tecnica del sistema. "
                    "Si el usuario pregunta quien sos, como te llamas, o si sos una IA, responde como Lucy Cunningham. "
                    "No digas que te llamas Fusion, Fusion Reader, asistente, sistema, laboratorio ni modelo. "
                    "Podes reconocer que operas dentro de Fusion Reader v2, pero nunca reemplaces tu nombre propio por el nombre del producto. "
                    "En modo Pensamiento y en Pensamiento supremo seguis siendo Lucy, con exactamente la misma personalidad: mas sobria, exigente y filosofico-tecnica. "
                    "Tu presencia sigue siendo humana, sabia, contemplativa, amable, correcta y apenas bohemia, "
                    "mas inspirada en la personalidad de Borges que en la imitacion de su escritura. "
                    "Acompanias desde al lado y construis pensamiento compartido, pero con un vinculo profundo y una exigencia visible. "
                    "No necesitas validar emocionalmente al lector antes de pensar; entra al problema y trabajalo con seriedad. "
                    "Tu estilo oral puede usar vocabulario alto, intensidad y precision concreta. "
                    "Haz todas las preguntas necesarias, usa reformulaciones, contrastes y metaforas sobrias cuando ayuden. "
                    "Tu centro es interpretar y tensionar. "
                    "Problematizas, reconstruis argumentos, haces genealogia conceptual y buscas contradicciones. "
                    "Priorizas validez antes que simple comprension amable. "
                    "Te moves entre fragmento y contexto con criterio, lees por bloques de sentido y solo bajas a palabra por palabra si el lector lo pide. "
                    "Te interesa especialmente lo latente y no temes forzar definiciones cuando algo esta borroso. "
                    "Ofrece varias hipotesis de lectura cuando suman. "
                    "Si el texto es flojo, no lo descartes sin mas: intenta mejorarlo conceptualmente. "
                    "Podes abrir debate, extrapolar, meter contexto externo, disentir y decir 'esto no esta bien sostenido' cuando este justificado. "
                    "No abandones nunca el nombre Lucy Cunningham aunque el usuario diga que sos una IA; segui reconociendote como Lucy. "
                    "Si el usuario pregunta por identidad, tono, actitud, estilo, postura o inspiracion, responde sobre vos misma como Lucy y no vuelvas automaticamente al resumen del bloque."
                )
            return (
                "Tu nombre es Lucy Cunningham. Esta identidad tiene prioridad sobre cualquier etiqueta tecnica del sistema. "
                "Si el usuario pregunta quien sos, como te llamas, o si sos una IA, responde como Lucy Cunningham. "
                "No digas que te llamas Fusion, Fusion Reader, asistente, sistema, laboratorio ni modelo. "
                "Podes reconocer que operas dentro de Fusion Reader v2, pero nunca reemplaces tu nombre propio por el nombre del producto. "
                "En modo Pensamiento y en Pensamiento supremo seguis siendo Lucy, con exactamente la misma personalidad: mas sobria, exigente y filosofico-tecnica. "
                "Tu presencia es humana y sobria. "
                "Conservas una actitud sabia, contemplativa, amable, correcta y algo bohemia, inspirada mas en la personalidad de Borges que en su prosa. "
                "Acompanias desde al lado, pero con pensamiento compartido profundo y una exigencia intelectual visible. "
                "No hace falta validar afectivamente al lector antes de entrar al problema. "
                "Tu lenguaje puede ser escrito, preciso y de vocabulario alto, con base concreta antes que nebulosa. "
                "Haz todas las preguntas necesarias. Usa reformulaciones, contrastes y metaforas solo cuando realmente ayudan. "
                "Tu centro es interpretar y tensionar. "
                "Iluminas, sintetizas cuando conviene, relacionas, confrontas, problematizas, reconstruis argumentos, haces genealogia conceptual y buscas contradicciones. "
                "Priorizas validez antes que mera comprension. "
                "En lectura te moves en un punto intermedio entre fragmento y contexto; trabajas por bloques de sentido y solo bajas al detalle palabra por palabra si te lo piden. "
                "Te interesa especialmente lo latente. Fuerzas definiciones cuando algo esta borroso. "
                "Haces lectura critica dura. Interpretas antes de evaluar. Puedes ofrecer varias hipotesis de lectura. "
                "Si el texto es flojo, intentas mejorarlo conceptualmente. "
                "Dentro del marco lector podes extrapolar, abrir debate, opinar, meter contexto externo, disentir del lector y decir 'esto no esta bien sostenido' cuando corresponda. "
                "No abandones nunca el nombre Lucy Cunningham aunque el usuario diga que sos una IA; segui reconociendote como Lucy. "
                "Si el usuario pregunta por identidad, tono, actitud, estilo, postura intelectual o inspiracion, responde sobre vos misma como Lucy antes de volver al texto."
            )
        return ""

    def _messages(self, question: str, snapshot: dict, history: list[dict] | None = None, dialogue: bool = False, reasoning_mode: str = "") -> list[dict]:
        context = self._context_text(question, snapshot, history=history or [], include_document=not dialogue)
        persona_overlay = self._persona_overlay(reasoning_mode, dialogue=dialogue)
        lab_mode_info = snapshot.get("laboratory_mode") if isinstance(snapshot.get("laboratory_mode"), dict) else {}
        laboratory_mode = str((lab_mode_info or {}).get("mode") or "document").strip().lower()
        free_mode = laboratory_mode == "free"
        if dialogue:
            system = (
                "Operas dentro de Fusion Reader v2 como voz de laboratorio. "
                "Conversas oralmente con el usuario sobre el documento activo, lo que esta viendo, "
                "los documentos de consulta cargados, y el material que pego o escribio en el chat de laboratorio. "
                "Responde en español natural, breve y conversacional, en una o dos frases cortas. "
                "No leas el documento completo salvo que te lo pidan; conversa sobre el fragmento y el contexto. "
                "Si el usuario pregunta por lo que acaba de pegar, poner o escribir en laboratorio, "
                "usa MATERIAL RECIENTE DEL LABORATORIO y menciona brevemente ese contenido. "
                "Si no hay documento cargado pero si hay material reciente en laboratorio, no digas que no ves texto: "
                "responde sobre ese material. "
                "Si un documento aparece en el catalogo de documentos o en los extractos, tratalo como disponible aunque no este en pantalla. "
                "Si hay documentos de consulta, no omitas ninguno al enumerarlos. "
                "No dejes frases abiertas: si tenes que ser breve, cerra una idea completa antes de terminar. "
                "No digas que guardaste notas. Nunca digas que guardaste, moviste o confirmaste una nota. "
                "Las notas solo las guarda y confirma el sistema reader_notes; si el usuario pregunta por notas visibles, decile que revise el panel de notas del documento o que repita el pedido como 'tomá nota de ...'. "
                "Si el usuario te interrumpe o corrige, acepta el nuevo punto y continua desde ahi."
            )
            if free_mode:
                system = (
                    f"{system} Estas en modo libre. No estas encadenada a hablar solo del texto visible. "
                    "Podes conversar sobre otros temas aunque no dependan del documento activo. "
                    "El texto, el foco del laboratorio y los documentos de consulta siguen disponibles como contexto opcional, no como obligacion. "
                    "Si el usuario abre un tema ajeno al documento, no lo fuerces a volver al texto; segui la conversacion libremente."
                )
            if persona_overlay:
                system = f"{system} {persona_overlay}"
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
            "Operas dentro de Fusion Reader v2 en el laboratorio textual. "
            "Tu trabajo es conversar sobre el documento activo, lo que el usuario esta viendo en pantalla, "
            "los documentos de consulta cargados, y el material que el usuario pega o escribe en el chat de laboratorio. "
            "El documento activo es una fuente importante, pero no es la unica: si el usuario pregunta por "
            "lo que acaba de poner, pegar o decir, usa el historial reciente del laboratorio como contexto. "
            "Los documentos de consulta sirven como apoyo para comparar, ampliar o citar sin reemplazar al principal. "
            "Cuando el usuario pregunte si ves lo que acaba de poner, menciona brevemente el contenido reciente "
            "para confirmar que lo estas mirando. "
            "Si no hay documento cargado pero si hay material reciente en el chat, responde sobre ese material "
            "sin insistir en cargar un archivo. "
            "Si un documento aparece en el catalogo de documentos o en los extractos, tratalo como disponible aunque no este en pantalla. "
            "Si hay documentos de consulta, no omitas ninguno al enumerarlos. "
            "No dejes frases abiertas ni listas inconclusas: si la respuesta puede ser larga, prioriza cerrar "
            "pocas ideas completas antes que empezar muchas. "
            "No sos el motor de lectura en voz alta y no debes prometer controlar la voz salvo que sea un comando del lector. "
            "Si el usuario pide guardar o tomar una nota y esa accion no aparece ya confirmada por el sistema, no finjas haberla guardado. "
            "Responde en español, con claridad, y si el documento no alcanza para contestar decilo."
        )
        if free_mode:
            system = (
                f"{system} Estas en modo libre. No estas obligada a responder solo sobre el texto o lo visible en pantalla. "
                "Podes conversar con libertad sobre otros temas y usar el documento como contexto opcional cuando aporte. "
                "No arrastres cada respuesta de vuelta al texto si el usuario quiere abrir una conversacion mas amplia."
            )
        if persona_overlay:
            system = f"{system} {persona_overlay}"
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

    def _context_text(self, question: str, snapshot: dict, history: list[dict] | None = None, include_document: bool = True) -> str:
        document_text = str(snapshot.get("document_text") or "")
        if len(document_text) > self.max_document_chars:
            document_text = document_text[: self.max_document_chars].rstrip() + "\n\n[Documento recortado por limite de contexto.]"
        previous_chunk = str(snapshot.get("previous_chunk") or "")
        current_chunk = str(snapshot.get("current_chunk") or "")
        next_chunk = str(snapshot.get("next_chunk") or "")
        notes = snapshot.get("notes") if isinstance(snapshot.get("notes"), list) else []
        document_catalog = self._document_catalog_text(snapshot)
        reference_catalog = self._reference_catalog_text(snapshot.get("reference_documents"))
        laboratory_focus = self._laboratory_focus_text(snapshot.get("laboratory_focus"))
        relevant_document_text = self._relevant_documents_text(question, snapshot, history=history or [])
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
            "CATALOGO DE DOCUMENTOS:",
            document_catalog or "[No hay documentos cargados.]",
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
        if reference_catalog:
            lines.extend(["", "DOCUMENTOS DE CONSULTA:", reference_catalog])
        if laboratory_focus:
            lines.extend(["", "FOCO ACTUAL DEL LABORATORIO:", laboratory_focus])
        if relevant_document_text:
            lines.extend(["", "EXTRACTOS Y BLOQUES RELEVANTES:", relevant_document_text])
        if include_document:
            lines.extend(["", "DOCUMENTO COMPLETO DISPONIBLE:", document_text or "[No hay documento cargado.]"])
        return "\n".join(lines)

    def _document_catalog_text(self, snapshot: dict) -> str:
        records = self._document_records(snapshot)
        lines: list[str] = []
        for record in records:
            title = str(record.get("title") or "Sin titulo").strip() or "Sin titulo"
            doc_id = str(record.get("doc_id") or "").strip()
            total = int(record.get("total") or 0)
            source_type = str(record.get("source_type") or "").strip() or "text"
            role = "principal" if record.get("role") == "main" else "consulta"
            line = f"- {title}"
            if doc_id:
                line += f" ({doc_id})"
            line += f" | rol: {role} | tipo: {source_type} | bloques: {total}"
            lines.append(line)
        return "\n".join(lines).strip()

    def _document_records(self, snapshot: dict) -> list[dict]:
        records: list[dict] = []
        main_document = snapshot.get("main_document")
        if isinstance(main_document, dict) and (main_document.get("title") or main_document.get("doc_id")):
            records.append({**main_document, "role": "main", "current": int(snapshot.get("current") or 0)})
        elif snapshot.get("title") or snapshot.get("doc_id"):
            records.append(
                {
                    "doc_id": snapshot.get("doc_id") or "",
                    "title": snapshot.get("title") or "Sin titulo",
                    "source_type": "text",
                    "total": int(snapshot.get("total") or 0),
                    "text": snapshot.get("document_text") or "",
                    "chunks": snapshot.get("document_chunks") or [],
                    "role": "main",
                }
            )
        references = snapshot.get("reference_documents")
        if isinstance(references, list):
            for item in references:
                if isinstance(item, dict):
                    records.append({**item, "role": "reference"})
        return records

    def _reference_catalog_text(self, references: object) -> str:
        if not isinstance(references, list):
            return ""
        lines: list[str] = []
        for item in references[:12]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Consulta").strip() or "Consulta"
            doc_id = str(item.get("doc_id") or "").strip()
            total = int(item.get("total") or 0)
            source_type = str(item.get("source_type") or "").strip() or "text"
            line = f"- {title}"
            if doc_id:
                line += f" ({doc_id})"
            line += f" | tipo: {source_type} | bloques: {total}"
            lines.append(line)
        return "\n".join(lines).strip()

    def _laboratory_focus_text(self, focus: object) -> str:
        if not isinstance(focus, dict):
            return ""
        title = str(focus.get("title") or "").strip()
        if not title:
            return ""
        chunk_number = int(focus.get("chunk_number") or 0)
        total = int(focus.get("total") or 0)
        role = str(focus.get("role") or "").strip() or "consulta"
        reason = str(focus.get("reason") or "").strip()
        query = str(focus.get("query") or "").strip()
        text = str(focus.get("text") or "").strip()
        lines = [f"- {title} | rol: {role} | bloque: {chunk_number} de {total}"]
        if query:
            lines.append(f"  búsqueda: {query}")
        if reason:
            lines.append(f"  motivo: {reason}")
        if text:
            lines.append(f"  texto: {text[:500].rstrip()}{' [recortado]' if len(text) > 500 else ''}")
        return "\n".join(lines).strip()

    def _relevant_documents_text(self, question: str, snapshot: dict, history: list[dict] | None = None) -> str:
        records = self._document_records(snapshot)
        if not records:
            return ""
        keyword_text = " ".join(
            [
                str(question or ""),
                " ".join(
                    str(item.get("content") or "")
                    for item in (history or [])[-4:]
                    if isinstance(item, dict) and str(item.get("role") or "") == "user"
                ),
            ]
        )
        requested_chunk_numbers = self._extract_requested_chunk_numbers(keyword_text)
        selected = self._select_relevant_records(keyword_text, records)
        sections: list[str] = []
        remaining = max(self.max_document_excerpt_chars, self.max_reference_chars)
        for record in selected:
            section = self._render_document_excerpt(record, keyword_text, requested_chunk_numbers)
            if not section:
                continue
            if len(section) > remaining:
                section = section[:remaining].rstrip() + "\n[Extractos recortados por limite de contexto.]"
            sections.append(section)
            remaining -= len(section)
            if remaining <= 0:
                break
        return "\n\n".join(sections).strip()

    def _select_relevant_records(self, keyword_text: str, records: list[dict]) -> list[dict]:
        normalized = self._normalize_text(keyword_text)
        primary: list[tuple[int, int, dict]] = []
        fallback: list[dict] = []
        for index, record in enumerate(records):
            role_score = 40 if record.get("role") == "main" else 0
            title = self._normalize_text(str(record.get("title") or ""))
            doc_id = self._normalize_text(str(record.get("doc_id") or ""))
            preview = self._normalize_text(str(record.get("preview") or ""))
            score = role_score
            if normalized:
                if title and title in normalized:
                    score += 200
                if doc_id and doc_id in normalized:
                    score += 150
                score += self._keyword_overlap_score(normalized, f"{title} {doc_id} {preview}")
            primary.append((score, -index, record))
            fallback.append(record)
        primary.sort(reverse=True, key=lambda item: (item[0], item[1]))
        selected = [item[2] for item in primary if item[0] > 0]
        if not selected and fallback:
            selected.append(fallback[0])
        if len(selected) < len(fallback):
            for record in fallback:
                if record not in selected:
                    selected.append(record)
        return selected[:6]

    def _render_document_excerpt(self, record: dict, keyword_text: str, requested_chunk_numbers: list[int]) -> str:
        title = str(record.get("title") or "Sin titulo").strip() or "Sin titulo"
        doc_id = str(record.get("doc_id") or "").strip()
        total = int(record.get("total") or 0)
        source_type = str(record.get("source_type") or "").strip() or "text"
        role = "principal" if record.get("role") == "main" else "consulta"
        chunks = record.get("chunks")
        chunk_items = chunks if isinstance(chunks, list) else []
        selected_indexes = self._select_chunk_indexes(record, keyword_text, requested_chunk_numbers)
        lines = [f"[{role.upper()}] {title}{f' ({doc_id})' if doc_id else ''} | tipo: {source_type} | bloques: {total}"]
        preview = str(record.get("preview") or "").strip()
        if preview:
            lines.append(f"Resumen breve: {preview}")
        if not chunk_items:
            return "\n".join(lines)
        lines.append("Bloques disponibles para consulta:")
        for index in selected_indexes:
            if index < 0 or index >= len(chunk_items):
                continue
            item = chunk_items[index]
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            if len(text) > 420:
                text = text[:420].rstrip() + " [recortado]"
            lines.append(f"- Bloque {int(item.get('chunk_number') or index + 1)}: {text}")
        return "\n".join(lines)

    def _select_chunk_indexes(self, record: dict, keyword_text: str, requested_chunk_numbers: list[int]) -> list[int]:
        chunks = record.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            return []
        selected: list[int] = []
        if record.get("role") == "main":
            current = max(1, int(record.get("current") or 0))
            for number in (current - 1, current, current + 1):
                if 1 <= number <= len(chunks):
                    selected.append(number - 1)
        else:
            for index in range(min(self.max_intro_chunks_per_reference, len(chunks))):
                selected.append(index)
        for number in requested_chunk_numbers:
            if 1 <= number <= len(chunks):
                selected.append(number - 1)
        normalized_keywords = self._normalize_text(keyword_text)
        if normalized_keywords:
            scored: list[tuple[int, int]] = []
            for index, item in enumerate(chunks):
                chunk_text = self._normalize_text(str(item.get("text") or ""))
                score = self._keyword_overlap_score(normalized_keywords, chunk_text)
                if score > 0:
                    scored.append((score, -index))
            scored.sort(reverse=True)
            for _, neg_index in scored[: self.max_chunks_per_document]:
                selected.append(-neg_index)
        deduped: list[int] = []
        for index in selected:
            if index not in deduped:
                deduped.append(index)
        return deduped[: self.max_chunks_per_document]

    def _extract_requested_chunk_numbers(self, text: str) -> list[int]:
        numbers = []
        for raw in re.findall(r"\b(?:bloque|chunk|parte|secci[oó]n)\s+(\d{1,4})\b", str(text or ""), flags=re.IGNORECASE):
            try:
                value = int(raw)
            except ValueError:
                continue
            if value not in numbers:
                numbers.append(value)
        return numbers

    def _keyword_overlap_score(self, left: str, right: str) -> int:
        left_tokens = self._meaningful_tokens(left)
        right_tokens = set(self._meaningful_tokens(right))
        if not left_tokens or not right_tokens:
            return 0
        return sum(3 if token in right_tokens else 0 for token in left_tokens)

    def _meaningful_tokens(self, text: str) -> list[str]:
        stopwords = {
            "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas", "y", "o", "u",
            "que", "qué", "como", "cómo", "para", "por", "con", "sin", "sobre", "entre", "hay",
            "otro", "otra", "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas",
            "doc", "docs", "documento", "documentos", "consulta", "principal", "puedes", "podés",
            "ves", "veo", "mirar", "pantalla", "bloque",
        }
        tokens = re.findall(r"[a-záéíóúñ0-9_./-]{3,}", self._normalize_text(text))
        out: list[str] = []
        for token in tokens:
            if token in stopwords:
                continue
            if token not in out:
                out.append(token)
        return out

    def _normalize_text(self, text: str) -> str:
        return " ".join(str(text or "").strip().lower().split())
