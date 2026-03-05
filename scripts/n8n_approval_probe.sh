#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-http://127.0.0.1:5678}"
SIGN_BIN="${SIGN_BIN:-./scripts/mcp_approval_sign.sh}"
mkdir -p _tmp/n8n_approval_probe

auto_sign_if_needed() {
  local meta_json="$1"
  local cid="$2"
  local level="$3"
  if [[ "$meta_json" != *"__AUTO_SIG__"* ]]; then
    printf '%s' "$meta_json"
    return 0
  fi
  local parsed
  parsed="$(python3 - <<'PY' "$meta_json"
import json,sys
m=json.loads(sys.argv[1])
approved_by=str(m.get("approved_by","")).strip()
token=str(m.get("approval_token","")).strip()
ts=str(m.get("approval_ts","")).strip()
requester=str(m.get("requested_by") or m.get("requester") or m.get("actor_user") or m.get("requester_user") or "").strip()
actions=m.get("mcp_actions",[])
scope=m.get("approval_scope",[])
ticket=str(m.get("approval_change_ticket","")).strip()
justification=str(m.get("approval_justification","")).strip()
if isinstance(actions,str):
    actions=[x.strip() for x in actions.split(",") if x.strip()]
if isinstance(scope,str):
    scope=[x.strip() for x in scope.split(",") if x.strip()]
print("\x1f".join([
    approved_by,
    token,
    ts,
    requester,
    ",".join(actions),
    ",".join(scope),
    ticket,
    justification,
]))
PY
)"
  local approved_by token ats requester actions_csv scope_csv ticket justification
  IFS=$'\x1f' read -r approved_by token ats requester actions_csv scope_csv ticket justification <<<"$parsed"
  local sig
  sig="$("$SIGN_BIN" \
    --approved-by "$approved_by" \
    --token "$token" \
    --ts "$ats" \
    --cid "$cid" \
    --level "$level" \
    --requester "$requester" \
    --actions "$actions_csv" \
    --scope "$scope_csv" \
    --ticket "$ticket" \
    --justification "$justification")"
  meta_json="${meta_json//__AUTO_SIG__/$sig}"
  printf '%s' "$meta_json"
}

post_case() {
  local name="$1"
  local meta_json="$2"
  local expect_ok="$3"
  local expect_reason_substr="${4:-}"
  local sig_level="${5:-write}"
  local cid="cid_approval_${name}_$(date +%s%N | cut -c1-16)"
  local ts
  ts="$(date --iso-8601=seconds)"
  meta_json="${meta_json//__TS__/$ts}"
  meta_json="${meta_json//__CID__/$cid}"
  meta_json="$(auto_sign_if_needed "$meta_json" "$cid" "$sig_level")"
  local req="_tmp/n8n_approval_probe/${name}_request.json"
  local res="_tmp/n8n_approval_probe/${name}_response.json"

  cat > "$req" <<JSON
{
  "kind": "text",
  "source": "ui_n8n_panel",
  "ts": "$ts",
  "text": "ejecuta operacion mcp controlada",
  "meta": $meta_json,
  "correlation_id": "$cid"
}
JSON

  local code=""
  for _ in $(seq 1 20); do
    code="$(curl -sS -o "$res" -w '%{http_code}' -X POST "${BASE_URL}/webhook/lucy-input" -H 'content-type: application/json' --data-binary "@$req" || true)"
    if [[ "$code" == "200" ]]; then
      break
    fi
    sleep 0.5
  done
  if [[ "$code" != "200" ]]; then
    echo "APPROVAL_PROBE_FAIL case=$name http=$code"
    return 1
  fi

  python3 - <<'PY' "$name" "$res" "$expect_ok" "$expect_reason_substr"
import json
import sys
from pathlib import Path

case_name,res_path,expect_ok,expect_reason = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
ack=json.loads(res_path.read_text())
ok_expected = expect_ok.lower() == "true"
ok_got = bool(ack.get("ok"))
reason = str(ack.get("reason") or "")
if ok_got != ok_expected:
    raise SystemExit(f"APPROVAL_PROBE_FAIL case={case_name} expected_ok={ok_expected} got_ok={ok_got}")
if expect_reason and expect_reason not in reason:
    raise SystemExit(f"APPROVAL_PROBE_FAIL case={case_name} expected_reason_contains={expect_reason} got_reason={reason}")
print(f"APPROVAL_PROBE_OK case={case_name} ok={ok_got} reason={reason}")
PY
}

