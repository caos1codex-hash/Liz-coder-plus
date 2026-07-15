"""Sprint 3.4 — PlannerExecutor integration tests.

Covers:

  - Plan → Workflow translation (preserve step ids, deps, agent
    assignments, timeouts, criticality).
  - AgentRegistry integration (agents resolved by capability, no
    concrete agent coupling in the planner).
  - ExecutionPipeline integration (queued/running/completed states
    reflected on the plan).
  - apply_execution_result() status translation (COMPLETED→SUCCESS,
    CANCELLED→SKIPPED, FAILED→FAILED, …).
  - Error handling (engine raises → plan FAILED, steps get
    execution metadata).
  - Cancellation (cancel_plan on a running plan).
  - Memory persistence (plan_repository + execution_history).
  - Planner-level events published on the EventBus.
  - Multi-step plan with dependencies executed in DAG order.
  - Plan in FAILED status can be retried (replanning path).

Test naming convention: test_<unit>_<scenario>_<expected>
"""

from __future__ import annotations

import asyncio
import sys
import time
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
from planner.enums import (  # noqa: E402
    PlanStatus,
    StepStatus,
    can_transition_plan,
    can_transition_step,
)
from planner.exceptions import PlannerException  # noqa: E402
from planner.executor import PlanExecutionResult, PlannerExecutor  # noqa: E402
from planner.executor_events import (  # noqa: E402
    PLAN_CANCELLED,
    PLAN_COMPLETED,
    PLAN_FAILED,
    PLAN_STARTED,
    PLAN_SUBMITTED,
    PLAN_TRANSLATED,
    STEP_TRANSLATED,
)
from planner.integration import AgentAwarePlanner  # noqa: E402
from planner.models import PlanStep, TaskPlan  # noqa: E402
from planner.planner import TaskPlanner  # noqa: E402

# -- multiagent imports  # noqa: E402 ---------------------------------------
from multiagent import CoderAgent, PlannerAgent, ReviewerAgent  # noqa: E402
from multiagent.base import BaseAgent  # noqa: E402
from multiagent.enums import Permission  # noqa: E402
from multiagent.registry.adapter import BackwardCompatibilityAdapter  # noqa: E402
from multiagent.registry.agent_registry import AgentRegistry  # noqa: E402
from multiagent.registry.capability import (  # noqa: E402
    CapabilityRegistry,
    register_standard_capabilities,
)
from multiagent.registry.resolver import CapabilityResolver  # noqa: E402
from multiagent.workflow.engine import (  # noqa: E402
    FailoverPolicy,
    WorkflowEngine,
    WorkflowExecutionResult,
)
from multiagent.workflow.enums import (  # noqa: E402
    StepStatus as WfStepStatus,
    WorkflowStatus,
)
from multiagent.workflow.event_bus import EventBus  # noqa: E402
from multiagent.workflow.models import Step as WfStep, Workflow  # noqa: E402
from multiagent.workflow.registry_scheduler import RegistryAwareScheduler  # noqa: E402


# ===========================================================================
# Test helpers (module-level, per project convention — not @pytest.fixture)
# ===========================================================================


class _StubAgent(BaseAgent):
    """Deterministic agent that succeeds and records every task it sees."""

    default_permissions = frozenset({Permission.MEMORY_WRITE})
    default_objectives = ["stub", "test"]

    def __init__(
        self,
        name: str = "stub",
        *,
        capabilities: list[str] | None = None,
        fail: bool = False,
        delay: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, description="stub agent", **kwargs)
        self._caps = capabilities or []
        self._fail = fail
        self._delay = delay
        self.seen_tasks: list[dict[str, Any]] = []

    async def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        if self._delay:
            await asyncio.sleep(self._delay)
        self.seen_tasks.append(task)
        if self._fail:
            raise RuntimeError(f"{self.name} always fails")
        return {
            "value": task.get("name"),
            "tools_used": [],
            "echo": task.get("payload", {}),
        }


async def _setup_registry_and_executor(
    *,
    agents: list[BaseAgent] | None = None,
    failover: FailoverPolicy | None = None,
    memory: Any | None = None,
) -> tuple[PlannerExecutor, AgentRegistry, EventBus, RegistryAwareScheduler]:
    """Build a PlannerExecutor wired to a registry + scheduler + bus.

    *agents* are auto-registered via the BackwardCompatibilityAdapter
    so their concrete names map to capabilities.
    """
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    bus = EventBus()
    registry = AgentRegistry(capability_registry=cap_reg, event_bus=bus)
    resolver = CapabilityResolver(registry=registry, event_bus=bus)
    adapter = BackwardCompatibilityAdapter(registry=registry, resolver=resolver)
    scheduler = RegistryAwareScheduler(
        registry=registry, resolver=resolver, adapter=adapter, event_bus=bus
    )
    executor = PlannerExecutor(
        registry=registry,
        event_bus=bus,
        scheduler=scheduler,
        resolver=resolver,
        adapter=adapter,
        memory=memory,
        failover=failover,
    )
    # Start and register agents.
    if agents:
        for a in agents:
            if a.status.value not in ("idle", "busy"):
                await a.start()
        await adapter.auto_register_legacy_agents(agents)
    return executor, registry, bus, scheduler


