"""Unit tests for ToolExecutor — Sprint 1.6 additions.

Tests: timeout, cancellation, context passing, agent tracking.
The original 7 tests (test_tool_executor.py) remain unchanged.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.event_bus import EventBus
from src.tool_executor import ToolExecutor


class SlowTool:
    """A tool that takes longer than the timeout."""

    def __init__(self) -> None:
        self.name = "slow"
        self.call_count = 0

    async def execute(self, params):
        self.call_count += 1
        await asyncio.sleep(10)
        return {"success": True}


class ContextReceivingTool:
    """A tool that accepts context parameter."""

    def __init__(self) -> None:
        self.name = "ctx_tool"
        self.received_context = None

    async def execute(self, params, *, context=None):
        self.received_context = context
        return {"success": True, "output": str(context)}


class CancelAwareTool:
    """A tool that can be cancelled."""

    def __init__(self) -> None:
        self.name = "cancel_tool"
        self.was_cancelled = False

    async def execute(self, params):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.was_cancelled = True
            raise
        return {"success": True}


# ─── Timeout ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_timeout() -> None:
    executor = ToolExecutor(default_timeout=0.1)
    tool = SlowTool()
    result = await executor.execute(tool, {}, session_id="s1")

    assert result.decision == "timeout"
    assert result.success is False
    assert "timed out" in result.error
    assert tool.call_count == 1


@pytest.mark.asyncio
async def test_execute_custom_timeout() -> None:
    executor = ToolExecutor(default_timeout=60)
    tool = SlowTool()
    result = await executor.execute(
        tool, {}, session_id="s1", timeout=0.1
    )

    assert result.decision == "timeout"


@pytest.mark.asyncio
async def test_execute_timeout_emits_failed_event() -> None:
    bus = EventBus()
    executor = ToolExecutor(event_bus=bus, default_timeout=0.1)
    tool = SlowTool()

    events = []
    bus.subscribe("tool.failed", lambda p: events.append(p))

    await executor.execute(tool, {}, session_id="s1")

    assert len(events) == 1
    assert events[0]["error_type"] == "timeout"


# ─── Cancellation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_running_tool() -> None:
    executor = ToolExecutor(default_timeout=60)
    tool = CancelAwareTool()

    # Start execution in a task.
    task = asyncio.create_task(
        executor.execute(tool, {}, session_id="s1")
    )

    # Give it a moment to start.
    await asyncio.sleep(0.05)

    # Cancel it.
    cancelled = await executor.cancel("cancel_tool")
    assert cancelled is True

    # The task should complete (with cancellation).
    result = await task
    assert result.decision == "cancelled"
    assert tool.was_cancelled is True


@pytest.mark.asyncio
async def test_cancel_nonexistent_returns_false() -> None:
    executor = ToolExecutor()
    assert await executor.cancel("nope") is False


@pytest.mark.asyncio
async def test_cancel_all() -> None:
    executor = ToolExecutor(default_timeout=60)
    tool1 = CancelAwareTool()
    tool1.name = "cancel_1"
    tool2 = CancelAwareTool()
    tool2.name = "cancel_2"

    t1 = asyncio.create_task(executor.execute(tool1, {}))
    t2 = asyncio.create_task(executor.execute(tool2, {}))

    await asyncio.sleep(0.05)

    count = await executor.cancel_all()
    assert count == 2

    # Clean up tasks.
    for t in (t1, t2):
        try:
            await t
        except Exception:
            pass


# ─── Context Passing ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_passed_to_tool() -> None:
    executor = ToolExecutor()
    tool = ContextReceivingTool()
    await executor.execute(
        tool, {},
        session_id="s1",
        agent_name="echo",
    )
    assert tool.received_context is not None
    assert tool.received_context["session_id"] == "s1"
    assert tool.received_context["agent_name"] == "echo"


# ─── Agent Tracking ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_name_in_result() -> None:
    executor = ToolExecutor()
    tool = ContextReceivingTool()
    result = await executor.execute(
        tool, {}, agent_name="test_agent"
    )
    assert result.agent_name == "test_agent"


@pytest.mark.asyncio
async def test_session_id_in_result() -> None:
    executor = ToolExecutor()
    tool = ContextReceivingTool()
    result = await executor.execute(
        tool, {}, session_id="sess_42"
    )
    assert result.session_id == "sess_42"


# ─── Active Count ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_count() -> None:
    executor = ToolExecutor(default_timeout=60)
    tool = CancelAwareTool()

    assert executor.active_count == 0

    task = asyncio.create_task(executor.execute(tool, {}))
    await asyncio.sleep(0.05)

    assert executor.active_count == 1

    await executor.cancel("cancel_tool")
    try:
        await task
    except Exception:
        pass

    assert executor.active_count == 0


# ─── Event includes agent/session ─────────────────────────────────


@pytest.mark.asyncio
async def test_events_include_agent_and_session() -> None:
    bus = EventBus()
    executor = ToolExecutor(event_bus=bus)
    tool = ContextReceivingTool()

    events = []
    bus.subscribe("tool.requested", lambda p: events.append(p))

    await executor.execute(
        tool, {},
        session_id="s1",
        agent_name="a1",
    )

    assert len(events) == 1
    assert events[0]["session_id"] == "s1"
    assert events[0]["agent_name"] == "a1"