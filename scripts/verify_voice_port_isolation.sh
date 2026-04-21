#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCTORA_ROOT="${DOCTORA_LUCY_ROOT:-/home/lucy-ubuntu/Escritorio/doctora-lucy}"
ALLTALK_DIR="${DIRECT_CHAT_ALLTALK_DIR:-/home/lucy-ubuntu/Archivo_proyectos/Taverna/Taverna-legacy/alltalk_tts}"
FUSION_TTS_PORT="${FUSION_READER_GPU_TTS_PORT:-7853}"
LUCY_TTS_PORT="${LUCY_TTS_PORT:-7854}"
LEGACY_TTS_PORT="${FUSION_READER_CPU_TTS_PORT:-7851}"
HISTORIC_PORT=7852
OWNER_FILE="${FUSION_READER_TTS_OWNER_FILE:-$ROOT/runtime/fusion_reader_v2/tts_owner.json}"

failures=0

fail() {
  echo "FAIL: $*" >&2
  failures=$((failures + 1))
}

ok() {
  echo "OK: $*"
}

require_file_contains() {
  local file="$1"
  local pattern="$2"
  [[ -f "$file" ]] || {
    fail "missing file: $file"
    return
  }
  grep -Fq -- "$pattern" "$file" || fail "$file does not contain required pattern: $pattern"
}

require_file_not_contains() {
  local file="$1"
  local pattern="$2"
  [[ -f "$file" ]] || {
    fail "missing file: $file"
    return
  }
  if grep -Fq -- "$pattern" "$file"; then
    fail "$file contains forbidden pattern: $pattern"
  fi
}

port_is_listening() {
  local port="$1"
  ss -ltn 2>/dev/null | grep -q "[.:]$port[[:space:]]"
}

owner_file_matches_fusion() {
  [[ -f "$OWNER_FILE" ]] || return 1
  grep -q '"owner"[[:space:]]*:[[:space:]]*"fusion_reader_v2"' "$OWNER_FILE" || return 1
  grep -q "\"port\"[[:space:]]*:[[:space:]]*$FUSION_TTS_PORT" "$OWNER_FILE" || return 1

  local owner_pid
  owner_pid="$(sed -n 's/.*"owner_pid"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$OWNER_FILE" | head -1)"
  [[ -n "$owner_pid" ]] || return 1
  [[ -r "/proc/$owner_pid/cmdline" ]] || return 1
  tr '\0' ' ' <"/proc/$owner_pid/cmdline" | grep -q -- "tts_server:app" || return 1
  tr '\0' ' ' <"/proc/$owner_pid/cmdline" | grep -q -- "--port $FUSION_TTS_PORT" || return 1
}

check_latest_doctora_boveda() {
  local db="$DOCTORA_ROOT/n8n_data/boveda_lucy.sqlite"
  if [[ ! -f "$db" ]]; then
    echo "WARN: Doctora boveda not found: $db" >&2
    return
  fi
  if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "WARN: sqlite3 not available; skipping Doctora boveda check" >&2
    return
  fi

  local latest
  latest="$(sqlite3 "$db" "SELECT coalesce(contenido_memoria,'') || ' ' || coalesce(metadatos,'') FROM memoria_core ORDER BY id DESC LIMIT 1;" 2>/dev/null || true)"
  if [[ -z "$latest" ]]; then
    fail "latest Doctora boveda memory is empty"
    return
  fi
  [[ "$latest" == *"$FUSION_TTS_PORT"* ]] || fail "latest Doctora boveda does not mention Fusion port $FUSION_TTS_PORT"
  [[ "$latest" == *"$LUCY_TTS_PORT"* ]] || fail "latest Doctora boveda does not mention Lucy port $LUCY_TTS_PORT"
  [[ "$latest" != *'"puerto_fusion":7852'* ]] || fail "latest Doctora boveda still assigns Fusion to 7852"
  [[ "$latest" != *'"puerto_fusion": 7852'* ]] || fail "latest Doctora boveda still assigns Fusion to 7852"
  [[ "$latest" != *'puerto_fusion:7852'* ]] || fail "latest Doctora boveda still assigns Fusion to 7852"
  [[ "$latest" != *'"alltalk_port":7851'* ]] || fail "latest Doctora boveda still assigns Lucy AllTalk to 7851"
  [[ "$latest" != *'"alltalk_port": 7851'* ]] || fail "latest Doctora boveda still assigns Lucy AllTalk to 7851"
}

