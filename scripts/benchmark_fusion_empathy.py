#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fusion_reader_v2.conversation import ConversationCore, OllamaChatProvider


TASKS = [
    {
        "key": "bug_fatigue_support",
        "history": [],
        "question": (
            "Me frustre porque toque repetir por error y senti que se colgo todo. "
            "Estoy cansada. Ayudame a pensar el problema sin tratarme como tonta."
        ),
        "expected_any": ["veamos", "podemos", "juntas", "cansada", "frustr"],
        "forbidden_any": ["documento", "bloque", "pantalla", "texto visible"],
    },
    {
        "key": "accepts_correction_gently",
        "history": [
            {"role": "user", "content": "No, no iba por ahi."},
            {"role": "assistant", "content": "Entiendo, corrijamos el rumbo."},
        ],
        "question": "No era eso: solo queria una respuesta simple y humana, sin tanto analisis.",
        "expected_any": ["entiendo", "bien", "humana", "simple", "decime", "queres"],
        "forbidden_any": ["segun el documento", "bloque", "extracto"],
    },
    {
        "key": "stays_in_free_chat",
        "history": [],
        "question": "Necesito bajar un cambio un minuto. Quedate conmigo y hablame normal.",
        "expected_any": ["aca", "contigo", "tranqui", "respira", "estoy"],
        "forbidden_any": ["documento", "texto", "archivo", "bloque"],
    },
    {
        "key": "warm_practical_guidance",
        "history": [],
        "question": "Estoy medio trabada y necesito que me ordenes sin retarme. Dame un paso corto para seguir.",
        "expected_any": ["paso", "corto", "primero", "hagamos", "seguimos", "podemos"],
        "forbidden_any": ["no puedo", "no es posible", "carga un documento"],
    },
]


def snapshot_for(history: list[dict]) -> dict:
    return {
        "doc_id": "",
        "title": "",
        "current": 0,
        "total": 0,
        "current_chunk": "",
        "previous_chunk": "",
        "next_chunk": "",
        "document_text": "",
        "notes": [],
        "main_document": {},
        "document_chunks": [],
        "reference_documents": [],
        "laboratory_focus": {},
        "laboratory_mode": {"mode": "free", "label": "Libre"},
        "laboratory_history": history,
    }


def score_empathy(answer: str, expected_any: list[str], forbidden_any: list[str]) -> dict:
    clean = " ".join(str(answer or "").lower().split())
    hits = [term for term in expected_any if term.lower() in clean]
    penalties = [term for term in forbidden_any if term.lower() in clean]
    if len(clean) < 60:
        penalties.append("demasiado_breve")
    return {
        "hits": hits,
        "hit_count": len(hits),
        "penalties": penalties,
        "score": len(hits) - len(penalties),
    }


def build_core(provider_name: str) -> ConversationCore:
    if provider_name == "qwen":
        provider = OllamaChatProvider(default_model="qwen3:14b-q8_0")
    elif provider_name == "gptoss_multilingual_reasoner_q8":
        provider = OllamaChatProvider(default_model="gpt-oss:20b-multilingual-reasoner-q8")
    elif provider_name == "gptoss_sanguine_q8":
        provider = OllamaChatProvider(default_model="gpt-oss:20b-sanguine-q8")
    elif provider_name == "nemo":
        provider = OllamaChatProvider(default_model="mistral-nemo:12b-q8")
    else:
        raise ValueError(provider_name)
    return ConversationCore(provider=provider)


def run_suite(provider_name: str, mode: str) -> dict:
    core = build_core(provider_name)
    rows = []
    for task in TASKS:
        snapshot = snapshot_for(task["history"])
        started = time.perf_counter()
        result = core.ask(task["question"], snapshot=snapshot, history=task["history"], reasoning_mode=mode)
        wall_ms = int((time.perf_counter() - started) * 1000)
        scored = score_empathy(result.answer, task["expected_any"], task["forbidden_any"])
        rows.append(
            {
                "task": task["key"],
                "ok": result.ok,
                "model": result.model,
                "reasoning_mode": result.reasoning_mode,
                "duration_ms": result.duration_ms,
                "wall_ms": wall_ms,
                "score": scored["score"],
                "hit_count": scored["hit_count"],
                "hits": scored["hits"],
                "penalties": scored["penalties"],
                "answer": result.answer,
            }
        )
    return {"provider_name": provider_name, "mode": mode, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--providers",
        nargs="+",
        choices=["qwen", "gptoss_multilingual_reasoner_q8", "gptoss_sanguine_q8", "nemo"],
        default=["qwen", "nemo"],
    )
    parser.add_argument("--mode", choices=["normal", "thinking", "supreme"], default="normal")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    report = {
        "generated_at": int(time.time()),
        "results": [run_suite(provider_name, args.mode) for provider_name in args.providers],
    }
    rendered = json.dumps(report, ensure_ascii=True, indent=2)
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("FUSION_READER_CHAT_TEMPERATURE", "0.35")
    os.environ.setdefault("FUSION_READER_CHAT_NUM_CTX", "8192")
    os.environ.setdefault("FUSION_READER_CHAT_NUM_PREDICT_NORMAL", "220")
    raise SystemExit(main())
