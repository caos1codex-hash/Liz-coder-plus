#!/usr/bin/env bash
# Lint the Python code (ruff + mypy).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Running ruff ==="
ruff check "$ROOT/apps/backend" "$ROOT/packages" "$ROOT/tests"

echo ""
echo "=== Running mypy ==="
mypy "$ROOT/apps/backend/src"

echo ""
echo "Lint complete."
