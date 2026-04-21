#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_BIN="${HOME}/.openclaw/bin/openclaw"
OLLAMA_BIN="${HOME}/.openclaw/bin/ollama"
CLOUD_MODEL="openai-codex/gpt-5.1-codex-mini"

cmd="${1:-}"
shift || true

if [[ "$cmd" == "check" ]]; then
  echo "MODEL_ROUTER_OK"
  exit 0
fi

if [[ "$cmd" != "ask-with-fallback" ]]; then
  echo "usage: model_router.sh check|ask-with-fallback <message>" >&2
  exit 2
fi

message="${*:-}"
"$OPENCLAW_BIN" models set "$CLOUD_MODEL" >/dev/null 2>&1 || true
first="$("$OPENCLAW_BIN" agent --json "$message" 2>&1 || true)"
if [[ "$first" != *"429"* && "$first" != *"rate limit"* ]]; then
  printf '%s\n' "$first"
  exit 0
fi

fallback_model=""
if [[ -x "$OLLAMA_BIN" ]]; then
  if "$OLLAMA_BIN" list 2>/dev/null | awk 'NR > 1 {print $1}' | grep -qx 'mistral-uncensored:latest'; then
    fallback_model="ollama/mistral-uncensored:latest"
  else
    first_installed="$("$OLLAMA_BIN" list 2>/dev/null | awk 'NR == 2 {print $1}')"
    if [[ -n "$first_installed" ]]; then
      fallback_model="ollama/${first_installed}"
    fi
  fi
fi

if [[ -z "$fallback_model" ]]; then
  printf '%s\n' "$first"
  exit 1
fi

"$OPENCLAW_BIN" models set "$fallback_model" >/dev/null 2>&1 || true
"$OPENCLAW_BIN" agent --json "$message"
