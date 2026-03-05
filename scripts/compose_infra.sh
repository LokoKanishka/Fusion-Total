#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-}"

cd "$ROOT_DIR"

# Política crítica: no mezclar Fusion con otros proyectos (puertos/n8n).
if [[ "${FUSION_ISOLATION_SKIP:-0}" != "1" ]]; then
  case "$ACTION" in
    up|down|start|stop|restart)
      ./scripts/fusion_isolation_guard.sh check
      ;;
  esac
fi

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-fusiontotal}"
exec docker compose -f docker-compose.yml "$@"
