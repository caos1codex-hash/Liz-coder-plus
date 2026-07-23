"""Tests para Discovery y RegistryOrchestrator (Sprint 2.3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent import (  # noqa: E402
    PlannerAgent,
)
from multiagent.base import BaseAgent  # noqa: E402
from multiagent.enums import Permission  # noqa: E402
from multiagent.registry.agent_registry import AgentRegistry  # noqa: E402
from multiagent.registry.capability import (  # noqa: E402
    CapabilityRegistry,
    register_standard_capabilities,
)
from multiagent.registry.lifecycle import LifecycleState  # noqa: E402
from multiagent.registry.manifest import AgentManifest  # noqa: E402
from multiagent.registry.orchestrator import RegistryOrchestrator  # noqa: E402


# ------------------------------------------------------------------
# Stub agent
# ------------------------------------------------------------------


class StubAgent(BaseAgent):
    default_permissions = frozenset({Permission.MEMORY_WRITE})
    default_objectives = ["stub", "code"]
    default_tools = ["file_tool"]

    def __init__(self, name: str = "stub", **kwargs):
        super().__init__(name=name, description="stub agent", **kwargs)

    async def _execute_impl(self, task: dict) -> dict:
        return {"handled": True, "tools_used": []}


# ------------------------------------------------------------------
# Discovery básico
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_by_capability() -> None:
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    registry = AgentRegistry(capability_registry=cap_reg)
    a1 = StubAgent("a1")
    a2 = StubAgent("a2")
    await a1.start()
    await a2.start()
    await registry.register_simple(a1, capabilities=["python", "typescript"])
    await registry.register_simple(a2, capabilities=["python", "rust"])

    results = registry.find_by_capability_simple("python")
    assert len(results) == 2
    # discovery no filtra por disponibilidad.
    results = registry.find_by_capability_simple("rust")
    assert len(results) == 1
    assert results[0].name == "a2"


@pytest.mark.asyncio
async def test_discover_manifests_by_capability() -> None:
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    registry = AgentRegistry(capability_registry=cap_reg)
    a1 = StubAgent("a1")
    await a1.start()
    await registry.register_simple(a1, capabilities=["python", "code.generate"])

    manifests = registry.find_manifests_by_capability("python")
    assert len(manifests) == 1
    assert manifests[0].has_capability("python")


@pytest.mark.asyncio
async def test_discover_no_match() -> None:
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    registry = AgentRegistry(capability_registry=cap_reg)
    a1 = StubAgent("a1")
    await a1.start()
    await registry.register_simple(a1, capabilities=["python"])
    assert registry.find_by_capability_simple("rust") == []


# ------------------------------------------------------------------
# RegistryOrchestrator — bootstrap
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_bootstrap_registers_standard_agents() -> None:
    orch = RegistryOrchestrator()
    count = await orch.bootstrap()
    assert count == 7
    agents = orch.list_agents()
    assert len(agents) == 7
    names = {a.name for a in agents}
    expected = {"planner", "coder", "reviewer", "research", "terminal", "git", "memory"}
    assert names == expected


@pytest.mark.asyncio
async def test_orchestrator_bootstrap_with_custom_agents() -> None:
    orch = RegistryOrchestrator()
    a1 = StubAgent("custom1")
    a2 = StubAgent("custom2")
    count = await orch.bootstrap(agents=[a1, a2], use_standard_manifests=False)
    assert count == 2
    assert orch.registry.exists("custom1")
    assert orch.registry.exists("custom2")


@pytest.mark.asyncio
async def test_orchestrator_list_manifests() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    manifests = orch.list_manifests()
    assert len(manifests) == 7
    ids = {m.id for m in manifests}
    assert "coder" in ids
    assert "planner" in ids


# ------------------------------------------------------------------
# RegistryOrchestrator — request
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_resolves_and_executes() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    result = await orch.request(
        capability="code.refactor",
        task={
            "id": "t1",
            "name": "refactor",
            "payload": {
                "op": "refactor",
                "content": "def f():\r\n    return 1   \r\n",
                "language": "python",
            },
        },
    )
    assert result.success is True
    assert result.agent_name == "coder"
    assert result.capability == "code.refactor"
    assert result.duration_ms > 0


@pytest.mark.asyncio
async def test_request_records_lifecycle() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    result = await orch.request(
        capability="code.review",
        task={
            "id": "t2",
            "name": "review",
            "payload": {"content": "def f():\n    return 1\n", "path": "f.py"},
        },
    )
    assert result.success is True
    assert result.agent_name == "reviewer"
    assert result.lifecycle_before == "ready"
    assert result.lifecycle_after in ("ready", "running")


@pytest.mark.asyncio
async def test_request_miss_returns_error() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    result = await orch.request(
        capability="nonexistent.capability",
        task={"id": "t3", "name": "x"},
    )
    assert result.success is False
    assert result.agent_name is None
    assert "no available agent" in result.error


@pytest.mark.asyncio
async def test_request_excludes_agents() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    # Excluir al coder.
    result = await orch.request(
        capability="code.refactor",
        task={"id": "t4", "name": "refactor", "payload": {"op": "refactor", "content": "x", "language": "python"}},
        exclude={"coder"},
    )
    # Sin otros agentes que provean code.refactor, debe fallar.
    assert result.success is False


@pytest.mark.asyncio
async def test_request_batch() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    tasks = [
        {"id": "t1", "name": "refactor", "payload": {"op": "refactor", "content": "x", "language": "python"}},
        {"id": "t2", "name": "refactor", "payload": {"op": "refactor", "content": "y", "language": "python"}},
    ]
    results = await orch.request_batch(capability="code.refactor", tasks=tasks)
    assert len(results) == 2
    assert all(r.success for r in results)
    assert all(r.agent_name == "coder" for r in results)


# ------------------------------------------------------------------
# RegistryOrchestrator — discovery
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_python() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    records = orch.discover("python")
    assert len(records) == 1
    assert records[0].name == "coder"


@pytest.mark.asyncio
async def test_discover_code_review() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    records = orch.discover("code.review")
    assert len(records) == 1
    assert records[0].name == "reviewer"


@pytest.mark.asyncio
async def test_discover_manifests() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    manifests = orch.discover_manifests("python")
    assert len(manifests) == 1
    assert manifests[0].id == "coder"


# ------------------------------------------------------------------
# RegistryOrchestrator — lifecycle integration
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_initialized_after_bootstrap() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    for name in ["planner", "coder", "reviewer"]:
        lm = orch.get_lifecycle(name)
        assert lm is not None
        assert lm.initialized is True
        assert lm.state == LifecycleState.READY


@pytest.mark.asyncio
async def test_lifecycle_started_count_increments() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    # Ejecutar 3 tasks con code.refactor.
    for i in range(3):
        await orch.request(
            capability="code.refactor",
            task={"id": f"t{i}", "name": "refactor",
                  "payload": {"op": "refactor", "content": "x", "language": "python"}},
        )
    lm = orch.get_lifecycle("coder")
    assert lm is not None
    assert lm.started_count == 3


@pytest.mark.asyncio
async def test_lifecycle_stop_all() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    stopped = await orch.stop_all()
    assert stopped == 7
    for name in ["planner", "coder", "reviewer"]:
        lm = orch.get_lifecycle(name)
        assert lm is not None
        assert lm.is_terminal is True


@pytest.mark.asyncio
async def test_shutdown_clears_lifecycles() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    await orch.shutdown()
    assert orch._lifecycles == {}  # noqa: SLF001


# ------------------------------------------------------------------
# RegistryOrchestrator — health & metrics
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_all_agents() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    h = orch.health()
    assert h["total_agents"] == 7
    assert "coder" in h["agents"]
    assert h["agents"]["coder"]["state"] == "ready"


@pytest.mark.asyncio
async def test_metrics_include_registry_and_resolver() -> None:
    orch = RegistryOrchestrator()
    await orch.bootstrap()
    await orch.request(
        capability="code.refactor",
        task={"id": "t1", "name": "refactor",
              "payload": {"op": "refactor", "content": "x", "language": "python"}},
    )
    m = orch.metrics()
    assert "registry" in m
    assert "resolver" in m
    assert "lifecycles" in m
    assert m["registry"]["registry_size"] == 7
    assert m["resolver"]["resolved_count"] >= 1


# ------------------------------------------------------------------
# Compatibilidad: AgentOrchestrator de Sprint 2.1 sigue funcionando
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_orchestrator_still_works() -> None:
    """El AgentOrchestrator de Sprint 2.1 no se rompe."""
    from multiagent import AgentOrchestrator

    orch = AgentOrchestrator()
    agent = PlannerAgent()
    await orch.register_agent(agent)
    assert "planner" in orch.list_agents()
    assert orch.agent_count == 1
    await orch.shutdown()


# ------------------------------------------------------------------
# Registro manual con manifest
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_with_manifest_via_orchestrator() -> None:
    orch = RegistryOrchestrator()
    agent = StubAgent("custom")
    manifest = AgentManifest(
        id="custom", name="Custom Agent", version="1.0.0",
        capabilities=["custom.cap"],
        priority=15,
    )
    await orch.register(agent, manifest=manifest)
    # El lifecycle debe estar inicializado.
    lm = orch.get_lifecycle("custom")
    assert lm is not None
    assert lm.initialized is True
    # El registry debe tener el manifest.
    manifests = orch.list_manifests()
    custom = next((m for m in manifests if m.id == "custom"), None)
    assert custom is not None
    assert custom.capabilities == ["custom.cap"]
    assert custom.priority == 15


@pytest.mark.asyncio
async def test_unregister_via_orchestrator() -> None:
    orch = RegistryOrchestrator()
    agent = StubAgent("temp")
    await orch.register(agent, capabilities=["temp.cap"])
    assert orch.registry.exists("temp")
    # Unregister detiene el lifecycle y elimina del registry.
    ok = await orch.unregister("temp")
    assert ok is True
    assert not orch.registry.exists("temp")
    assert orch.get_lifecycle("temp") is None
