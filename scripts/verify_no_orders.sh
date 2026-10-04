#!/usr/bin/env bash
# Fail if executable order-placement methods are defined (not safety guards).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "Scanning for forbidden Kalshi order-placement implementations..."

if rg -n --glob '!**/test_*.py' --glob '!**/.venv/**' --glob '!**/node_modules/**' \
  -e '^\s*def place_order|^\s*async def place_order|^\s*def create_order|^\s*async def create_order|^\s*def cancel_order|^\s*async def cancel_order' \
  "$ROOT/backend/app" ; then
  echo "FAIL: order placement method definition found"
  exit 1
fi

echo "OK: no order-placement functionality detected in app code"
