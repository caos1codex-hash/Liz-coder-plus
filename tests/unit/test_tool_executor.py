"""Unit tests for the ToolExecutor.

Sprint 1.5 - Phase 6.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.event_bus import EventBus  # noqa: E402
from src.tool_executor import ToolExecutor  # noqa: E402


class StubTool:
    """Minimal tool satisfying the Tool protocol."""

    def __init__(self, name: str = "stub", succeed: bool = True) -> None:
        self.name = name
        self._succeed = succeed
        self.call_count = 0

    async def execute(self, params: dict) -> dict:
        self.call_count += 1
        if self._succeed:
            return {"success": True, "output": "done"}
        raise RuntimeError("tool explosion")


@pytest.mark.asyncio
async def test_execute_without_permission_service() -> None:
    """Without permissions, tool runs directly (safe default: allow)."""
    executor = ToolExecutor()
    tool = StubTool()
    result = await executor.execute(tool, {"key": "val"}, session_id="s1")

    assert result.success is True
    assert result.decision == "allow"
    assert result.output == "done"
    assert result.duration_ms >= 0
    assert tool.call_count == 1


@pytest.mark.asyncio
async def test_execute_emits_events() -> None:
    bus = EventBus()
    executor = ToolExecutor(event_bus=bus)
    tool = StubTool()

    events = []
    bus.subscribe("tool.requested", lambda p: events.append(("requested", p)))
    bus.subscribe("tool.executed", lambda p: events.append(("executed", p)))

    await executor.execute(tool, {}, session_id="s1")

    assert len(events) == 2
    assert events[0][0] == "requested"
    assert events[1][0] == "executed"
    assert events[1][1]["success"] is True


@pytest.mark.asyncio
async def test_execute_denied_by_permission_service() -> None:
    """When permission says deny, tool does not execute."""

    class DenyService:
        def evaluate(self, command):
            from dataclasses import dataclass
            @dataclass
            class D:
                decision = "deny"
                reason = "Not allowed"
                mode = None
            return D()

    executor = ToolExecutor(permission_service=DenyService())
    tool = StubTool()
    result = await executor.execute(tool, {}, session_id="s1")

    assert result.decision == "deny"
    assert result.success is False
    assert "Not allowed" in result.error
    assert tool.call_count == 0


@pytest.mark.asyncio
async def test_execute_confirm_by_permission_service() -> None:
    """When permission says confirm, tool does not execute."""

    class ConfirmService:
        def evaluate(self, command):
            from dataclasses import dataclass
            @dataclass
            class D:
                decision = "confirm"
                reason = "Needs approval"
                mode = None
            return D()

    executor = ToolExecutor(permission_service=ConfirmService())
    tool = StubTool()
    result = await executor.execute(tool, {}, session_id="s1")

    assert result.decision == "confirm"
    assert tool.call_count == 0


@pytest.mark.asyncio
async def test_execute_tool_failure() -> None:
    executor = ToolExecutor()
    tool = StubTool(succeed=False)
    result = await executor.execute(tool, {}, session_id="s1")

    assert result.success is False
    assert result.decision == "allow"
    assert "explosion" in result.error


@pytest.mark.asyncio
async def test_execute_emits_failure_event() -> None:
    bus = EventBus()
    executor = ToolExecutor(event_bus=bus)
    tool = StubTool(succeed=False)

    events = []
    bus.subscribe("tool.failed", lambda p: events.append(p))

    await executor.execute(tool, {}, session_id="s1")

    assert len(events) == 1
    assert "explosion" in events[0]["error"]


@pytest.mark.asyncio
async def test_result_to_dict() -> None:
    executor = ToolExecutor()
    tool = StubTool()
    result = await executor.execute(tool, {})
    d = result.to_dict()
    assert d["tool_name"] == "stub"
    assert d["success"] is True
    assert "duration_ms" in d