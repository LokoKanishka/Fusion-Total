#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${FUSION_READER_V2_PORT:-8010}"
GPU_TTS_PORT="${FUSION_READER_GPU_TTS_PORT:-7853}"
CPU_TTS_PORT="${FUSION_READER_CPU_TTS_PORT:-${DIRECT_CHAT_ALLTALK_PORT:-7851}}"
GPU_TTS_URL="http://127.0.0.1:${GPU_TTS_PORT}"
CPU_TTS_URL="http://127.0.0.1:${CPU_TTS_PORT}"
OWNER_FILE="${FUSION_READER_TTS_OWNER_FILE:-$ROOT/runtime/fusion_reader_v2/tts_owner.json}"
GPU_TTS_WAIT_SECONDS="${FUSION_READER_GPU_TTS_WAIT_SECONDS:-30}"

cd "$ROOT"

source "$ROOT/scripts/fusion_reader_gpu_guard.sh"
fusion_reader_refuse_when_gpu_conflict "Fusion Reader v2 academic/chat server"
if fusion_reader_apply_game_coexistence_mode; then
  echo "Modo convivencia GPU activo: chat sin thinking, num_ctx=${FUSION_READER_CHAT_NUM_CTX}, num_predict=${FUSION_READER_CHAT_NUM_PREDICT}" >&2
fi

export FUSION_READER_CHAT_MODEL="${FUSION_READER_CHAT_MODEL:-qwen3:14b-q8_0}"
export FUSION_READER_CHAT_THINK="${FUSION_READER_CHAT_THINK:-1}"
export FUSION_READER_CHAT_NUM_PREDICT="${FUSION_READER_CHAT_NUM_PREDICT:-1536}"
if [[ -z "${FUSION_READER_REASONING_MODE:-}" ]]; then
  if [[ "${FUSION_READER_CHAT_THINK}" == "0" || "${FUSION_READER_CHAT_THINK,,}" == "false" || "${FUSION_READER_CHAT_THINK,,}" == "no" ]]; then
    export FUSION_READER_REASONING_MODE="normal"
  else
    export FUSION_READER_REASONING_MODE="thinking"
  fi
fi

fusion_tts_owner_ok() {
  [[ -f "$OWNER_FILE" ]] || return 1
  grep -q '"owner"[[:space:]]*:[[:space:]]*"fusion_reader_v2"' "$OWNER_FILE" || return 1
  grep -q "\"port\"[[:space:]]*:[[:space:]]*$GPU_TTS_PORT" "$OWNER_FILE" || return 1

  local owner_pid
  owner_pid="$(sed -n 's/.*"owner_pid"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$OWNER_FILE" | head -1)"
  if [[ -z "$owner_pid" ]]; then
    return 1
  fi
  [[ -r "/proc/$owner_pid/cmdline" ]] || return 1
  tr '\0' ' ' <"/proc/$owner_pid/cmdline" | grep -q -- "tts_server:app" || return 1
  tr '\0' ' ' <"/proc/$owner_pid/cmdline" | grep -q -- "--port $GPU_TTS_PORT" || return 1
}

fusion_gpu_ready() {
  curl -fsS --max-time 1 "${GPU_TTS_URL}/api/ready" >/dev/null 2>&1 && fusion_tts_owner_ok
}

select_fusion_tts_url() {
  if fusion_gpu_ready; then
    export FUSION_READER_ALLTALK_URL="$GPU_TTS_URL"
    echo "Fusion TTS URL selected: ${FUSION_READER_ALLTALK_URL}" >&2
    return 0
  fi

  gpu_wait_deadline=$(( $(date +%s) + GPU_TTS_WAIT_SECONDS ))
  while (( $(date +%s) < gpu_wait_deadline )); do
    if fusion_gpu_ready; then
      export FUSION_READER_ALLTALK_URL="$GPU_TTS_URL"
      echo "Fusion TTS URL selected: ${FUSION_READER_ALLTALK_URL}" >&2
      return 0
    fi
    sleep 1
  done

  export FUSION_READER_ALLTALK_URL="$CPU_TTS_URL"
  if curl -fsS --max-time 1 "${GPU_TTS_URL}/api/ready" >/dev/null 2>&1; then
    echo "AllTalk en ${GPU_TTS_URL} respondio Ready pero no tiene owner valido; usando fallback: ${FUSION_READER_ALLTALK_URL}" >&2
  else
    echo "AllTalk GPU Fusion no quedo listo tras ${GPU_TTS_WAIT_SECONDS}s; usando fallback: ${FUSION_READER_ALLTALK_URL}" >&2
  fi
  echo "Fusion TTS fallback selected: ${FUSION_READER_ALLTALK_URL}" >&2
}

if [[ "${FUSION_READER_GAME_COEXISTENCE_ACTIVE:-0}" == "1" ]]; then
  if fusion_gpu_ready; then
    echo "Modo convivencia GPU activo, pero Fusion conserva su TTS GPU owner-valid." >&2
    select_fusion_tts_url
  else
    export FUSION_READER_ALLTALK_URL="$CPU_TTS_URL"
    echo "Modo convivencia GPU: usando TTS CPU/fallback: ${FUSION_READER_ALLTALK_URL}" >&2
    echo "Fusion TTS fallback selected: ${FUSION_READER_ALLTALK_URL}" >&2
  fi
else
  select_fusion_tts_url
fi

echo "Fusion Reader v2: http://127.0.0.1:${PORT}"
echo "AllTalk esperado: ${FUSION_READER_ALLTALK_URL}"
echo "Puerto GPU Fusion reservado: ${GPU_TTS_PORT}"
echo "Prefetch ahead: ${FUSION_READER_PREFETCH_AHEAD:-3}"
echo "Modelo chat: ${FUSION_READER_CHAT_MODEL}"
echo "Thinking chat: ${FUSION_READER_CHAT_THINK:-0}"
echo "Modo razonamiento: ${FUSION_READER_REASONING_MODE}"

exec python3 scripts/fusion_reader_v2_server.py
