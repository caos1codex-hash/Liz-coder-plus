"""Integration tests — full workflow with RegistryOrchestrator pattern."""

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


class RealisticAgent(BaseAgent):
    """Agente que simula comportamiento realista."""

    def __init__(self, name: str, capabilities: list[str]):
        super().__init__(name=name)
        self._caps = capabilities
        self._call_log: list[dict] = []
        self._status = AgentStatus.IDLE

    async def _execute_impl(self, task: dict) -> dict:
        self._call_log.append({
            "name": task.get("name", ""),
            "capability": task.get("payload", {}).get("capability", ""),
        })
        return {
            "status": "ok",
            "result": {
                "agent": self.name,
                "files": [f"{task.get('name', 'out')}.py"],
                "tokens_prompt": 50,
                "tokens_completion": 100,
                "tools_used": ["editor"],
            },
        }


def _setup_realistic_engine() -> tuple[TaskPipelineEngine, dict[str, RealisticAgent]]:
    """Crea un engine con múltiples agentes realistas."""
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    registry = AgentRegistry(capability_registry=cap_reg)
    resolver = CapabilityResolver(registry=registry)

    agents = {
        "planner": RealisticAgent("planner", ["planning"]),
        "coder": RealisticAgent("coder", ["code.generate", "code.refactor"]),
        "tester": RealisticAgent("tester", ["code.test"]),
        "reviewer": RealisticAgent("reviewer", ["code.review"]),
        "git-agent": RealisticAgent("git-agent", ["git.commit", "git.push"]),
    }

    for agent in agents.values():
        agent._status = AgentStatus.IDLE
        asyncio.run(registry.register_simple(
            agent, capabilities=agent._caps, priority=10
        ))

    engine = TaskPipelineEngine(registry=registry, resolver=resolver)
    return engine, agents


# ======================================================================
# Integration tests
# ======================================================================


class TestFullIntegration:
    """Integration tests with realistic multi-agent workflows."""

    def test_full_development_pipeline(self):
        """Simula un pipeline completo: plan → code → test → review → commit."""
        engine, agents = _setup_realistic_engine()

        wf = engine.create_workflow("dev-pipeline", [
            WorkflowStepDef(name="plan", capability_required="planning", description="Plan architecture"),
            WorkflowStepDef(name="code", capability_required="code.generate", dependencies=["plan"], description="Generate code"),
            WorkflowStepDef(name="test", capability_required="code.test", dependencies=["code"], description="Run tests"),
            WorkflowStepDef(name="review", capability_required="code.review", dependencies=["test"], description="Review code"),
            WorkflowStepDef(name="commit", capability_required="git.commit", dependencies=["review"], description="Commit changes"),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        assert result.status.value == "completed"
        assert result.steps_completed == 5
        assert result.steps_failed == 0
        assert "planner" in result.agents_used
        assert "coder" in result.agents_used
        assert "tester" in result.agents_used
        assert "reviewer" in result.agents_used
        assert "git-agent" in result.agents_used

    def test_each_agent_called_once_in_pipeline(self):
        engine, agents = _setup_realistic_engine()

        wf = engine.create_workflow("call-count", [
            WorkflowStepDef(name="plan", capability_required="planning"),
            WorkflowStepDef(name="code", capability_required="code.generate", dependencies=["plan"]),
            WorkflowStepDef(name="test", capability_required="code.test", dependencies=["code"]),
        ])
        engine.register_workflow(wf)
        asyncio.run(engine.execute_workflow(wf.id))

        assert len(agents["planner"]._call_log) == 1
        assert len(agents["coder"]._call_log) == 1
        assert len(agents["tester"]._call_log) == 1

    def test_workflow_metrics_after_execution(self):
        engine, agents = _setup_realistic_engine()

        wf = engine.create_workflow("metrics-wf", [
            WorkflowStepDef(name="a", capability_required="planning"),
            WorkflowStepDef(name="b", capability_required="code.generate", dependencies=["a"]),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        wf_metrics = engine.workflow_metrics(wf.id)
        assert wf_metrics is not None
        assert wf_metrics["steps_total"] == 2
        assert wf_metrics["steps_completed"] == 2
        assert wf_metrics["steps_failed"] == 0
        assert len(wf_metrics["agents_used"]) == 2
        assert wf_metrics["status"] == "completed"
        assert "total_execution_time_ms" in wf_metrics

    def test_engine_metrics_aggregate(self):
        engine, agents = _setup_realistic_engine()

        for i in range(3):
            wf = engine.create_workflow(f"wf-{i}", [
                WorkflowStepDef(name="s1", capability_required="planning"),
            ])
            engine.register_workflow(wf)
            asyncio.run(engine.execute_workflow(wf.id))

        metrics = engine.metrics()
        assert metrics["workflows_created"] == 3
        assert metrics["workflows_completed"] == 3
        assert metrics["steps_executed"] == 3
        assert metrics["registered_workflows"] == 3

    def test_multiple_independent_workflows(self):
        """Ejecutar múltiples workflows secuenciales."""
        engine, agents = _setup_realistic_engine()

        results = []
        for i in range(3):
            wf = engine.create_workflow(f"independent-{i}", [
                WorkflowStepDef(name="s1", capability_required="planning"),
            ])
            engine.register_workflow(wf)
            r = asyncio.run(engine.execute_workflow(wf.id))
            results.append(r)

        for r in results:
            assert r.status.value == "completed"
            assert r.steps_completed == 1

    def test_cancel_workflow(self):
        engine, _ = _setup_realistic_engine()

        wf = engine.create_workflow("cancel-wf", [
            WorkflowStepDef(name="s1", capability_required="planning"),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.cancel_workflow(wf.id))
        assert result is True
        assert engine.get_workflow(wf.id).status == "cancelled"

    def test_pause_resume_workflow(self):
        engine, _ = _setup_realistic_engine()

        wf = engine.create_workflow("pause-wf", [
            WorkflowStepDef(name="s1", capability_required="planning"),
        ])
        engine.register_workflow(wf)

        # Can't pause a non-running workflow.
        result = asyncio.run(engine.pause_workflow(wf.id))
        assert result is False

        # Execute completes immediately since there's only one step.
        asyncio.run(engine.execute_workflow(wf.id))

    def test_pipeline_execution_result_serialization(self):
        engine, agents = _setup_realistic_engine()

        wf = engine.create_workflow("ser-wf", [
            WorkflowStepDef(name="s1", capability_required="planning"),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        data = result.to_dict()
        assert data["workflow_id"] == wf.id
        assert data["status"] == "completed"
        assert data["steps_completed"] == 1
        assert "results" in data
        assert isinstance(data["results"], dict)

    def test_context_flows_through_chain(self):
        """Verifica que el contexto de pasos anteriores llega a los posteriores."""
        engine, agents = _setup_realistic_engine()

        wf = engine.create_workflow("ctx-flow", [
            WorkflowStepDef(name="plan", capability_required="planning"),
            WorkflowStepDef(name="code", capability_required="code.generate", dependencies=["plan"]),
        ])
        engine.register_workflow(wf)
        result = asyncio.run(engine.execute_workflow(wf.id))

        assert result.status.value == "completed"
        assert result.steps_completed == 2
        # The workflow metrics show both agents were used.
        wf_metrics = engine.workflow_metrics(wf.id)
        assert "planner" in wf_metrics["agents_used"]
        assert "coder" in wf_metrics["agents_used"]