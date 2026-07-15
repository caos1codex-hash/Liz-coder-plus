"""Sprint 3.5 — Advanced Planner tests.

Covers:

  - Task decomposition (compound, multi-component, CRUD, fallback).
  - Dependency graph (cycles, topological order, parallel batches,
    critical path, ready/blocked queries).
  - Execution strategy (per-step decisions, parallel/sequential mode,
    retry budgets, human-confirm flags, escalation targets).
  - Failure recovery (retry → escalate → fail_plan chain).
  - Planner memory (pattern storage, similarity search, suggest_plan).
  - Planner-agent bridge (capability validation, missing-capability
    requests, escalation suggestions).

Test naming convention: test_<unit>_<scenario>_<expected>
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup — must match the project's per-module convention.
# ---------------------------------------------------------------------------
_PACKAGES_DIR = Path(__file__).resolve().parents[2] / "packages"
sys.path.insert(0, str(_PACKAGES_DIR))

_MULTIAGENT_SRC = _PACKAGES_DIR / "multiagent" / "src"
sys.path.insert(0, str(_MULTIAGENT_SRC))
# Clear any cached 'multiagent' modules to ensure re-import safety.
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

import pytest  # noqa: E402

# -- planner imports (after path setup)  # noqa: E402 ----------------------
from planner.decomposer import DecompositionResult, TaskDecomposer  # noqa: E402
from planner.dependency_graph import (  # noqa: E402
    CriticalPath,
    DependencyGraph,
    GraphValidationResult,
    ParallelBatch,
)
from planner.enums import PlanStatus, Priority, StepStatus  # noqa: E402
from planner.exceptions import (  # noqa: E402
    CircularDependencyError,
    DecompositionError,
    DependencyError,
    EscalationError,
    GraphError,
    PlanningMemoryError,
    StrategyError,
)
from planner.models import PlanStep, TaskPlan  # noqa: E402
from planner.planner_agent_bridge import (  # noqa: E402
    CapabilityRequest,
    CapabilityRequestBroker,
    PlannerAgentBridge,
)
from planner.planner_memory import (  # noqa: E402
    InMemoryPatternStore,
    MemoryBackedPatternStore,
    PatternStore,
    PlannerMemory,
    PlanningPattern,
)
from planner.strategy import (  # noqa: E402
    ExecutionDecision,
    ExecutionStrategyEngine,
    FailureRecoveryEngine,
    FailureRecoveryPlan,
    PlanExecutionStrategy,
)
from planner.strategy_enums import (  # noqa: E402
    CapabilityRequestStatus,
    ExecutionMode,
    FailureDecision,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_step(
    title: str = "",
    *,
    id: str | None = None,
    dependencies: list[str] | None = None,
    capabilities: list[str] | None = None,
    duration: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> PlanStep:
    """Build a PlanStep with sensible defaults for tests."""
    md = dict(metadata or {})
    if capabilities is not None:
        md["required_capabilities"] = list(capabilities)
    return PlanStep(
        id=id or uuid.uuid4().hex[:8],
        title=title or f"step-{id or 'x'}",
        description=title,
        estimated_duration=duration,
        dependencies=list(dependencies or []),
        metadata=md,
    )


def _make_linear_plan(n: int = 3, *, goal: str = "linear plan") -> TaskPlan:
    """Build a plan with n sequentially-dependent steps."""
    steps: list[PlanStep] = []
    prev_id: str | None = None
    for i in range(n):
        s = _make_step(
            f"Step {i + 1}",
            dependencies=[prev_id] if prev_id else [],
            duration=100.0 * (i + 1),
        )
        steps.append(s)
        prev_id = s.id
    return TaskPlan(goal=goal, steps=steps)


def _make_diamond_plan() -> TaskPlan:
    """Build a diamond-shaped plan: A → {B, C} → D."""
    a = _make_step("A", duration=100.0)
    b = _make_step("B", dependencies=[a.id], duration=200.0)
    c = _make_step("C", dependencies=[a.id], duration=150.0)
    d = _make_step("D", dependencies=[b.id, c.id], duration=50.0)
    return TaskPlan(goal="diamond plan", steps=[a, b, c, d])


# ===========================================================================
# TaskDecomposer
# ===========================================================================


class TestTaskDecomposer:
    """Sprint 3.5 — Task decomposition."""

    def test_decomposer_compound_sequential(self) -> None:
        """Compound goal with 'and then' should split sequentially."""
        decom = TaskDecomposer()
        result = decom.decompose("Create the API and then test it")
        assert result.strategy == "compound_sequential"
        assert len(result.subtasks) >= 2
        # Each subsequent step should depend on the previous.
        for i in range(1, len(result.subtasks)):
            assert result.subtasks[i].dependencies, (
                f"Subtask {i} should depend on previous"
            )

    def test_decomposer_multi_component_parallel(self) -> None:
        """Frontend + backend goal should produce parallel subtasks."""
        decom = TaskDecomposer()
        result = decom.decompose("Build frontend and backend")
        assert result.strategy == "multi_component_parallel"
        assert len(result.subtasks) >= 2
        # Parallel subtasks have no dependencies among each other.
        for st in result.subtasks:
            assert st.dependencies == []

    def test_decomposer_crud_pattern(self) -> None:
        """Create/update/delete goals should trigger CRUD pattern."""
        decom = TaskDecomposer()
        result = decom.decompose("Create a user authentication module")
        assert result.strategy == "crud_pattern"
        assert len(result.subtasks) == 3
        # CRUD pattern: validate → implement → test.
        assert "validate" in result.subtasks[0].title.lower()
        assert "implement" in result.subtasks[1].title.lower()
        assert "test" in result.subtasks[2].title.lower()

    def test_decomposer_research_implement_report(self) -> None:
        """Research/report goals should trigger research pattern."""
        decom = TaskDecomposer()
        result = decom.decompose("Research the API and write a report")
        assert result.strategy == "research_implement_report"
        assert len(result.subtasks) >= 2

    def test_decomposer_fallback_single_step(self) -> None:
        """Goals that match no pattern fall back to a single step."""
        decom = TaskDecomposer()
        result = decom.decompose("Do something completely generic")
        assert result.strategy == "fallback"
        assert len(result.subtasks) == 1

    def test_decomposer_empty_goal_raises(self) -> None:
        """Empty goal should raise DecompositionError."""
        decom = TaskDecomposer()
        with pytest.raises(DecompositionError):
            decom.decompose("")

    def test_decomposer_attaches_required_capabilities(self) -> None:
        """Each subtask should have required_capabilities in metadata."""
        decom = TaskDecomposer()
        result = decom.decompose("Build the frontend and backend API")
        for st in result.subtasks:
            caps = st.metadata.get("required_capabilities", [])
            assert isinstance(caps, list)
            assert len(caps) >= 1, f"Step '{st.title}' should have capabilities"

    def test_decomposer_attaches_expected_outputs(self) -> None:
        """Each subtask should have expected_outputs in metadata."""
        decom = TaskDecomposer()
        result = decom.decompose("Test the implementation")
        for st in result.subtasks:
            outs = st.metadata.get("expected_outputs", [])
            assert isinstance(outs, list)
            assert len(outs) >= 1

    def test_decomposer_avoids_redundant_steps(self) -> None:
        """Decomposer should drop steps whose outputs are already covered."""
        decom = TaskDecomposer(avoid_redundant_steps=True)
        result = decom.decompose("Create the API and test it")
        # All subtasks should have at least one unique output.
        seen_outputs: set[str] = set()
        for st in result.subtasks:
            outs = st.metadata.get("expected_outputs", [])
            assert any(o not in seen_outputs for o in outs), (
                f"Step '{st.title}' outputs {outs} are all redundant"
            )
            seen_outputs.update(outs)

    def test_decomposer_into_plan_returns_taskplan(self) -> None:
        """decompose_into_plan should return a valid TaskPlan."""
        decom = TaskDecomposer()
        plan = decom.decompose_into_plan("Build the API and then deploy")
        assert isinstance(plan, TaskPlan)
        assert plan.metadata.get("generated_by") == "TaskDecomposer"
        assert plan.metadata.get("decomposition_strategy") is not None
        assert plan.metadata.get("decomposition_confidence") is not None
        assert len(plan.steps) >= 2

    def test_decomposer_confidence_in_range(self) -> None:
        """Confidence score should be between 0 and 1."""
        decom = TaskDecomposer()
        for goal in (
            "Create the API and then test it",
            "Build frontend and backend",
            "Do something generic",
        ):
            result = decom.decompose(goal)
            assert 0.0 <= result.confidence <= 1.0, (
                f"Confidence {result.confidence} for goal '{goal}' out of range"
            )

    def test_decomposer_deterministic(self) -> None:
        """Same goal should yield the same decomposition."""
        decom = TaskDecomposer()
        r1 = decom.decompose("Create the API and then test it")
        r2 = decom.decompose("Create the API and then test it")
        assert r1.strategy == r2.strategy
        assert len(r1.subtasks) == len(r2.subtasks)
        assert [s.title for s in r1.subtasks] == [s.title for s in r2.subtasks]


# ===========================================================================
# DependencyGraph
# ===========================================================================


class TestDependencyGraph:
    """Sprint 3.5 — Dependency graph system."""

    def test_graph_builds_from_plan(self) -> None:
        """Graph should build node and edge counts from a plan."""
        plan = _make_linear_plan(3)
        graph = DependencyGraph(plan)
        assert graph.node_count == 3
        # 2 edges (A → B, B → C).
        assert graph.edge_count == 2

    def test_graph_detects_cycle(self) -> None:
        """Graph should detect circular dependencies."""
        a = _make_step("A")
        b = _make_step("B", dependencies=[a.id])
        c = _make_step("C", dependencies=[b.id])
        # Close the cycle: A depends on C.
        a.dependencies = [c.id]
        plan = TaskPlan(goal="cyclic", steps=[a, b, c])
        graph = DependencyGraph(plan)
        result = graph.validate()
        assert not result.ok
        assert len(result.cycles) >= 1
        # The cycle should mention all 3 nodes.
        cycle = result.cycles[0]
        assert len(cycle) >= 3

    def test_graph_validate_or_raise_cycle(self) -> None:
        """validate_or_raise should raise CircularDependencyError on cycle."""
        a = _make_step("A")
        b = _make_step("B", dependencies=[a.id])
        a.dependencies = [b.id]
        plan = TaskPlan(goal="cyclic", steps=[a, b])
        graph = DependencyGraph(plan)
        with pytest.raises(CircularDependencyError):
            graph.validate_or_raise()

    def test_graph_detects_broken_dep(self) -> None:
        """Graph should detect dependencies that point to missing steps."""
        s = _make_step("S", dependencies=["nonexistent-id"])
        plan = TaskPlan(goal="broken", steps=[s])
        graph = DependencyGraph(plan)
        result = graph.validate()
        assert not result.ok
        assert len(result.broken_deps) == 1
        assert result.broken_deps[0] == (s.id, "nonexistent-id")

    def test_graph_validate_or_raise_broken(self) -> None:
        """Broken dep should raise DependencyError."""
        s = _make_step("S", dependencies=["missing"])
        plan = TaskPlan(goal="broken", steps=[s])
        graph = DependencyGraph(plan)
        with pytest.raises(DependencyError):
            graph.validate_or_raise()

    def test_graph_topological_order_linear(self) -> None:
        """Linear plan should topologically sort to its original order."""
        plan = _make_linear_plan(4)
        graph = DependencyGraph(plan)
        order = graph.topological_order()
        assert len(order) == 4
        # First step has no deps; last depends on all previous.
        first = plan.steps[0]
        last = plan.steps[-1]
        assert order[0] == first.id
        assert order[-1] == last.id

    def test_graph_topological_order_diamond(self) -> None:
        """Diamond plan: A → {B, C} → D should give A, B/C, D."""
        plan = _make_diamond_plan()
        graph = DependencyGraph(plan)
        order = graph.topological_order()
        # A first, D last.
        assert order[0] == plan.steps[0].id  # A
        assert order[-1] == plan.steps[-1].id  # D
        # B and C are in the middle.
        middle = set(order[1:-1])
        assert middle == {plan.steps[1].id, plan.steps[2].id}

    def test_graph_parallel_batches_linear(self) -> None:
        """Linear plan should produce 4 batches of 1 step each."""
        plan = _make_linear_plan(4)
        graph = DependencyGraph(plan)
        batches = graph.parallel_batches()
        assert len(batches) == 4
        for b in batches:
            assert len(b.step_ids) == 1

    def test_graph_parallel_batches_diamond(self) -> None:
        """Diamond plan should produce 3 batches: [A], [B,C], [D]."""
        plan = _make_diamond_plan()
        graph = DependencyGraph(plan)
        batches = graph.parallel_batches()
        assert len(batches) == 3
        assert batches[0].step_ids == [plan.steps[0].id]  # A
        assert set(batches[1].step_ids) == {plan.steps[1].id, plan.steps[2].id}  # B, C
        assert batches[2].step_ids == [plan.steps[3].id]  # D

    def test_graph_parallel_batches_duration(self) -> None:
        """Batch duration should be max of step durations in the batch."""
        plan = _make_diamond_plan()
        graph = DependencyGraph(plan)
        batches = graph.parallel_batches()
        # Middle batch has B (200) and C (150) → max = 200.
        assert batches[1].estimated_duration == 200.0

    def test_graph_critical_path(self) -> None:
        """Critical path should be the longest chain."""
        plan = _make_diamond_plan()
        # Durations: A=100, B=200, C=150, D=50.
        # Paths: A→B→D = 350, A→C→D = 300.  Critical = A→B→D.
        graph = DependencyGraph(plan)
        critical = graph.critical_path()
        assert critical.total_duration == 350.0
        assert len(critical.step_ids) == 3
        # Should include A, B, D.
        ids = set(critical.step_ids)
        assert plan.steps[0].id in ids  # A
        assert plan.steps[1].id in ids  # B
        assert plan.steps[3].id in ids  # D
        # Should NOT include C (not on critical path).
        assert plan.steps[2].id not in ids

    def test_graph_ancestors_descendants(self) -> None:
        """ancestors and descendants should compute transitive closure."""
        plan = _make_diamond_plan()
        graph = DependencyGraph(plan)
        a, b, c, d = plan.steps
        # B's ancestors should be {A}.
        assert graph.ancestors(b.id) == {a.id}
        # D's ancestors should be {A, B, C}.
        assert graph.ancestors(d.id) == {a.id, b.id, c.id}
        # A's descendants should be {B, C, D}.
        assert graph.descendants(a.id) == {b.id, c.id, d.id}
        # B's descendants should be {D}.
        assert graph.descendants(b.id) == {d.id}

    def test_graph_has_path(self) -> None:
        """has_path should correctly identify reachability."""
        plan = _make_diamond_plan()
        graph = DependencyGraph(plan)
        a, b, c, d = plan.steps
        assert graph.has_path(a.id, d.id)
        assert graph.has_path(a.id, b.id)
        assert not graph.has_path(b.id, c.id)  # B and C are siblings
        assert graph.has_path(a.id, a.id)  # Reflexive

    def test_graph_ready_steps(self) -> None:
        """ready_steps should return steps whose deps are all completed."""
        plan = _make_diamond_plan()
        graph = DependencyGraph(plan)
        a, b, c, d = plan.steps
        # Initially only A is ready.
        assert graph.ready_steps(set()) == [a.id]
        # After A completes, B and C are ready.
        ready_after_a = graph.ready_steps({a.id})
        assert set(ready_after_a) == {b.id, c.id}
        # After A, B, C complete, D is ready.
        assert graph.ready_steps({a.id, b.id, c.id}) == [d.id]

    def test_graph_blocked_steps(self) -> None:
        """blocked_steps should return transitively-blocked steps."""
        plan = _make_diamond_plan()
        graph = DependencyGraph(plan)
        a, b, c, d = plan.steps
        # If B fails, D is blocked.
        blocked = graph.blocked_steps({b.id})
        assert d.id in blocked

    def test_graph_unknown_node_raises(self) -> None:
        """Queries on unknown nodes should raise GraphError."""
        plan = _make_linear_plan(2)
        graph = DependencyGraph(plan)
        with pytest.raises(GraphError):
            graph.ancestors("nonexistent-id")
        with pytest.raises(GraphError):
            graph.descendants("nonexistent-id")

    def test_graph_refresh_after_mutation(self) -> None:
        """refresh() should rebuild the graph after plan mutation."""
        plan = _make_linear_plan(2)
        graph = DependencyGraph(plan)
        assert graph.edge_count == 1
        # Add a third step depending on the second.
        s3 = _make_step("S3", dependencies=[plan.steps[1].id])
        plan.steps.append(s3)
        # Graph doesn't see it yet.
        assert graph.node_count == 2
        graph.refresh()
        assert graph.node_count == 3
        assert graph.edge_count == 2

    def test_graph_execution_plan_summary(self) -> None:
        """execution_plan_summary should return a JSON-serialisable dict."""
        plan = _make_diamond_plan()
        graph = DependencyGraph(plan)
        summary = graph.execution_plan_summary()
        assert isinstance(summary, dict)
        assert summary["node_count"] == 4
        # Diamond: A→B, A→C, B→D, C→D = 4 edges.
        assert summary["edge_count"] == 4
        assert "topological_order" in summary
        assert "parallel_batches" in summary
        assert "critical_path" in summary
        assert "validation" in summary
        assert summary["max_parallelism"] == 2  # Diamond middle batch.

    def test_graph_empty_plan(self) -> None:
        """Empty plan should produce an empty graph."""
        plan = TaskPlan(goal="empty", steps=[])
        graph = DependencyGraph(plan)
        assert graph.node_count == 0
        assert graph.edge_count == 0
        assert graph.topological_order() == []
        assert graph.parallel_batches() == []
        assert graph.critical_path().total_duration == 0.0


# ===========================================================================
# ExecutionStrategyEngine
# ===========================================================================


class TestExecutionStrategy:
    """Sprint 3.5 — Execution strategy layer."""

    def test_strategy_builds_for_linear_plan(self) -> None:
        """Linear plan should produce a sequential global mode."""
        plan = _make_linear_plan(3)
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine()
        strat = engine.build(plan, graph)
        assert strat.global_mode == ExecutionMode.SEQUENTIAL
        assert strat.max_parallelism == 1
        assert len(strat.decisions) == 3
        # All steps in sequential mode.
        for dec in strat.decisions.values():
            assert dec.mode == ExecutionMode.SEQUENTIAL

    def test_strategy_builds_for_diamond_plan(self) -> None:
        """Diamond plan should produce parallel global mode."""
        plan = _make_diamond_plan()
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine()
        strat = engine.build(plan, graph)
        assert strat.global_mode == ExecutionMode.PARALLEL
        assert strat.max_parallelism == 2  # Middle batch has 2 steps.

    def test_strategy_parallel_safe_caps(self) -> None:
        """Steps with parallel-safe capabilities get PARALLEL mode."""
        # Build a plan with 2 parallel testing steps.
        s1 = _make_step("Test A", capabilities=["testing"], duration=100.0)
        s2 = _make_step("Test B", capabilities=["testing"], duration=100.0)
        plan = TaskPlan(goal="parallel tests", steps=[s1, s2])
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine()
        strat = engine.build(plan, graph)
        # Both steps should be in PARALLEL mode.
        for dec in strat.decisions.values():
            assert dec.mode == ExecutionMode.PARALLEL

    def test_strategy_non_parallel_safe_caps(self) -> None:
        """Steps with non-parallel-safe capabilities stay SEQUENTIAL."""
        # Two parallel backend steps (caps not in default parallel_safe set).
        s1 = _make_step("Backend A", capabilities=["backend"], duration=100.0)
        s2 = _make_step("Backend B", capabilities=["backend"], duration=100.0)
        plan = TaskPlan(goal="parallel backends", steps=[s1, s2])
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine()
        strat = engine.build(plan, graph)
        # Both steps should be SEQUENTIAL even though they're in the same batch.
        for dec in strat.decisions.values():
            assert dec.mode == ExecutionMode.SEQUENTIAL

    def test_strategy_retry_budget_critical_step(self) -> None:
        """Critical-path steps should get +1 retry budget."""
        plan = _make_linear_plan(3)
        # Make step 2 (middle) the critical path (longest duration).
        plan.steps[1].estimated_duration = 500.0
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine(max_retries=1)
        strat = engine.build(plan, graph)
        # Step 2 is on the critical path; it should get retry_budget=2.
        dec = strat.decisions[plan.steps[1].id]
        assert dec.retry_budget == 2

    def test_strategy_failure_decision_escalate(self) -> None:
        """Steps with past failures >= threshold should ESCALATE."""
        plan = _make_linear_plan(2)
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine(critical_retry_threshold=2)
        strat = engine.build(
            plan, graph,
            failure_history={plan.steps[0].id: 2},
        )
        dec = strat.decisions[plan.steps[0].id]
        assert dec.failure_decision == FailureDecision.ESCALATE
        assert dec.escalation_target != ""

    def test_strategy_failure_decision_skip_for_aux(self) -> None:
        """Testing/documentation steps should default to SKIP on failure.

        Uses a 3-step diamond where testing is NOT on the critical path
        (the documentation branch has a longer duration).
        """
        s1 = _make_step(
            "Implement feature",
            capabilities=["backend"],
            duration=300.0,
        )
        s2 = _make_step(
            "Run tests",
            capabilities=["testing"],
            duration=10.0,
            dependencies=[s1.id],
        )
        s3 = _make_step(
            "Write docs",
            capabilities=["documentation"],
            duration=500.0,
            dependencies=[s1.id],
        )
        plan = TaskPlan(goal="implement, test, document", steps=[s1, s2, s3])
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine()
        strat = engine.build(plan, graph)
        # Critical path: s1 → s3 (300+500=800). s2 (300+10=310) is NOT critical.
        dec2 = strat.decisions[s2.id]
        assert dec2.failure_decision == FailureDecision.SKIP
        # s1 and s3 are on the critical path → RETRY.
        assert strat.decisions[s1.id].failure_decision == FailureDecision.RETRY
        assert strat.decisions[s3.id].failure_decision == FailureDecision.RETRY

    def test_strategy_human_confirm_for_security(self) -> None:
        """Security/devops capabilities should require human confirmation."""
        s = _make_step(
            "Deploy to production",
            capabilities=["devops"],
            duration=60.0,
        )
        plan = TaskPlan(goal="deploy", steps=[s])
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine()
        strat = engine.build(plan, graph)
        dec = strat.decisions[s.id]
        assert dec.requires_human_confirm is True
        assert strat.requires_any_human_confirm is True

    def test_strategy_human_confirm_via_metadata(self) -> None:
        """Step metadata 'priority' >= 2 should trigger human confirmation."""
        s = _make_step(
            "Critical step",
            capabilities=["backend"],
            duration=60.0,
            metadata={"priority": 3},
        )
        plan = TaskPlan(goal="critical", steps=[s])
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine()
        strat = engine.build(plan, graph)
        dec = strat.decisions[s.id]
        assert dec.requires_human_confirm is True

    def test_strategy_timeout_hint(self) -> None:
        """timeout_hint should be 1.5x the step's estimated_duration."""
        s = _make_step("S", duration=100.0)
        plan = TaskPlan(goal="t", steps=[s])
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine()
        strat = engine.build(plan, graph)
        dec = strat.decisions[s.id]
        assert dec.timeout_hint == 150.0

    def test_strategy_decision_for_lookup(self) -> None:
        """decision_for should return the decision for a step_id."""
        plan = _make_linear_plan(2)
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine()
        strat = engine.build(plan, graph)
        dec = strat.decision_for(plan.steps[0].id)
        assert dec is not None
        assert dec.step_id == plan.steps[0].id
        # Unknown step returns None.
        assert strat.decision_for("nonexistent") is None

    def test_strategy_to_dict(self) -> None:
        """to_dict should produce a JSON-serialisable summary."""
        plan = _make_diamond_plan()
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine()
        strat = engine.build(plan, graph)
        d = strat.to_dict()
        assert isinstance(d, dict)
        assert d["plan_id"] == plan.id
        assert d["global_mode"] in ("sequential", "parallel")
        assert "decisions" in d
        assert "batches" in d
        assert "critical_path_step_ids" in d


