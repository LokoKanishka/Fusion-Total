#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
CONTAINER="${CONTAINER:-lucy_brain_n8n}"
WORKFLOW_ID="${WORKFLOW_ID:-SnRyEoRbJuDC-5PBLt8os}"
TMP_DIR="${TMP_DIR:-./_tmp/lucy_outbox_patch}"
N8N_HOME="${N8N_HOME:-/home/node}"
N8N_USER_FOLDER="${N8N_USER_FOLDER:-${N8N_HOME}/.n8n}"
N8N_EXEC_USER="${N8N_EXEC_USER:-}"

n8n_exec() {
  if [[ -n "$N8N_EXEC_USER" ]]; then
    docker exec -e "HOME=$N8N_HOME" -e "N8N_USER_FOLDER=$N8N_USER_FOLDER" -u "$N8N_EXEC_USER" "$CONTAINER" "$@"
  else
    docker exec -e "HOME=$N8N_HOME" -e "N8N_USER_FOLDER=$N8N_USER_FOLDER" "$CONTAINER" "$@"
  fi
}

mkdir -p "$TMP_DIR"
SRC="$TMP_DIR/workflow_src.json"
PATCHED="$TMP_DIR/workflow_patched.json"

n8n_exec n8n export:workflow --id "$WORKFLOW_ID" --output /tmp/lucy_outbox_src.json >/dev/null
docker cp "$CONTAINER":/tmp/lucy_outbox_src.json "$SRC" >/dev/null

python3 tools/patch_lucy_outbox_v1.py "$SRC" "$PATCHED"

docker cp "$PATCHED" "$CONTAINER":/tmp/lucy_outbox_patched.json >/dev/null
n8n_exec n8n import:workflow --input=/tmp/lucy_outbox_patched.json >/dev/null
n8n_exec n8n update:workflow --id="$WORKFLOW_ID" --active=true >/dev/null
n8n_exec n8n publish:workflow --id="$WORKFLOW_ID" >/dev/null
./scripts/compose_infra.sh restart n8n >/dev/null

echo "OUTBOX_PATCH_APPLIED workflow_id=$WORKFLOW_ID"
