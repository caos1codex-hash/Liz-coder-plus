"""Tests para AgentRegistry — API simplificada Sprint 2.3."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent import CoderAgent, ReviewerAgent  # noqa: E402
from multiagent.base import BaseAgent  # noqa: E402
from multiagent.enums import Permission  # noqa: E402
from multiagent.registry.agent_registry import AgentRegistry  # noqa: E402
from multiagent.registry.capability import (  # noqa: E402
    CapabilityRegistry,
    register_standard_capabilities,
)
from multiagent.registry.manifest import AgentManifest, standard_manifests  # noqa: E402


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


async def _setup_registry() -> AgentRegistry:
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    return AgentRegistry(capability_registry=cap_reg)


# ------------------------------------------------------------------
# register_simple
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_simple_with_explicit_capabilities() -> None:
    reg = await _setup_registry()
    agent = await _started(StubAgent("a1"))
    record = await reg.register_simple(
        agent, capabilities=["python", "typescript"], priority=5,
    )
    assert record.name == "a1"
    assert record.provided_capabilities == ["python", "typescript"]
    assert record.priority == 5


@pytest.mark.asyncio
async def test_register_simple_infers_capabilities() -> None:
    reg = await _setup_registry()
    # StubAgent tiene objectives=["stub","test","code"] y tools=["file_tool","planner"].
    agent = await _started(StubAgent("a1"))
    record = await reg.register_simple(agent)
    # _infer_capabilities debe derivar caps de objectives y tools.
    assert len(record.provided_capabilities) > 0
    assert "code.generate" in record.provided_capabilities  # de objective "code"
    assert "planning" in record.provided_capabilities  # de tool "planner"


@pytest.mark.asyncio
async def test_register_simple_default_priority() -> None:
    reg = await _setup_registry()
    agent = await _started(StubAgent("a1"))
    record = await reg.register_simple(agent, capabilities=["python"])
    assert record.priority == 0


# ------------------------------------------------------------------
# register_with_manifest
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_with_manifest() -> None:
    reg = await _setup_registry()
    agent = await _started(StubAgent("coder"))
    manifest = AgentManifest(
        id="coder", name="Coder", version="2.0.0",
        capabilities=["python", "code.generate"],
        priority=10,
        metadata={"cost": 0.5},
    )
    record = await reg.register_with_manifest(agent, manifest)
    assert record.name == "coder"
    assert record.version == "2.0.0"
    assert record.provided_capabilities == ["python", "code.generate"]
    assert record.priority == 10
    assert record.metadata == {"cost": 0.5}


@pytest.mark.asyncio
async def test_register_with_manifest_overrides_existing() -> None:
    reg = await _setup_registry()
    agent = await _started(StubAgent("coder"))
    # Registro inicial sin manifest.
    await reg.register_simple(agent, capabilities=["old_cap"])
    # Re-registro con manifest (replace_existing=True por defecto).
    manifest = AgentManifest(
        id="coder", name="Coder", version="2.0.0",
        capabilities=["python", "code.generate"],
    )
    record = await reg.register_with_manifest(agent, manifest)
    assert record.provided_capabilities == ["python", "code.generate"]
    assert "old_cap" not in record.provided_capabilities


# ------------------------------------------------------------------
# list_agents (alias simplificado)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agents_returns_all() -> None:
    reg = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    await reg.register_simple(a1, capabilities=["python"])
    await reg.register_simple(a2, capabilities=["typescript"])
    agents = reg.list_agents()
    assert len(agents) == 2
    names = {a.name for a in agents}
    assert names == {"a1", "a2"}


@pytest.mark.asyncio
async def test_list_agents_empty() -> None:
    reg = await _setup_registry()
    assert reg.list_agents() == []


# ------------------------------------------------------------------
# list_manifests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_manifests_synthesizes_when_no_manifest() -> None:
    reg = await _setup_registry()
    agent = await _started(StubAgent("a1"))
    await reg.register_simple(agent, capabilities=["python"], priority=5)
    manifests = reg.list_manifests()
    assert len(manifests) == 1
    m = manifests[0]
    assert m.id == "a1"
    assert m.capabilities == ["python"]
    assert m.priority == 5


@pytest.mark.asyncio
async def test_list_manifests_uses_agent_manifest_method() -> None:
    reg = await _setup_registry()

    class ManifestAgent(StubAgent):
        def manifest(self) -> AgentManifest:
            return AgentManifest(
                id="custom", name="Custom Agent", version="3.0.0",
                capabilities=["custom.cap"],
                priority=99,
            )

    agent = await _started(ManifestAgent("custom"))
    await reg.register_simple(agent, capabilities=["dummy"])
    manifests = reg.list_manifests()
    # Debe usar el manifest() del agente, no el sintetizado.
    m = manifests[0]
    assert m.id == "custom"
    assert m.name == "Custom Agent"
    assert m.version == "3.0.0"
    assert m.capabilities == ["custom.cap"]
    assert m.priority == 99


# ------------------------------------------------------------------
# find_by_capability_simple
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_by_capability_simple_returns_all_providers() -> None:
    reg = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    await reg.register_simple(a1, capabilities=["python"], priority=5)
    await reg.register_simple(a2, capabilities=["python"], priority=10)
    results = reg.find_by_capability_simple("python")
    assert len(results) == 2
    # Ordenado por prioridad desc: a2 (10) primero.
    assert results[0].name == "a2"
    assert results[1].name == "a1"


@pytest.mark.asyncio
async def test_find_by_capability_simple_no_filter_availability() -> None:
    """A diferencia de find_by_capability, no filtra por disponibilidad."""
    reg = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    await reg.register_simple(a1, capabilities=["python"])
    # Pausar al agente (no disponible).
    await a1.pause()
    reg.update_health()
    # find_by_capability_simple devuelve TODOS, incluso no disponibles.
    results = reg.find_by_capability_simple("python")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_find_by_capability_simple_orders_by_priority_then_usage() -> None:
    reg = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    r1 = await reg.register_simple(a1, capabilities=["python"], priority=10)
    await reg.register_simple(a2, capabilities=["python"], priority=10)
    # a1 tiene más usage.
    r1.record_usage(success=True, duration_ms=10.0)
    r1.record_usage(success=True, duration_ms=10.0)
    results = reg.find_by_capability_simple("python")
    # Misma prioridad → menor usage gana (a2).
    assert results[0].name == "a2"


@pytest.mark.asyncio
async def test_find_by_capability_simple_empty_when_no_match() -> None:
    reg = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    await reg.register_simple(a1, capabilities=["python"])
    assert reg.find_by_capability_simple("rust") == []


# ------------------------------------------------------------------
# find_manifests_by_capability
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_manifests_by_capability() -> None:
    reg = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    await reg.register_simple(a1, capabilities=["python", "typescript"])
    await reg.register_simple(a2, capabilities=["python", "rust"])
    manifests = reg.find_manifests_by_capability("python")
    assert len(manifests) == 2
    manifests = reg.find_manifests_by_capability("rust")
    assert len(manifests) == 1
    assert manifests[0].id == "a2"


# ------------------------------------------------------------------
# Integración con agentes concretos de Sprint 2.1
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_concrete_agents_with_manifests() -> None:
    reg = await _setup_registry()
    coder = CoderAgent()
    reviewer = ReviewerAgent()
    await coder.start()
    await reviewer.start()

    manifests = {m.id: m for m in standard_manifests()}
    await reg.register_with_manifest(coder, manifests["coder"])
    await reg.register_with_manifest(reviewer, manifests["reviewer"])

    # Verificar que find_by_capability_simple encuentra al coder.
    results = reg.find_by_capability_simple("python")
    assert len(results) == 1
    assert results[0].name == "coder"

    # Verificar que encuentra al reviewer.
    results = reg.find_by_capability_simple("code.review")
    assert len(results) == 1
    assert results[0].name == "reviewer"


# ------------------------------------------------------------------
# Validaciones
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_rejects_non_agent() -> None:
    reg = await _setup_registry()
    with pytest.raises(TypeError):
        await reg.register_simple("not an agent")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_register_rejects_duplicate_without_replace() -> None:
    reg = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a1"))  # mismo nombre
    await reg.register_simple(a1, capabilities=["python"])
    # register_simple usa replace_existing=True, así que no falla.
    record = await reg.register_simple(a2, capabilities=["typescript"])
    assert record.provided_capabilities == ["typescript"]
