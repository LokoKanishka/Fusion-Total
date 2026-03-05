#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-http://127.0.0.1:5678}"
mkdir -p _tmp/n8n_approval_probe

post_case() {
  local name="$1"
  local meta_json="$2"
  local expect_ok="$3"
  local expect_reason_substr="${4:-}"
  local cid="cid_approval_${name}_$(date +%s%N | cut -c1-16)"
  local ts
  ts="$(date --iso-8601=seconds)"
  meta_json="${meta_json//__TS__/$ts}"
  meta_json="${meta_json//__CID__/$cid}"
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

  local code
  code="$(curl -sS -o "$res" -w '%{http_code}' -X POST "${BASE_URL}/webhook/lucy-input" -H 'content-type: application/json' --data-binary "@$req")"
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
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_update"],"approved_by":"diego","approval_token":"tok_local_001","approval_ts":"__TS__","approval_scope":["workflow_update"]}' \
  "true"

# Caso read: debe pasar sin aprobación.
post_case \
  "read_without_approval" \
  '{"mcp_profile":"safe_readonly","mcp_actions":["workflow_list"]}' \
  "true"

# Caso sensitive con token pero sin justificación: debe bloquear.
post_case \
  "sensitive_without_justification" \
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_delete"],"approved_by":"diego","approval_token":"tok_local_002","approval_ts":"__TS__","approval_scope":["workflow_delete"]}' \
  "false" \
  "missing_approval_justification"

# Caso sensitive con justificación: debe pasar.
post_case \
  "sensitive_with_justification" \
  '{"mcp_profile":"ops_automation","mcp_actions":["workflow_delete"],"approved_by":"diego","approval_token":"tok_local_003","approval_ts":"__TS__","approval_scope":["workflow_delete"],"approval_justification":"Cambio aprobado por operacion programada"}' \
  "true"

echo "N8N_APPROVAL_PROBE=PASS"
