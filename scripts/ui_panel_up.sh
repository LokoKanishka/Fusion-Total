#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
./scripts/compose_infra.sh up -d --build lucy_ui_panel

echo "UI_PANEL_URL=http://127.0.0.1:5100"
