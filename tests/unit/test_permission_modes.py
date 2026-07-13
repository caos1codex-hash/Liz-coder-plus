"""Unit tests for the permission modes enum.

Sprint 1 - Prompt 1: covers the from_string() parser and the
requires_confirmation property.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add backend src to path for import
ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = ROOT / "apps" / "backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

from permissions.modes import PermissionMode  # noqa: E402


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
