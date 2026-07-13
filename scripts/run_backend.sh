#!/usr/bin/env bash
# Run the Liz Coder Plus backend in development mode.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/apps/backend"

if [ ! -d ".venv" ]; then
    echo "Virtual environment not found. Run scripts/setup_dev.py first."
    exit 1
fi

# Activate venv
# shellcheck disable=SC1091
source .venv/bin/activate

export LIZ_ENV="${LIZ_ENV:-development}"

echo "Starting backend (env=$LIZ_ENV)..."
exec uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000
