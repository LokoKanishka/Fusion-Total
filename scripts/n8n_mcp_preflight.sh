#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

N8N_URL="${N8N_URL:-http://127.0.0.1:5678/healthz}"
DC_ENV_FILE="${DC_ENV_FILE:-runtime/direct_chat.env}"
EXPECT_CLOUD_MODEL="${EXPECT_CLOUD_MODEL:-openai-codex/gpt-5.1-codex-mini}"
EXPECT_LOCAL_MODEL="${EXPECT_LOCAL_MODEL:-qwen2.5-coder:14b-instruct-q8_0}"
ROUTING_CONFIG="${ROUTING_CONFIG:-config/n8n_flow_routing.json}"
MCP_CONFIG="${MCP_CONFIG:-config/n8n_mcp_matrix.json}"
MCP_ACTION_POLICY_CONFIG="${MCP_ACTION_POLICY_CONFIG:-config/mcp_action_policies.json}"

need_bin() {
  local bin="$1"
  command -v "$bin" >/dev/null 2>&1 || {
    echo "PREFLIGHT_FAIL missing_bin=$bin" >&2
    exit 1
  }
}

need_bin docker
need_bin curl
need_bin rg

./scripts/fusion_isolation_guard.sh check >/dev/null
echo "PREFLIGHT_OK isolation_guard"

http_code="$(curl -sS -o /dev/null -w '%{http_code}' "$N8N_URL" 2>/dev/null || true)"
if [[ "$http_code" != "200" ]]; then
  echo "PREFLIGHT_FAIL n8n_health http=$http_code url=$N8N_URL" >&2
  exit 2
fi
echo "PREFLIGHT_OK n8n_health url=$N8N_URL"

if [[ ! -f "$DC_ENV_FILE" ]]; then
  echo "PREFLIGHT_FAIL missing_dc_env file=$DC_ENV_FILE" >&2
  exit 3
fi
if [[ ! -f "$ROUTING_CONFIG" ]]; then
  echo "PREFLIGHT_FAIL missing_routing_config file=$ROUTING_CONFIG" >&2
  exit 7
fi
if [[ ! -f "$MCP_CONFIG" ]]; then
  echo "PREFLIGHT_FAIL missing_mcp_config file=$MCP_CONFIG" >&2
  exit 8
fi
if [[ ! -f "$MCP_ACTION_POLICY_CONFIG" ]]; then
  echo "PREFLIGHT_FAIL missing_mcp_action_policy_config file=$MCP_ACTION_POLICY_CONFIG" >&2
  exit 9
fi

if ! rg -n "^DIRECT_CHAT_CLOUD_MODELS=${EXPECT_CLOUD_MODEL}$" "$DC_ENV_FILE" >/dev/null; then
  echo "PREFLIGHT_FAIL cloud_model_mismatch expected=$EXPECT_CLOUD_MODEL file=$DC_ENV_FILE" >&2
  exit 4
fi
if ! rg -n "^DIRECT_CHAT_OLLAMA_MODELS=${EXPECT_LOCAL_MODEL}$" "$DC_ENV_FILE" >/dev/null; then
  echo "PREFLIGHT_FAIL local_model_mismatch expected=$EXPECT_LOCAL_MODEL file=$DC_ENV_FILE" >&2
  exit 5
fi
echo "PREFLIGHT_OK model_lock cloud=${EXPECT_CLOUD_MODEL} local=${EXPECT_LOCAL_MODEL}"
echo "PREFLIGHT_OK routing_config file=${ROUTING_CONFIG}"
echo "PREFLIGHT_OK mcp_config file=${MCP_CONFIG}"
echo "PREFLIGHT_OK mcp_action_policy_config file=${MCP_ACTION_POLICY_CONFIG}"

if command -v mcporter >/dev/null 2>&1; then
  if ./scripts/community_mcp_bridge.sh check >/dev/null; then
    echo "PREFLIGHT_OK mcp_bridge configured=10"
  else
    echo "PREFLIGHT_FAIL mcp_bridge_check" >&2
    exit 6
  fi
else
  echo "PREFLIGHT_WARN mcporter_missing bridge_check_skipped"
fi

echo "N8N_MCP_PREFLIGHT=PASS"
