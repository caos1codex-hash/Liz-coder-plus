#!/usr/bin/env bash
# ============================================================
# Liz Coder Plus — Linux Desktop Installer
# Installs dependencies and creates a launcher script.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_DIR="$PROJECT_ROOT/apps/linux-desktop"
VENV_DIR="$INSTALL_DIR/venv"

echo "=========================================="
echo "  Liz Coder Plus — Linux Desktop Setup"
echo "=========================================="
echo ""

# ---- Check Python 3 ----
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is not installed."
    echo "Install it with: sudo apt install python3 python3-venv"
    exit 1
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "[OK] Python $PYTHON_VERSION found"

# ---- Check / install system dependencies ----
MISSING_DEPS=()

check_lib() {
    if ! ldconfig -p 2>/dev/null | grep -q "$1"; then
        MISSING_DEPS+=("$1")
    fi
}

check_lib "libEGL.so.1"
check_lib "libGLESv2.so.2"

if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
    echo ""
    echo "Missing system libraries: ${MISSING_DEPS[*]}"
    echo "Install them with:"
    echo "  sudo apt install libegl1 libgles2"
    echo ""
    echo "Attempting to continue anyway..."
fi

# ---- Create virtual environment ----
if [ ! -d "$VENV_DIR" ]; then
    echo "[..] Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "[OK] Virtual environment created"
else
    echo "[OK] Virtual environment exists"
fi

# ---- Install Python dependencies ----
echo "[..] Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet PySide6 websocket-client
echo "[OK] Python dependencies installed"

# ---- Create launcher script ----
LAUNCHER="$INSTALL_DIR/run.sh"
cat > "$LAUNCHER" << LAUNCHER_EOF
#!/usr/bin/env bash
# Auto-generated launcher for Liz Coder Plus
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
VENV="\$SCRIPT_DIR/venv"

# Set library path for EGL/GLES
export LD_LIBRARY_PATH="\$SCRIPT_DIR/venv/lib:\${LD_LIBRARY_PATH:-}"

# Run the app
exec "\$VENV/bin/python" "\$SCRIPT_DIR/main.py" "\$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
echo "[OK] Launcher created: $LAUNCHER"

# ---- Create .desktop entry ----
DESKTOP_FILE="$HOME/.local/share/applications/liz-coder-plus.desktop"
mkdir -p "$(dirname "$DESKTOP_FILE")"
cat > "$DESKTOP_FILE" << DESKTOP_EOF
[Desktop Entry]
Name=Liz Coder Plus
Comment=AI Desktop Assistant
Exec=$LAUNCHER
Icon=text-editor
Terminal=false
Type=Application
Categories=Development;Utility;
StartupNotify=true
DESKTOP_EOF
echo "[OK] Desktop entry created: $DESKTOP_FILE"

echo ""
echo "=========================================="
echo "  Setup complete!"
echo "=========================================="
echo ""
echo "  To run the app:"
echo "    $LAUNCHER"
echo ""
echo "  Or search 'Liz Coder Plus' in your app menu."
echo ""
echo "  To start the backend first:"
echo "    cd $PROJECT_ROOT && python3 -m apps.backend.main"
echo ""