# ===========================================================================
# FailureRecoveryEngine
# ===========================================================================


class TestFailureRecovery:
    """Sprint 3.5 — Failure recovery decisions."""

    def test_recovery_retry_first_failure(self) -> None:
        """First failure should produce a RETRY decision."""
        engine = FailureRecoveryEngine(default_max_retries=2)
        recovery = engine.plan_recovery(
            step_id="step-1",
            step_capability="backend",
            retry_count=0,
        )
        assert recovery.decision == FailureDecision.RETRY
        assert recovery.retry_attempt == 1
        assert recovery.max_retries == 2

    def test_recovery_escalate_after_retries(self) -> None:
        """After exhausting retries, should escalate."""
        engine = FailureRecoveryEngine(default_max_retries=2)
        recovery = engine.plan_recovery(
            step_id="step-1",
            step_capability="backend",
            retry_count=2,
        )
        assert recovery.decision == FailureDecision.ESCALATE
        assert recovery.next_agent_capability != "backend"

    def test_recovery_fail_plan_after_escalation_chain(self) -> None:
        """When escalation chain is exhausted, should FAIL_PLAN."""
        engine = FailureRecoveryEngine(
            default_max_retries=1,
            escalation_chain=["review", "planning"],
        )
        # First failure → retry.
        r1 = engine.plan_recovery(
            step_id="s1", step_capability="backend", retry_count=0,
        )
        assert r1.decision == FailureDecision.RETRY
        # Second failure → escalate to "review".
        r2 = engine.plan_recovery(
            step_id="s1", step_capability="backend", retry_count=1,
        )
        assert r2.decision == FailureDecision.ESCALATE
        assert r2.next_agent_capability == "review"
        # Third failure → escalate to "planning".
        r3 = engine.plan_recovery(
            step_id="s1", step_capability="review", retry_count=2,
        )
        assert r3.next_agent_capability == "planning"
        # Fourth failure → escalation chain exhausted → FAIL_PLAN.
        r4 = engine.plan_recovery(
            step_id="s1", step_capability="planning", retry_count=3,
        )
        assert r4.decision == FailureDecision.FAIL_PLAN

    def test_recovery_uses_strategy_decision(self) -> None:
        """If a strategy is provided, its decision should be used.

        Uses a 3-step diamond where the testing step is NOT on the
        critical path (so its strategy decision is SKIP).
        """
        s1 = _make_step(
            "Implement feature",
            capabilities=["backend"],
            duration=300.0,
        )
        s2 = _make_step(
            "Run tests",
            capabilities=["testing"],
            duration=10.0,
            dependencies=[s1.id],
        )
        s3 = _make_step(
            "Write docs",
            capabilities=["documentation"],
            duration=500.0,
            dependencies=[s1.id],
        )
        plan = TaskPlan(goal="implement, test, document", steps=[s1, s2, s3])
        graph = DependencyGraph(plan)
        strat_engine = ExecutionStrategyEngine()
        strat = strat_engine.build(plan, graph)
        # Confirm strategy says SKIP for the testing step.
        assert strat.decisions[s2.id].failure_decision == FailureDecision.SKIP

        # Failure recovery engine should respect that.
        engine = FailureRecoveryEngine()
        recovery = engine.plan_recovery(
            step_id=s2.id,
            step_capability="testing",
            retry_count=0,
            strategy=strat,
        )
        assert recovery.decision == FailureDecision.SKIP

    def test_recovery_to_dict(self) -> None:
        """FailureRecoveryPlan should serialise to dict."""
        engine = FailureRecoveryEngine()
        recovery = engine.plan_recovery(
            step_id="s1", step_capability="backend", retry_count=0,
        )
        d = recovery.to_dict()
        assert d["step_id"] == "s1"
        assert d["decision"] == "retry"
        assert d["retry_attempt"] == 1


