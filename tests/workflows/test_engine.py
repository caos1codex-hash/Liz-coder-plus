"""Tests for the TaskPipelineEngine — creation, registry, status."""

import asyncio
import pytest

from packages.multiagent.src.multiagent.workflow.pipeline_models import (
    PipelineWorkflow,
    StepRetryPolicy,
    TaskRequest,
    TaskResult,
    WorkflowStepDef,
)
from packages.multiagent.src.multiagent.workflow.task_pipeline_engine import (
    PipelineExecutionResult,
    TaskPipelineEngine,
)
from packages.multiagent.src.multiagent.registry.agent_registry import AgentRegistry
from packages.multiagent.src.multiagent.registry.capability import (
    CapabilityRegistry,
    register_standard_capabilities,
)
from packages.multiagent.src.multiagent.registry.resolver import CapabilityResolver
from packages.multiagent.src.multiagent.base import BaseAgent
from packages.multiagent.src.multiagent.enums import AgentStatus


# ======================================================================
# Fixtures
# ======================================================================


class MockAgent(BaseAgent):
    """Agente mock para testing."""

    def __init__(self, name: str = "mock", result: dict | None = None,
                 fail_count: int = 0):
        super().__init__(name=name)
        self._result = result or {"output": f"{name}-done"}
        self._fail_count = fail_count
        self._call_count = 0
        # Force to IDLE so it can execute.
        self._status = AgentStatus.IDLE

    async def _execute_impl(self, task: dict) -> dict:
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise RuntimeError("mock failure")
        return self._result


def _make_registry_with_agent(
    agent: BaseAgent, capabilities: list[str]
) -> AgentRegistry:
    """Crea un AgentRegistry con un agente registrado."""
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    registry = AgentRegistry(capability_registry=cap_reg)

    # Start the agent so it's IDLE.
    agent._status = AgentStatus.IDLE

    import asyncio
    loop = asyncio.get_event_loop()
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, registry.register_simple(
                agent, capabilities=capabilities, priority=10
            ))
            future.result()
    else:
        asyncio.run(registry.register_simple(
            agent, capabilities=capabilities, priority=10
        ))
    return registry


def _make_engine(agent: BaseAgent, capabilities: list[str]) -> TaskPipelineEngine:
    """Crea un TaskPipelineEngine con un agente mock."""
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    registry = AgentRegistry(capability_registry=cap_reg)
    resolver = CapabilityResolver(registry=registry)

    agent._status = AgentStatus.IDLE
    asyncio.run(registry.register_simple(
        agent, capabilities=capabilities, priority=10
    ))

    return TaskPipelineEngine(registry=registry, resolver=resolver)


# ======================================================================
# Engine creation tests
# ======================================================================


