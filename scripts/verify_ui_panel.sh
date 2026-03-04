#!/usr/bin/env bash
set -euo pipefail

FAIL=0

if curl -fsS http://127.0.0.1:5100/ >/dev/null; then
  echo "UI_PANEL_HTTP=PASS"
else
  echo "UI_PANEL_HTTP=FAIL"
  FAIL=1
fi

if curl -fsS http://127.0.0.1:5100/browse/inbox >/dev/null; then
  echo "UI_PANEL_BROWSE=PASS"
else
  echo "UI_PANEL_BROWSE=FAIL"
  FAIL=1
fi

if docker ps --format '{{.Names}}' | grep -q '^lucy_ui_panel$'; then
  echo "UI_PANEL_CONTAINER=PASS"
else
  echo "UI_PANEL_CONTAINER=FAIL"
  FAIL=1
fi

if [[ "$FAIL" -eq 0 ]]; then
  echo "UI_PANEL_VERIFY=PASS"
else
  echo "UI_PANEL_VERIFY=FAIL"
  exit 1
fi