# ===========================================================================
# PlannerMemory
# ===========================================================================


class TestPlannerMemory:
    """Sprint 3.5 — Planner memory integration."""

    def test_pattern_from_plan(self) -> None:
        """PlanningPattern.from_plan should extract metadata."""
        plan = _make_linear_plan(3)
        plan.metadata["task_type"] = "development"
        plan.metadata["template"] = "DevelopmentTaskTemplate"
        plan.metadata["decomposition_strategy"] = "compound_sequential"
        pattern = PlanningPattern.from_plan(
            plan,
            outcome="success",
            duration_ms=1234.0,
            agent_ids=["agent-1"],
        )
        assert pattern.goal == plan.goal
        assert pattern.outcome == "success"
        assert pattern.duration_ms == 1234.0
        assert pattern.agent_ids == ["agent-1"]
        assert pattern.task_type == "development"
        assert pattern.template_name == "DevelopmentTaskTemplate"
        assert pattern.strategy == "compound_sequential"
        assert pattern.step_count == 3
        assert len(pattern.step_titles) == 3
        assert pattern.plan_dict != {}

    def test_pattern_rebuild_plan(self) -> None:
        """rebuild_plan should reconstruct the original TaskPlan."""
        plan = _make_linear_plan(2)
        pattern = PlanningPattern.from_plan(plan)
        rebuilt = pattern.rebuild_plan()
        assert isinstance(rebuilt, TaskPlan)
        assert rebuilt.goal == plan.goal
        assert len(rebuilt.steps) == len(plan.steps)

    def test_pattern_similarity_to(self) -> None:
        """similarity_to should return 0-1 score."""
        pattern = PlanningPattern(goal="Build a REST API")
        # Identical goal → 1.0.
        assert pattern.similarity_to("Build a REST API") == 1.0
        # Different goal → low score.
        score = pattern.similarity_to("Cook a pizza")
        assert 0.0 <= score < 0.5
        # Overlapping keywords → mid score.
        score = pattern.similarity_to("Build a GraphQL API")
        assert 0.3 < score < 1.0

    def test_pattern_serialization_roundtrip(self) -> None:
        """Pattern should survive a to_dict / from_dict roundtrip."""
        plan = _make_linear_plan(2)
        pattern = PlanningPattern.from_plan(plan, outcome="success")
        d = pattern.to_dict()
        restored = PlanningPattern.from_dict(d)
        assert restored.goal == pattern.goal
        assert restored.outcome == pattern.outcome
        assert restored.step_count == pattern.step_count
        assert restored.step_titles == pattern.step_titles

    def test_planner_memory_store_and_retrieve(self) -> None:
        """store_pattern then find_similar_plans should find it."""
        mem = PlannerMemory(pattern_store=InMemoryPatternStore())
        plan = _make_linear_plan(2)
        plan.goal = "Build a REST API for users"

        async def run() -> None:
            pid = await mem.store_pattern(plan, outcome="success")
            assert pid
            similar = await mem.find_similar_plans("Build a REST API for users")
            assert len(similar) >= 1
            assert similar[0][1].goal == plan.goal
            assert similar[0][0] == 1.0  # Identical goal.

        asyncio.run(run())

    def test_planner_memory_find_similar_successful(self) -> None:
        """find_similar_successful_plans should filter to outcome=success."""
        mem = PlannerMemory(pattern_store=InMemoryPatternStore())

        async def run() -> None:
            # Store a success and a failure pattern.
            p1 = _make_linear_plan(2)
            p1.goal = "Build an API"
            await mem.store_pattern(p1, outcome="success")
            p2 = _make_linear_plan(2)
            p2.goal = "Build an API"
            await mem.store_pattern(p2, outcome="failure")
            # find_similar_successful_plans should only return p1.
            results = await mem.find_similar_successful_plans("Build an API")
            assert len(results) == 1
            assert results[0][1].outcome == "success"

        asyncio.run(run())

    def test_planner_memory_suggest_plan(self) -> None:
        """suggest_plan should return a rebuilt plan when similarity is high."""
        mem = PlannerMemory(pattern_store=InMemoryPatternStore())

        async def run() -> None:
            plan = _make_linear_plan(2)
            plan.goal = "Build a REST API"
            await mem.store_pattern(plan, outcome="success")
            suggested = await mem.suggest_plan("Build a REST API", min_similarity=0.5)
            assert suggested is not None
            assert suggested.goal == "Build a REST API"
            # IDs should be reset (different from the original).
            assert suggested.id != plan.id
            # Metadata should record the rebuild.
            assert suggested.metadata.get("rebuild_from_pattern") is not None

        asyncio.run(run())

    def test_planner_memory_suggest_plan_no_match(self) -> None:
        """suggest_plan returns None when similarity is too low."""
        mem = PlannerMemory(pattern_store=InMemoryPatternStore())

        async def run() -> None:
            plan = _make_linear_plan(2)
            plan.goal = "Cook a pizza"
            await mem.store_pattern(plan, outcome="success")
            suggested = await mem.suggest_plan(
                "Build a REST API", min_similarity=0.5
            )
            assert suggested is None

        asyncio.run(run())

    def test_planner_memory_list_patterns(self) -> None:
        """list_patterns should return all stored patterns."""
        mem = PlannerMemory(pattern_store=InMemoryPatternStore())

        async def run() -> None:
            for i in range(3):
                p = _make_linear_plan(2)
                p.goal = f"Goal {i}"
                await mem.store_pattern(p, outcome="success")
            patterns = await mem.list_patterns()
            assert len(patterns) == 3
            # Filter by outcome.
            successes = await mem.list_patterns(outcome="success")
            assert len(successes) == 3
            failures = await mem.list_patterns(outcome="failure")
            assert len(failures) == 0

        asyncio.run(run())

    def test_planner_memory_store_outcome(self) -> None:
        """store_outcome should pull metadata from the plan."""
        mem = PlannerMemory(pattern_store=InMemoryPatternStore())

        async def run() -> None:
            plan = _make_linear_plan(2)
            plan.metadata["task_type"] = "development"
            plan.metadata["template"] = "DevelopmentTaskTemplate"
            plan.metadata["decomposition_strategy"] = "crud_pattern"
            pid = await mem.store_outcome(
                plan, outcome="success", duration_ms=500.0,
            )
            pattern = await mem.get_pattern(pid)
            assert pattern is not None
            assert pattern.task_type == "development"
            assert pattern.template_name == "DevelopmentTaskTemplate"
            assert pattern.strategy == "crud_pattern"

        asyncio.run(run())

    def test_pattern_rebuild_empty_plan_dict_raises(self) -> None:
        """rebuild_plan with empty plan_dict should raise PlanningMemoryError."""
        pattern = PlanningPattern(goal="x", plan_dict={})
        with pytest.raises(PlanningMemoryError):
            pattern.rebuild_plan()


