#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

WINDOW_H="${WINDOW_H:-24}"
LIMIT_EXEC_ROWS="${LIMIT_EXEC_ROWS:-2000}"
DB_PATH="${DB_PATH:-data/n8n/database.sqlite}"
METRICS_FILE="${METRICS_FILE:-ipc/metrics/lucy_gateway_events.jsonl}"

if [[ ! -f "$DB_PATH" ]]; then
  ALT_DB_PATH="data/n8n/.n8n/database.sqlite"
  if [[ -f "$ALT_DB_PATH" ]]; then
    DB_PATH="$ALT_DB_PATH"
  fi
fi

export WINDOW_H LIMIT_EXEC_ROWS DB_PATH METRICS_FILE

python3 - <<'PY'
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_dt(s: str | None):
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


window_h = max(1, int(os.environ.get("WINDOW_H", "24")))
limit_exec_rows = max(50, int(os.environ.get("LIMIT_EXEC_ROWS", "2000")))
db_path = Path(os.environ.get("DB_PATH", ""))
metrics_path = Path(os.environ.get("METRICS_FILE", ""))
cutoff = datetime.now(timezone.utc) - timedelta(hours=window_h)

print(f"OBS_WINDOW_H={window_h}")
print(f"OBS_CUTOFF_UTC={cutoff.isoformat()}")

exec_total = 0
exec_by_status = Counter()
wf_stats = defaultdict(Counter)

if db_path.exists():
    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT e.id, e.workflowId, e.status, e.startedAt, w.name AS workflowName
            FROM execution_entity e
            LEFT JOIN workflow_entity w ON w.id = e.workflowId
            ORDER BY e.id DESC
            LIMIT ?
            """,
            (limit_exec_rows,),
        ).fetchall()
    except sqlite3.Error as exc:
        print(f"N8N_DB_ERROR={exc}")
        rows = []

    for row in rows:
        started = parse_dt(row["startedAt"])
        if started is None or started < cutoff:
            continue
        status = str(row["status"] or "unknown").lower()
        wf_name = str(row["workflowName"] or row["workflowId"] or "unknown")
        exec_total += 1
        exec_by_status[status] += 1
        wf_stats[wf_name][status] += 1

print(f"N8N_EXEC_TOTAL={exec_total}")
for key in ("success", "error", "failed", "running", "canceled", "unknown"):
    if exec_by_status[key]:
        print(f"N8N_EXEC_STATUS_{key.upper()}={exec_by_status[key]}")

for wf_name in sorted(wf_stats):
    c = wf_stats[wf_name]
    total = sum(c.values())
    success = c.get("success", 0)
    error = c.get("error", 0) + c.get("failed", 0)
    running = c.get("running", 0)
    rate = (success / total * 100.0) if total else 0.0
    print(
        "N8N_WF "
        f"name={wf_name} total={total} success={success} error={error} running={running} success_rate={rate:.1f}"
    )

gw_total = 0
gw_status = Counter()
gw_profile = Counter()
gw_model = Counter()
gw_mcp = Counter()
gw_action_level = Counter()
gw_approval_required = Counter()
gw_approval_blocked = Counter()

if metrics_path.exists():
    for line in metrics_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = parse_dt(ev.get("ts"))
        if ts is None or ts < cutoff:
            continue
        gw_total += 1
        gw_status[str(ev.get("ingress_status") or "unknown")] += 1
        gw_profile[str(ev.get("route_profile") or "unknown")] += 1
        gw_model[str(ev.get("model") or "unknown")] += 1
        gw_mcp[str(ev.get("mcp_profile") or "unknown")] += 1
        gw_action_level[str(ev.get("mcp_highest_action_level") or "unknown")] += 1
        gw_approval_required[str(bool(ev.get("mcp_requires_human_approval"))).lower()] += 1
        gw_approval_blocked[str(bool(ev.get("mcp_approval_blocked"))).lower()] += 1

print(f"GATEWAY_EVENTS_TOTAL={gw_total}")
for status, count in sorted(gw_status.items()):
    print(f"GATEWAY_STATUS name={status} count={count}")
for profile, count in sorted(gw_profile.items()):
    print(f"GATEWAY_PROFILE name={profile} count={count}")
for model, count in sorted(gw_model.items()):
    print(f"GATEWAY_MODEL name={model} count={count}")
for mcp, count in sorted(gw_mcp.items()):
    print(f"GATEWAY_MCP_PROFILE name={mcp} count={count}")
for level, count in sorted(gw_action_level.items()):
    print(f"GATEWAY_MCP_ACTION_LEVEL name={level} count={count}")
for key, count in sorted(gw_approval_required.items()):
    print(f"GATEWAY_MCP_APPROVAL_REQUIRED value={key} count={count}")
for key, count in sorted(gw_approval_blocked.items()):
    print(f"GATEWAY_MCP_APPROVAL_BLOCKED value={key} count={count}")
PY
