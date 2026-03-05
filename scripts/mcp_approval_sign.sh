#!/usr/bin/env bash
set -euo pipefail

APPROVED_BY=""
APPROVAL_TOKEN=""
APPROVAL_TS=""
CORRELATION_ID=""
LEVEL=""
ACTIONS=""
SCOPE=""
TICKET=""
JUSTIFICATION=""
SECRET="${MCP_APPROVAL_HMAC_SECRET:-fusion-local-approval-hmac-secret}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --approved-by) APPROVED_BY="${2:-}"; shift 2 ;;
    --token) APPROVAL_TOKEN="${2:-}"; shift 2 ;;
    --ts) APPROVAL_TS="${2:-}"; shift 2 ;;
    --cid) CORRELATION_ID="${2:-}"; shift 2 ;;
    --level) LEVEL="${2:-}"; shift 2 ;;
    --actions) ACTIONS="${2:-}"; shift 2 ;;
    --scope) SCOPE="${2:-}"; shift 2 ;;
    --ticket) TICKET="${2:-}"; shift 2 ;;
    --justification) JUSTIFICATION="${2:-}"; shift 2 ;;
    --secret) SECRET="${2:-}"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
Usage:
  ./scripts/mcp_approval_sign.sh \
    --approved-by <user> \
    --token <approval_token> \
    --ts <RFC3339> \
    --cid <correlation_id> \
    --level <write|sensitive> \
    --actions <csv_actions> \
    --scope <csv_scope> \
    [--ticket <change_ticket>] \
    [--justification <text>] \
    [--secret <hmac_secret>]
EOF
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$APPROVED_BY" || -z "$APPROVAL_TOKEN" || -z "$APPROVAL_TS" || -z "$CORRELATION_ID" || -z "$LEVEL" ]]; then
  echo "missing required args" >&2
  exit 2
fi

python3 - <<'PY' "$APPROVED_BY" "$APPROVAL_TOKEN" "$APPROVAL_TS" "$CORRELATION_ID" "$LEVEL" "$ACTIONS" "$SCOPE" "$TICKET" "$JUSTIFICATION" "$SECRET"
import hashlib
import hmac
import sys

approved_by, approval_token, approval_ts, cid, level, actions_csv, scope_csv, ticket, justification, secret = sys.argv[1:]
actions = sorted([x.strip().lower() for x in actions_csv.split(",") if x.strip()])
scope = sorted([x.strip().lower() for x in scope_csv.split(",") if x.strip()])
canonical = "\n".join([
    approved_by.strip(),
    approval_token.strip(),
    approval_ts.strip(),
    cid.strip(),
    level.strip(),
    ",".join(actions),
    ",".join(scope),
    ticket.strip(),
    justification.strip(),
])
sig = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
print(sig)
PY
