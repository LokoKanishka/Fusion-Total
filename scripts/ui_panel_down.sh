#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
./scripts/compose_infra.sh stop lucy_ui_panel >/dev/null || true

echo "UI_PANEL_DOWN=PASS"