# Caso write sin aprobación: debe bloquear.
post_case \
  "write_without_approval" \
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_update"]}' \
  "false" \
  "human_approval_required_for_mcp_actions"

# Caso write con aprobación: debe pasar.
post_case \
  "write_with_approval" \
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_update"],"approved_by":"diego","approval_token":"tok_local_001","approval_ts":"__TS__","approval_scope":["workflow_update"],"approval_sig":"__AUTO_SIG__"}' \
  "true" \
  "" \
  "write"

# Caso read: debe pasar sin aprobación.
post_case \
  "read_without_approval" \
  '{"mcp_profile":"safe_readonly","mcp_actions":["workflow_list"]}' \
  "true"

# Caso sensitive con token pero sin justificación: debe bloquear.
post_case \
  "sensitive_without_justification" \
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_delete"],"approved_by":"diego","requested_by":"lucy","approval_token":"tok_local_002","approval_ts":"__TS__","approval_scope":["workflow_delete"],"approval_change_ticket":"CHG-2026-001","approval_sig":"__AUTO_SIG__"}' \
  "false" \
  "missing_approval_justification" \
  "sensitive"

# Caso sensitive con justificación: debe pasar.
post_case \
  "sensitive_with_justification" \
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_delete"],"approved_by":"diego","requested_by":"lucy","approval_token":"tok_local_003","approval_ts":"__TS__","approval_scope":["workflow_delete"],"approval_justification":"Cambio aprobado por operacion programada","approval_change_ticket":"CHG-2026-002","approval_sig":"__AUTO_SIG__"}' \
  "true" \
  "" \
  "sensitive"

# Caso write con scope incorrecto: bloqueado.
post_case \
  "write_scope_mismatch" \
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_update"],"approved_by":"diego","approval_token":"tok_local_004","approval_ts":"__TS__","approval_scope":["workflow_list"],"approval_sig":"__AUTO_SIG__"}' \
  "false" \
  "scope_missing:workflow_update" \
  "write"

# Caso sensitive expirado: bloqueado por ventana temporal.
post_case \
  "sensitive_expired" \
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_delete"],"approved_by":"diego","requested_by":"lucy","approval_token":"tok_local_005","approval_ts":"2026-01-01T00:00:00Z","approval_scope":["workflow_delete"],"approval_justification":"Cambio de emergencia","approval_change_ticket":"CHG-2026-003","approval_sig":"__AUTO_SIG__"}' \
  "false" \
  "approval_expired" \
  "sensitive"

# Caso sensitive sin ticket: bloqueado.
post_case \
  "sensitive_missing_ticket" \
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_delete"],"approved_by":"diego","requested_by":"lucy","approval_token":"tok_local_006","approval_ts":"__TS__","approval_scope":["workflow_delete"],"approval_justification":"Cambio aprobado","approval_sig":"__AUTO_SIG__"}' \
  "false" \
  "missing_approval_change_ticket" \
  "sensitive"

# Caso sensitive con ticket inválido: bloqueado.
post_case \
  "sensitive_invalid_ticket" \
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_delete"],"approved_by":"diego","requested_by":"lucy","approval_token":"tok_local_007","approval_ts":"__TS__","approval_scope":["workflow_delete"],"approval_justification":"Cambio aprobado","approval_change_ticket":"BADTICKET","approval_sig":"__AUTO_SIG__"}' \
  "false" \
  "invalid_approval_change_ticket_format" \
  "sensitive"

# Caso sensitive con requester igual al aprobador: bloqueado por regla de doble control.
post_case \
  "sensitive_same_requester_approver" \
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_delete"],"approved_by":"diego","requested_by":"diego","approval_token":"tok_local_008","approval_ts":"__TS__","approval_scope":["workflow_delete"],"approval_justification":"Cambio aprobado","approval_change_ticket":"CHG-2026-008","approval_sig":"__AUTO_SIG__"}' \
  "false" \
  "approver_equals_requester" \
  "sensitive"

# Caso sensitive con requester distinto al aprobador: pasa.
post_case \
  "sensitive_distinct_requester_approver" \
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_delete"],"approved_by":"diego","requested_by":"lucy","approval_token":"tok_local_009","approval_ts":"__TS__","approval_scope":["workflow_delete"],"approval_justification":"Cambio aprobado","approval_change_ticket":"CHG-2026-009","approval_sig":"__AUTO_SIG__"}' \
  "true" \
  "" \
  "sensitive"

echo "N8N_APPROVAL_PROBE=PASS"
