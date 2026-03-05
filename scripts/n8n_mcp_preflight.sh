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
MCP_APPROVAL_POLICY_CONFIG="${MCP_APPROVAL_POLICY_CONFIG:-config/mcp_approval_policy.json}"

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
need_bin jq

./scripts/fusion_isolation_guard.sh check >/dev/null
echo "PREFLIGHT_OK isolation_guard"

http_code="$(curl -sS -o /dev/null -w '%{http_code}' "$N8N_URL" 2>/dev/null || true)"
if [[ "$http_code" != "200" ]]; then
  echo "PREFLIGHT_FAIL n8n_health http=$http_code url=$N8N_URL" >&2
  exit 2
fi
echo "PREFLIGHT_OK n8n_health url=$N8N_URL"

if docker ps --format '{{.Names}}' | grep -Fxq "lucy_brain_n8n"; then
  secret_len="$(docker exec lucy_brain_n8n sh -lc 'printf %s "${MCP_APPROVAL_HMAC_SECRET:-}" | wc -c' 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -z "$secret_len" || "$secret_len" -le 0 ]]; then
    echo "PREFLIGHT_FAIL missing_hmac_secret_in_n8n_env MCP_APPROVAL_HMAC_SECRET" >&2
    exit 11
  fi
  echo "PREFLIGHT_OK hmac_secret_present_in_n8n_env len=${secret_len}"
fi

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
if [[ ! -f "$MCP_APPROVAL_POLICY_CONFIG" ]]; then
  echo "PREFLIGHT_FAIL missing_mcp_approval_policy_config file=$MCP_APPROVAL_POLICY_CONFIG" >&2
  exit 10
fi

if ! jq -e '
  (.required_fields.sensitive | type == "array") and
  (.required_fields.sensitive | index("approval_change_ticket") != null) and
  (.required_fields.sensitive | index("approval_justification") != null) and
  (.ticket_pattern | type == "string" and length > 0) and
  (.two_person.enabled_for_levels | type == "array") and
  (.two_person.enabled_for_levels | index("sensitive") != null) and
  (.two_person.require_requester_identity == true) and
  (.two_person.approver_must_differ == true) and
  (.two_person.requester_fields | type == "array" and length > 0)
' "$MCP_APPROVAL_POLICY_CONFIG" >/dev/null; then
  echo "PREFLIGHT_FAIL invalid_mcp_approval_policy_shape file=$MCP_APPROVAL_POLICY_CONFIG" >&2
  exit 12
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
echo "PREFLIGHT_OK mcp_approval_policy_config file=${MCP_APPROVAL_POLICY_CONFIG}"
echo "PREFLIGHT_OK mcp_approval_policy_shape two_person=sensitive_required"

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
