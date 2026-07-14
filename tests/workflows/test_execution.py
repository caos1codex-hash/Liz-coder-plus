"""Tests for workflow execution with capabilities, context, and agent resolution."""

import asyncio
import pytest

from packages.multiagent.src.multiagent.workflow.pipeline_models import (
    PipelineWorkflow,
    StepRetryPolicy,
    TaskResult,
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


class TrackingAgent(BaseAgent):
    """Agente que trackea sus ejecuciones."""

    def __init__(self, name: str, capabilities: list[str] | None = None,
                 result: dict | None = None):
        super().__init__(name=name)
        self._capabilities = capabilities or []
        self._custom_result = result
        self.executed_tasks: list[dict] = []
        self._status = AgentStatus.IDLE

    async def _execute_impl(self, task: dict) -> dict:
        self.executed_tasks.append(task)
        if self._custom_result:
            return self._custom_result
        return {
            "status": "ok",
            "result": {
                "agent": self.name,
                "capability": task.get("payload", {}).get("capability", ""),
                "output": f"{self.name}-done",
            },
        }


def _make_multi_engine(agents: list[tuple[BaseAgent, list[str]]]) -> TaskPipelineEngine:
    """Crea engine con múltiples agentes."""
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    registry = AgentRegistry(capability_registry=cap_reg)
    resolver = CapabilityResolver(registry=registry)

    for agent, caps in agents:
        agent._status = AgentStatus.IDLE
        asyncio.run(registry.register_simple(
            agent, capabilities=caps, priority=10
        ))

    return TaskPipelineEngine(registry=registry, resolver=resolver)


# ======================================================================
# Capability resolution tests
# ======================================================================


class TestCapabilityResolution:
    """Tests that the engine resolves agents by capability correctly."""

    def test_resolves_correct_agent_for_capability(self):
        coder = TrackingAgent("coder")
        tester = TrackingAgent("tester")
        engine = _make_multi_engine([
            (coder, ["code.generate"]),
            (tester, ["code.test"]),
        ])

        wf = engine.create_workflow("w", [
            WorkflowStepDef(name="gen", capability_required="code.generate"),
            WorkflowStepDef(name="test", capability_required="code.test", dependencies=["gen"]),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        assert result.status.value == "completed"
        assert result.steps_completed == 2
        assert "coder" in result.agents_used
        assert "tester" in result.agents_used
        assert len(coder.executed_tasks) == 1
        assert len(tester.executed_tasks) == 1

    def test_capability_context_includes_workflow_id(self):
        agent = TrackingAgent("a")
        engine = _make_multi_engine([(agent, ["code.generate"])])
        wf = engine.create_workflow("ctx-wf", [
            WorkflowStepDef(name="s1", capability_required="code.generate"),
        ])
        engine.register_workflow(wf)
        asyncio.run(engine.execute_workflow(wf.id))

        assert len(agent.executed_tasks) == 1
        task = agent.executed_tasks[0]
        assert task["workflow_id"] == wf.id
        assert "step_id" in task

    def test_agent_receives_capability_in_payload(self):
        agent = TrackingAgent("a")
        engine = _make_multi_engine([(agent, ["code.generate"])])
        wf = engine.create_workflow("w", [
            WorkflowStepDef(name="s1", capability_required="code.generate"),
        ])
        engine.register_workflow(wf)
        asyncio.run(engine.execute_workflow(wf.id))

        task = agent.executed_tasks[0]
        assert task["payload"]["capability"] == "code.generate"

    def test_no_matching_capability_fails_gracefully(self):
        agent = TrackingAgent("a")
        engine = _make_multi_engine([(agent, ["code.generate"])])
        wf = engine.create_workflow("w", [
            WorkflowStepDef(name="s1", capability_required="git.commit"),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))
        assert result.steps_failed == 1

    def test_execute_nonexistent_workflow_raises(self):
        engine = _make_multi_engine([(TrackingAgent("a"), ["code.generate"])])
        with pytest.raises(KeyError):
            asyncio.run(engine.execute_workflow("nonexistent"))


# ======================================================================
# Context propagation tests
# ======================================================================


class TestContextPropagation:
    """Tests that step outputs propagate as context to dependent steps."""

    def test_step_output_in_next_step_context(self):
        agent = TrackingAgent("a", result={
            "files": ["generated.py"], "tokens_prompt": 10, "tokens_completion": 20,
        })
        engine = _make_multi_engine([(agent, ["code.generate", "code.test"])])
        wf = engine.create_workflow("ctx-wf", [
            WorkflowStepDef(name="gen", capability_required="code.generate"),
            WorkflowStepDef(name="test", capability_required="code.test", dependencies=["gen"]),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        assert result.status.value == "completed"
        # Second task should have context from first.
        second_task = agent.executed_tasks[1]
        assert "gen" in second_task["context"]
        assert second_task["context"]["gen"]["files"] == ["generated.py"]


# ======================================================================
# TaskRequest → agent task format tests
# ======================================================================


class TestTaskRequestFormat:
    """Tests that TaskRequest.to_agent_task produces correct format."""

    def test_task_format_has_required_fields(self):
        agent = TrackingAgent("a")
        engine = _make_multi_engine([(agent, ["planning"])])
        wf = engine.create_workflow("w", [
            WorkflowStepDef(
                name="plan", capability_required="planning",
                description="Create plan",
                metadata={"priority": 5},
            ),
        ])
        engine.register_workflow(wf)
        asyncio.run(engine.execute_workflow(wf.id))

        task = agent.executed_tasks[0]
        assert "id" in task
        assert "name" in task
        assert "description" in task
        assert "payload" in task
        assert "priority" in task
        assert "workflow_id" in task
        assert "step_id" in task
        assert "context" in task

    def test_task_preserves_step_description(self):
        agent = TrackingAgent("a")
        engine = _make_multi_engine([(agent, ["code.generate"])])
        wf = engine.create_workflow("w", [
            WorkflowStepDef(
                name="gen",
                capability_required="code.generate",
                description="Generate API endpoints",
            ),
        ])
        engine.register_workflow(wf)
        asyncio.run(engine.execute_workflow(wf.id))

        task = agent.executed_tasks[0]
        assert task["name"] == "Generate API endpoints"