# ===========================================================================
# PlannerAgentBridge
# ===========================================================================


class TestPlannerAgentBridge:
    """Sprint 3.5 — Planner-agent intelligence bridge."""

    def test_broker_request_immediate_fulfill(self) -> None:
        """Broker should fulfill a request if capability is already available."""
        from planner.agent_context import AgentInfo, AgentPlanningContext

        ctx = AgentPlanningContext()
        ctx.add_agent(AgentInfo(
            id="agent-1", name="Coder", capabilities=["code.generate"],
        ))
        broker = CapabilityRequestBroker()
        broker.attach_context(ctx)
        req = broker.request("code.generate", reason="step needs coding")
        assert req.status == CapabilityRequestStatus.FULFILLED
        assert req.fulfilled_by_agent_id == "agent-1"

    def test_broker_request_pending(self) -> None:
        """Broker should leave a request PENDING if capability is missing."""
        from planner.agent_context import AgentInfo, AgentPlanningContext

        ctx = AgentPlanningContext()
        ctx.add_agent(AgentInfo(id="a1", name="Coder", capabilities=["code"]))
        broker = CapabilityRequestBroker()
        broker.attach_context(ctx)
        req = broker.request("nonexistent.cap", reason="missing")
        assert req.status == CapabilityRequestStatus.PENDING
        assert broker.pending_requests() == [req]

    def test_broker_check_new_agent_fulfills(self) -> None:
        """check_new_agent should fulfill matching pending requests."""
        from planner.agent_context import AgentInfo, AgentPlanningContext

        ctx = AgentPlanningContext()
        broker = CapabilityRequestBroker()
        broker.attach_context(ctx)
        req = broker.request("code.generate")
        assert req.status == CapabilityRequestStatus.PENDING

        # A new agent appears.
        new_agent = AgentInfo(
            id="new-coder", name="NewCoder", capabilities=["code.generate"],
        )
        fulfilled = broker.check_new_agent(new_agent)
        assert len(fulfilled) == 1
        assert fulfilled[0].id == req.id
        assert req.status == CapabilityRequestStatus.FULFILLED
        assert req.fulfilled_by_agent_id == "new-coder"

    def test_broker_deny(self) -> None:
        """deny should mark a pending request as DENIED."""
        broker = CapabilityRequestBroker()
        req = broker.request("missing.cap")
        ok = broker.deny(req.id, reason="user rejected")
        assert ok
        assert req.status == CapabilityRequestStatus.DENIED
        # Denying twice should fail (already denied).
        assert broker.deny(req.id) is False

    def test_broker_expire(self) -> None:
        """expire should mark old pending requests as TIMEOUT."""
        import time

        broker = CapabilityRequestBroker()
        req = broker.request("missing.cap")
        # Manually backdate the request's created_at.
        from datetime import datetime, timedelta, timezone

        old_time = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        req.created_at = old_time
        # Expire anything older than 300s.
        expired = broker.expire(older_than_seconds=300)
        assert expired == 1
        assert req.status == CapabilityRequestStatus.TIMEOUT

    def test_bridge_validate_plan_capabilities(self) -> None:
        """validate_plan_capabilities should detect missing capabilities."""
        from planner.agent_context import AgentInfo, AgentPlanningContext

        ctx = AgentPlanningContext()
        ctx.add_agent(AgentInfo(id="a1", name="Coder", capabilities=["backend"]))
        bridge = PlannerAgentBridge(ctx)
        # Step requires "backend" (covered) and "frontend" (not covered).
        s1 = _make_step("Step 1", capabilities=["backend"])
        s2 = _make_step("Step 2", capabilities=["frontend"])
        plan = TaskPlan(goal="g", steps=[s1, s2])
        missing = bridge.validate_plan_capabilities(plan)
        assert "frontend" in missing[s2.id]
        assert s1.id not in missing

    def test_bridge_request_missing_capabilities(self) -> None:
        """request_missing_capabilities should raise requests for missing caps."""
        from planner.agent_context import AgentInfo, AgentPlanningContext

        ctx = AgentPlanningContext()
        ctx.add_agent(AgentInfo(id="a1", name="Coder", capabilities=["backend"]))
        bridge = PlannerAgentBridge(ctx)
        s = _make_step("Step", capabilities=["backend", "frontend", "database"])
        plan = TaskPlan(goal="g", steps=[s])
        requests = bridge.request_missing_capabilities(plan)
        # frontend and database are missing.
        requested_caps = {r.capability for r in requests}
        assert "frontend" in requested_caps
        assert "database" in requested_caps
        assert "backend" not in requested_caps

    def test_bridge_agent_summary(self) -> None:
        """agent_summary should return capability coverage info."""
        from planner.agent_context import AgentInfo, AgentPlanningContext

        ctx = AgentPlanningContext()
        ctx.add_agent(AgentInfo(
            id="a1", name="Coder", capabilities=["backend", "code.generate"],
            priority=5,
        ))
        ctx.add_agent(AgentInfo(
            id="a2", name="Tester", capabilities=["testing"], priority=3,
        ))
        bridge = PlannerAgentBridge(ctx)
        summary = bridge.agent_summary()
        assert summary["total_agents"] == 2
        assert summary["available_agents"] == 2
        assert "backend" in summary["capabilities"]
        assert "testing" in summary["capabilities"]
        assert summary["capability_coverage"]["backend"] == 1
        # Top agent for backend should be Coder.
        assert summary["top_agent_per_capability"]["backend"] == "Coder"

    def test_bridge_select_agents_for_plan(self) -> None:
        """select_agents_for_plan should assign agents to capable steps."""
        from planner.agent_context import AgentInfo, AgentPlanningContext

        ctx = AgentPlanningContext()
        ctx.add_agent(AgentInfo(
            id="a1", name="Coder", capabilities=["backend"], priority=5,
        ))
        bridge = PlannerAgentBridge(ctx)
        s = _make_step("Implement API", capabilities=["backend"])
        plan = TaskPlan(goal="g", steps=[s])
        assignments = bridge.select_agents_for_plan(plan)
        assert s.id in assignments
        assert assignments[s.id]["agent_id"] == "a1"

    def test_bridge_suggest_escalation_agent(self) -> None:
        """suggest_escalation_agent should return a different agent."""
        from planner.agent_context import AgentInfo, AgentPlanningContext

        ctx = AgentPlanningContext()
        ctx.add_agent(AgentInfo(
            id="a1", name="Junior", capabilities=["backend"], priority=2,
        ))
        ctx.add_agent(AgentInfo(
            id="a2", name="Senior", capabilities=["backend"], priority=8,
        ))
        bridge = PlannerAgentBridge(ctx)
        # Escalate from junior to senior.
        agent = bridge.suggest_escalation_agent(
            "backend", exclude_agent_ids={"a1"},
        )
        assert agent is not None
        assert agent.id == "a2"

    def test_bridge_suggest_escalation_no_candidate(self) -> None:
        """suggest_escalation_agent returns None when no candidate exists."""
        from planner.agent_context import AgentInfo, AgentPlanningContext

        ctx = AgentPlanningContext()
        ctx.add_agent(AgentInfo(
            id="a1", name="Solo", capabilities=["backend"], priority=5,
        ))
        bridge = PlannerAgentBridge(ctx)
        agent = bridge.suggest_escalation_agent(
            "backend", exclude_agent_ids={"a1"},
        )
        assert agent is None


