#!/usr/bin/env bash

fusion_reader_gpu_conflict_processes() {
  local line lower
  ps -eo pid=,comm=,args= 2>/dev/null | while IFS= read -r line; do
    lower="${line,,}"
    if [[ "$lower" == *"/bg3"* ]] \
      || [[ "$lower" == *"baldur"* && "$lower" == *"gate 3"* ]] \
      || [[ "$lower" == *"steam://rungameid/1086940"* ]] \
      || [[ "$lower" == *"steamapps/common/baldurs gate 3"* ]] \
      || [[ "$lower" == *"steam_app_1086940"* ]]; then
      printf '%s\n' "$line"
    fi
  done
}

fusion_reader_gpu_conflict_active() {
  [[ "${FUSION_READER_GPU_GUARD:-1}" != "0" ]] || return 1
  [[ "${FUSION_READER_ALLOW_GPU_WITH_GAMES:-0}" != "1" ]] || return 1
  [[ -n "$(fusion_reader_gpu_conflict_processes)" ]]
}

fusion_reader_gpu_conflict_summary() {
  fusion_reader_gpu_conflict_processes | sed -n '1,8p'
}

fusion_reader_refuse_when_gpu_conflict() {
  local component="${1:-Fusion Reader GPU service}"
  if ! fusion_reader_gpu_conflict_active; then
    return 0
  fi

  if [[ "${FUSION_READER_GPU_CONFLICT_POLICY:-warn}" == "block" ]]; then
    echo "Refusing to start ${component}: GPU game/process detected." >&2
    echo "Detected process(es):" >&2
    fusion_reader_gpu_conflict_summary >&2
    echo "" >&2
    echo "Close the game first, or override deliberately with:" >&2
    echo "  FUSION_READER_ALLOW_GPU_WITH_GAMES=1 ${0}" >&2
    exit 3
  fi

  echo "Warning: ${component} is starting while a GPU game/process is active." >&2
  echo "Fusion will prefer lighter coexistence settings where the launcher supports it." >&2
  echo "Detected process(es):" >&2
  fusion_reader_gpu_conflict_summary >&2
}

fusion_reader_apply_game_coexistence_mode() {
  fusion_reader_gpu_conflict_active || return 1
  [[ "${FUSION_READER_GAME_COEXISTENCE:-1}" != "0" ]] || return 1

  export FUSION_READER_GAME_COEXISTENCE_ACTIVE=1
  export FUSION_READER_CHAT_THINK=0
  export FUSION_READER_CHAT_NUM_PREDICT="${FUSION_READER_CHAT_NUM_PREDICT_WITH_GAME:-384}"
  export FUSION_READER_CHAT_NUM_CTX="${FUSION_READER_CHAT_NUM_CTX_WITH_GAME:-8192}"
  return 0
}
