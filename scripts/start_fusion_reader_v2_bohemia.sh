#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BOHEMIA_MODEL="huihui_ai/qwen3-abliterated:14b-v2-q8_0"
export FUSION_READER_BOHEMIA_CHAT_MODEL="$BOHEMIA_MODEL"

# Fallar de forma clara si el modelo no está instalado
if ! curl -s http://127.0.0.1:11434/api/tags | grep -q "\"name\":\"${BOHEMIA_MODEL}\""; then
  echo "Error: El modelo Bohemia ($BOHEMIA_MODEL) no está instalado en Ollama local." >&2
  exit 1
fi

echo "Académica: qwen3:14b-q8_0"
echo "Bohemia: $BOHEMIA_MODEL"

exec "$ROOT/scripts/start_fusion_reader_v2.sh" "$@"