# ===========================================================================
# Integration: Decomposer → Graph → Strategy → Memory
# ===========================================================================


class TestSprint35Integration:
    """Sprint 3.5 — End-to-end integration tests."""

    def test_full_pipeline_decompose_to_strategy(self) -> None:
        """Decompose a goal, build graph, build strategy, store in memory."""
        # 1. Decompose.
        decom = TaskDecomposer()
        plan = decom.decompose_into_plan("Build frontend and backend in parallel")
        assert len(plan.steps) >= 2

        # 2. Build dependency graph.
        graph = DependencyGraph(plan)
        result = graph.validate()
        assert result.ok, f"Graph validation failed: {result.errors}"

        # 3. Build execution strategy.
        engine = ExecutionStrategyEngine()
        strat = engine.build(plan, graph)
        assert strat.global_mode in (ExecutionMode.PARALLEL, ExecutionMode.SEQUENTIAL)
        assert len(strat.decisions) == len(plan.steps)

        # 4. Store in memory.
        mem = PlannerMemory(pattern_store=InMemoryPatternStore())

        async def run() -> None:
            pid = await mem.store_pattern(
                plan,
                outcome="success",
                duration_ms=1000.0,
                strategy=plan.metadata.get("decomposition_strategy", ""),
            )
            assert pid
            # Retrieve.
            similar = await mem.find_similar_plans(plan.goal)
            assert len(similar) >= 1
            assert similar[0][1].strategy == plan.metadata.get("decomposition_strategy")

        asyncio.run(run())

    def test_full_pipeline_failure_recovery(self) -> None:
        """Simulate a step failure and check recovery decisions."""
        # Build a simple plan.
        s1 = _make_step("Research", capabilities=["research"], duration=100.0)
        s2 = _make_step("Implement", capabilities=["backend"], duration=200.0,
                        dependencies=[s1.id])
        plan = TaskPlan(goal="build feature", steps=[s1, s2])
        graph = DependencyGraph(plan)
        engine = ExecutionStrategyEngine()
        strat = engine.build(plan, graph)

        # Simulate s2 failing 3 times.
        recovery_engine = FailureRecoveryEngine(default_max_retries=1)
        # First failure: retry.
        r1 = recovery_engine.plan_recovery(
            step_id=s2.id, step_capability="backend", retry_count=0,
            strategy=strat,
        )
        assert r1.decision in (FailureDecision.RETRY, FailureDecision.ESCALATE)
        # After many failures: escalate or fail_plan.
        r_final = recovery_engine.plan_recovery(
            step_id=s2.id, step_capability="backend", retry_count=5,
            strategy=strat,
        )
        assert r_final.decision in (
            FailureDecision.ESCALATE, FailureDecision.FAIL_PLAN,
        )

    def test_full_pipeline_capability_request(self) -> None:
        """Decompose, find missing capability, request it, fulfill it."""
        from planner.agent_context import AgentInfo, AgentPlanningContext

        # 1. Decompose a goal that needs "frontend".
        decom = TaskDecomposer()
        plan = decom.decompose_into_plan("Build frontend and backend in parallel")

        # 2. Build context with only "backend".
        ctx = AgentPlanningContext()
        ctx.add_agent(AgentInfo(
            id="a1", name="BackendAgent", capabilities=["backend"],
        ))
        bridge = PlannerAgentBridge(ctx)

        # 3. Validate capabilities — "frontend" should be missing.
        missing = bridge.validate_plan_capabilities(plan)
        assert missing, "Expected missing capabilities"
        # At least one step is missing "frontend".
        all_missing_caps: set[str] = set()
        for caps in missing.values():
            all_missing_caps.update(caps)
        assert "frontend" in all_missing_caps

        # 4. Request missing capabilities.
        requests = bridge.request_missing_capabilities(plan)
        assert len(requests) >= 1
        # All requests should be pending (no agent provides them).
        pending = [r for r in requests if r.status == CapabilityRequestStatus.PENDING]
        assert len(pending) >= 1

        # 5. A new agent appears with "frontend".
        new_agent = AgentInfo(
            id="a2", name="FrontendAgent", capabilities=["frontend"],
        )
        ctx.add_agent(new_agent)
        bridge.broker.refresh_context(ctx)
        # Now the previously-pending requests should be fulfilled.
        fulfilled = [
            r for r in bridge.broker.fulfilled_requests()
            if r.capability == "frontend"
        ]
        assert len(fulfilled) >= 1

    def test_dependency_graph_handles_complex_plan(self) -> None:
        """Graph should handle a complex multi-branch plan correctly."""
        # Build: A → B → {C, D} → E, plus F → G independent chain.
        a = _make_step("A", duration=10.0)
        b = _make_step("B", dependencies=[a.id], duration=20.0)
        c = _make_step("C", dependencies=[b.id], duration=15.0)
        d = _make_step("D", dependencies=[b.id], duration=25.0)
        e = _make_step("E", dependencies=[c.id, d.id], duration=10.0)
        f = _make_step("F", duration=30.0)
        g = _make_step("G", dependencies=[f.id], duration=10.0)
        plan = TaskPlan(
            goal="complex plan", steps=[a, b, c, d, e, f, g],
        )
        graph = DependencyGraph(plan)
        # 7 nodes, 6 edges (A→B, B→C, B→D, C→E, D→E, F→G).
        assert graph.node_count == 7
        assert graph.edge_count == 6
        # Validate.
        result = graph.validate()
        assert result.ok
        # Parallel batches:
        # Batch 0: A, F (no deps)
        # Batch 1: B (dep=A), G (dep=F)
        # Batch 2: C, D (dep=B)
        # Batch 3: E (dep=C, D)
        batches = graph.parallel_batches()
        assert len(batches) == 4
        # The first batch should contain A and F (no deps).
        assert set(batches[0].step_ids) == {a.id, f.id}
        # The second batch should contain B and G (parallel chains).
        assert set(batches[1].step_ids) == {b.id, g.id}
        # The third batch should contain C and D.
        assert set(batches[2].step_ids) == {c.id, d.id}
        # The last batch should contain only E (it has the longest
        # dep chain).
        assert batches[3].step_ids == [e.id]
        # Topological order should be valid (no dep appears after dependent).
        order = graph.topological_order()
        for i, sid in enumerate(order):
            for dep in graph._reverse.get(sid, []):
                if dep in order:
                    assert order.index(dep) < i, (
                        f"Dependency {dep} appears after dependent {sid}"
                    )


