"""Unit tests for the PermissionService.

Sprint 1 - Prompt 1: covers the three possible decisions
(allow / confirm / deny) and the interaction between modes and lists.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = ROOT / "apps" / "backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

from core.config import PermissionsConfig  # noqa: E402
from permissions.service import PermissionService  # noqa: E402


def _service(mode: str, allowed: list[str], denied: list[str]) -> PermissionService:
    cfg = PermissionsConfig(mode=mode, allowed_commands=allowed, denied_commands=denied)
    return PermissionService(cfg)


def test_denied_command_always_denied_even_in_automatic() -> None:
    svc = _service("Automatic", allowed=["rm"], denied=["rm -rf"])
    decision = svc.evaluate("rm -rf /tmp")
    assert decision.decision == "deny"


def test_whitelisted_command_in_automatic_mode_is_allowed() -> None:
    svc = _service("Automatic", allowed=["git status"], denied=[])
    decision = svc.evaluate("git status")
    assert decision.decision == "allow"


def test_non_whitelisted_in_automatic_mode_requires_confirm() -> None:
    svc = _service("Automatic", allowed=["git status"], denied=[])
    decision = svc.evaluate("git push")
    assert decision.decision == "confirm"


def test_confirmation_mode_always_confirms() -> None:
    svc = _service("Confirmation", allowed=["git status"], denied=[])
    decision = svc.evaluate("git status")
    assert decision.decision == "confirm"


def test_denied_overrides_allowed_in_confirmation_mode() -> None:
    svc = _service("Confirmation", allowed=["rm"], denied=["rm"])
    decision = svc.evaluate("rm")
    assert decision.decision == "deny"


def test_evaluation_is_case_insensitive() -> None:
    svc = _service("Automatic", allowed=["Git Status"], denied=[])
    decision = svc.evaluate("git status")
    assert decision.decision == "allow"
