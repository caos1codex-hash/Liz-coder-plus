"""Tests para AgentRegistry (Sprint 2.2/2)."""

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
from multiagent.enums import HealthState, Permission  # noqa: E402
from multiagent.registry.agent_registry import AgentRecord, AgentRegistry  # noqa: E402


# ------------------------------------------------------------------
# Stub agent
# ------------------------------------------------------------------


class StubAgent(BaseAgent):
    default_permissions = frozenset({Permission.MEMORY_WRITE})
    default_objectives = ["stub", "test", "code"]
    default_tools = ["file_tool", "planner"]

    def __init__(self, name: str = "stub", **kwargs):
        super().__init__(name=name, description="stub agent", **kwargs)

    async def _execute_impl(self, task: dict) -> dict:
        return {"handled": True}


async def _started(agent: BaseAgent) -> BaseAgent:
    await agent.start()
    return agent


# ------------------------------------------------------------------
# Registro básico
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_agent() -> None:
    reg = AgentRegistry()
    agent = await _started(StubAgent("a1"))
    record = await reg.register(agent, provided_capabilities=["code.generate"])
    assert reg.exists("a1")
    assert reg.exists(record.id)
    assert record.name == "a1"
    assert record.provided_capabilities == ["code.generate"]


@pytest.mark.asyncio
async def test_register_rejects_non_baseagent() -> None:
    reg = AgentRegistry()
    # Un string no tiene los atributos de BaseAgent.
    with pytest.raises(TypeError):
        await reg.register("not an agent")  # type: ignore[arg-type]
    # Un objeto sin los métodos clave tampoco.
    with pytest.raises(TypeError):
        await reg.register(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_register_replace_existing() -> None:
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a1"))  # mismo nombre
    await reg.register(a1, provided_capabilities=["code.generate"])
    # Reemplazar.
    await reg.register(a2, provided_capabilities=["code.review"], replace_existing=True)
    record = reg.get_by_name("a1")
    assert record is not None
    assert record.provided_capabilities == ["code.review"]
    assert reg.metrics()["replaced_total"] == 1


@pytest.mark.asyncio
async def test_register_no_replace_raises() -> None:
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a1"))
    await reg.register(a1)
    with pytest.raises(ValueError, match="already registered"):
        await reg.register(a2, replace_existing=False)


@pytest.mark.asyncio
async def test_unregister_by_name() -> None:
    reg = AgentRegistry()
    agent = await _started(StubAgent("a1"))
    await reg.register(agent)
    assert await reg.unregister("a1") is True
    assert reg.exists("a1") is False


@pytest.mark.asyncio
async def test_unregister_by_id() -> None:
    reg = AgentRegistry()
    agent = await _started(StubAgent("a1"))
    record = await reg.register(agent)
    assert await reg.unregister(record.id) is True
    assert reg.exists(record.id) is False


@pytest.mark.asyncio
async def test_unregister_nonexistent_returns_false() -> None:
    reg = AgentRegistry()
    assert await reg.unregister("nonexistent") is False


@pytest.mark.asyncio
async def test_replace_agent() -> None:
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a1"))
    await reg.register(a1, provided_capabilities=["code.generate"], priority=5)
    record = await reg.replace("a1", a2, provided_capabilities=["code.review"])
    assert record.provided_capabilities == ["code.review"]
    # El priority se mantiene del original (via kwargs.setdefault).
    assert record.priority == 5


@pytest.mark.asyncio
async def test_replace_nonexistent_raises() -> None:
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    with pytest.raises(KeyError):
        await reg.replace("nonexistent", a1)


# ------------------------------------------------------------------
# Consultas
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_by_name_and_id() -> None:
    reg = AgentRegistry()
    agent = await _started(StubAgent("a1"))
    record = await reg.register(agent)
    assert reg.get_by_name("a1") is not None
    assert reg.get_by_id(record.id) is not None
    assert reg.get_by_name("nonexistent") is None
    assert reg.get_by_id("nonexistent") is None


@pytest.mark.asyncio
async def test_get_all() -> None:
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    await reg.register(a1)
    await reg.register(a2)
    all_records = reg.get_all()
    assert len(all_records) == 2


@pytest.mark.asyncio
async def test_names() -> None:
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    await reg.register(a1)
    await reg.register(a2)
    names = reg.names()
    assert set(names) == {"a1", "a2"}


@pytest.mark.asyncio
async def test_find_by_capability_exact() -> None:
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    await reg.register(a1, provided_capabilities=["code.generate"])
    await reg.register(a2, provided_capabilities=["code.review"])
    coders = reg.find_by_capability("code.generate")
    assert len(coders) == 1
    assert coders[0].name == "a1"


@pytest.mark.asyncio
async def test_find_by_capability_hierarchical() -> None:
    """Un agente que provee 'code' debe matchear 'code.generate'."""
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    await reg.register(a1, provided_capabilities=["code"])
    coders = reg.find_by_capability("code.generate")
    assert len(coders) == 1
    assert coders[0].name == "a1"


@pytest.mark.asyncio
async def test_find_by_capability_wildcard() -> None:
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    await reg.register(a1, provided_capabilities=["code.generate"])
    await reg.register(a2, provided_capabilities=["code.review"])
    results = reg.find_by_capability_pattern("code.*")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_find_by_capability_filters_unavailable() -> None:
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    await reg.register(a1, provided_capabilities=["code.generate"])
    # Poner a1 en PAUSED (no disponible) y sincronizar el record.
    await a1.pause()
    reg.update_health()
    results = reg.find_by_capability("code.generate", only_available=True)
    assert len(results) == 0
    # Sin filtro de disponibilidad.
    results = reg.find_by_capability("code.generate", only_available=False)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_find_by_capability_orders_by_priority() -> None:
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    await reg.register(a1, provided_capabilities=["code.generate"], priority=1)
    await reg.register(a2, provided_capabilities=["code.generate"], priority=10)
    results = reg.find_by_capability("code.generate")
    # Mayor prioridad primero.
    assert results[0].name == "a2"


# ------------------------------------------------------------------
# Health / heartbeat
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_updates_timestamp() -> None:
    reg = AgentRegistry()
    agent = await _started(StubAgent("a1"))
    record = await reg.register(agent)
    old_hb = record.last_heartbeat
    await asyncio.sleep(0.01)
    assert reg.heartbeat("a1") is True
    assert record.last_heartbeat != old_hb


@pytest.mark.asyncio
async def test_heartbeat_nonexistent_returns_false() -> None:
    reg = AgentRegistry()
    assert reg.heartbeat("nonexistent") is False


@pytest.mark.asyncio
async def test_update_health_detects_changes() -> None:
    reg = AgentRegistry()
    agent = await _started(StubAgent("a1"))
    record = await reg.register(agent)
    # Estado inicial: ALIVE.
    assert record.health_state == HealthState.ALIVE
    # Detener el agente → health cambia a DEAD.
    await agent.stop()
    changes = reg.update_health()
    assert changes >= 1
    assert record.health_state == HealthState.DEAD


@pytest.mark.asyncio
async def test_health_summary() -> None:
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    await reg.register(a1)
    await reg.register(a2)
    await a2.stop()
    reg.update_health()
    h = reg.health()
    assert h["total"] == 2
    assert h["dead"] >= 1
    assert "a1" in h["agents"]
    assert "a2" in h["agents"]


@pytest.mark.asyncio
async def test_heartbeat_loop() -> None:
    reg = AgentRegistry(heartbeat_timeout=0.05)
    agent = await _started(StubAgent("a1"))
    await reg.register(agent)
    await reg.start_heartbeat_loop(interval=0.02)
    await asyncio.sleep(0.1)
    await reg.stop_heartbeat_loop()
    assert reg.metrics()["heartbeat_checks"] > 0


# ------------------------------------------------------------------
# Auto-discovery
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_via_hooks() -> None:
    reg = AgentRegistry()

    async def hook(registry):
        a1 = StubAgent("discovered1")
        a2 = StubAgent("discovered2")
        await a1.start()
        await a2.start()
        return [a1, a2]

    reg.add_discovery_hook(hook)
    discovered = await reg.discover()
    assert len(discovered) == 2
    assert reg.exists("discovered1")
    assert reg.exists("discovered2")
    assert reg.metrics()["discoveries_total"] == 2


@pytest.mark.asyncio
async def test_discover_infers_capabilities() -> None:
    reg = AgentRegistry()
    # StubAgent tiene objectives=["stub","test","code"] y tools=["file_tool","planner"].
    # _infer_capabilities debe mapear:
    #   "code" → code.generate
    #   "test" → code.test
    #   "planner" tool → planning
    #   "file_tool" tool → filesystem.read
    async def hook(registry):
        a1 = StubAgent("a1")
        await a1.start()
        return [a1]

    reg.add_discovery_hook(hook)
    await reg.discover()
    record = reg.get_by_name("a1")
    assert record is not None
    caps = record.provided_capabilities
    # Al menos debe haber detectado code.generate (de objective "code")
    # y planning (de tool "planner").
    assert "code.generate" in caps
    assert "planning" in caps


@pytest.mark.asyncio
async def test_discovery_hook_failure_isolated() -> None:
    reg = AgentRegistry()

    async def bad_hook(registry):
        raise RuntimeError("boom")

    async def good_hook(registry):
        a1 = StubAgent("a1")
        await a1.start()
        return [a1]

    reg.add_discovery_hook(bad_hook)
    reg.add_discovery_hook(good_hook)
    discovered = await reg.discover()
    # El hook bueno funcionó aunque el malo fallara.
    assert len(discovered) == 1


# ------------------------------------------------------------------
# AgentRecord
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_usage_tracking() -> None:
    reg = AgentRegistry()
    agent = await _started(StubAgent("a1"))
    record = await reg.register(agent)
    record.record_usage(success=True, duration_ms=100.0)
    record.record_usage(success=True, duration_ms=200.0)
    record.record_usage(success=False, duration_ms=50.0)
    assert record.usage_count == 3
    assert record.success_count == 2
    assert record.failure_count == 1
    assert record.success_rate == 2 / 3
    assert record.total_duration_ms == 350.0
    assert record.avg_latency_ms == 350.0 / 3


@pytest.mark.asyncio
async def test_record_add_error_with_cap() -> None:
    reg = AgentRegistry()
    agent = await _started(StubAgent("a1"))
    record = await reg.register(agent)
    for i in range(30):
        record.add_error(f"error {i}")
    assert len(record.errors) == AgentRecord.MAX_ERRORS


@pytest.mark.asyncio
async def test_record_provides_with_wildcard() -> None:
    reg = AgentRegistry()
    agent = await _started(StubAgent("a1"))
    record = await reg.register(agent, provided_capabilities=["code.*"])
    assert record.provides("code.generate") is True
    assert record.provides("code.review") is True
    assert record.provides("git.commit") is False


@pytest.mark.asyncio
async def test_record_to_dict() -> None:
    reg = AgentRegistry()
    agent = await _started(StubAgent("a1"))
    record = await reg.register(agent, provided_capabilities=["code.generate"], priority=5)
    d = record.to_dict()
    assert d["name"] == "a1"
    assert d["priority"] == 5
    assert d["provided_capabilities"] == ["code.generate"]
    assert d["status"] == "idle"
    assert d["health_state"] == "alive"
    assert "created_at" in d
    assert "last_heartbeat" in d


# ------------------------------------------------------------------
# Métricas
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_metrics() -> None:
    reg = AgentRegistry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    await reg.register(a1, provided_capabilities=["code.generate"])
    await reg.register(a2, provided_capabilities=["code.review"])
    m = reg.metrics()
    assert m["registry_size"] == 2
    assert m["registered_total"] == 2
    assert m["available_count"] == 2
    assert m["healthy_count"] == 2
    assert m["avg_success_rate"] == 1.0


# ------------------------------------------------------------------
# Eventos
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_emits_events() -> None:
    from multiagent.workflow.event_bus import EventBus

    bus = EventBus()
    received: list[str] = []

    async def handler(e):
        received.append(e.type_str)

    await bus.subscribe(handler)
    reg = AgentRegistry(event_bus=bus)
    agent = await _started(StubAgent("a1"))
    await reg.register(agent)
    await asyncio.sleep(0.05)  # esperar emisión async
    assert "agent.registered" in received
    await reg.unregister("a1")
    await asyncio.sleep(0.05)
    assert "agent.removed" in received
