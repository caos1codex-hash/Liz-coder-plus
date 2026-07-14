"""Tests de compatibilidad de API pública (Sprint 2.3.1).

Verifican que las APIs clave del sistema multiagente siguen funcionando
después de cada sprint. Son tests de **contrato**: si alguno falla, la
API pública ha cambiado de forma incompatible.

APIs verificadas:

- AgentRegistry: register(), get(), list_agents(), find_by_capability()
- Lifecycle: initialize(), start(), stop(), health_check()
- RegistryOrchestrator: discover(), request(), metrics()
- AgentManifest: creación, validación, has_capability()
- BaseAgent: execute(), health(), serialize()
- WorkflowEngine: run() básico
"""

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
    AgentRegistry,
    CapabilityRegistry,
    CoderAgent,
    RegistryOrchestrator,
    register_standard_capabilities,
)
from multiagent.base import BaseAgent  # noqa: E402
from multiagent.enums import Permission  # noqa: E402
from multiagent.registry import (  # noqa: E402
    AgentManifest,
    LifecycleManager,
    LifecycleState,
)


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


async def _started(agent: BaseAgent) -> BaseAgent:
    await agent.start()
    return agent


# ==================================================================
# AgentRegistry API
# ==================================================================


class TestAgentRegistryAPI:
    """Contrato de la API pública de AgentRegistry."""

    @pytest.mark.asyncio
    async def test_register_returns_agent_record(self) -> None:
        """register() devuelve un AgentRecord con name y capabilities."""
        cap_reg = CapabilityRegistry()
        register_standard_capabilities(cap_reg)
        registry = AgentRegistry(capability_registry=cap_reg)
        agent = await _started(StubAgent("a1"))
        record = await registry.register(
            agent, provided_capabilities=["python"],
        )
        assert record.name == "a1"
        assert "python" in record.provided_capabilities

    @pytest.mark.asyncio
    async def test_get_by_name_and_id(self) -> None:
        """get() funciona por id y por nombre."""
        cap_reg = CapabilityRegistry()
        register_standard_capabilities(cap_reg)
        registry = AgentRegistry(capability_registry=cap_reg)
        agent = await _started(StubAgent("a1"))
        record = await registry.register(agent, provided_capabilities=["python"])
        assert registry.get("a1") is not None
        assert registry.get(record.id) is not None
        assert registry.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_agents_returns_all(self) -> None:
        """list_agents() devuelve todos los agentes registrados."""
        cap_reg = CapabilityRegistry()
        register_standard_capabilities(cap_reg)
        registry = AgentRegistry(capability_registry=cap_reg)
        a1 = await _started(StubAgent("a1"))
        a2 = await _started(StubAgent("a2"))
        await registry.register(a1, provided_capabilities=["python"])
        await registry.register(a2, provided_capabilities=["python"])
        agents = registry.list_agents()
        assert len(agents) == 2

    @pytest.mark.asyncio
    async def test_find_by_capability(self) -> None:
        """find_by_capability() encuentra agentes por capability."""
        cap_reg = CapabilityRegistry()
        register_standard_capabilities(cap_reg)
        registry = AgentRegistry(capability_registry=cap_reg)
        a1 = await _started(StubAgent("a1"))
        a2 = await _started(StubAgent("a2"))
        await registry.register(a1, provided_capabilities=["python"])
        await registry.register(a2, provided_capabilities=["typescript"])
        results = registry.find_by_capability("python")
        assert len(results) == 1
        assert results[0].name == "a1"

    @pytest.mark.asyncio
    async def test_unregister(self) -> None:
        """unregister() elimina un agente."""
        cap_reg = CapabilityRegistry()
        register_standard_capabilities(cap_reg)
        registry = AgentRegistry(capability_registry=cap_reg)
        agent = await _started(StubAgent("a1"))
        await registry.register(agent, provided_capabilities=["python"])
        assert await registry.unregister("a1") is True
        assert registry.get("a1") is None

    @pytest.mark.asyncio
    async def test_exists(self) -> None:
        """exists() verifica si un agente está registrado."""
        cap_reg = CapabilityRegistry()
        register_standard_capabilities(cap_reg)
        registry = AgentRegistry(capability_registry=cap_reg)
        agent = await _started(StubAgent("a1"))
        await registry.register(agent, provided_capabilities=["python"])
        assert registry.exists("a1") is True
        assert registry.exists("nonexistent") is False


# ==================================================================
# Lifecycle API
# ==================================================================


class TestLifecycleAPI:
    """Contrato de la API pública de LifecycleManager."""

    @pytest.mark.asyncio
    async def test_initialize(self) -> None:
        """initialize() transiciona CREATED → READY."""
        agent = StubAgent("a1")
        lm = LifecycleManager(agent)
        assert lm.state == LifecycleState.CREATED
        await lm.initialize()
        assert lm.state == LifecycleState.READY

    @pytest.mark.asyncio
    async def test_start(self) -> None:
        """start() transiciona READY → RUNNING."""
        agent = StubAgent("a1")
        lm = LifecycleManager(agent)
        await lm.initialize()
        await lm.start()
        assert lm.state == LifecycleState.RUNNING

    @pytest.mark.asyncio
    async def test_stop(self) -> None:
        """stop() transiciona a STOPPED."""
        agent = StubAgent("a1")
        lm = LifecycleManager(agent)
        await lm.initialize()
        await lm.start()
        await lm.stop()
        assert lm.state == LifecycleState.STOPPED
        assert lm.is_terminal is True

    @pytest.mark.asyncio
    async def test_health_check_returns_lifecycle_health(self) -> None:
        """health_check() devuelve un LifecycleHealth."""
        agent = StubAgent("a1")
        lm = LifecycleManager(agent)
        await lm.initialize()
        health = lm.health_check()
        assert health is not None
        assert health.agent_name == "a1"
        assert health.state == LifecycleState.READY
        assert health.healthy is True
        assert health.initialized is True