# ===========================================================================
# Smoke tests for exception hierarchy
# ===========================================================================


class TestSprint35Exceptions:
    """Sprint 3.5 — New exception classes."""

    def test_decomposition_error(self) -> None:
        err = DecompositionError("my goal", "no parse")
        assert err.goal == "my goal"
        assert err.reason == "no parse"
        assert "my goal" in str(err)

    def test_strategy_error(self) -> None:
        err = StrategyError("bad mode", step_id="s1")
        assert err.reason == "bad mode"
        assert err.step_id == "s1"
        assert "s1" in str(err)

    def test_graph_error(self) -> None:
        err = GraphError("missing node", node_id="x")
        assert err.reason == "missing node"
        assert err.node_id == "x"

    def test_planning_memory_error(self) -> None:
        err = PlanningMemoryError("store", "disk full")
        assert err.operation == "store"
        assert err.reason == "disk full"

    def test_escalation_error(self) -> None:
        err = EscalationError("s1", "no agent")
        assert err.step_id == "s1"
        assert err.reason == "no agent"

    def test_all_inherit_from_planner_exception(self) -> None:
        """All Sprint 3.5 exceptions should inherit from PlannerException."""
        from planner.exceptions import PlannerException

        for exc_class in (
            DecompositionError, StrategyError, GraphError,
            PlanningMemoryError, EscalationError,
        ):
            assert issubclass(exc_class, PlannerException), (
                f"{exc_class.__name__} should inherit from PlannerException"
            )
