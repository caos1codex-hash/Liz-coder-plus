"""Tests for the retry system in the TaskPipelineEngine."""

import asyncio

from packages.multiagent.src.multiagent.workflow.pipeline_models import (
    StepRetryPolicy,
    WorkflowStepDef,
)
from packages.multiagent.src.multiagent.workflow.task_pipeline_engine import TaskPipelineEngine
from packages.multiagent.src.multiagent.registry.agent_registry import AgentRegistry
from packages.multiagent.src.multiagent.registry.capability import (
    CapabilityRegistry,
    register_standard_capabilities,
)
from packages.multiagent.src.multiagent.registry.resolver import CapabilityResolver
from packages.multiagent.src.multiagent.base import BaseAgent
from packages.multiagent.src.multiagent.enums import AgentStatus


class RetryAgent(BaseAgent):
    """Agente que falla N veces y luego tiene éxito."""

    def __init__(self, name: str = "retry", fail_count: int = 2):
        super().__init__(name=name)
        self._fail_count = fail_count
        self._call_count = 0
        self._status = AgentStatus.IDLE

    async def _execute_impl(self, task: dict) -> dict:
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise RuntimeError("transient failure")
        return {"attempts": self._call_count}


class AlwaysFailAgent(BaseAgent):
    """Agente que siempre falla."""

    def __init__(self, name: str = "always_fail", error: str = "permanent error"):
        super().__init__(name=name)
        self._error = error
        self._call_count = 0
        self._status = AgentStatus.IDLE

    async def _execute_impl(self, task: dict) -> dict:
        self._call_count += 1
        raise RuntimeError(self._error)


def _make_retry_engine(agent: BaseAgent, caps: list[str]) -> TaskPipelineEngine:
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    registry = AgentRegistry(capability_registry=cap_reg)
    resolver = CapabilityResolver(registry=registry)
    agent._status = AgentStatus.IDLE
    asyncio.run(registry.register_simple(agent, capabilities=caps, priority=10))
    return TaskPipelineEngine(registry=registry, resolver=resolver)


# ======================================================================
# Retry tests
# ======================================================================


class TestRetrySystem:
    """Tests for the StepRetryPolicy and engine retry behavior."""

    def test_retry_then_success(self):
        """Agente falla 1 vez, reintenta y tiene éxito."""
        agent = RetryAgent("r", fail_count=1)
        engine = _make_retry_engine(agent, ["code.generate"])

        wf = engine.create_workflow("retry-wf", [
            WorkflowStepDef(
                name="s1",
                capability_required="code.generate",
                retry_policy=StepRetryPolicy(max_attempts=3, retry_delay=0.01),
            ),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        assert result.status.value == "completed"
        assert result.steps_completed == 1
        assert result.steps_failed == 0
        assert result.total_retries == 1
        assert agent._call_count == 2  # 1 fail + 1 success

    def test_retry_exhausted(self):
        """Agente siempre falla, se agotan reintentos."""
        agent = AlwaysFailAgent("fail")
        engine = _make_retry_engine(agent, ["code.generate"])

        wf = engine.create_workflow("exhaust", [
            WorkflowStepDef(
                name="s1",
                capability_required="code.generate",
                retry_policy=StepRetryPolicy(max_attempts=3, retry_delay=0.01),
            ),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        assert result.steps_failed == 1
        assert agent._call_count == 3

    def test_no_retry_by_default(self):
        """Sin retry_policy, el step se ejecuta una sola vez."""
        agent = AlwaysFailAgent("fail")
        engine = _make_retry_engine(agent, ["code.generate"])

        wf = engine.create_workflow("no-retry", [
            WorkflowStepDef(name="s1", capability_required="code.generate"),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        assert result.steps_failed == 1
        # Default StepRetryPolicy has max_attempts=3, so it will retry.
        assert agent._call_count == 3

    def test_single_attempt_no_retry(self):
        """max_attempts=1 means no retries."""
        agent = AlwaysFailAgent("fail")
        engine = _make_retry_engine(agent, ["code.generate"])

        wf = engine.create_workflow("single", [
            WorkflowStepDef(
                name="s1",
                capability_required="code.generate",
                retry_policy=StepRetryPolicy(max_attempts=1),
            ),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        assert result.steps_failed == 1
        assert agent._call_count == 1

    def test_retry_on_specific_errors(self):
        """Sólo reintentar en errores específicos."""
        agent = AlwaysFailAgent("fail", error="Connection timeout")
        engine = _make_retry_engine(agent, ["code.generate"])

        wf = engine.create_workflow("specific", [
            WorkflowStepDef(
                name="s1",
                capability_required="code.generate",
                retry_policy=StepRetryPolicy(
                    max_attempts=3,
                    retry_delay=0.01,
                    retry_on_errors=["timeout"],
                ),
            ),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        # Should retry because error contains "timeout".
        assert agent._call_count == 3

    def test_retry_not_on_non_matching_errors(self):
        """No reintentar si error no matchea la lista."""
        agent = AlwaysFailAgent("fail", error="syntax error")
        engine = _make_retry_engine(agent, ["code.generate"])

        wf = engine.create_workflow("no-match", [
            WorkflowStepDef(
                name="s1",
                capability_required="code.generate",
                retry_policy=StepRetryPolicy(
                    max_attempts=3,
                    retry_delay=0.01,
                    retry_on_errors=["timeout", "connection"],
                ),
            ),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        # Should NOT retry because "syntax error" doesn't match.
        assert agent._call_count == 1
        assert result.steps_failed == 1

    def test_retry_with_dependencies(self):
        """Retry en step intermedio de una cadena."""
        agent = RetryAgent("r", fail_count=1)
        engine = _make_retry_engine(agent, ["code.generate", "code.test"])

        wf = engine.create_workflow("chain-retry", [
            WorkflowStepDef(
                name="a",
                capability_required="code.generate",
                retry_policy=StepRetryPolicy(max_attempts=3, retry_delay=0.01),
            ),
            WorkflowStepDef(name="b", capability_required="code.test", dependencies=["a"]),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        assert result.status.value == "completed"
        assert result.steps_completed == 2
        assert result.total_retries == 1

    def test_retry_metrics_tracked(self):
        """Los reintentos se trackean en métricas del engine."""
        agent = RetryAgent("r", fail_count=1)
        engine = _make_retry_engine(agent, ["code.generate"])

        wf = engine.create_workflow("m", [
            WorkflowStepDef(
                name="s1",
                capability_required="code.generate",
                retry_policy=StepRetryPolicy(max_attempts=3, retry_delay=0.01),
            ),
        ])
        engine.register_workflow(wf)
        asyncio.run(engine.execute_workflow(wf.id))

        metrics = engine.metrics()
        assert metrics["steps_retried"] == 1
        assert metrics["steps_executed"] == 1