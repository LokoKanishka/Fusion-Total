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
        
        # 1. Basic Command Parsing (Deterministic)
        if any(x in msg for x in ["seguí", "continua", "sigue", "resume", "dale"]):
            # Natural "continue": We transition the state back to reading
            self.store.resume_session(session_id) 
            return {"ok": True, "response": "Entendido, reanudo la lectura.", "action": "resume"}

        if any(x in msg for x in ["pausa", "pará", "detente", "espera", "stop"]):
            self.store.mark_barge_in(session_id, detail="manual_pause")
            return {"ok": True, "response": "Lectura pausada.", "action": "pause"}

        if any(x in msg for x in ["repetí", "otra vez", "no escuché"]):
            # Reset pending to force replay of current cursor
            def _reset(state):
                sess = state.get(session_id)
                if sess: sess["pending"] = None
                return {"ok": True}
            self.store._with_state(True, _reset)
            self.store.resume_session(session_id)
            return {"ok": True, "response": "Repito el último fragmento.", "action": "repeat"}

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
                return {"ok": True, "response": f"Entendido, saltando al párrafo {idx+1}. Retomo la lectura desde allí.", "action": "seek"}
            else:
                return {"ok": False, "response": f"No pude encontrar el párrafo {idx+1}. El texto tiene {self.store.get_session(session_id).get('total_chunks', 0)} párrafos."}

        # 3. Contextual Question (¿...?)
        # Detect intent: summarize vs explain
        is_summary = any(x in msg for x in ["resumi", "resumen", "resúm", "sintetiza"])
        is_explanation = any(x in msg for x in ["explica", "que quiso decir", "no entendi", "qué significa"]) or "?" in msg

        if is_summary or is_explanation:
            sess = self.store.get_session(session_id)
            if not sess or not sess.get("ok"):
                return {"ok": False, "response": "No tengo una sesión activa de lectura para responder."}
            
            chunk_text = ""
            pending = sess.get("pending")
            last_active = sess.get("last_active_chunk")
            
            if pending:
                chunk_text = pending.get("text", "")
            elif last_active:
                chunk_text = last_active.get("text", "")
            
            if not chunk_text:
                return {"ok": True, "response": "No estoy seguro de a qué parte te refieres. ¿Podemos seguir leyendo?"}

            if is_summary:
                prompt = f"Resume el siguiente fragmento de texto de forma muy breve, directa y sintética. Máximo 2 oraciones:\n\n'{chunk_text}'"
                response = self._call_ollama(prompt)
                return {"ok": True, "response": f"[Resumen]: {response}", "intent": "summarize"}
            else:
                prompt = f"Explica qué quiso decir el autor en este fragmento de forma conversacional y clara:\n\n'{chunk_text}'"
                response = self._call_ollama(prompt)
                return {"ok": True, "response": f"[Explicación]: {response}", "intent": "explain"}

        return {"ok": True, "response": "Recibido. No estoy seguro de cómo procesar ese comando, pero estoy atento.", "action": "none"}

