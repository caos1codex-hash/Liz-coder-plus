"""Unit tests for the PermissionService with levels.

Sprint 1.6 - Phase 7.
Tests: evaluate_tool with LOW/MEDIUM/HIGH, audit logging,
level-aware automatic mode, set_auto_allow_up_to.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "apps" / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.core.config import PermissionsConfig
from src.permissions.service import PermissionService


def _service(mode: str = "Automatic", allowed=None, denied=None) -> PermissionService:
    cfg = PermissionsConfig(
        mode=mode,
        allowed_commands=allowed or [],
        denied_commands=denied or [],
    )
    return PermissionService(cfg)


# ─── LOW level ───────────────────────────────────────────────────


def test_low_tool_auto_mode_allowed_by_default() -> None:
    svc = _service("Automatic")
    d = svc.evaluate_tool("info_tool", level="low", agent_name="echo")
    assert d.decision == "allow"
    assert d.level == "low"


def test_low_tool_confirmation_mode_confirms() -> None:
    svc = _service("Confirmation")
    d = svc.evaluate_tool("info_tool", level="low", agent_name="echo")
    assert d.decision == "confirm"


def test_low_tool_denied_even_if_low() -> None:
    svc = _service("Automatic", denied=["info_tool"])
    d = svc.evaluate_tool("info_tool", level="low")
    assert d.decision == "deny"


# ─── MEDIUM level ────────────────────────────────────────────────


def test_medium_tool_auto_mode_confirms_by_default() -> None:
    svc = _service("Automatic")
    d = svc.evaluate_tool("write_tool", level="medium", agent_name="coder")
    assert d.decision == "confirm"
    assert d.level == "medium"


def test_medium_tool_auto_mode_allowed_with_threshold() -> None:
    svc = _service("Automatic")
    svc.set_auto_allow_up_to("medium")
    d = svc.evaluate_tool("write_tool", level="medium")
    assert d.decision == "allow"


def test_medium_tool_confirmation_mode_confirms() -> None:
    svc = _service("Confirmation")
    d = svc.evaluate_tool("write_tool", level="medium")
    assert d.decision == "confirm"


# ─── HIGH level ──────────────────────────────────────────────────


def test_high_tool_auto_mode_confirms() -> None:
    svc = _service("Automatic")
    d = svc.evaluate_tool("terminal", level="high", agent_name="sys_agent")
    assert d.decision == "confirm"
    assert d.level == "high"


def test_high_tool_auto_mode_allowed_with_high_threshold() -> None:
    svc = _service("Automatic")
    svc.set_auto_allow_up_to("high")
    d = svc.evaluate_tool("terminal", level="high")
    assert d.decision == "allow"


def test_high_tool_confirmation_mode_always_confirms() -> None:
    svc = _service("Confirmation")
    d = svc.evaluate_tool("terminal", level="high")
    assert d.decision == "confirm"


# ─── Whitelist overrides ─────────────────────────────────────────


def test_whitelisted_high_tool_allowed_in_auto() -> None:
    svc = _service("Automatic", allowed=["terminal"])
    d = svc.evaluate_tool("terminal", level="high")
    assert d.decision == "allow"


def test_denied_high_tool_still_denied_in_auto() -> None:
    svc = _service("Automatic", allowed=["terminal"], denied=["terminal"])
    d = svc.evaluate_tool("terminal", level="high")
    assert d.decision == "deny"


# ─── Audit Log ───────────────────────────────────────────────────


def test_audit_log_records_entries() -> None:
    svc = _service("Automatic")
    svc.evaluate_tool("info_tool", level="low", agent_name="echo")
    svc.evaluate_tool("terminal", level="high", agent_name="sys")

    log = svc.audit_log
    assert len(log) == 2
    assert log[0].tool_name == "info_tool"
    assert log[0].agent_name == "echo"
    assert log[0].decision == "allow"
    assert log[1].tool_name == "terminal"
    assert log[1].decision == "confirm"


def test_audit_log_cleared() -> None:
    svc = _service("Automatic")
    svc.evaluate_tool("info_tool", level="low")
    svc.evaluate_tool("terminal", level="high")
    count = svc.clear_audit_log()
    assert count == 2
    assert len(svc.audit_log) == 0


# ─── set_auto_allow_up_to ────────────────────────────────────────


def test_set_auto_allow_invalid_raises() -> None:
    svc = _service("Automatic")
    with pytest.raises(ValueError, match="Invalid permission level"):
        svc.set_auto_allow_up_to("super_high")


def test_auto_allow_up_to_property() -> None:
    svc = _service("Automatic")
    assert svc.auto_allow_up_to == "low"
    svc.set_auto_allow_up_to("medium")
    assert svc.auto_allow_up_to == "medium"


# ─── Backward compatibility ──────────────────────────────────────


def test_legacy_evaluate_still_works() -> None:
    svc = _service("Automatic", allowed=["git status"])
    d = svc.evaluate("git status")
    assert d.decision == "allow"


def test_legacy_evaluate_records_audit() -> None:
    svc = _service("Automatic")
    svc.evaluate("git status")
    assert len(svc.audit_log) == 1