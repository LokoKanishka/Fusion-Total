import os
import json
import requests
from pathlib import Path

def _build_system_prompt(mode: str, allowed_tools: set[str]) -> str:
    # Prompt construction logic
    return "You are a conversational reader."

def _model_catalog(force_refresh: bool = False) -> dict:
    # Catalog logic
    return {"models": []}

class ModelRouter:
    def __init__(self, gateway_url: str = "http://127.0.0.1:8001") -> None:
        self.gateway_url = gateway_url

    def call(self, provider: str, payload: dict) -> dict:
        # Logic for calling Ollama or Gateway
        return {}
