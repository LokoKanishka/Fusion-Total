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

    def handle_message(self, session_id: str, message: str) -> dict:
        msg = message.lower().strip()
        
        # 1. Basic Command Parsing (Deterministic)
        if any(x in msg for x in ["seguí", "continua", "sigue", "resume", "dale"]):
            # Natural "continue": commit current and get next
            # We simulate the flow: commit the last pending if any, then signal readiness
            # In simple terms, we just return an instruction or trigger the next chunk logic.
            # But the chat API usually returns a text response.
            # We'll trigger a commit + status update.
            self.store.update_progress(session_id) 
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
            return {"ok": True, "response": "Repito el último fragmento.", "action": "repeat"}

        # 2. Navigation / Seek
        # "andá al párrafo 3" -> seek index 2
        match_p = re.search(r"(?:parrafo|párrafo|bloque)\s+(\d+)", msg)
        if match_p:
            idx = int(match_p.group(1)) - 1
            def _seek_idx(state):
                sess = state.get(session_id)
                if not sess: return {"ok": False}
                if 0 <= idx < sess.get("total_chunks", 0):
                    sess["cursor"] = idx
                    sess["pending"] = None
                    return {"ok": True, "cursor": idx}
                return {"ok": False, "error": "out_of_bounds"}
            res = self.store._with_state(True, _seek_idx)
            if res.get("ok"):
                return {"ok": True, "response": f"Entendido, saltando al párrafo {idx+1}.", "action": "seek"}
            else:
                return {"ok": False, "response": "No pude encontrar ese párrafo."}

        # "continuá desde <frase>"
        match_f = re.search(r"(?:desde|frase)\s+['\"]?([^'\"]+)['\"]?", msg)
        if match_f:
            phrase = match_f.group(1)
            res = self.store.seek_phrase(session_id, phrase)
            if res.get("ok"):
                return {"ok": True, "response": f"Localizado. Reanudo desde '{phrase}'.", "action": "seek"}
            else:
                return {"ok": False, "response": f"No encontré la frase '{phrase}' en el texto."}

        # 3. Contextual Question (¿...?)
        if "?" in msg or any(x in msg for x in ["explicame", "que quiso decir", "resumi", "no entendi"]):
            sess = self.store.get_session(session_id)
            if not sess or not sess.get("ok"):
                return {"ok": False, "response": "No tengo una sesión activa de lectura para responder."}
            
            # Use chunks if available in memory or re-read from disk
            # For brevity in this hotfix, we use the text of the current/last chunk
            chunk_text = ""
            # Logic: If reading, use pending. If paused/commenting, use the one at cursor-1 or current cursor
            # In the modular store, 'pending' holds what was last delivered.
            pending = sess.get("pending")
            if pending:
                chunk_text = pending.get("text", "")
            
            if not chunk_text:
                return {"ok": True, "response": "No estoy seguro de qué parte te refieres. ¿Podemos seguir leyendo?"}

            # Response logic (In a real product, this goes to an LLM)
            # Here we demonstrate that we KNOW the context.
            return {
                "ok": True, 
                "response": f"Sobre el fragmento que dice '{chunk_text[:50]}...', entiendo que estamos hablando del tema central del libro. ¿Quieres que profundice o seguimos?",
                "context_used": True
            }

        return {"ok": True, "response": "Recibido. No estoy seguro de cómo procesar ese comando, pero estoy atento.", "action": "none"}
