import time
import re

_CHAT_EVENTS = []

class ReaderChatController:
    """
    Parses natural language messages and dispatches commands to the reader store.
    Provides contextual responses based on the current reading chunk.
    """
    def __init__(self, store, library=None):
        self.store = store
        self.library = library

    def _call_ollama(self, prompt: str) -> str:
        """Helper to call local Ollama instance."""
        try:
            import requests
            # Using llama3.1:8b as discussed
            resp = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "llama3.1:8b",
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3}
                },
                timeout=15
            )
            if resp.status_code == 200:
                return resp.json().get("response", "").strip()
            return f"[Error Ollama {resp.status_code}]"
        except Exception as e:
            return f"[Error de conexión con Ollama: {e}]"

    def handle_message(self, session_id: str, message: str) -> dict:
        msg = message.lower().strip()

        # 0. Library Commands (New for UI compatibility)
        if any(x in msg for x in ["biblioteca", "libros", "qué hay para leer"]):
            if self.library:
                books = self.library.list_books().get("books", [])
                titles = [f"- {b['title']} (id: {b['id']})" for b in books]
                return {"ok": True, "reply": "Libros disponibles:\n" + "\n".join(titles), "action": "library"}
            return {"ok": False, "reply": "No cargué la biblioteca todavía."}

        match_read = re.search(r"leer\s+(?:el\s+libro\s+)?(\d+|[\w-]+)", msg)
        if match_read:
            target = match_read.group(1)
            # Support "leer libro 1" -> first book
            if target.isdigit():
                if self.library:
                    books = self.library.list_books().get("books", [])
                    idx = int(target) - 1
                    if 0 <= idx < len(books):
                        target = books[idx]["id"]

            # Start or restart session
            self.store.start_session(session_id, book_id=target)
            return {"ok": True, "reply": f"Iniciando lectura de '{target}'.", "action": "start"}

        # 1. Basic Command Parsing (Deterministic)
        if any(x in msg for x in ["seguí", "segui", "continua", "sigue", "resume", "dale"]):
            # Natural "continue": We transition the state back to reading
            self.store.resume_session(session_id)
            return {"ok": True, "reply": "Entendido, reanudo la lectura.", "action": "resume"}

        if any(x in msg for x in ["pausa", "pará", "detente", "espera", "stop"]):
            self.store.mark_barge_in(session_id, detail="manual_pause")
            return {"ok": True, "reply": "Lectura pausada.", "action": "pause"}

        if any(x in msg for x in ["repetí", "otra vez", "no escuché"]):
            # Reset pending to force replay of current cursor
            def _reset(state):
                sess = state.get(session_id)
                if sess: sess["pending"] = None
                return {"ok": True}
            self.store._with_state(True, _reset)
            self.store.resume_session(session_id)
            return {"ok": True, "reply": "Repito el último fragmento.", "action": "repeat"}

        # 2. Navigation / Seek
        # "andá al párrafo 3" -> seek index 2
        match_p = re.search(r"(?:parrafo|párrafo|bloque|página|pagina)\s+(\d+)", msg)
        if match_p:
            idx = int(match_p.group(1)) - 1
            def _seek_idx(state):
                sess = state.get(session_id)
                if not sess: return {"ok": False}
                if 0 <= idx < sess.get("total_chunks", 0):
                    sess["cursor"] = idx
                    sess["pending"] = None
                    # Ensure state is reading so it's picked up
                    sess["reader_state"] = "reading"
                    return {"ok": True, "cursor": idx}
                return {"ok": False, "error": "out_of_bounds"}
            res = self.store._with_state(True, _seek_idx)
            if res.get("ok"):
                # No need to call resume_session separately if we set it in _seek_idx
                return {"ok": True, "reply": f"Entendido, saltando al párrafo {idx+1}. Retomo la lectura desde allí.", "action": "seek"}
            else:
                return {"ok": False, "reply": f"No pude encontrar el párrafo {idx+1}. El texto tiene {self.store.get_session(session_id).get('total_chunks', 0)} párrafos."}

        # 3. Contextual Note Capture (Tramo 2)
        is_note_capture = any(x in msg for x in ["anotar resumen", "guardar nota", "tomar nota", "anotar esto"])
        if is_note_capture:
            sess = self.store.get_session(session_id, include_chunks=True)
            if not sess or not sess.get("ok"):
                return {"ok": False, "reply": "No hay lectura activa para anotar."}

            import app.documents
            import app.notes

            cursor = sess.get("cursor", 0)
            page_num = app.documents.get_page_for_chunk(cursor, 5)
            chunks = app.documents.get_chunks_for_page(sess.get("chunks", []), page_num, 5)
            page_text = "\n".join(chunks)

            if not page_text.strip():
                return {"ok": False, "reply": "La página actual está vacía."}

            prompt = f"Resume brevemente estos párrafos para una nota de estudio (máximo 2 oraciones directas):\n\n'{page_text[:1500]}'"
            response = self._call_ollama(prompt)

            app.notes.add_note(sess.get("book_id"), page_num, response, role="ai")

            return {"ok": True, "reply": f"Guardé esta nota inteligente en la página {page_num}:\n\n{response}", "action": "note_saved"}

        # 4. Contextual Question (¿...?)
        # Detect intent: summarize vs explain
        is_summary = any(x in msg for x in ["resumi", "resumen", "resúm", "sintetiza"])
        is_explanation = any(x in msg for x in ["explica", "que quiso decir", "no entendi", "qué significa"]) or "?" in msg

        if is_summary or is_explanation:
            sess = self.store.get_session(session_id)
            if not sess or not sess.get("ok"):
                return {"ok": False, "reply": "No tengo una sesión activa de lectura para responder."}

            chunk_text = ""
            pending = sess.get("pending")
            last_active = sess.get("last_active_chunk")

            if pending:
                chunk_text = pending.get("text", "")
            elif last_active:
                chunk_text = last_active.get("text", "")

            if not chunk_text:
                return {"ok": True, "reply": "No estoy seguro de a qué parte te refieres. ¿Podemos seguir leyendo?"}

            if is_summary:
                prompt = f"Resume el siguiente fragmento de texto de forma muy breve, directa y sintética. Máximo 2 oraciones:\n\n'{chunk_text}'"
                response = self._call_ollama(prompt)
                return {"ok": True, "reply": f"[Resumen]: {response}", "intent": "summarize"}
            else:
                prompt = f"Explica qué quiso decir el autor en este fragmento de forma conversacional y clara:\n\n'{chunk_text}'"
                response = self._call_ollama(prompt)
                return {"ok": True, "reply": f"[Explicación]: {response}", "intent": "explain"}

        return {"ok": True, "reply": "Recibido. No estoy seguro de cómo procesar ese comando, pero estoy atento.", "action": "none"}