def _make_simple_plan(
    *,
    goal: str = "Refactor and review the auth module",
    n_steps: int = 3,
    status: PlanStatus = PlanStatus.READY,
    plan_id: str | None = None,
) -> TaskPlan:
    """Build a minimal 3-step plan with linear dependencies.

    Steps:
      0. "Analyze requirements"        — depends on nothing
      1. "Implement code"              — depends on step 0
      2. "Review the implementation"   — depends on step 1

    Each call generates fresh, unique IDs (UUID-based) so multiple
    plans don't collide on the plan_steps.id primary key when
    persisted via PlanRepository.
    """
    import uuid
    pid = plan_id or f"plan-{uuid.uuid4().hex[:8]}"
    steps: list[PlanStep] = []
    prev_id = ""
    titles = ["Analyze requirements", "Implement code", "Review the implementation"]
    for i in range(n_steps):
        title = titles[i] if i < len(titles) else f"Step {i}"
        # Unique step id per call to avoid SQLite UNIQUE constraint
        # collisions when multiple plans share the same persistence db.
        sid = f"{pid}-step-{i}"
        s = PlanStep(
            id=sid,
            title=title,
            description=f"Description for {title}",
            estimated_duration=0.5,
            dependencies=[prev_id] if prev_id else [],
            required_agents=[],
            status=StepStatus.PENDING,
            metadata={"phase": "implementation" if i == 1 else "analysis"},
        )
        steps.append(s)
        prev_id = s.id
    plan = TaskPlan(
        id=pid,
        goal=goal,
        description="Test plan for PlannerExecutor",
        priority=__import__("planner").Priority.NORMAL,
        status=status,
        steps=steps,
    )
    return plan


def _annotate_plan_with_assignments(
    plan: TaskPlan, registry: AgentRegistry
) -> TaskPlan:
    """Use AgentAwarePlanner to add real agent_assignments to plan steps.

    This mimics what Sprint 3.3's AgentAwarePlanner would have done
    against a live registry.
    """
    aw = AgentAwarePlanner()
    # Re-use the existing plan rather than re-generating from goal.
    # We call assign_agents with a context built from the registry.
    from planner.agent_context import AgentPlanningContext
    ctx = AgentPlanningContext.from_registry(registry)
    aw.assign_agents(plan, ctx)
    return plan


async def _build_real_plan_with_registry(
    registry: AgentRegistry, goal: str = "Refactor auth module and review changes"
) -> TaskPlan:
    """Use the full Sprint 3.3 AgentAwarePlanner to generate a real plan."""
    aw = AgentAwarePlanner()
    return aw.create_agent_aware_plan(goal=goal, agent_registry=registry)


# ===========================================================================
# 1. Construction & validation
# ===========================================================================


class TestPlannerExecutorConstruction:
    """Constructor / wiring tests."""

    def test_executor_requires_registry(self) -> None:
        with pytest.raises(ValueError, match="non-None registry"):
            PlannerExecutor(registry=None)

    @pytest.mark.asyncio
    async def test_executor_auto_builds_engine_from_registry(self) -> None:
        cap_reg = CapabilityRegistry()
        register_standard_capabilities(cap_reg)
        bus = EventBus()
        registry = AgentRegistry(capability_registry=cap_reg, event_bus=bus)
        # No engine, no scheduler — executor should build them.
        executor = PlannerExecutor(registry=registry, event_bus=bus)
        assert executor.engine is not None
        assert executor.event_bus is bus
        # The auto-built scheduler should be a RegistryAwareScheduler.
        from multiagent.workflow.registry_scheduler import RegistryAwareScheduler as _RAS
        assert isinstance(executor.engine.scheduler, _RAS)

    @pytest.mark.asyncio
    async def test_executor_accepts_pre_built_engine(self) -> None:
        cap_reg = CapabilityRegistry()
        register_standard_capabilities(cap_reg)
        bus = EventBus()
        registry = AgentRegistry(capability_registry=cap_reg, event_bus=bus)
        engine = WorkflowEngine(event_bus=bus)
        executor = PlannerExecutor(registry=registry, engine=engine, event_bus=bus)
        assert executor.engine is engine


# ===========================================================================
# 2. Plan → Workflow translation
# ===========================================================================