class TestTaskPipelineEngineCreation:
    """Tests for engine creation and workflow management."""

    def test_create_engine(self):
        cap_reg = CapabilityRegistry()
        register_standard_capabilities(cap_reg)
        registry = AgentRegistry(capability_registry=cap_reg)
        engine = TaskPipelineEngine(registry=registry)
        assert engine.registry is registry
        assert engine.resolver is not None
        assert engine.list_workflows() == []

    def test_create_workflow(self):
        engine = _make_engine(MockAgent("coder"), ["code.generate"])
        wf = engine.create_workflow(
            "test-wf",
            [WorkflowStepDef(name="s1", capability_required="code.generate")],
        )
        assert wf.name == "test-wf"
        assert wf.status == "created"
        assert len(wf.steps) == 1
        assert wf.id

    def test_register_workflow(self):
        engine = _make_engine(MockAgent("coder"), ["code.generate"])
        wf = engine.create_workflow(
            "test-wf",
            [WorkflowStepDef(name="s1", capability_required="code.generate")],
        )
        registered = engine.register_workflow(wf)
        assert registered.id == wf.id
        assert engine.get_workflow(wf.id) is not None
        assert len(engine.list_workflows()) == 1

    def test_register_workflow_with_dependencies(self):
        engine = _make_engine(MockAgent("multi"), ["code.generate", "code.test"])
        wf = engine.create_workflow("dep-wf", [
            WorkflowStepDef(name="code", capability_required="code.generate"),
            WorkflowStepDef(
                name="test", capability_required="code.test", dependencies=["code"]
            ),
        ])
        registered = engine.register_workflow(wf)
        assert registered.id == wf.id
        assert engine.get_workflow(wf.id) is not None

    def test_register_workflow_cycle_raises(self):
        engine = _make_engine(MockAgent("a"), ["code.generate"])
        wf = engine.create_workflow("cycle-wf", [
            WorkflowStepDef(name="a", capability_required="code.generate", dependencies=["b"]),
            WorkflowStepDef(name="b", capability_required="code.generate", dependencies=["a"]),
        ])
        with pytest.raises(ValueError, match="invalid DAG"):
            engine.register_workflow(wf)

    def test_get_workflow_not_found(self):
        engine = _make_engine(MockAgent("a"), ["code.generate"])
        assert engine.get_workflow("nonexistent") is None

    def test_list_workflows_empty(self):
        engine = _make_engine(MockAgent("a"), ["code.generate"])
        assert engine.list_workflows() == []

    def test_list_workflows_multiple(self):
        engine = _make_engine(MockAgent("a"), ["code.generate"])
        wf1 = engine.create_workflow("w1", [
            WorkflowStepDef(name="s1", capability_required="code.generate")
        ])
        wf2 = engine.create_workflow("w2", [
            WorkflowStepDef(name="s1", capability_required="code.generate")
        ])
        engine.register_workflow(wf1)
        engine.register_workflow(wf2)
        assert len(engine.list_workflows()) == 2

    def test_get_status_not_found(self):
        engine = _make_engine(MockAgent("a"), ["code.generate"])
        status = engine.get_status("nonexistent")
        assert "error" in status

    def test_get_status_registered(self):
        engine = _make_engine(MockAgent("a"), ["code.generate"])
        wf = engine.create_workflow("w", [
            WorkflowStepDef(name="s1", capability_required="code.generate")
        ])
        engine.register_workflow(wf)
        status = engine.get_status(wf.id)
        assert status["workflow_id"] == wf.id
        assert status["name"] == "w"
        assert status["status"] == "created"
        assert "step_statuses" in status


# ======================================================================
# Engine execution tests
# ======================================================================


class TestTaskPipelineEngineExecution:
    """Tests for engine execution."""

    def test_execute_simple_workflow(self):
        agent = MockAgent("coder", result={"files": ["a.py"]})
        engine = _make_engine(agent, ["code.generate"])
        wf = engine.create_workflow("simple", [
            WorkflowStepDef(name="gen", capability_required="code.generate"),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))
        assert result.status.value == "completed"
        assert result.steps_completed == 1
        assert result.steps_failed == 0
        assert result.duration_ms > 0

    def test_execute_sequential_dependencies(self):
        agent = MockAgent("multi", result={"done": True})
        engine = _make_engine(agent, ["code.generate", "code.test"])
        wf = engine.create_workflow("sequential", [
            WorkflowStepDef(name="code", capability_required="code.generate"),
            WorkflowStepDef(
                name="test", capability_required="code.test", dependencies=["code"]
            ),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))
        assert result.status.value == "completed"
        assert result.steps_completed == 2

    def test_execute_result_has_agent_info(self):
        agent = MockAgent("coder", result={"status": "ok", "result": {}})
        engine = _make_engine(agent, ["code.generate"])
        wf = engine.create_workflow("w", [
            WorkflowStepDef(name="s1", capability_required="code.generate"),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))
        assert result.steps_completed == 1
        assert "coder" in result.agents_used

    def test_execute_missing_capability_fails(self):
        agent = MockAgent("coder", result={"status": "ok", "result": {}})
        engine = _make_engine(agent, ["code.generate"])
        wf = engine.create_workflow("w", [
            WorkflowStepDef(name="s1", capability_required="nonexistent.cap"),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))
        assert result.steps_failed == 1

    def test_execute_context_propagation(self):
        agent = MockAgent("coder", result={"output": "hello"})
        engine = _make_engine(agent, ["code.generate", "code.test"])
        wf = engine.create_workflow("ctx", [
            WorkflowStepDef(name="gen", capability_required="code.generate"),
            WorkflowStepDef(
                name="test", capability_required="code.test", dependencies=["gen"]
            ),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))
        assert result.steps_completed == 2

    def test_execute_empty_workflow(self):
        engine = _make_engine(MockAgent("a"), ["code.generate"])
        wf = engine.create_workflow("empty", [])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))
        assert result.status.value == "completed"
        assert result.steps_total == 0