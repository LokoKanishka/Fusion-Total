#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export FUSION_READER_CHAT_MODEL="${FUSION_READER_CHAT_MODEL:-qwen3:14b-q8_0}"
export FUSION_READER_CHAT_THINK="${FUSION_READER_CHAT_THINK:-1}"
export FUSION_READER_CHAT_NUM_PREDICT="${FUSION_READER_CHAT_NUM_PREDICT:-1536}"
export FUSION_READER_CHAT_TEMPERATURE="${FUSION_READER_CHAT_TEMPERATURE:-0.35}"

echo "Fusion Reader v2 modo academico"
echo "Modelo chat: ${FUSION_READER_CHAT_MODEL}"
echo "Thinking: ${FUSION_READER_CHAT_THINK}"
echo "Num predict: ${FUSION_READER_CHAT_NUM_PREDICT}"

exec "${SCRIPT_DIR}/start_fusion_reader_v2.sh"