check_latest_doctora_bunker() {
  local log="$DOCTORA_ROOT/data/lucy_bunker_log.jsonl"
  if [[ ! -f "$log" ]]; then
    echo "WARN: Doctora bunker log not found: $log" >&2
    return
  fi

  local latest
  latest="$(tail -n 1 "$log" 2>/dev/null || true)"
  if [[ -z "$latest" ]]; then
    fail "latest Doctora bunker log entry is empty"
    return
  fi
  [[ "$latest" == *"$FUSION_TTS_PORT"* ]] || fail "latest Doctora bunker entry does not mention Fusion port $FUSION_TTS_PORT"
  [[ "$latest" == *"$LUCY_TTS_PORT"* ]] || fail "latest Doctora bunker entry does not mention Lucy port $LUCY_TTS_PORT"
  [[ "$latest" != *'"puerto_fusion":7852'* ]] || fail "latest Doctora bunker still assigns Fusion to 7852"
  [[ "$latest" != *'"puerto_fusion": 7852'* ]] || fail "latest Doctora bunker still assigns Fusion to 7852"
  [[ "$latest" != *'"alltalk_port":7851'* ]] || fail "latest Doctora bunker still assigns Lucy AllTalk to 7851"
  [[ "$latest" != *'"alltalk_port": 7851'* ]] || fail "latest Doctora bunker still assigns Lucy AllTalk to 7851"
}

echo "Checking Fusion/Doctora voice port isolation..."

require_file_contains "$ROOT/AGENTS.md" "Fusion GPU URL: http://127.0.0.1:$FUSION_TTS_PORT"
require_file_contains "$ROOT/AGENTS.md" "Doctora Lucy/Antigravity owns $LUCY_TTS_PORT"
require_file_contains "$ROOT/FUSION_READER_V2_BLUEPRINT.md" "owner=fusion_reader_v2"
require_file_contains "$ROOT/FUSION_READER_V2_BLUEPRINT.md" "puerto GPU estable y protegido es $FUSION_TTS_PORT"
require_file_contains "$ROOT/agente/agent.yaml" "tts_url: http://127.0.0.1:$FUSION_TTS_PORT"
require_file_not_contains "$ROOT/agente/agent.yaml" "tts_url: http://127.0.0.1:$LEGACY_TTS_PORT"
require_file_contains "$ROOT/agente/system_prompt.md" "URL Fusion GPU: http://127.0.0.1:$FUSION_TTS_PORT"
require_file_contains "$ROOT/fusion_reader_v2/tts.py" "tts_foreign_doctora_lucy_port"
require_file_contains "$ROOT/fusion_reader_v2/tts.py" "tts_historic_unassigned_port"
require_file_contains "$ROOT/scripts/start_reader_neural_tts_gpu_5090.sh" "FUSION_READER_TTS_OWNER_FILE"
require_file_contains "$ROOT/scripts/start_reader_neural_tts_gpu_5090.sh" "owner_pid"
require_file_contains "$ROOT/scripts/start_fusion_reader_v2.sh" "fusion_tts_owner_ok"
require_file_contains "$ROOT/scripts/open_fusion_reader.sh" "fusion_gpu_ready"
require_file_not_contains "$ROOT/scripts/start_fusion_reader_v2.sh" "127.0.0.1:$HISTORIC_PORT"
require_file_not_contains "$ROOT/scripts/open_fusion_reader.sh" "127.0.0.1:$HISTORIC_PORT"

