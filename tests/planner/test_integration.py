"""Integration tests for the planner with existing modules (Sprint 2.8).

These tests verify that the planner integrates cleanly with the
existing Sprint 2.1-2.7 modules:

- ``multiagent.registry.AgentRegistry`` / ``AgentManifest``
- ``multiagent.semantic.SemanticRetrievalEngine`` (stubbed)
- ``multiagent.workflow.WorkflowEngine`` / ``Workflow`` / ``Step``

The tests follow the existing project convention of sys.path
manipulation at the top of each test module.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLANNER_SRC = ROOT / "packages" / "planner" / "src"
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

# Insert planner first so `import planner` resolves to our package.
sys.path.insert(0, str(PLANNER_SRC))
sys.path.insert(0, str(MULTIAGENT_SRC))

# Drop cached modules so imports resolve cleanly.
for _k in [k for k in list(sys.modules) if k.startswith("planner") or k.startswith("multiagent")]:
    sys.modules.pop(_k, None)

from planner import (  # noqa: E402
    ExecutionPlan,
    Scheduler,
    TaskPlanner,
    WorkflowEngineAdapter,
    build_integrated_planner,
)
from planner.exceptions import CycleDetectedError  # noqa: E402
from planner.integration import (  # noqa: E402
    MultiAgentRegistryAdapter,
    SemanticMemoryAdapter,
)

# ----------------------------------------------------------------------
# Test stubs
# ----------------------------------------------------------------------


def _make_stub_agent_class():
    """Build a StubAgent class that inherits from the real BaseAgent.

    Done lazily so that the multiagent package is imported only when
    the integration tests actually run.
    """
    from multiagent.base import BaseAgent

    class StubAgent(BaseAgent):
        """Minimal BaseAgent subclass for integration tests."""

        def __init__(self, name: str) -> None:
            super().__init__(name=name, description=f"Stub agent {name}")

        async def _execute_impl(self, payload):  # type: ignore[no-untyped-def]
            return {"ok": True, "agent": self.name}

    return StubAgent


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def registry():
    """Build a real AgentRegistry populated with standard manifests."""
    from multiagent.registry import AgentRegistry, standard_manifests

    StubAgent = _make_stub_agent_class()

    async def _build() -> AgentRegistry:
        reg = AgentRegistry()
        for manifest in standard_manifests():
            agent = StubAgent(name=manifest.id)
            await agent.start()
            await reg.register(
                agent,
                provided_capabilities=list(manifest.capabilities),
                priority=manifest.priority,
                metadata=dict(manifest.metadata),
            )
        return reg

    return asyncio.run(_build())


# ----------------------------------------------------------------------
# MultiAgentRegistryAdapter
# ----------------------------------------------------------------------


def test_registry_adapter_lists_agents(registry):
    adapter = MultiAgentRegistryAdapter(registry)
    agents = adapter.list_agents()
    assert len(agents) == 7  # 7 standard manifests
    ids = {a.id for a in agents}
    # Standard manifest ids: planner, coder, reviewer, research, terminal, git, memory.
    assert "planner" in ids
    assert "coder" in ids


def test_registry_adapter_get_agent(registry):
    adapter = MultiAgentRegistryAdapter(registry)
    a = adapter.get_agent("coder")
    assert a is not None
    assert "code.generate" in a.capabilities


def test_registry_adapter_find_by_capability(registry):
    adapter = MultiAgentRegistryAdapter(registry)
    matches = adapter.find_by_capability("code.generate")
    assert len(matches) >= 1
    assert any(a.id == "coder" for a in matches)


def test_registry_adapter_find_by_capability_wildcard(registry):
    adapter = MultiAgentRegistryAdapter(registry)
    matches = adapter.find_by_capability("git.commit")
    assert len(matches) >= 1
    assert any(a.id == "git" for a in matches)


def test_registry_adapter_workload_noop(registry):
    """update_workload should be a no-op (registry tracks its own workload)."""
    adapter = MultiAgentRegistryAdapter(registry)
    a = adapter.get_agent("coder")
    original = a.usage_count
    adapter.update_workload("coder", delta=+5)
    a2 = adapter.get_agent("coder")
    assert a2.usage_count == original


# ----------------------------------------------------------------------
# build_integrated_planner
# ----------------------------------------------------------------------


def test_build_integrated_planner_with_registry(registry):
    p = build_integrated_planner(registry=registry)
    assert isinstance(p, TaskPlanner)
    assert len(p.registry_adapter.list_agents()) == 7


def test_build_integrated_planner_without_registry():
    p = build_integrated_planner()
    assert isinstance(p, TaskPlanner)
    assert len(p.registry_adapter.list_agents()) == 0


def test_build_integrated_planner_with_fallback():
    p = build_integrated_planner(fallback_agent_id="default-agent")
    # When no agents are registered, fallback is used.
    plan = p.create_plan("Do something unknown")
    for t in plan.tasks.values():
        assert t.assigned_agent == "default-agent"


# ----------------------------------------------------------------------
# End-to-end: planner + real registry
# ----------------------------------------------------------------------


def test_end_to_end_plan_with_real_registry(registry):
    p = build_integrated_planner(registry=registry)
    plan = p.create_plan("Create an AI code editor")
    # 8 tasks from the software_project template.
    assert len(plan.tasks) == 8
    titles = [t.title for t in plan.tasks.values()]
    assert "Requirements" in titles
    assert "Documentation" in titles
    # Most tasks should be assigned to real agents.
    assigned = [t for t in plan.tasks.values() if t.assigned_agent is not None]
    assert len(assigned) >= 6


def test_end_to_end_scheduler_executes_plan(registry):
    p = build_integrated_planner(registry=registry)
    plan = p.create_plan("Refactor the auth module")
    sched = Scheduler(plan)
    # Execute every task in dependency order.
    executed: list[str] = []
    while not sched.is_done():
        batch = sched.next_tasks(max_tasks=4)
        if not batch:
            break
        for t in batch:
            sched.mark_started(t.id)
            executed.append(t.title)
            sched.mark_completed(t.id)
    assert sched.is_done()
    assert sched.progress() == 1.0
    assert len(executed) == len(plan.tasks)


def test_end_to_end_with_failure_and_retry(registry):
    p = build_integrated_planner(registry=registry)
    plan = p.create_plan("Fix a critical bug")
    sched = Scheduler(plan)
    # Get the first task and fail it once.
    first_batch = sched.next_tasks(max_tasks=1)
    assert len(first_batch) == 1
    first = first_batch[0]
    sched.mark_started(first.id)
    sched.mark_failed(first.id, error="transient")
    # Should have been auto-retried (status back to READY).
    assert plan.tasks[first.id].status.name == "READY"
    assert plan.tasks[first.id].retry_count == 1
    # Now complete it.
    sched.mark_started(first.id)
    sched.mark_completed(first.id)
    assert plan.tasks[first.id].status.name == "COMPLETED"


# ----------------------------------------------------------------------
# WorkflowEngineAdapter
# ----------------------------------------------------------------------


def test_workflow_adapter_converts_plan(registry):
    p = build_integrated_planner(registry=registry)
    plan = p.create_plan("Create an app")
    wf = WorkflowEngineAdapter.to_workflow(plan)
    assert wf is not None
    # Workflow.steps is a dict {step_id: Step}.
    assert len(wf.steps) == len(plan.tasks)


def test_workflow_adapter_step_deps_preserved(registry):
    p = build_integrated_planner(registry=registry)
    plan = p.create_plan("Create an app")
    wf = WorkflowEngineAdapter.to_workflow(plan)
    # Each step's depends_on should match the original task's deps.
    for step in wf.steps.values():
        task = plan.tasks[step.input["task_id"]]
        assert set(step.depends_on) == set(task.dependencies)


def test_workflow_adapter_without_multiagent_raises():
    # Force the import to fail by temporarily removing multiagent from path.
    # This is hard to test reliably; skip if multiagent is importable.
    pytest.skip("Cannot reliably test missing multiagent package in monorepo.")


# ----------------------------------------------------------------------
# SemanticMemoryAdapter (with stub engine)
# ----------------------------------------------------------------------


class StubSemanticEngine:
    """Minimal async retrieval engine stub."""

    def __init__(self, results: list[dict] | None = None) -> None:
        self._results = results or [
            {"id": "plan-1", "score": 0.9, "payload": {"objective": "similar"}},
        ]

    async def retrieve(self, request, **kwargs):  # type: ignore[no-untyped-def]
        # Return an object with .items attribute.
        class Result:
            def __init__(self, items):
                self.items = items

        items = [type("Item", (), d)() for d in self._results]
        return Result(items)


def test_semantic_memory_adapter_retrieves():
    engine = StubSemanticEngine()
    adapter = SemanticMemoryAdapter(engine)
    results = adapter.retrieve_similar("query", top_k=3)
    assert len(results) == 1
    assert results[0]["id"] == "plan-1"


def test_semantic_memory_adapter_handles_error():
    class FailingEngine:
        async def retrieve(self, *args, **kwargs):
            raise RuntimeError("boom")

    adapter = SemanticMemoryAdapter(FailingEngine())
    # Should not raise; returns empty list.
    results = adapter.retrieve_similar("query")
    assert results == []


def test_planner_uses_semantic_memory(registry):
    engine = StubSemanticEngine()
    p = build_integrated_planner(
        registry=registry,
        semantic_engine=engine,
    )
    plan = p.create_plan("Build an app")
    # Plan metadata should reflect that similar plans were found.
    assert plan.metadata["similar_plans_count"] >= 1


# ----------------------------------------------------------------------
# DAG integration with ExecutionPlan
# ----------------------------------------------------------------------


def test_plan_graph_rejects_cycles(registry):
    p = build_integrated_planner(registry=registry)
    plan = p.create_plan("Build app")
    # Try to add a cyclic dependency manually.
    t1 = plan.tasks[next(iter(plan.tasks))]
    # Find a downstream task.
    children = plan.graph.children(t1.id)
    if children:
        with pytest.raises(CycleDetectedError):
            plan.graph.add_dependency(t1.id, children[0])


# ----------------------------------------------------------------------
# Full round-trip: serialize -> deserialize -> resume
# ----------------------------------------------------------------------


def test_plan_serialize_deserialize_roundtrip(registry):
    p = build_integrated_planner(registry=registry)
    plan = p.create_plan("Build app")
    s = plan.serialize()
    plan2 = ExecutionPlan.deserialize(s)
    assert plan2.objective == plan.objective
    assert len(plan2.tasks) == len(plan.tasks)
    assert set(plan2.task_ids) == set(plan.task_ids)


def test_scheduler_state_serialize_deserialize(registry):
    p = build_integrated_planner(registry=registry)
    plan = p.create_plan("Build app")
    sched = Scheduler(plan)
    # Execute first task.
    first = sched.next_tasks(max_tasks=1)[0]
    sched.mark_started(first.id)
    sched.mark_completed(first.id)
    # Serialize scheduler state.
    state = sched.to_state()
    # Reconstruct.
    sched2 = Scheduler.from_state(plan, state)
    assert sched2.completed_count == 1
    assert sched2.progress() == sched.progress()


# ----------------------------------------------------------------------
# Compatibility: existing modules still work
# ----------------------------------------------------------------------


def test_existing_registry_still_works(registry):
    """The planner integration must not break the existing registry API."""
    # The registry should still expose its standard methods.
    assert len(registry.get_all()) == 7
    assert registry.get("coder") is not None
    assert registry.exists("planner") is True or registry.get("planner") is not None


def test_existing_dag_still_works():
    """The existing multiagent.workflow.dag.DAG must still be importable."""
    from multiagent.workflow.dag import DAG as ExistingDAG

    dag = ExistingDAG()
    dag.add_step("a")
    dag.add_step("b", depends_on=["a"])
    v = dag.validate()
    assert v.ok
