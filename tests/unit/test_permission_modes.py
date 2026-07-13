"""Unit tests for the permission modes enum.

Sprint 1 - Prompt 1: covers the from_string() parser and the
requires_confirmation property.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The backend uses absolute imports like `from src.permissions.modes`,
# so we need the backend ROOT (not its src/) on sys.path.
ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "apps" / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

# Drop any cached `src` namespace so the backend's `src` is the one that
# gets imported, regardless of test execution order.
for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.permissions.modes import PermissionMode  # noqa: E402


def test_confirmation_mode_requires_confirmation() -> None:
    mode = PermissionMode.CONFIRMATION
    assert mode.requires_confirmation is True


def test_automatic_mode_does_not_require_confirmation() -> None:
    mode = PermissionMode.AUTOMATIC
    assert mode.requires_confirmation is False


def test_from_string_confirmation() -> None:
    assert PermissionMode.from_string("Confirmation") is PermissionMode.CONFIRMATION


def test_from_string_automatic() -> None:
    assert PermissionMode.from_string("Automatic") is PermissionMode.AUTOMATIC


def test_from_string_case_insensitive() -> None:
    assert PermissionMode.from_string("automatic") is PermissionMode.AUTOMATIC
    assert PermissionMode.from_string("CONFIRMATION") is PermissionMode.CONFIRMATION


def test_from_string_none_defaults_to_confirmation() -> None:
    assert PermissionMode.from_string(None) is PermissionMode.CONFIRMATION


def test_from_string_invalid_defaults_to_confirmation() -> None:
    assert PermissionMode.from_string("nonsense") is PermissionMode.CONFIRMATION
