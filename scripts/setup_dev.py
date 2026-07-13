#!/usr/bin/env python3
"""Set up the local development environment for Liz Coder Plus.

Creates a Python virtual environment inside apps/backend and installs
all required dependencies. Safe to run repeatedly.

Usage:
    python scripts/setup_dev.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "apps" / "backend"
VENV = BACKEND / ".venv"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"-> {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    print("=== Liz Coder Plus - Development Setup ===\n")

    if not VENV.exists():
        print(f"Creating virtual environment at {VENV}")
        run([sys.executable, "-m", "venv", str(VENV)])
    else:
        print(f"Virtual environment already exists at {VENV}")

    pip = str(VENV / "bin" / "pip") if sys.platform != "win32" else str(VENV / "Scripts" / "pip.exe")

    print("\nUpgrading pip...")
    run([pip, "install", "--upgrade", "pip"])

    print("\nInstalling backend dependencies...")
    run([pip, "install", "-r", str(BACKEND / "requirements.txt")])

    print("\nSetup complete.")
    print("Activate the venv with:")
    if sys.platform == "win32":
        print(f"    {VENV}\\Scripts\\activate")
    else:
        print(f"    source {VENV}/bin/activate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
