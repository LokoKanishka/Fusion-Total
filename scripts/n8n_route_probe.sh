#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-http://127.0.0.1:5678}"
mkdir -p _tmp/n8n_route_probe

probe_case() {
  local name="$1"
  local endpoint="$2"
  local kind="$3"
  local source="$4"
  local text="$5"
  local meta_json="$6"
  local cid="cid_route_${name}_$(date +%s%N | cut -c1-16)"
  local req="_tmp/n8n_route_probe/${name}_request.json"
  local res="_tmp/n8n_route_probe/${name}_response.json"
  local ts
  ts="$(date --iso-8601=seconds)"

  cat > "$req" <<JSON
{
  "kind": "$kind",
  "source": "$source",
  "ts": "$ts",
  "text": "$text",
  "meta": $meta_json,
  "correlation_id": "$cid"
}
JSON

  local code
  code="$(curl -sS -o "$res" -w '%{http_code}' -X POST "${BASE_URL}/webhook/${endpoint}" -H 'content-type: application/json' --data-binary "@$req")"
  if [[ "$code" != "200" ]]; then
    echo "ROUTE_PROBE_FAIL case=$name http=$code endpoint=$endpoint"
    return 1
  fi

  python3 - <<'PY' "$name" "$res"
import json
import sys
from pathlib import Path

case_name=sys.argv[1]
res_path=Path(sys.argv[2])
ack=json.loads(res_path.read_text())
cid=ack.get("correlation_id","")
if not cid:
    raise SystemExit(f"ROUTE_PROBE_FAIL case={case_name} reason=missing_correlation_id")

env=None
for _ in range(30):
    for base in (Path("ipc/inbox"), Path("ipc/payloads")):
        p=base / f"{cid}.json"
        if p.exists():
            env=json.loads(p.read_text())
            break
    if env is not None:
        break
    import time
    time.sleep(0.1)
if env is None:
    raise SystemExit(f"ROUTE_PROBE_FAIL case={case_name} reason=envelope_not_found")

payload=env.get("payload") if isinstance(env,dict) else {}
meta=(payload.get("meta") if isinstance(payload,dict) else {}) or {}
route=(env.get("routing") if isinstance(env,dict) else {}) or meta.get("routing") or {}
mcp=(env.get("mcp") if isinstance(env,dict) else {}) or meta.get("mcp") or {}
print(
    "ROUTE_PROBE_OK "
    f"case={case_name} "
    f"profile={route.get('profile','')} "
    f"model={route.get('model','')} "
    f"backend={route.get('backend','')} "
    f"mcp_profile={mcp.get('profile','')}"
)
PY
}

probe_case "automation" "lucy-input" "text" "ui_n8n_panel" "orquesta este flujo" '{"workflow_profile":"automation","task_type":"workflow"}'
probe_case "coding" "lucy-input" "text" "dc" "debug de script python" '{"task_type":"code"}'
probe_case "research" "lucy-input" "text" "dc" "investigar proveedores MCP" '{"intent":"research"}'
probe_case "voice" "voice-input" "voice" "voice_chat" "necesito un resumen rapido" '{"task_type":"assist"}'

echo "N8N_ROUTE_PROBE=PASS"