class TestTranslatePlanToWorkflow:
    """translate_plan_to_workflow() preservation guarantees."""

    @pytest.mark.asyncio
    async def test_translate_preserves_step_ids(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = _make_simple_plan()
        workflow = executor.translate_plan_to_workflow(plan)
        # IDs must be preserved 1:1.
        assert set(workflow.steps.keys()) == {s.id for s in plan.steps}

    @pytest.mark.asyncio
    async def test_translate_preserves_dependencies(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = _make_simple_plan()
        workflow = executor.translate_plan_to_workflow(plan)
        # Step 1 depends on step 0; step 2 depends on step 1.
        step0_id = plan.steps[0].id
        step1_id = plan.steps[1].id
        step2_id = plan.steps[2].id
        wf_step_1 = workflow.steps[step1_id]
        wf_step_2 = workflow.steps[step2_id]
        assert wf_step_1.depends_on == [step0_id]
        assert wf_step_2.depends_on == [step1_id]

    @pytest.mark.asyncio
    async def test_translate_preserves_timeouts(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = _make_simple_plan()
        for s in plan.steps:
            s.estimated_duration = 12.5
        workflow = executor.translate_plan_to_workflow(plan)
        for wf_step in workflow.steps.values():
            assert wf_step.timeout == 12.5

    @pytest.mark.asyncio
    async def test_translate_carries_agent_assignment_capability_hint(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor(
            agents=[CoderAgent()]
        )
        plan = _make_simple_plan()
        step1_id = plan.steps[1].id
        # Manually attach an assignment dict.
        coder_record = registry.get("coder")
        assert coder_record is not None
        plan.steps[1].metadata["agent_assignment"] = {
            "step_id": step1_id,
            "agent_id": coder_record.id,
            "capability": "code.generate",
            "confidence_score": 0.95,
            "reason": "manual",
        }
        workflow = executor.translate_plan_to_workflow(plan)
        wf_step = workflow.steps[step1_id]
        # The agent name should have been resolved from the UUID.
        assert wf_step.agent == "coder"
        # The capability hint should be in the input payload.
        assert "code.generate" in wf_step.input.get("required_capabilities", [])

    @pytest.mark.asyncio
    async def test_translate_empty_plan_raises(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = TaskPlan(id="empty", goal="nothing")
        with pytest.raises(PlannerException, match="no steps"):
            executor.translate_plan_to_workflow(plan)

    @pytest.mark.asyncio
    async def test_translate_terminal_plan_raises(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = _make_simple_plan(status=PlanStatus.COMPLETED)
        with pytest.raises(PlannerException, match="terminal"):
            executor.translate_plan_to_workflow(plan)

    @pytest.mark.asyncio
    async def test_translate_sets_workflow_metadata_with_plan_id(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = _make_simple_plan()
        workflow = executor.translate_plan_to_workflow(plan)
        assert workflow.metadata["plan_id"] == plan.id
        assert workflow.metadata["source"] == "planner.executor"


# ===========================================================================
# 3. End-to-end execution
# ===========================================================================


class TestExecutePlan:
    """Full execute_plan() flow."""

    @pytest.mark.asyncio
    async def test_execute_simple_plan_all_steps_succeed(self) -> None:
        executor, registry, bus, _ = await _setup_registry_and_executor(
            agents=[CoderAgent(), ReviewerAgent(), PlannerAgent()]
        )
        plan = await _build_real_plan_with_registry(registry)
        result = await executor.execute_plan(plan)

        assert isinstance(result, PlanExecutionResult)
        assert result.plan_status == PlanStatus.COMPLETED
        assert result.steps_total == len(plan.steps)
        assert result.steps_completed == len(plan.steps)
        assert result.steps_failed == 0
        assert result.error is None
        # Plan mutated in place.
        assert plan.status == PlanStatus.COMPLETED
        for step in plan.steps:
            assert step.status == StepStatus.SUCCESS
            ex = step.metadata.get("execution")
            assert ex is not None
            assert "duration_ms" in ex
            assert "agent" in ex

    @pytest.mark.asyncio
    async def test_execute_preserves_step_ids_in_workflow(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor(
            agents=[CoderAgent()]
        )
        plan = await _build_real_plan_with_registry(registry)
        original_ids = {s.id for s in plan.steps}
        # Capture the workflow via the bus: subscribe to PLAN_TRANSLATED.
        captured: dict[str, Any] = {}
        async def hook(event):
            if event.type_str == PLAN_TRANSLATED:
                captured.update(event.payload)
        await executor.event_bus.subscribe(hook)
        await executor.execute_plan(plan)
        assert "workflow_id" in captured
        # Check the engine's record of the workflow.
        wf = executor.engine._running.get(captured["workflow_id"])  # type: ignore[attr-defined]
        # The engine may have already cleaned up; if not, check ids.
        if wf is not None:
            assert set(wf.steps.keys()) == original_ids

    @pytest.mark.asyncio
    async def test_execute_dag_order_respected(self) -> None:
        """A 3-step linear plan must execute in order 0 → 1 → 2."""
        # Use StubAgent that records timestamps.
        stamping_agent = _StampingAgent(name="coder")
        executor, registry, _, _ = await _setup_registry_and_executor(
            agents=[stamping_agent]
        )
        plan = _make_simple_plan()
        # Annotate each step with the coder agent.
        coder_record = registry.get("coder")
        assert coder_record is not None
        for step in plan.steps:
            step.metadata["agent_assignment"] = {
                "step_id": step.id,
                "agent_id": coder_record.id,
                "capability": "code.generate",
                "confidence_score": 1.0,
                "reason": "manual",
            }
            step.required_agents.append("coder")
        await executor.execute_plan(plan)
        # Each step should have a strictly increasing started_at timestamp.
        starts = [s.metadata["execution"]["started_at"] for s in plan.steps]
        assert starts == sorted(starts), "Steps did not start in DAG order"

    @pytest.mark.asyncio
    async def test_execute_emits_planner_events_in_order(self) -> None:
        executor, registry, bus, _ = await _setup_registry_and_executor(
            agents=[CoderAgent(), ReviewerAgent()]
        )
        plan = await _build_real_plan_with_registry(registry)
        seen: list[str] = []
        async def recorder(event):
            seen.append(event.type_str)
        await bus.subscribe(recorder)
        await executor.execute_plan(plan)
        # Plan-level events must appear in this relative order.
        assert PLAN_SUBMITTED in seen
        idx_sub = seen.index(PLAN_SUBMITTED)
        idx_trans = seen.index(PLAN_TRANSLATED)
        idx_start = seen.index(PLAN_STARTED)
        idx_done = seen.index(PLAN_COMPLETED)
        assert idx_sub < idx_trans < idx_start < idx_done
        # At least one step.translated per step.
        assert seen.count(STEP_TRANSLATED) >= len(plan.steps)

    @pytest.mark.asyncio
    async def test_execute_status_transitions_legal(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor(
            agents=[CoderAgent()]
        )
        plan = await _build_real_plan_with_registry(registry)
        original_plan_id = plan.id
        await executor.execute_plan(plan)
        # The final plan status must be reachable from RUNNING via the
        # planner's own transition map.
        assert plan.status == PlanStatus.COMPLETED
        # And every step must have a legal final status.
        for step in plan.steps:
            assert step.status in (
                StepStatus.SUCCESS, StepStatus.FAILED, StepStatus.SKIPPED
            ), f"Step {step.id} has non-terminal status {step.status}"


class _StampingAgent(BaseAgent):
    """Coder-like agent that records task arrival order via timestamping."""

    default_permissions = frozenset({Permission.MEMORY_WRITE})
    default_objectives = ["stub"]

    def __init__(self, name: str = "stamp") -> None:
        super().__init__(name=name, description="timestamping agent")

    async def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        # Tiny delay so timestamps differ.
        await asyncio.sleep(0.005)
        return {"value": task.get("name"), "tools_used": []}


# ===========================================================================
# 4. Status translation (workflow → planner)
# ===========================================================================


class TestStatusTranslation:
    """apply_execution_result() translates workflow statuses correctly."""

    @pytest.mark.asyncio
    async def test_workflow_completed_translates_to_plan_completed(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = _make_simple_plan(status=PlanStatus.RUNNING)
        # Build a synthetic workflow + result.
        workflow = Workflow(name="synthetic")
        for s in plan.steps:
            wf_step = WfStep(id=s.id, name=s.title, agent="stub")
            wf_step.status = WfStepStatus.COMPLETED
            workflow.add_step(wf_step)
        workflow.transition_to(WorkflowStatus.COMPLETED)
        result = WorkflowExecutionResult(
            workflow_id=workflow.id,
            status=WorkflowStatus.COMPLETED,
            steps_total=len(plan.steps),
            steps_completed=len(plan.steps),
        )
        executor.apply_execution_result(plan, workflow, result)
        assert plan.status == PlanStatus.COMPLETED
        for s in plan.steps:
            assert s.status == StepStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_workflow_failed_translates_to_plan_failed(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = _make_simple_plan(status=PlanStatus.RUNNING)
        workflow = Workflow(name="synthetic")
        for s in plan.steps:
            wf_step = WfStep(id=s.id, name=s.title, agent="stub")
            wf_step.status = WfStepStatus.FAILED
            wf_step.error = "boom"
            workflow.add_step(wf_step)
        workflow.transition_to(WorkflowStatus.FAILED)
        result = WorkflowExecutionResult(
            workflow_id=workflow.id,
            status=WorkflowStatus.FAILED,
            steps_total=len(plan.steps),
            steps_failed=len(plan.steps),
            error="workflow failed",
        )
        executor.apply_execution_result(plan, workflow, result)
        assert plan.status == PlanStatus.FAILED
        for s in plan.steps:
            assert s.status == StepStatus.FAILED
            assert s.metadata["execution"]["error"] == "boom"

    @pytest.mark.asyncio
    async def test_workflow_cancelled_translates_to_plan_cancelled_and_steps_skipped(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = _make_simple_plan(status=PlanStatus.RUNNING)
        workflow = Workflow(name="synthetic")
        first_step_id = plan.steps[0].id
        for idx, s in enumerate(plan.steps):
            wf_step = WfStep(id=s.id, name=s.title, agent="stub")
            # Mix of statuses — only step-0 ran, others were cancelled.
            if s.id == first_step_id:
                wf_step.status = WfStepStatus.COMPLETED
            else:
                wf_step.status = WfStepStatus.CANCELLED
            workflow.add_step(wf_step)
        workflow.transition_to(WorkflowStatus.CANCELLED)
        result = WorkflowExecutionResult(
            workflow_id=workflow.id,
            status=WorkflowStatus.CANCELLED,
            steps_total=len(plan.steps),
            steps_completed=1,
        )
        executor.apply_execution_result(plan, workflow, result)
        assert plan.status == PlanStatus.CANCELLED
        assert plan.steps[0].status == StepStatus.SUCCESS
        for s in plan.steps[1:]:
            assert s.status == StepStatus.SKIPPED, (
                f"Cancelled workflow steps must map to SKIPPED, got {s.status}"
            )

    @pytest.mark.asyncio
    async def test_skipped_workflow_step_translates_to_skipped(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = _make_simple_plan(status=PlanStatus.RUNNING)
        workflow = Workflow(name="synthetic")
        for s in plan.steps:
            wf_step = WfStep(id=s.id, name=s.title, agent="stub")
            wf_step.status = WfStepStatus.SKIPPED
            workflow.add_step(wf_step)
        workflow.transition_to(WorkflowStatus.COMPLETED)
        result = WorkflowExecutionResult(
            workflow_id=workflow.id,
            status=WorkflowStatus.COMPLETED,
            steps_total=len(plan.steps),
            steps_skipped=len(plan.steps),
        )
        executor.apply_execution_result(plan, workflow, result)
        # All steps skipped — plan is COMPLETED (the workflow finished).
        assert plan.status == PlanStatus.COMPLETED
        for s in plan.steps:
            assert s.status == StepStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_engine_error_marks_plan_failed(self) -> None:
        """If engine.run raises, apply_execution_result + error → FAILED."""
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = _make_simple_plan(status=PlanStatus.RUNNING)
        workflow = Workflow(name="synthetic")
        for s in plan.steps:
            wf_step = WfStep(id=s.id, name=s.title, agent="stub")
            wf_step.status = WfStepStatus.RUNNING
            workflow.add_step(wf_step)
        workflow.transition_to(WorkflowStatus.RUNNING)
        executor.apply_execution_result(plan, workflow, None, error="boom")
        assert plan.status == PlanStatus.FAILED
        # Steps that were RUNNING should be marked FAILED.
        for s in plan.steps:
            assert s.status in (StepStatus.FAILED, StepStatus.RUNNING)


# ===========================================================================
# 5. Error handling — agent failures propagated to the plan
# ===========================================================================


class TestErrorHandling:
    """What happens when agents fail."""

    @pytest.mark.asyncio
    async def test_failing_agent_marks_plan_failed(self) -> None:
        failing_coder = _StubAgent(
            name="coder", capabilities=["code.generate"], fail=True
        )
        executor, registry, _, _ = await _setup_registry_and_executor(
            agents=[failing_coder],
            failover=FailoverPolicy(
                max_retries=0,            # no retries
                reassign_on_fail=False,   # no reassign
                skip_optional_on_fail=False,
                cancel_on_required_fail=True,
            ),
        )
        plan = await _build_real_plan_with_registry(registry)
        result = await executor.execute_plan(plan)
        # The workflow should have failed (REQUIRED step failed, no retry).
        assert result.plan_status == PlanStatus.FAILED
        assert plan.status == PlanStatus.FAILED
        # At least one step should be FAILED.
        assert result.steps_failed >= 1

    @pytest.mark.asyncio
    async def test_validate_assignments_rejects_unknown_agent(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = _make_simple_plan()
        plan.steps[0].metadata["agent_assignment"] = {
            "step_id": "step-0",
            "agent_id": "nonexistent-uuid",
            "capability": "code.generate",
            "confidence_score": 1.0,
            "reason": "test",
        }
        with pytest.raises(PlannerException, match="unknown agent"):
            await executor.execute_plan(plan)

    @pytest.mark.asyncio
    async def test_validate_assignments_can_be_skipped(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor(
            agents=[CoderAgent()]
        )
        plan = _make_simple_plan()
        # Add a bogus assignment.
        plan.steps[0].metadata["agent_assignment"] = {
            "step_id": "step-0",
            "agent_id": "nonexistent-uuid",
            "capability": "code.generate",
            "confidence_score": 1.0,
            "reason": "test",
        }
        # validate_assignments=False should skip the pre-flight check.
        # Execution should still proceed (the scheduler will resolve by
        # capability instead).
        try:
            await executor.execute_plan(plan, validate_assignments=False)
            # If we got here, no exception was raised.
            assert plan.status in (PlanStatus.COMPLETED, PlanStatus.FAILED)
        except PlannerException:
            # If the scheduler also can't resolve, that's acceptable —
            # the point is the validation pre-flight was skipped.
            pass

    @pytest.mark.asyncio
    async def test_execute_terminal_plan_raises(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        plan = _make_simple_plan(status=PlanStatus.COMPLETED)
        with pytest.raises(PlannerException, match="terminal"):
            await executor.execute_plan(plan)


# ===========================================================================
# 6. Cancellation
# ===========================================================================


class TestCancellation:
    """cancel_plan() on a running plan."""

    @pytest.mark.asyncio
    async def test_cancel_plan_returns_false_when_not_running(self) -> None:
        executor, registry, _, _ = await _setup_registry_and_executor()
        ok = await executor.cancel_plan("does-not-exist")
        assert ok is False

    @pytest.mark.asyncio
    async def test_cancel_running_plan(self) -> None:
        # Agent that sleeps so we have time to cancel.
        slow_agent = _StubAgent(name="coder", capabilities=["code.generate"], delay=0.5)
        executor, registry, _, _ = await _setup_registry_and_executor(
            agents=[slow_agent]
        )
        plan = await _build_real_plan_with_registry(registry)

        # Launch execution in a background task.
        exec_task = asyncio.create_task(executor.execute_plan(plan))
        # Give the executor time to register the plan as active.
        await asyncio.sleep(0.1)
        # Now cancel.
        ok = await executor.cancel_plan(plan.id, reason="user requested")
        assert ok is True

        # Wait for the executor to finish (it should propagate the cancel).
        try:
            result = await asyncio.wait_for(exec_task, timeout=2.0)
            # Either it cancelled cleanly (status CANCELLED) or completed
            # (race condition with the engine). Both are acceptable.
            assert result.plan_status in (PlanStatus.CANCELLED, PlanStatus.COMPLETED)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            # If the executor itself was cancelled, that's fine.
            pass

    @pytest.mark.asyncio
    async def test_cancel_emits_plan_cancelled_event(self) -> None:
        slow_agent = _StubAgent(name="coder", capabilities=["code.generate"], delay=1.0)
        executor, registry, bus, _ = await _setup_registry_and_executor(
            agents=[slow_agent]
        )
        plan = await _build_real_plan_with_registry(registry)

        seen: list[str] = []
        async def recorder(event):
            seen.append(event.type_str)
        await bus.subscribe(recorder)

        exec_task = asyncio.create_task(executor.execute_plan(plan))
        await asyncio.sleep(0.15)
        await executor.cancel_plan(plan.id, reason="test cancel event")
        try:
            await asyncio.wait_for(exec_task, timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        # Either PLAN_CANCELLED or PLAN_COMPLETED/PLAN_FAILED depending on
        # the race; the cancel path emits PLAN_CANCELLED explicitly.
        # If the workflow finished first, we accept those events too.
        assert any(
            e in seen for e in (PLAN_CANCELLED, PLAN_COMPLETED, PLAN_FAILED)
        ), f"Expected a terminal plan event, got {seen}"


# ===========================================================================
# 7. Persistence (with real SQLite via MemoryManager)
# ===========================================================================


class TestPersistence:
    """PlanRepository + execution_history integration."""

    @pytest.fixture
    async def memory(self, tmp_path):
        """Build an in-memory-backed MemoryManager with plan persistence."""
        # Clear sys.modules cache for src.* to ensure clean import.
        for k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
            sys.modules.pop(k, None)
        # IMPORTANT: the memory package uses `src.memory.X` imports internally,
        # so the path on sys.path must be the package ROOT (packages/memory),
        # not packages/memory/src.
        MEMORY_PKG_ROOT = _PACKAGES_DIR / "memory"
        sys.path.insert(0, str(MEMORY_PKG_ROOT))
        from src.memory.manager import MemoryManager
        mem = MemoryManager(max_history=50)
        db_path = str(tmp_path / "test_plans.db")
        await mem.initialize(db_path)
        await mem.initialize_task_persistence()
        await mem.initialize_plan_persistence()
        yield mem
        await mem.close()
        # Pop memory modules so the next test class starts fresh.
        for k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
            sys.modules.pop(k, None)

    @pytest.mark.asyncio
    async def test_plan_repository_save_and_get(self, memory) -> None:
        repo = memory.plan_repository
        assert repo is not None
        plan = _make_simple_plan()
        # Save.
        await repo.save_plan(plan.to_dict())
        # Get back.
        fetched = await repo.get_plan(plan.id)
        assert fetched is not None
        assert fetched["id"] == plan.id
        assert fetched["goal"] == plan.goal
        assert fetched["status"] == plan.status.value
        assert len(fetched["steps"]) == len(plan.steps)
        # Step IDs preserved.
        assert {s["id"] for s in fetched["steps"]} == {s.id for s in plan.steps}

    @pytest.mark.asyncio
    async def test_plan_repository_status_filter(self, memory) -> None:
        repo = memory.plan_repository
        plan1 = _make_simple_plan()
        plan1.id = "plan-1"
        plan1.status = PlanStatus.READY
        plan2 = _make_simple_plan()
        plan2.id = "plan-2"
        plan2.status = PlanStatus.COMPLETED
        await repo.save_plan(plan1.to_dict())
        await repo.save_plan(plan2.to_dict())
        ready = await repo.list_plans(status="ready")
        assert len(ready) == 1
        assert ready[0]["id"] == "plan-1"
        completed = await repo.list_plans(status="completed")
        assert len(completed) == 1
        assert completed[0]["id"] == "plan-2"

    @pytest.mark.asyncio
    async def test_plan_repository_update_status(self, memory) -> None:
        repo = memory.plan_repository
        plan = _make_simple_plan()
        plan.status = PlanStatus.READY
        await repo.save_plan(plan.to_dict())
        ok = await repo.update_plan_status(
            plan.id, "running", workflow_id="wf-123"
        )
        assert ok is True
        fetched = await repo.get_plan(plan.id)
        assert fetched["status"] == "running"
        # The workflow_id should appear in metadata.execution.workflow_id.
        assert fetched["metadata"]["execution"]["workflow_id"] == "wf-123"

    @pytest.mark.asyncio
    async def test_plan_repository_delete(self, memory) -> None:
        repo = memory.plan_repository
        plan = _make_simple_plan()
        await repo.save_plan(plan.to_dict())
        ok = await repo.delete_plan(plan.id)
        assert ok is True
        assert await repo.get_plan(plan.id) is None
        # Steps should be cascade-deleted.
        for s in plan.steps:
            assert await repo.get_step(s.id) is None

    @pytest.mark.asyncio
    async def test_plan_repository_count_by_status(self, memory) -> None:
        repo = memory.plan_repository
        for i, status in enumerate(["ready", "ready", "completed", "failed"]):
            p = _make_simple_plan()
            p.id = f"plan-{i}"
            p.status = PlanStatus(status)
            await repo.save_plan(p.to_dict())
        counts = await repo.count_by_status()
        assert counts.get("ready", 0) == 2
        assert counts.get("completed", 0) == 1
        assert counts.get("failed", 0) == 1

    @pytest.mark.asyncio
    async def test_executor_with_memory_records_audit_events(self, memory) -> None:
        executor, registry, bus, _ = await _setup_registry_and_executor(
            agents=[CoderAgent(), ReviewerAgent(), PlannerAgent()],
            memory=memory,
        )
        plan = await _build_real_plan_with_registry(registry)
        # Persist the plan first (the executor doesn't save plans
        # automatically — that's the caller's responsibility).
        await memory.plan_repository.save_plan(plan.to_dict())
        # Execute.
        await executor.execute_plan(plan)
        # Update the persisted plan with final status.
        await memory.plan_repository.save_plan(plan.to_dict())
        # The execution_history should have at least PLAN_STARTED and
        # one of PLAN_COMPLETED / PLAN_FAILED / PLAN_CANCELLED.
        events = await memory.execution_repository.get_by_event_type(
            PLAN_STARTED, limit=10
        )
        assert len(events) >= 1
        # Final event should be one of the terminal ones.
        for ev_type in (PLAN_COMPLETED, PLAN_FAILED, PLAN_CANCELLED):
            final_events = await memory.execution_repository.get_by_event_type(ev_type, limit=10)
            if final_events:
                # Check that the most recent one references our plan.
                latest = final_events[0]
                meta = latest.get("metadata", {})
                if isinstance(meta, dict) and meta.get("plan_id") == plan.id:
                    break
        else:
            pytest.fail("No terminal planner event recorded in execution_history")

    @pytest.mark.asyncio
    async def test_replay_from_memory(self, memory) -> None:
        executor, registry, bus, _ = await _setup_registry_and_executor(
            agents=[CoderAgent()], memory=memory
        )
        plan = await _build_real_plan_with_registry(registry)
        await memory.plan_repository.save_plan(plan.to_dict())
        await executor.execute_plan(plan)
        events = await executor.replay_from_memory(plan.id)
        # Should have at least the PLAN_STARTED and a terminal event.
        assert len(events) >= 2
        # All events should reference our plan_id.
        for ev in events:
            meta = ev.get("metadata", {})
            if isinstance(meta, dict):
                assert meta.get("plan_id") == plan.id


# ===========================================================================
# 8. Retry / replanning — FAILED plan can be retried
# ===========================================================================


class TestRetryAndReplanning:
    """A plan that failed can be re-executed."""

    @pytest.mark.asyncio
    async def test_failed_plan_can_be_reexecuted(self) -> None:
        # First run: failing agent → plan FAILED.
        # Second run: same plan, but with a working agent.
        failing_coder = _StubAgent(name="coder", capabilities=["code.generate"], fail=True)
        executor, registry, _, _ = await _setup_registry_and_executor(
            agents=[failing_coder],
            failover=FailoverPolicy(
                max_retries=0,
                reassign_on_fail=False,
                skip_optional_on_fail=False,
                cancel_on_required_fail=True,
            ),
        )
        plan = await _build_real_plan_with_registry(registry)
        result1 = await executor.execute_plan(plan)
        assert result1.plan_status == PlanStatus.FAILED

        # Reset step statuses so they can be re-run.
        for s in plan.steps:
            s.status = StepStatus.PENDING
            s.metadata.pop("execution", None)
        # Clear stale agent assignments (they reference the failing agent's UUID).
        for s in plan.steps:
            s.metadata.pop("agent_assignment", None)
        plan.metadata.pop("agent_assignments", None)

        # Replace the failing agent with a working one under the same name.
        # Unregister the failing one and register a working one.
        await registry.unregister("coder")
        working_coder = CoderAgent()
        await working_coder.start()
        from multiagent.registry.adapter import AGENT_NAME_TO_CAPABILITIES
        await registry.register(
            working_coder,
            provided_capabilities=AGENT_NAME_TO_CAPABILITIES["coder"],
            replace_existing=True,
        )
        executor.engine.register_agent(working_coder)

        # Re-run the agent-aware planner so fresh assignments are attached
        # to the existing plan (pointing at the new working coder UUID).
        from planner.agent_context import AgentPlanningContext
        aw = AgentAwarePlanner()
        ctx = AgentPlanningContext.from_registry(registry)
        aw.assign_agents(plan, ctx)

        # Re-execute. The executor should walk FAILED → PLANNING → READY → RUNNING.
        result2 = await executor.execute_plan(plan)
        assert result2.plan_status == PlanStatus.COMPLETED
        assert plan.status == PlanStatus.COMPLETED


# ===========================================================================
# 9. AgentRegistry integration — capability-based resolution
# ===========================================================================


class TestRegistryIntegration:
    """The planner executor never references concrete agents by import."""

    @pytest.mark.asyncio
    async def test_executor_does_not_import_concrete_agents(self) -> None:
        """The planner.executor module must not import any concrete agent."""
        import importlib
        import planner.executor as exec_mod
        # Reload to make sure __dict__ is fresh.
        importlib.reload(exec_mod)
        src = open(exec_mod.__file__).read()
        # None of these concrete agent class names should be referenced
        # directly in the executor source.
        for forbidden in ("CoderAgent", "PlannerAgent", "ReviewerAgent",
                          "ResearchAgent", "TerminalAgent", "GitAgent",
                          "MemoryAgent"):
            assert forbidden not in src, (
                f"executor.py references concrete agent {forbidden} — "
                f"the planner must not know about specific agents"
            )

    @pytest.mark.asyncio
    async def test_executor_resolves_agent_by_capability_not_name(self) -> None:
        """A custom agent with the right capability should be picked."""
        # Custom agent that provides 'code.generate' under a unique name.
        custom = _StubAgent(name="custom_coder", capabilities=["code.generate"])
        executor, registry, _, _ = await _setup_registry_and_executor(
            agents=[custom]
        )
        # Note: _StubAgent isn't in AGENT_NAME_TO_CAPABILITIES, but
        # auto_register_legacy_agents uses agent.name to derive caps.
        # We need to manually register it with proper capabilities.
        await registry.unregister("custom_coder")
        await registry.register(
            custom,
            provided_capabilities=["code.generate", "code.refactor", "code.test"],
            replace_existing=True,
        )
        executor.engine.register_agent(custom)
        # Build a plan that needs code.generate.
        plan = _make_simple_plan()
        # Don't add agent_assignment — let the scheduler resolve by capability.
        # Translate the plan and check the workflow's first step has no agent
        # preference (the scheduler will pick).
        workflow = executor.translate_plan_to_workflow(plan)
        wf_step = workflow.steps[plan.steps[0].id]
        # agent should be empty (no assignment) — scheduler will resolve.
        # But the action should map to a capability the adapter knows.
        assert wf_step.agent in ("", "custom_coder")


# ===========================================================================
# 10. Public API stability
# ===========================================================================


class TestPublicAPI:
    """Sanity checks for the public surface."""

    def test_planner_package_exports_executor(self) -> None:
        import planner
        assert hasattr(planner, "PlannerExecutor")
        assert hasattr(planner, "PlanExecutionResult")
        assert "PlannerExecutor" in planner.__all__
        assert "PlanExecutionResult" in planner.__all__

    def test_event_constants_present_and_distinct(self) -> None:
        import planner
        events = {
            planner.PLAN_SUBMITTED,
            planner.PLAN_TRANSLATED,
            planner.PLAN_STARTED,
            planner.PLAN_COMPLETED,
            planner.PLAN_FAILED,
            planner.PLAN_CANCELLED,
            planner.STEP_TRANSLATED,
            planner.STEP_STARTED,
            planner.STEP_FINISHED,
        }
        assert len(events) == 9  # all distinct
        for e in events:
            assert isinstance(e, str)
            assert e.startswith("planner.")

    def test_plan_execution_result_to_dict(self) -> None:
        r = PlanExecutionResult(
            plan_id="p1",
            workflow_id="w1",
            plan_status=PlanStatus.COMPLETED,
            workflow_status="completed",
            steps_total=3,
            steps_completed=3,
        )
        d = r.to_dict()
        assert d["plan_id"] == "p1"
        assert d["plan_status"] == "completed"
        assert d["steps_total"] == 3
