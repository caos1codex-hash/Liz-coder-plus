"""Unit tests for the Orchestrator with events, lifecycle, and timeout.

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

from src.orchestrator import Orchestrator  # noqa: E402
from src.event_bus import EventBus  # noqa: E402


@pytest.mark.asyncio
async def test_orchestrator_emits_user_message_received() -> None:
    bus = EventBus()
    orch = Orchestrator(event_bus=bus)

    events = []
    bus.subscribe("user.message.received", lambda p: events.append(p))

    await orch.handle_message("hola", "s1")

    assert len(events) == 1
    assert events[0]["session_id"] == "s1"
    assert events[0]["message_length"] == 4


@pytest.mark.asyncio
async def test_orchestrator_emits_agent_invoked_and_completed() -> None:
    bus = EventBus()
    orch = Orchestrator(event_bus=bus)

    events = []
    bus.subscribe("agent.invoked", lambda p: events.append(("invoked", p)))
    bus.subscribe("agent.completed", lambda p: events.append(("completed", p)))

    await orch.handle_message("hola", "s1")

    types = [e[0] for e in events]
    assert "invoked" in types
    assert "completed" in types

    completed = [e[1] for e in events if e[0] == "completed"][0]
    assert completed["agent_name"] == "echo"
    assert completed["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_orchestrator_emits_agent_failed_on_error() -> None:
    from src.agent_router import AgentRouter

    bus = EventBus()
    events = []
    bus.subscribe("agent.failed", lambda p: events.append(p))

    class BoomAgent:
        name = "boom"
        async def handle(self, msg, ctx):
            raise ValueError("test error")

    router = AgentRouter(default_agent=BoomAgent())
    orch = Orchestrator(event_bus=bus, router=router)
    await orch.handle_message("hola", "s1")

    assert len(events) == 1
    assert events[0]["error_type"] == "validation"


@pytest.mark.asyncio
async def test_orchestrator_timeout_returns_error() -> None:
    from src.agent_router import AgentRouter

    class SlowAgent:
        name = "slow"
        async def handle(self, msg, ctx):
            import asyncio
            await asyncio.sleep(10)

    router = AgentRouter(default_agent=SlowAgent())
    orch = Orchestrator(router=router, agent_timeout=0.05)

    result = await orch.handle_message("hola", "s1")
    assert result["type"] == "error"
    assert result["error"] == "agent_timeout"
    assert "demasiado" in result["content"]


@pytest.mark.asyncio
async def test_orchestrator_lifecycle_initialize_and_shutdown() -> None:
    bus = EventBus()
    orch = Orchestrator(event_bus=bus)

    assert orch.is_initialized is False
    assert orch.is_shutdown is False

    events = []
    bus.subscribe("system.started", lambda p: events.append(("started", p)))
    bus.subscribe("system.stopped", lambda p: events.append(("stopped", p)))

    await orch.initialize()
    assert orch.is_initialized is True

    await orch.shutdown()
    assert orch.is_shutdown is True

    assert len(events) == 2
    assert events[0][0] == "started"
    assert events[1][0] == "stopped"


@pytest.mark.asyncio
async def test_orchestrator_initialize_is_idempotent() -> None:
    orch = Orchestrator()
    await orch.initialize()
    await orch.initialize()  # second call is no-op
    assert orch.is_initialized is True


@pytest.mark.asyncio
async def test_orchestrator_shutdown_is_idempotent() -> None:
    orch = Orchestrator()
    await orch.shutdown()
    await orch.shutdown()  # second call is no-op
    assert orch.is_shutdown is True


@pytest.mark.asyncio
async def test_orchestrator_register_agent_validates_protocol() -> None:
    orch = Orchestrator()

    with pytest.raises(TypeError, match="Agent protocol"):
        orch.register_agent("not an agent")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_orchestrator_register_tool_validates_protocol() -> None:
    orch = Orchestrator()

    with pytest.raises(TypeError, match="Tool protocol"):
        orch.register_tool("not a tool")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_orchestrator_execute_tool_not_found() -> None:
    orch = Orchestrator()
    result = await orch.execute_tool("nonexistent", {})

    assert result["success"] is False
    assert result["decision"] == "deny"
    assert "not registered" in result["error"]


@pytest.mark.asyncio
async def test_orchestrator_execute_tool_success() -> None:
    orch = Orchestrator()

    class GoodTool:
        name = "good"
        async def execute(self, params):
            return {"success": True, "output": "result"}

    orch.register_tool(GoodTool())
    result = await orch.execute_tool("good", {"key": "val"}, session_id="s1")

    assert result["success"] is True
    assert result["output"] == "result"


@pytest.mark.asyncio
async def test_orchestrator_agent_status() -> None:
    orch = Orchestrator()
    status = orch.agent_status()

    assert "echo" in status
    assert status["echo"]["name"] == "echo"


@pytest.mark.asyncio
async def test_orchestrator_response_includes_agent_name_and_duration() -> None:
    orch = Orchestrator()
    result = await orch.handle_message("test", "s1")

    assert result.get("agent_name") == "echo"
    assert result.get("duration_ms", 0) >= 0


@pytest.mark.asyncio
async def test_orchestrator_event_bus_accessor() -> None:
    bus = EventBus()
    orch = Orchestrator(event_bus=bus)
    assert orch.event_bus is bus