#!/usr/bin/env bash
# Auto-generated launcher for Liz Coder Plus
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/venv"

# Set library path for EGL/GLES
export LD_LIBRARY_PATH="$SCRIPT_DIR/venv/lib:${LD_LIBRARY_PATH:-}"

# Run the app
exec "$VENV/bin/python" "$SCRIPT_DIR/main.py" "$@"