# ==================================================================
# RegistryOrchestrator API
# ==================================================================


class TestRegistryOrchestratorAPI:
    """Contrato de la API pública de RegistryOrchestrator."""

    @pytest.mark.asyncio
    async def test_discover(self) -> None:
        """discover() encuentra agentes por capability."""
        orch = RegistryOrchestrator()
        await orch.bootstrap()
        results = orch.discover("python")
        assert len(results) >= 1
        assert results[0].name == "coder"

    @pytest.mark.asyncio
    async def test_request(self) -> None:
        """request() ejecuta una task con la capability dada."""
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

    @pytest.mark.asyncio
    async def test_metrics(self) -> None:
        """metrics() devuelve un dict con registry y resolver."""
        orch = RegistryOrchestrator()
        await orch.bootstrap()
        m = orch.metrics()
        assert "registry" in m
        assert "resolver" in m
        assert "lifecycles" in m
        assert m["registry"]["registry_size"] == 7

    @pytest.mark.asyncio
    async def test_request_miss_returns_error(self) -> None:
        """request() con capability inexistente devuelve error."""
        orch = RegistryOrchestrator()
        await orch.bootstrap()
        result = await orch.request(
            capability="nonexistent.capability",
            task={"id": "t1", "name": "x"},
        )
        assert result.success is False
        assert result.agent_name is None


# ==================================================================
# AgentManifest API
# ==================================================================


class TestAgentManifestAPI:
    """Contrato de la API pública de AgentManifest."""

    def test_creation(self) -> None:
        """AgentManifest se crea con los campos requeridos."""
        m = AgentManifest(
            id="coder",
            name="Coder Agent",
            version="1.0.0",
            capabilities=["python", "code.generate"],
            priority=10,
        )
        assert m.id == "coder"
        assert m.name == "Coder Agent"
        assert m.version == "1.0.0"
        assert m.capabilities == ["python", "code.generate"]
        assert m.priority == 10

    def test_has_capability(self) -> None:
        """has_capability() verifica capabilities con wildcard y jerarquía."""
        m = AgentManifest(
            id="x", name="X", version="1.0.0",
            capabilities=["code.generate", "python"],
        )
        assert m.has_capability("code.generate") is True
        assert m.has_capability("python") is True
        assert m.has_capability("rust") is False

    def test_validation_rejects_invalid_id(self) -> None:
        """IDs inválidos son rechazados."""
        with pytest.raises(ValueError):
            AgentManifest(id="Invalid", name="X", version="1.0.0")

    def test_validation_rejects_invalid_version(self) -> None:
        """Versiones inválidas son rechazadas."""
        with pytest.raises(ValueError):
            AgentManifest(id="x", name="X", version="1.0")  # falta patch

    def test_serialization_roundtrip(self) -> None:
        """to_dict/from_dict roundtrip preserva los datos."""
        m1 = AgentManifest(
            id="x", name="X", version="1.0.0",
            capabilities=["python"], priority=5,
        )
        m2 = AgentManifest.from_dict(m1.to_dict())
        assert m2.id == m1.id
        assert m2.capabilities == m1.capabilities
        assert m2.priority == m1.priority


# ==================================================================
# BaseAgent API (Sprint 2.1 — no debe romper)
# ==================================================================


class TestBaseAgentAPI:
    """Contrato de la API pública de BaseAgent (Sprint 2.1)."""

    @pytest.mark.asyncio
    async def test_execute(self) -> None:
        """execute() ejecuta una task y devuelve un dict con status."""
        agent = CoderAgent()
        await agent.start()
        result = await agent.execute({
            "id": "t1",
            "name": "refactor",
            "payload": {
                "op": "refactor",
                "content": "def f():\r\n    return 1   \r\n",
                "language": "python",
            },
        })
        assert result["status"] in ("ok", "error")
        assert "agent" in result
        assert "task_id" in result

    @pytest.mark.asyncio
    async def test_health(self) -> None:
        """health() devuelve un HealthReport."""
        agent = CoderAgent()
        await agent.start()
        h = agent.health()
        assert h is not None
        assert h.agent == "coder"
        assert h.state is not None

    @pytest.mark.asyncio
    async def test_serialize(self) -> None:
        """serialize() devuelve un dict con los campos clave."""
        agent = CoderAgent()
        await agent.start()
        d = agent.serialize()
        assert "id" in d
        assert "name" in d
        assert "status" in d
        assert "permissions" in d


# ==================================================================
# WorkflowEngine API (Sprint 2.2 — no debe romper)
# ==================================================================


class TestWorkflowEngineAPI:
    """Contrato de la API pública de WorkflowEngine (Sprint 2.2)."""

    @pytest.mark.asyncio
    async def test_run_basic_workflow(self) -> None:
        """run() ejecuta un workflow simple hasta completar."""
        from multiagent import Step, Workflow, WorkflowEngine, WorkflowStatus

        coder = CoderAgent()
        await coder.start()
        engine = WorkflowEngine(agents=[coder])
        wf = Workflow(name="test")
        s1 = Step(name="s1", agent="coder", action="refactor",
                  input={"op": "refactor", "content": "x", "language": "python"})
        wf.add_step(s1)
        result = await engine.run(wf)
        assert result.status == WorkflowStatus.COMPLETED
        assert result.steps_completed == 1
