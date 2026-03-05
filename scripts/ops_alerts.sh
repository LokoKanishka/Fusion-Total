#!/usr/bin/env bash
set -euo pipefail

mkdir -p DOCS/RUNS
log="DOCS/RUNS/ops_alerts.log"

notify() {
  msg="$1"
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "OpenClaw Alert" "$msg" || true
  fi
  echo "$(date -Is) ALERT: $msg" >> "$log"
}

cmd="${1:-check}"

case "$cmd" in
  check)
    [ -x ./scripts/verify_gateway.sh ]
    [ -x ./scripts/verify_all.sh ]
    [ -x ./scripts/fusion_isolation_guard.sh ]
    [ -x ./scripts/n8n_mcp_preflight.sh ]
    [ -x ./scripts/n8n_observability_snapshot.sh ]
    echo "OPS_ALERTS_OK"
    ;;
  run)
    if ! ./scripts/fusion_isolation_guard.sh check >/dev/null 2>&1; then
      notify "Fusion isolation guard failed"
    fi
    n8n_code="$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:5678/healthz 2>/dev/null || true)"
    if [[ "$n8n_code" != "200" ]]; then
      notify "n8n healthz failed (http=$n8n_code)"
    fi
    if ! ./scripts/n8n_mcp_preflight.sh >/dev/null 2>&1; then
      notify "n8n+mcp preflight failed"
    fi
    if ! WINDOW_H=1 ./scripts/n8n_observability_snapshot.sh >/dev/null 2>&1; then
      notify "n8n observability snapshot failed"
    fi
    if ! ./scripts/verify_gateway.sh >/dev/null 2>&1; then
      notify "Gateway failed health check"
    fi
    if ! ./scripts/verify_all.sh >/dev/null 2>&1; then
      notify "verify_all failed"
    fi
    echo "OPS_ALERTS_RUN_OK"
    ;;
  cron-install)
    line="*/5 * * * * cd $PWD && ./scripts/ops_alerts.sh run >> DOCS/RUNS/ops_alerts_cron.log 2>&1"
    (crontab -l 2>/dev/null | grep -v 'scripts/ops_alerts.sh run' ; echo "$line") | crontab -
    echo "OPS_ALERTS_CRON_INSTALLED"
    ;;
  cron-remove)
    (crontab -l 2>/dev/null | grep -v 'scripts/ops_alerts.sh run') | crontab - || true
    echo "OPS_ALERTS_CRON_REMOVED"
    ;;
  *)
    echo "usage: $0 {check|run|cron-install|cron-remove}" >&2
    exit 2
    ;;
esac
