import os
import json
import requests
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_AGENT_PROMPT_PATH = _PROJECT_ROOT / "agente" / "system_prompt.md"

def _build_system_prompt(mode: str, allowed_tools: set[str]) -> str:
    try:
        prompt = _AGENT_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        prompt = "Eres un lector conversacional dedicado a leer y conversar sobre documentos activos."

    tools = ", ".join(sorted(str(t) for t in allowed_tools)) if allowed_tools else "ninguna"
    return (
        f"{prompt}\n\n"
        f"# Contexto De Ejecucion\n"
        f"- modo: {mode or 'reader'}\n"
        f"- herramientas permitidas: {tools}\n"
    )

def agent_manifest() -> dict:
    return {
        "id": "lector_conversacional",
        "path": str(_PROJECT_ROOT / "agente" / "agent.yaml"),
        "system_prompt_path": str(_AGENT_PROMPT_PATH),
    }

def _model_catalog(force_refresh: bool = False) -> dict:
    # Catalog logic
    return {"models": []}

class ModelRouter:
    def __init__(self, gateway_url: str = "http://127.0.0.1:8001") -> None:
        self.gateway_url = gateway_url

    def call(self, provider: str, payload: dict) -> dict:
        # Logic for calling Ollama or Gateway
        return {}
