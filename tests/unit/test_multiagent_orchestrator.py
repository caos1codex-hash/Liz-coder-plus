"""Tests para AgentOrchestrator (Sprint 2.1)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.base import BaseAgent  # noqa: E402
from multiagent.config import MultiAgentConfig  # noqa: E402
from multiagent.enums import (  # noqa: E402
    AgentStatus,
    MessageType,
    Permission,
)
from multiagent.orchestrator import AgentOrchestrator  # noqa: E402


# ------------------------------------------------------------------
# Agente concreto de test
# ------------------------------------------------------------------


class StubAgent(BaseAgent):
    """Agente stub configurable."""

    default_permissions = frozenset({Permission.MEMORY_WRITE})
    default_objectives = ["stub", "test"]

    def __init__(self, name: str = "stub", *, fail: bool = False, **kwargs):
        super().__init__(name=name, description="stub", **kwargs)
        self._fail = fail
        self.handled: list[dict] = []

    async def _execute_impl(self, task: dict) -> dict:
        self.handled.append(task)
        if self._fail:
            raise RuntimeError("stub failure")
        return {"handled": task.get("name"), "tools_used": []}


# ------------------------------------------------------------------
# Registro
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_and_get_agent() -> None:
    o = AgentOrchestrator()
    a = StubAgent("a1")
    await o.register_agent(a)
    assert o.agent_count == 1
    assert o.get_agent("a1") is a
    assert "a1" in o.list_agents()


@pytest.mark.asyncio
async def test_register_starts_agent_if_created() -> None:
    o = AgentOrchestrator()
    a = StubAgent("a1")
    assert a.status == AgentStatus.CREATED
    await o.register_agent(a)
    assert a.status == AgentStatus.IDLE  # start() fue llamado


@pytest.mark.asyncio
async def test_register_rejects_non_baseagent() -> None:
    o = AgentOrchestrator()
    with pytest.raises(TypeError):
        await o.register_agent("not an agent")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_register_max_agents_enforced() -> None:
    config = MultiAgentConfig(max_agents=2)
    o = AgentOrchestrator(config=config)
    await o.register_agent(StubAgent("a1"))
    await o.register_agent(StubAgent("a2"))
    with pytest.raises(ValueError, match="Max agents reached"):
        await o.register_agent(StubAgent("a3"))


@pytest.mark.asyncio
async def test_remove_agent() -> None:
    o = AgentOrchestrator()
    a = StubAgent("a1")
    await o.register_agent(a)
    assert await o.remove_agent("a1") is True
    assert o.agent_count == 0
    assert a.status == AgentStatus.STOPPED
    assert await o.remove_agent("nonexistent") is False


# ------------------------------------------------------------------
# Dispatch
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_empty_returns_empty() -> None:
    o = AgentOrchestrator()
    r = await o.dispatch()
    assert r["status"] == "empty"


@pytest.mark.asyncio
async def test_dispatch_assigns_to_named_agent() -> None:
    o = AgentOrchestrator()
    a = StubAgent("a1")
    await o.register_agent(a)
    await o.enqueue("t1", agent_name="a1", payload={"x": 1})
    r = await o.dispatch()
    assert r["status"] == "ok"
    assert a.handled[0]["name"] == "t1"


@pytest.mark.asyncio
async def test_dispatch_auto_selects_agent() -> None:
    o = AgentOrchestrator()
    a = StubAgent("stub")  # nombre coincide con kind "stub" en objectives
    await o.register_agent(a)
    await o.enqueue("t1", payload={"kind": "stub"})
    r = await o.dispatch()
    assert r["status"] == "ok"
    assert len(a.handled) == 1


@pytest.mark.asyncio
async def test_dispatch_no_agent_available_fails_task() -> None:
    o = AgentOrchestrator()
    await o.register_agent(StubAgent("a1"))
    # Encolar tarea para un agente inexistente.
    await o.enqueue("t1", agent_name="nonexistent", payload={})
    r = await o.dispatch()
    assert r["status"] == "error"
    assert "no agent available" in r["error"]


@pytest.mark.asyncio
async def test_dispatch_failure_marks_failed() -> None:
    o = AgentOrchestrator()
    a = StubAgent("a1", fail=True)
    await o.register_agent(a)
    await o.enqueue("t1", agent_name="a1")
    r = await o.dispatch()
    assert r["status"] == "error"
    assert o.metrics()["orchestrator"]["failed"] == 1


@pytest.mark.asyncio
async def test_run_until_empty() -> None:
    o = AgentOrchestrator()
    await o.register_agent(StubAgent("a1"))
    await o.enqueue("t1", agent_name="a1")
    await o.enqueue("t2", agent_name="a1")
    await o.enqueue("t3", agent_name="a1")
    results = await o.run_until_empty()
    assert len(results) == 3
    assert all(r["status"] == "ok" for r in results)


# ------------------------------------------------------------------
# Broadcast
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_reaches_subscribers() -> None:
    o = AgentOrchestrator()
    await o.register_agent(StubAgent("a1"))
    await o.register_agent(StubAgent("a2"))
    # Cada agente se suscribe automáticamente al bus sobre su nombre.
    # Para broadcast usamos el canal "*".
    received: list[str] = []

    async def handler(m):
        received.append(m.sender)

    await o.message_bus.subscribe_broadcast(handler)
    n = await o.broadcast({"hello": "all"})
    assert n >= 1
    assert o.metrics()["orchestrator"]["broadcasts"] == 1


@pytest.mark.asyncio
async def test_send_to_specific_agent() -> None:
    o = AgentOrchestrator()
    a1 = StubAgent("a1")
    await o.register_agent(a1)
    msg = await o.send_to("a1", MessageType.REQUEST, {"x": 1})
    assert msg.receiver == "a1"
    # El handler por defecto del agente lo almacena en memoria corta.
    assert len(a1.memory.recent(5)) >= 1


# ------------------------------------------------------------------
# Pause / resume all
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_all_and_resume_all() -> None:
    o = AgentOrchestrator()
    await o.register_agent(StubAgent("a1"))
    await o.register_agent(StubAgent("a2"))
    paused = await o.pause_all()
    assert paused == 2
    assert all(a.status == AgentStatus.PAUSED for a in o.agents.values())
    resumed = await o.resume_all()
    assert resumed == 2
    assert all(a.status == AgentStatus.IDLE for a in o.agents.values())


@pytest.mark.asyncio
async def test_stop_all() -> None:
    o = AgentOrchestrator()
    await o.register_agent(StubAgent("a1"))
    await o.register_agent(StubAgent("a2"))
    stopped = await o.stop_all()
    assert stopped == 2
    assert all(a.status == AgentStatus.STOPPED for a in o.agents.values())


# ------------------------------------------------------------------
# Health & metrics
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_summary() -> None:
    o = AgentOrchestrator()
    await o.register_agent(StubAgent("a1"))
    await o.register_agent(StubAgent("a2"))
    h = o.health()
    assert h["total_agents"] == 2
    assert h["summary"]["alive"] == 2
    assert h["healthy"] == 2


@pytest.mark.asyncio
async def test_metrics_contains_agent_stats() -> None:
    o = AgentOrchestrator()
    a = StubAgent("a1")
    await o.register_agent(a)
    await o.enqueue("t1", agent_name="a1", payload={})
    await o.dispatch()
    m = o.metrics()
    assert m["orchestrator"]["dispatched"] == 1
    assert m["orchestrator"]["completed"] == 1
    assert "a1" in m["agents"]
    assert m["agents"]["a1"]["tasks_ok"] == 1


@pytest.mark.asyncio
async def test_agent_status_dict() -> None:
    o = AgentOrchestrator()
    await o.register_agent(StubAgent("a1"))
    s = o.agent_status()
    assert s == {"a1": "idle"}


# ------------------------------------------------------------------
# Heartbeat
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_start_stop() -> None:
    config = MultiAgentConfig(heartbeat_interval=0.05, enable_heartbeat=True)
    o = AgentOrchestrator(config=config)
    await o.register_agent(StubAgent("a1"))
    await o.start_heartbeat()
    assert o.is_running is True
    await asyncio.sleep(0.15)  # esperar al menos un beat
    await o.stop_heartbeat()
    assert o.is_running is False


# ------------------------------------------------------------------
# Serialize
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serialize_orchestrator() -> None:
    o = AgentOrchestrator()
    await o.register_agent(StubAgent("a1"))
    s = o.serialize()
    assert "config" in s
    assert "agents" in s
    assert len(s["agents"]) == 1
    assert s["agents"][0]["name"] == "a1"


# ------------------------------------------------------------------
# Shutdown
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_stops_all() -> None:
    o = AgentOrchestrator()
    await o.register_agent(StubAgent("a1"))
    await o.register_agent(StubAgent("a2"))
    await o.shutdown()
    assert all(a.status == AgentStatus.STOPPED for a in o.agents.values())