if [[ -d "$DOCTORA_ROOT" ]]; then
  require_file_contains "$DOCTORA_ROOT/VOICE_PORTS.md" "**$LUCY_TTS_PORT** | **Doctora Lucy TTS**"
  require_file_contains "$DOCTORA_ROOT/VOICE_PORTS.md" "**$FUSION_TTS_PORT** | **Fusion Reader TTS GPU**"
  require_file_contains "$DOCTORA_ROOT/AGENTS.md" "127.0.0.1:$LUCY_TTS_PORT"
  require_file_contains "$DOCTORA_ROOT/AGENTS.md" "127.0.0.1:$FUSION_TTS_PORT"
  require_file_contains "$DOCTORA_ROOT/GEMINI.md" "http://127.0.0.1:$LUCY_TTS_PORT"
  require_file_contains "$DOCTORA_ROOT/GEMINI.md" "http://127.0.0.1:$FUSION_TTS_PORT"
  require_file_contains "$DOCTORA_ROOT/bitacora_mantenimiento.md" "Doctora Lucy usa \`$LUCY_TTS_PORT\`; Fusion Reader v2 usa \`$FUSION_TTS_PORT\`"
  require_file_contains "$DOCTORA_ROOT/memoria/bitacora_mantenimiento.md" "Doctora Lucy usa AllTalk/TTS exclusivamente en \`127.0.0.1:$LUCY_TTS_PORT\`"
  require_file_contains "$DOCTORA_ROOT/memoria/demo.py" "Fusion Reader v2 usa $FUSION_TTS_PORT"
  require_file_contains "$DOCTORA_ROOT/scripts/lucy_alltalk.py" "127.0.0.1:$LUCY_TTS_PORT/api/generate"
  require_file_contains "$DOCTORA_ROOT/scripts/start_lucy_voice_tts.sh" 'PORT="${LUCY_TTS_PORT:-7854}"'
  require_file_contains "$DOCTORA_ROOT/scripts/start_lucy_voice_tts.sh" "--host 127.0.0.1"
  require_file_contains "$DOCTORA_ROOT/scripts/start_lucy_voice_tts.sh" "setsid"
  require_file_contains "$DOCTORA_ROOT/scripts/start_lucy_voice_tts.sh" ".gemini/antigravity/voice_env"
  require_file_contains "$DOCTORA_ROOT/.agents/workflows/boot.md" "127.0.0.1:$LUCY_TTS_PORT/api/ready"
  require_file_not_contains "$DOCTORA_ROOT/.agents/workflows/boot.md" "grep $LEGACY_TTS_PORT"
  require_file_not_contains "$DOCTORA_ROOT/scripts/start_lucy_voice_tts.sh" "fuser -k $FUSION_TTS_PORT"
  require_file_not_contains "$DOCTORA_ROOT/scripts/start_lucy_voice_tts.sh" "fuser -k $HISTORIC_PORT"
  check_latest_doctora_boveda
  check_latest_doctora_bunker
else
  echo "WARN: Doctora root not found: $DOCTORA_ROOT" >&2
fi

if [[ -d "$ALLTALK_DIR" ]]; then
  require_file_contains "$ALLTALK_DIR/launch.sh" 'PORT="${LUCY_TTS_PORT:-7854}"'
  require_file_contains "$ALLTALK_DIR/launch.sh" "Refusing to start on reserved/historical port"
  require_file_contains "$ALLTALK_DIR/launch.sh" "--host 127.0.0.1"
  require_file_not_contains "$ALLTALK_DIR/launch.sh" "--port $HISTORIC_PORT"
  require_file_not_contains "$ALLTALK_DIR/launch.sh" "fuser -k $HISTORIC_PORT"
  if [[ -f "$ALLTALK_DIR/launch.sh.bak" ]]; then
    require_file_contains "$ALLTALK_DIR/launch.sh.bak" 'PORT="${LUCY_TTS_PORT:-7854}"'
    require_file_contains "$ALLTALK_DIR/launch.sh.bak" "--host 127.0.0.1"
    require_file_not_contains "$ALLTALK_DIR/launch.sh.bak" "--port $HISTORIC_PORT"
    require_file_not_contains "$ALLTALK_DIR/launch.sh.bak" "fuser -k $HISTORIC_PORT"
  fi
else
  echo "WARN: AllTalk directory not found: $ALLTALK_DIR" >&2
fi

if port_is_listening "$HISTORIC_PORT"; then
  fail "historic/unassigned port $HISTORIC_PORT is listening"
else
  ok "historic port $HISTORIC_PORT is free"
fi

if port_is_listening "$FUSION_TTS_PORT"; then
  if owner_file_matches_fusion; then
    ok "Fusion TTS port $FUSION_TTS_PORT has owner=fusion_reader_v2"
  else
    fail "Fusion TTS port $FUSION_TTS_PORT is listening without a valid Fusion owner file"
  fi
else
  echo "WARN: Fusion TTS port $FUSION_TTS_PORT is not listening" >&2
fi

if port_is_listening "$LUCY_TTS_PORT"; then
  ok "Doctora Lucy TTS port $LUCY_TTS_PORT is listening"
else
  echo "WARN: Doctora Lucy TTS port $LUCY_TTS_PORT is not listening" >&2
fi

if port_is_listening "$LEGACY_TTS_PORT"; then
  echo "WARN: legacy/shared TTS port $LEGACY_TTS_PORT is listening; verify this is intentional" >&2
fi

if (( failures > 0 )); then
  echo "Voice port isolation FAILED with $failures issue(s)." >&2
  exit 1
fi

echo "Voice port isolation OK."
