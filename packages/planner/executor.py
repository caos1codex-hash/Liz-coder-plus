"""Sprint 3.4 — Planner Executor: bridge between Planner and Orchestrator.

This module implements the long-missing integration layer between the
**Planner** package (Sprint 3.1 / 3.2 / 3.3) and the **Multi-Agent
System** (Sprint 2.x).  It turns an annotated :class:`TaskPlan` into a
real :class:`Workflow` that the :class:`WorkflowEngine` runs against
the live agent registry, then writes the execution outcome back onto
the original plan so callers observe the final state.

High-level flow::

    TaskPlan (annotated by AgentAwarePlanner)
        │
        ▼  PlannerExecutor.translate_plan_to_workflow()
    Workflow (multiagent.workflow.models.Workflow)
        │
        ▼  WorkflowEngine.run(workflow)   (uses RegistryAwareScheduler)
    WorkflowExecutionResult
        │
        ▼  PlannerExecutor.apply_execution_result()
    TaskPlan (status + per-step execution metadata mutated in place)
        │
        ▼  (optional) PlannerPersistence
    SQLite (plans + plan_steps + execution_history)

Design principles
-----------------

1. **The planner never imports concrete agents.** It only knows about
   capabilities.  When a ``PlanStep.metadata["agent_assignment"]`` is
   present, its ``agent_id`` (the registry UUID) is resolved back to
   an agent *name* through the live :class:`AgentRegistry`.  The
   actual agent dispatch happens inside the
   :class:`RegistryAwareScheduler`, which double-checks the assignment
   via :class:`CapabilityResolver`.
2. **No direct coupling to the orchestrator.** The executor accepts
   either a pre-built :class:`WorkflowEngine` (caller-owned) or just a
   :class:`AgentRegistry` and constructs one with a
   :class:`RegistryAwareScheduler` on the fly.
3. **Status translation lives in one place.** The planner's
   :class:`StepStatus` uses ``SUCCESS``; the workflow's uses
   ``COMPLETED``.  All translation happens in
   :data:`_WF_STEP_STATUS_TO_PLANNER` and
   :data:`_WF_STATUS_TO_PLAN`, never sprinkled through call-sites.
4. **Transition validation is enforced.** Every status mutation on
   the plan or its steps goes through
   :func:`planner.enums.can_transition_plan` /
   :func:`planner.enums.can_transition_step`.  Illegal transitions are
   logged and skipped (instead of crashing mid-execution), preserving
   the plan object for inspection.
5. **Memory persistence is optional.** Passing ``memory=...`` wires
   per-step events into the
   :class:`~src.memory.repositories.execution_repository.ExecutionRepository`
   audit trail.  Without it, the executor is fully in-memory and
   reversible.
6. **Parallel-ready.** The executor never holds global state; each
   :meth:`execute_plan` call builds its own workflow, runs it, and
   writes back.  Multiple plans can run concurrently on the same
   executor.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .enums import (
    PlanStatus,
    Priority as PlannerPriority,
    StepStatus,
    can_transition_plan,
    can_transition_step,
)
from .exceptions import InvalidStatusError, PlannerException
from .executor_events import (
    PLAN_CANCELLED,
    PLAN_COMPLETED,
    PLAN_FAILED,
    PLAN_STARTED,
    PLAN_SUBMITTED,
    PLAN_TRANSLATED,
    STEP_FINISHED,
    STEP_STARTED,
    STEP_TRANSLATED,
)
from .models import PlanStep, TaskPlan

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cross-package imports (deferred to runtime; multiagent lives in a sibling
# package and may not be importable in some restricted test contexts).
# ---------------------------------------------------------------------------
# We import lazily inside __init__ / methods to keep this module importable
# without multiagent on sys.path.  At runtime, the executor will fail loudly
# if multiagent is missing.


# ---------------------------------------------------------------------------
# Status translation tables
# ---------------------------------------------------------------------------

# Workflow StepStatus (multiagent) → Planner StepStatus.
# The planner has no CANCELLED; the workflow's CANCELLED collapses into
# SKIPPED (the step did not run).
_WF_STEP_STATUS_TO_PLANNER: dict[str, StepStatus] = {
    "pending": StepStatus.PENDING,
    "ready": StepStatus.READY,
    "running": StepStatus.RUNNING,
    "completed": StepStatus.SUCCESS,
    "failed": StepStatus.FAILED,
    "skipped": StepStatus.SKIPPED,
    "cancelled": StepStatus.SKIPPED,
}

# WorkflowStatus → PlanStatus.
_WF_STATUS_TO_PLAN: dict[str, PlanStatus] = {
    "pending": PlanStatus.READY,        # never observed post-run, but be safe
    "running": PlanStatus.RUNNING,
    "paused": PlanStatus.RUNNING,       # planner has no PAUSED
    "completed": PlanStatus.COMPLETED,
    "failed": PlanStatus.FAILED,
    "cancelled": PlanStatus.CANCELLED,
}

# Planner Priority (str Enum) → multiagent Priority (str Enum).
# They share the same string values ("low"/"normal"/"high"/"critical"),
# so direct value passing works.  We use a helper for clarity.


def _planner_priority_to_wf(p: PlannerPriority | str) -> str:
    """Return the multiagent Priority string for a planner priority."""
    if hasattr(p, "value"):
        return p.value  # type: ignore[no-any-return]
    return str(p)


# ---------------------------------------------------------------------------
# Result dataclass (public)
# ---------------------------------------------------------------------------


@dataclass
class PlanExecutionResult:
    """Outcome of :meth:`PlannerExecutor.execute_plan`.

    Wraps the workflow-level :class:`WorkflowExecutionResult` together
    with the (mutated) :class:`TaskPlan` and the underlying workflow
    id, so callers can correlate everything via a single object.

    Attributes:
        plan_id:        The :attr:`TaskPlan.id` that was executed.
        workflow_id:    The :attr:`Workflow.id` that ran it.
        plan_status:    Final :class:`PlanStatus` written back to the plan.
        workflow_status: Final workflow status (str value).
        steps_total:    Total step count.
        steps_completed: Steps that reached SUCCESS.
        steps_failed:   Steps that reached FAILED.
        steps_skipped:  Steps that reached SKIPPED.
        duration_ms:    Wall-clock duration of the executor call.
        workflow_result: The raw :class:`WorkflowExecutionResult` from
                         the engine (also available as
                         ``workflow_result.to_dict()``).
        plan:           The mutated :class:`TaskPlan` (same instance
                        the caller passed in).
        error:          Error message if the plan failed.
    """

    plan_id: str = ""
    workflow_id: str = ""
    plan_status: PlanStatus = PlanStatus.NOT_STARTED
    workflow_status: str = ""
    steps_total: int = 0
    steps_completed: int = 0
    steps_failed: int = 0
    steps_skipped: int = 0
    duration_ms: float = 0.0
    workflow_result: Any = None
    plan: TaskPlan | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        wf_result = self.workflow_result
        wf_dict = wf_result.to_dict() if wf_result is not None and hasattr(wf_result, "to_dict") else None
        return {
            "plan_id": self.plan_id,
            "workflow_id": self.workflow_id,
            "plan_status": self.plan_status.value if hasattr(self.plan_status, "value") else str(self.plan_status),
            "workflow_status": self.workflow_status,
            "steps_total": self.steps_total,
            "steps_completed": self.steps_completed,
            "steps_failed": self.steps_failed,
            "steps_skipped": self.steps_skipped,
            "duration_ms": round(self.duration_ms, 2),
            "workflow_result": wf_dict,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# PlannerExecutor
# ---------------------------------------------------------------------------


class PlannerExecutor:
    """Bridge a :class:`TaskPlan` into a runnable multi-agent workflow.

    The executor is the Sprint 3.4 counterpart to the Sprint 3.3
    :class:`~planner.integration.AgentAwarePlanner`: where the latter
    *annotates* a plan with :class:`AgentAssignment` metadata, this
    class *consumes* that metadata and drives real execution through
    the existing multi-agent stack.

    Args:
        registry:      Live :class:`AgentRegistry`.  Required.
        engine:        Optional pre-configured :class:`WorkflowEngine`.
                       If omitted, one is built with a
                       :class:`RegistryAwareScheduler` so capability
                       resolution is used end-to-end.
        scheduler:     Optional :class:`RegistryAwareScheduler` to use
                       when building the engine.  Ignored if *engine*
                       is supplied.
        resolver:      Optional :class:`CapabilityResolver`.  Used to
                       construct a scheduler if neither *engine* nor
                       *scheduler* are provided.
        adapter:       Optional :class:`BackwardCompatibilityAdapter`.
                       Used when constructing a scheduler.
        event_bus:     Optional :class:`EventBus`.  Required for
                       planner-level events to be observable; if
                       absent, one is created (shared with the engine).
        memory:        Optional :class:`MemoryManager` (already
                       initialized with task persistence) for audit
                       trail persistence.  If ``None``, no SQLite
                       writes happen.
        failover:      Optional :class:`FailoverPolicy` for the engine.
        max_iterations: Cap on the engine's run loop (default 1000).
        auto_register_agents: If ``True`` (default) and *engine* is
                       being built internally, every agent registered
                       in *registry* is mirrored into the engine's
                       own ``register_agent`` map so the engine can
                       dispatch them by name.

    Note:
        The executor never instantiates agents itself.  It assumes the
        caller (or the registry) has already started any required
        agents.  This keeps lifecycle concerns outside the executor
        and matches the pattern used by
        :class:`RegistryOrchestrator`.
    """

    def __init__(
        self,
        *,
        registry: Any,
        engine: Any | None = None,
        scheduler: Any | None = None,
        resolver: Any | None = None,
        adapter: Any | None = None,
        event_bus: Any | None = None,
        memory: Any | None = None,
        failover: Any | None = None,
        max_iterations: int = 1000,
        auto_register_agents: bool = True,
    ) -> None:
        if registry is None:
            raise ValueError("PlannerExecutor requires a non-None registry")

        self._registry = registry
        self._memory = memory
        self._max_iterations = max_iterations

        # Lazy-import multiagent pieces so this module can be imported
        # even when multiagent isn't on sys.path (some planner unit
        # tests don't need it).
        EventBus = _import("multiagent.workflow.event_bus", "EventBus")
        WorkflowEngine = _import("multiagent.workflow.engine", "WorkflowEngine")
        FailoverPolicy = _import("multiagent.workflow.engine", "FailoverPolicy")
        RegistryAwareScheduler = _import(
            "multiagent.workflow.registry_scheduler", "RegistryAwareScheduler"
        )
        CapabilityResolver = _import(
            "multiagent.registry.resolver", "CapabilityResolver"
        )
        BackwardCompatibilityAdapter = _import(
            "multiagent.registry.adapter", "BackwardCompatibilityAdapter"
        )

        # Bus: prefer caller-provided; else reuse the engine's; else new.
        if event_bus is not None:
            self._bus = event_bus
        elif engine is not None:
            self._bus = engine.event_bus
        else:
            self._bus = EventBus()

        # Engine: prefer caller-provided; else build one.
        if engine is not None:
            self._engine = engine
        else:
            if scheduler is not None:
                self._scheduler = scheduler
            else:
                if resolver is None:
                    resolver = CapabilityResolver(
                        registry=registry, event_bus=self._bus
                    )
                if adapter is None:
                    adapter = BackwardCompatibilityAdapter(
                        registry=registry, resolver=resolver
                    )
                self._scheduler = RegistryAwareScheduler(
                    registry=registry,
                    resolver=resolver,
                    adapter=adapter,
                    event_bus=self._bus,
                )
            self._engine = WorkflowEngine(
                event_bus=self._bus,
                scheduler=self._scheduler,
                failover=failover or FailoverPolicy(),
            )

        # Mirror registry agents into the engine so it can dispatch
        # by name.  The engine's scheduler resolves by capability, but
        # the actual ``agent.execute()`` call happens through the
        # engine's internal ``_agents`` dict.
        if auto_register_agents and engine is None:
            self._sync_engine_agents_from_registry()

        # Track in-flight executions so we can support cancel().
        self._active: dict[str, tuple[Any, Any]] = {}  # plan_id -> (workflow, asyncio.Future)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def registry(self) -> Any:
        return self._registry

    @property
    def engine(self) -> Any:
        return self._engine

    @property
    def event_bus(self) -> Any:
        return self._bus

    @property
    def memory(self) -> Any:
        return self._memory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def translate_plan_to_workflow(self, plan: TaskPlan) -> Any:
        """Translate a :class:`TaskPlan` into a multiagent :class:`Workflow`.

        Each :class:`PlanStep` becomes a :class:`Step` (from
        ``multiagent.workflow.models``) preserving:

        - ``step.id`` → ``Step.id`` (so back-mapping is trivial).
        - ``step.title`` → ``Step.name``.
        - ``step.description`` → ``Step.input["description"]`` and
          ``Step.action`` (the action used for capability resolution
          by the BackwardCompatibilityAdapter).
        - ``step.dependencies`` → ``Step.depends_on``.
        - ``step.estimated_duration`` → ``Step.timeout``.
        - ``step.metadata["agent_assignment"]`` → ``Step.agent`` (the
          agent's *name*, looked up by UUID in the registry).  If the
          lookup fails or no assignment exists, ``Step.agent`` is left
          blank so the scheduler can resolve a capability at runtime.
        - ``step.metadata`` → ``Step.input["plan_metadata"]`` and the
          relevant ``required_capabilities`` if present.

        Args:
            plan: The plan to translate.  Must have at least one step.

        Returns:
            A :class:`Workflow` ready for ``engine.run()``.

        Raises:
            PlannerException: If the plan has no steps or is in a
                terminal state.
        """
        if not plan.steps:
            raise PlannerException(
                f"Cannot translate plan '{plan.id}' with no steps"
            )
        if plan.status in (PlanStatus.COMPLETED, PlanStatus.CANCELLED):
            raise PlannerException(
                f"Cannot translate plan '{plan.id}' in terminal status "
                f"{plan.status.value}"
            )

        Step = _import("multiagent.workflow.models", "Step")
        Workflow = _import("multiagent.workflow.models", "Workflow")
        Priority = _import("multiagent.enums", "Priority")

        # Map planner priority value to multiagent Priority enum.
        prio_value = _planner_priority_to_wf(plan.priority)
        try:
            wf_priority = Priority(prio_value)
        except Exception:  # noqa: BLE001
            wf_priority = Priority.NORMAL

        workflow = Workflow(
            name=plan.goal or f"plan-{plan.id}",
            description=plan.description or plan.goal,
            priority=wf_priority,
            owner="planner_executor",
            metadata={
                "plan_id": plan.id,
                "source": "planner.executor",
                "plan_priority": prio_value,
            },
            context={
                "plan_id": plan.id,
                "plan_goal": plan.goal,
            },
        )

        for pstep in plan.steps:
            wf_step = self._translate_step(pstep)
            workflow.add_step(wf_step)

        return workflow

    async def execute_plan(
        self,
        plan: TaskPlan,
        *,
        validate_assignments: bool = True,
    ) -> PlanExecutionResult:
        """Execute a plan end-to-end and mutate it in place with results.

        Pipeline:

        1. Optionally validate that every ``agent_assignment`` in the
           plan still resolves to an available agent in the registry.
        2. Transition the plan ``READY → RUNNING`` (if legal).
        3. Translate the plan to a :class:`Workflow`.
        4. Register any agents present in the registry but missing
           from the engine (defensive; should be a no-op if the
           constructor already mirrored them).
        5. Call ``await self._engine.run(workflow,
           max_iterations=self._max_iterations)``.
        6. Walk the resulting workflow steps, translate each
           ``Step.status`` back to the planner's ``StepStatus``, and
           attach execution metadata (timestamps, durations, agent
           used, errors, retries, …) to the corresponding
           ``PlanStep.metadata["execution"]``.
        7. Translate the workflow's final status to ``PlanStatus`` and
           mutate ``plan.status`` (only if the transition is legal).
        8. Emit planner-level events on the bus.
        9. If a :class:`MemoryManager` is wired up, record audit
           entries in the execution_history table.

        Args:
            plan: The plan to execute.  Will be mutated in place.
            validate_assignments: If ``True`` (default), verify every
                step's agent_assignment resolves to an available agent.
                Failures raise :class:`PlannerException` *before* any
                execution begins.

        Returns:
            A :class:`PlanExecutionResult` summarising the run.

        Raises:
            PlannerException: If validation fails or the plan is in a
                terminal state.
        """
        started = time.monotonic()

        # ----- pre-flight ---------------------------------------------------
        if plan.status in (PlanStatus.COMPLETED, PlanStatus.CANCELLED):
            raise PlannerException(
                f"Cannot execute plan '{plan.id}' in terminal status "
                f"{plan.status.value}"
            )
        if not plan.steps:
            raise PlannerException(
                f"Cannot execute plan '{plan.id}' with no steps"
            )

        if validate_assignments:
            self._validate_plan_assignments(plan)

        # Emit "submitted".
        await self._bus.publish(
            PLAN_SUBMITTED,
            source="planner_executor",
            payload={
                "plan_id": plan.id,
                "plan_goal": plan.goal,
                "step_count": len(plan.steps),
                "priority": plan.priority.value
                if hasattr(plan.priority, "value")
                else str(plan.priority),
            },
            correlation_id=plan.id,
        )

        # ----- transition READY → RUNNING ----------------------------------
        # Walk the plan through the canonical lifecycle so the final
        # RUNNING transition is legal.  Supports the retry path where
        # the plan starts in FAILED status (FAILED → PLANNING → READY
        # → RUNNING is the replanning path allowed by the planner's
        # transition map).
        if plan.status == PlanStatus.NOT_STARTED:
            self._safe_plan_transition(plan, PlanStatus.PLANNING)
        if plan.status == PlanStatus.FAILED:
            # Re-planning path: FAILED → PLANNING → READY → RUNNING.
            self._safe_plan_transition(plan, PlanStatus.PLANNING)
        if plan.status == PlanStatus.PLANNING:
            self._safe_plan_transition(plan, PlanStatus.READY)
        if plan.status == PlanStatus.READY:
            self._safe_plan_transition(plan, PlanStatus.RUNNING)

        # ----- translate ----------------------------------------------------
        workflow = self.translate_plan_to_workflow(plan)
        await self._bus.publish(
            PLAN_TRANSLATED,
            source="planner_executor",
            payload={
                "plan_id": plan.id,
                "workflow_id": workflow.id,
                "step_count": workflow.step_count,
            },
            correlation_id=plan.id,
        )
        # Per-step translation events.
        for wf_step in workflow.steps.values():
            pstep = plan.step_by_id(wf_step.id)
            assignment = pstep.metadata.get("agent_assignment") if pstep else None
            await self._bus.publish(
                STEP_TRANSLATED,
                source="planner_executor",
                payload={
                    "plan_step_id": wf_step.id,
                    "workflow_step_id": wf_step.id,
                    "agent": wf_step.agent,
                    "plan_title": pstep.title if pstep else "",
                    "agent_assignment": assignment,
                },
                correlation_id=plan.id,
            )

        # ----- ensure engine has agents ------------------------------------
        self._sync_engine_agents_from_registry()

        # ----- run ----------------------------------------------------------
        await self._bus.publish(
            PLAN_STARTED,
            source="planner_executor",
            payload={
                "plan_id": plan.id,
                "workflow_id": workflow.id,
            },
            correlation_id=plan.id,
        )
        # Record plan-level start in execution_history if memory present.
        # NOTE: the execution_history.status column has a CHECK constraint
        # that only allows ('', 'success', 'failed', 'timeout', 'cancelled',
        # 'denied'). We pass '' for the start event (the event_type itself
        # communicates "started") and reserve the status column for the
        # terminal record below.
        await self._record_execution_event(
            event_type=PLAN_STARTED,
            task_id=None,
            workflow_id=workflow.id,
            agent_name="planner_executor",
            status="",
            metadata={"plan_id": plan.id},
        )

        # Track in-flight for cancel().
        run_future: asyncio.Future = asyncio.get_event_loop().create_future()
        async with self._lock:
            self._active[plan.id] = (workflow, run_future)

        wf_result: Any = None
        error: str | None = None
        try:
            wf_result = await self._engine.run(
                workflow, max_iterations=self._max_iterations
            )
        except asyncio.CancelledError:
            # Cooperative cancel: ask engine to cancel too.
            try:
                await self._engine.cancel(workflow.id, reason="planner_executor_cancelled")
            except Exception:  # noqa: BLE001
                logger.debug("Engine cancel during executor cancellation failed", exc_info=True)
            error = "cancelled by caller"
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("WorkflowEngine.run failed for plan %s", plan.id)
            error = f"{type(exc).__name__}: {exc}"
        finally:
            # Mark future done.
            if not run_future.done():
                if error is not None:
                    run_future.set_exception(
                        RuntimeError(error) if error != "cancelled by caller"
                        else asyncio.CancelledError()
                    )
                else:
                    run_future.set_result(wf_result)
            async with self._lock:
                self._active.pop(plan.id, None)

        # ----- apply result back to plan -----------------------------------
        self.apply_execution_result(plan, workflow, wf_result, error=error)

        # ----- emit final event --------------------------------------------
        final_status = plan.status
        if final_status == PlanStatus.COMPLETED:
            await self._bus.publish(
                PLAN_COMPLETED,
                source="planner_executor",
                payload={
                    "plan_id": plan.id,
                    "workflow_id": workflow.id,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                },
                correlation_id=plan.id,
            )
            audit_status = "success"
            audit_event = PLAN_COMPLETED
        elif final_status == PlanStatus.FAILED:
            await self._bus.publish(
                PLAN_FAILED,
                source="planner_executor",
                payload={
                    "plan_id": plan.id,
                    "workflow_id": workflow.id,
                    "error": error or wf_result.error if wf_result else error,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                },
                correlation_id=plan.id,
            )
            audit_status = "failed"
            audit_event = PLAN_FAILED
        elif final_status == PlanStatus.CANCELLED:
            await self._bus.publish(
                PLAN_CANCELLED,
                source="planner_executor",
                payload={
                    "plan_id": plan.id,
                    "workflow_id": workflow.id,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                },
                correlation_id=plan.id,
            )
            audit_status = "cancelled"
            audit_event = PLAN_CANCELLED
        else:
            # Plan stuck in RUNNING (e.g., engine hit max_iterations).
            # Use 'failed' (allowed by the execution_history CHECK
            # constraint) instead of 'unknown'.
            audit_status = "failed"
            audit_event = PLAN_FAILED
            await self._bus.publish(
                PLAN_FAILED,
                source="planner_executor",
                payload={
                    "plan_id": plan.id,
                    "workflow_id": workflow.id,
                    "error": f"plan did not reach terminal state (final={final_status.value})",
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                },
                correlation_id=plan.id,
            )

        await self._record_execution_event(
            event_type=audit_event,
            task_id=None,
            workflow_id=workflow.id,
            agent_name="planner_executor",
            status=audit_status,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=error,
            metadata={"plan_id": plan.id, "plan_status": final_status.value},
        )

        # ----- build summary ------------------------------------------------
        wf_status_str = (
            wf_result.status.value if wf_result is not None and hasattr(wf_result.status, "value")
            else (getattr(workflow.status, "value", str(workflow.status)))
        )
        return PlanExecutionResult(
            plan_id=plan.id,
            workflow_id=workflow.id,
            plan_status=plan.status,
            workflow_status=wf_status_str,
            steps_total=len(plan.steps),
            steps_completed=sum(1 for s in plan.steps if s.status == StepStatus.SUCCESS),
            steps_failed=sum(1 for s in plan.steps if s.status == StepStatus.FAILED),
            steps_skipped=sum(1 for s in plan.steps if s.status == StepStatus.SKIPPED),
            duration_ms=(time.monotonic() - started) * 1000,
            workflow_result=wf_result,
            plan=plan,
            error=error,
        )

    def apply_execution_result(
        self,
        plan: TaskPlan,
        workflow: Any,
        result: Any,
        *,
        error: str | None = None,
    ) -> TaskPlan:
        """Write a :class:`WorkflowExecutionResult` back onto a plan.

        This mutates ``plan.status`` and each ``plan.steps[i].status``
        in place, and attaches an ``"execution"`` dict to both the
        plan's and each step's ``metadata``.

        Safe to call even if *result* is ``None`` (engine raised); in
        that case every still-RUNNING step is marked FAILED and the
        plan is FAILED.

        Args:
            plan: The plan to update.
            workflow: The :class:`Workflow` that ran (its ``steps``
                dict carries the per-step terminal state).
            result: The :class:`WorkflowExecutionResult` returned by
                the engine, or ``None`` if the engine raised.
            error: Optional error string captured from the engine.

        Returns:
            The same *plan* instance, mutated.
        """
        # ----- workflow → plan status --------------------------------------
        wf_status_str: str
        if result is not None and hasattr(result, "status"):
            wf_status_str = (
                result.status.value if hasattr(result.status, "value") else str(result.status)
            )
        else:
            wf_status_str = getattr(workflow.status, "value", str(workflow.status))

        target_plan_status = _WF_STATUS_TO_PLAN.get(wf_status_str, PlanStatus.FAILED)
        if error is not None and target_plan_status == PlanStatus.RUNNING:
            # Engine raised mid-run; treat as failure.
            target_plan_status = PlanStatus.FAILED
        self._safe_plan_transition(plan, target_plan_status)

        # ----- workflow steps → plan steps ---------------------------------
        wf_steps = getattr(workflow, "steps", {})
        for wf_step in wf_steps.values():
            pstep = plan.step_by_id(wf_step.id)
            if pstep is None:
                # Step was added by the engine (shouldn't happen, but be defensive).
                continue

            # Translate status.
            wf_step_status_str = (
                wf_step.status.value if hasattr(wf_step.status, "value") else str(wf_step.status)
            )
            target = _WF_STEP_STATUS_TO_PLANNER.get(wf_step_status_str, pstep.status)
            self._safe_step_transition(pstep, target)

            # Attach execution metadata.
            pstep.metadata["execution"] = {
                "started_at": wf_step.started_at,
                "finished_at": wf_step.finished_at,
                "duration_ms": wf_step.duration_ms,
                "retry": wf_step.retry,
                "max_retries": wf_step.max_retries,
                "tokens_prompt": wf_step.tokens_prompt,
                "tokens_completion": wf_step.tokens_completion,
                "tools_used": list(wf_step.tools_used),
                "files_modified": list(wf_step.files_modified),
                "warnings": list(wf_step.warnings),
                "error": wf_step.error,
                "agent": wf_step.agent,
                "agent_reassigned": list(wf_step.agent_reassigned),
                "output": _safe_serialize(wf_step.output),
            }

        # ----- plan-level execution summary --------------------------------
        plan.metadata["execution"] = {
            "workflow_id": getattr(workflow, "id", ""),
            "duration_ms": (
                result.duration_ms if result is not None and hasattr(result, "duration_ms")
                else 0.0
            ),
            "steps_total": len(plan.steps),
            "steps_completed": sum(1 for s in plan.steps if s.status == StepStatus.SUCCESS),
            "steps_failed": sum(1 for s in plan.steps if s.status == StepStatus.FAILED),
            "steps_skipped": sum(1 for s in plan.steps if s.status == StepStatus.SKIPPED),
            "parallelism_max": (
                result.parallelism_max if result is not None and hasattr(result, "parallelism_max")
                else 0
            ),
            "retry_count": (
                result.retry_count if result is not None and hasattr(result, "retry_count")
                else 0
            ),
            "reassign_count": (
                result.reassign_count if result is not None and hasattr(result, "reassign_count")
                else 0
            ),
            "error": error or (result.error if result is not None and hasattr(result, "error") else None),
            "workflow_status": wf_status_str,
        }
        plan.touch()
        return plan

    async def cancel_plan(self, plan_id: str, *, reason: str = "") -> bool:
        """Cancel an in-flight plan execution.

        Args:
            plan_id: The :attr:`TaskPlan.id` of the running plan.
            reason: Optional reason recorded in the audit trail.

        Returns:
            ``True`` if a running execution was cancelled, ``False`` if
            no in-flight execution was found for *plan_id*.
        """
        async with self._lock:
            entry = self._active.pop(plan_id, None)
        if entry is None:
            return False
        workflow, future = entry
        try:
            await self._engine.cancel(workflow.id, reason=reason or "planner_executor_cancel")
        except Exception:  # noqa: BLE001
            logger.debug("engine.cancel failed during executor cancel", exc_info=True)
        if not future.done():
            future.cancel()
        await self._record_execution_event(
            event_type=PLAN_CANCELLED,
            task_id=None,
            workflow_id=workflow.id,
            agent_name="planner_executor",
            status="cancelled",
            error=reason or "cancelled",
            metadata={"plan_id": plan_id},
        )
        return True

    async def replay_from_memory(self, plan_id: str) -> list[dict[str, Any]]:
        """Retrieve all execution events recorded for a plan.

        Requires a wired-up :class:`MemoryManager`.  Returns events
        newest-first.

        Args:
            plan_id: The plan identifier.

        Returns:
            List of execution_history rows (as dicts) whose metadata
            references *plan_id*.  Empty list if no memory is
            configured or no events are recorded.
        """
        if self._memory is None or self._memory.execution_repository is None:
            return []
        repo = self._memory.execution_repository
        # execution_history doesn't have a plan_id column; we filter by
        # metadata.plan_id in Python.  Use get_recent with a generous
        # limit then filter.
        recent = await repo.get_recent(limit=500)
        return [r for r in recent if _row_refers_to_plan(r, plan_id)]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _translate_step(self, pstep: PlanStep) -> Any:
        """Translate a single :class:`PlanStep` to a multiagent :class:`Step`."""
        Step = _import("multiagent.workflow.models", "Step")
        StepCriticality = _import("multiagent.workflow.enums", "StepCriticality")
        # The workflow's StepStatus enum (distinct from the planner's).
        WfStepStatus = _import("multiagent.workflow.enums", "StepStatus")

        # Resolve the agent name from the assignment UUID.
        agent_name = self._resolve_agent_name_for_step(pstep)

        # Determine the action.  The BackwardCompatibilityAdapter maps
        # actions like "refactor", "review", "test" to capabilities.
        # We use the step title (slugified) by default; if the plan
        # metadata carries a hint, prefer that.
        action = pstep.metadata.get("action") or _slugify_action(pstep.title)

        # Pull the capability hint through so the scheduler has a
        # stronger signal than action alone.
        required_caps: list[str] = []
        if "required_capabilities" in pstep.metadata:
            rc = pstep.metadata["required_capabilities"]
            if isinstance(rc, list):
                required_caps = [str(c) for c in rc]
        # If there's an agent_assignment, use its capability as a hint.
        assignment = pstep.metadata.get("agent_assignment")
        if isinstance(assignment, dict) and assignment.get("capability"):
            cap = str(assignment["capability"])
            if cap not in required_caps:
                required_caps.insert(0, cap)

        input_payload: dict[str, Any] = {
            "description": pstep.description,
            "plan_step_id": pstep.id,
            "plan_step_title": pstep.title,
            "required_capabilities": required_caps,
            "plan_metadata": dict(pstep.metadata),
        }

        # Criticality: default REQUIRED unless the plan metadata opts out.
        crit_str = pstep.metadata.get("criticality", "required")
        try:
            criticality = StepCriticality(crit_str)
        except Exception:  # noqa: BLE001
            criticality = StepCriticality.REQUIRED

        wf_step = Step(
            id=pstep.id,
            name=pstep.title or f"step-{pstep.id}",
            agent=agent_name,
            action=action,
            input=input_payload,
            depends_on=list(pstep.dependencies),
            timeout=pstep.estimated_duration or 0.0,
            criticality=criticality,
            status=WfStepStatus.PENDING,
        )
        # Stash the capability hint on the step so the adapter can
        # see it (the adapter looks at .agent and .action; we add the
        # capability as a non-standard attribute that doesn't break it).
        try:
            # wf_step is a dataclass; attach as attribute.
            object.__setattr__(wf_step, "_planner_required_capabilities", required_caps)
        except Exception:  # noqa: BLE001
            pass

        return wf_step

    def _resolve_agent_name_for_step(self, pstep: PlanStep) -> str:
        """Resolve the agent *name* (not UUID) for a step, if possible.

        Order of preference:
        1. ``agent_assignment.agent_id`` (UUID) → registry.get_by_id.
        2. ``agent_assignment.agent_id`` if it's already a known name.
        3. The last entry of ``required_agents`` if it exists in the
           registry (AgentAwarePlanner appends the real agent name).
        4. Empty string (let the scheduler resolve by capability).
        """
        assignment = pstep.metadata.get("agent_assignment")
        if isinstance(assignment, dict):
            agent_id = assignment.get("agent_id", "")
            if agent_id:
                # Try as UUID.
                record = None
                try:
                    record = self._registry.get_by_id(agent_id)
                except Exception:  # noqa: BLE001
                    record = None
                if record is None:
                    # Try as name.
                    try:
                        record = self._registry.get_by_name(agent_id)
                    except Exception:  # noqa: BLE001
                        record = None
                if record is None:
                    # Try the generic get().
                    try:
                        record = self._registry.get(agent_id)
                    except Exception:  # noqa: BLE001
                        record = None
                if record is not None:
                    return record.name

        # Fall back to required_agents (last entry is the real agent
        # name appended by AgentAwarePlanner, if any).
        if pstep.required_agents:
            for name in reversed(pstep.required_agents):
                try:
                    if self._registry.exists(name):
                        record = self._registry.get(name)
                        if record is not None:
                            return record.name
                except Exception:  # noqa: BLE001
                    continue

        return ""

    def _validate_plan_assignments(self, plan: TaskPlan) -> None:
        """Verify every step's agent_assignment resolves to a live agent.

        Raises :class:`PlannerException` on the first failure.
        """
        for step in plan.steps:
            assignment = step.metadata.get("agent_assignment")
            if not isinstance(assignment, dict):
                # No assignment — the scheduler will resolve by capability.
                continue
            agent_id = assignment.get("agent_id", "")
            if not agent_id:
                continue
            # Accept either UUID or name; if neither resolves, fail.
            record = None
            for getter in ("get_by_id", "get_by_name", "get"):
                try:
                    record = getattr(self._registry, getter)(agent_id)
                    if record is not None:
                        break
                except Exception:  # noqa: BLE001
                    continue
            if record is None:
                raise PlannerException(
                    f"Step '{step.title}' ({step.id}): agent_assignment "
                    f"references unknown agent '{agent_id}'"
                )
            # Optionally check availability — warn (don't fail) if not
            # available, since the scheduler may pick a different agent
            # via capability resolution.
            if hasattr(record, "is_available") and not record.is_available:
                logger.warning(
                    "Step '%s' (%s): assigned agent '%s' is not available; "
                    "scheduler will attempt to resolve a substitute.",
                    step.title, step.id, record.name,
                )

    def _safe_plan_transition(self, plan: TaskPlan, target: PlanStatus) -> None:
        """Transition plan.status if legal; log + skip otherwise."""
        current = plan.status
        if current == target:
            return
        if not can_transition_plan(current, target):
            # Special case: FAILED → PLANNING → READY → RUNNING is the
            # replanning path; if the caller is retrying a FAILED plan
            # we transparently walk it through.
            if current == PlanStatus.FAILED and target in (PlanStatus.RUNNING, PlanStatus.READY):
                if can_transition_plan(PlanStatus.FAILED, PlanStatus.PLANNING):
                    plan.status = PlanStatus.PLANNING
                    plan.touch()
                    if can_transition_plan(PlanStatus.PLANNING, PlanStatus.READY):
                        plan.status = PlanStatus.READY
                        plan.touch()
                        if target == PlanStatus.RUNNING and can_transition_plan(PlanStatus.READY, PlanStatus.RUNNING):
                            plan.status = PlanStatus.RUNNING
                            plan.touch()
                        return
                    return
                return
            logger.warning(
                "Illegal plan transition %s → %s for plan %s; skipping",
                current.value, target.value, plan.id,
            )
            return
        plan.status = target
        plan.touch()

    def _safe_step_transition(self, step: PlanStep, target: StepStatus) -> None:
        """Transition step.status if legal; log + skip otherwise.

        The planner's transition map is strict (PENDING → READY →
        RUNNING → SUCCESS/FAILED), but the workflow engine may report
        terminal states without the executor seeing every intermediate
        state (e.g., a step that fails on its first run appears as
        FAILED even though the executor never published a READY event
        for it).  When the *direct* transition is illegal, this method
        attempts to walk through the canonical intermediate states
        before reaching *target*.  If no legal path exists, the
        transition is logged and skipped (the plan object is left
        intact for inspection).
        """
        current = step.status
        if current == target:
            return
        if can_transition_step(current, target):
            step.status = target
            return

        # Build a BFS over allowed transitions to find a legal path
        # from current to target.  This generalises the previous
        # hard-coded special cases (PENDING → SUCCESS, READY → SUCCESS,
        # RUNNING → SKIPPED, etc.).
        path = _find_step_transition_path(current, target)
        if path is None:
            logger.warning(
                "Illegal step transition %s → %s for step '%s' (%s); skipping",
                current.value if hasattr(current, "value") else current,
                target.value if hasattr(target, "value") else target,
                step.title, step.id,
            )
            return
        for intermediate in path:
            step.status = intermediate

    def _sync_engine_agents_from_registry(self) -> None:
        """Mirror every registered agent into the engine's agent map."""
        try:
            records = self._registry.get_all()
        except Exception:  # noqa: BLE001
            logger.debug("registry.get_all() failed during agent sync", exc_info=True)
            return
        for record in records:
            try:
                # The engine's register_agent takes a BaseAgent.
                agent = getattr(record, "agent", None)
                if agent is not None:
                    self._engine.register_agent(agent)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Failed to mirror agent '%s' into engine",
                    getattr(record, "name", "?"),
                    exc_info=True,
                )

    async def _record_execution_event(
        self,
        *,
        event_type: str,
        task_id: str | None,
        workflow_id: str | None,
        agent_name: str = "",
        status: str = "",
        duration_ms: int | None = None,
        error: str | None = None,
        result: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist an execution event if memory is configured.

        The ``execution_history`` table has a FOREIGN KEY on
        ``workflow_id`` → ``workflows(id)``.  Because the executor
        creates ephemeral :class:`Workflow` objects that are not
        persisted to the ``workflows`` table, we **do not** pass the
        workflow_id as the FK column — instead we stash it in the
        row's ``metadata`` dict so the audit trail remains
        queryable (via ``metadata.workflow_id``) without violating
        referential integrity.  The same applies to ``task_id``.

        Callers that want the FK columns populated should pre-persist
        the workflow / task rows before invoking the executor.
        """
        if self._memory is None or self._memory.execution_repository is None:
            return
        # Merge workflow_id / plan_id into metadata so they survive the
        # round-trip even though we don't write them to the FK columns.
        meta = dict(metadata or {})
        if workflow_id is not None and "workflow_id" not in meta:
            meta["workflow_id"] = workflow_id
        if task_id is not None and "task_id" not in meta:
            meta["task_id"] = task_id
        try:
            await self._memory.execution_repository.record(
                event_type=event_type,
                task_id=None,        # avoid FK violation; stored in metadata
                workflow_id=None,    # avoid FK violation; stored in metadata
                agent_name=agent_name,
                duration_ms=duration_ms,
                status=status,
                error=error,
                result=result,
                metadata=meta,
            )
        except Exception as exc:  # noqa: BLE001
            # Surface the error at WARNING level so silent audit-trail
            # failures don't go unnoticed. The executor itself stays
            # resilient — a persistence failure must never crash a
            # running plan — but the operator should see what went
            # wrong in the logs.
            logger.warning(
                "Failed to record execution event %s (status=%r): %s",
                event_type, status, exc,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _import(module_path: str, attr: str) -> Any:
    """Import *attr* from *module_path*, raising a helpful error on failure."""
    import importlib
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, attr)
    except (ImportError, AttributeError) as exc:
        raise PlannerException(
            f"PlannerExecutor requires the multiagent package. "
            f"Could not import {attr} from {module_path}: {exc}"
        ) from exc


# Pre-compute the planner's step transition graph at import time so
# per-step BFS is O(1) per edge (the graph is tiny — 6 nodes).
_STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.READY, StepStatus.SKIPPED}),
    StepStatus.READY: frozenset({StepStatus.RUNNING, StepStatus.SKIPPED}),
    StepStatus.RUNNING: frozenset({StepStatus.SUCCESS, StepStatus.FAILED}),
    StepStatus.SUCCESS: frozenset(),
    StepStatus.FAILED: frozenset({StepStatus.READY, StepStatus.SKIPPED}),
    StepStatus.SKIPPED: frozenset(),
}


def _find_step_transition_path(
    start: StepStatus, target: StepStatus
) -> list[StepStatus] | None:
    """BFS-shortest legal path from *start* to *target* in the step graph.

    Returns the list of intermediate states (excluding *start*,
    including *target*), or ``None`` if no path exists.  Used by
    :meth:`PlannerExecutor._safe_step_transition` to walk through
    canonical intermediate states when a direct transition is illegal.
    """
    if start == target:
        return []
    from collections import deque
    queue: deque[tuple[StepStatus, list[StepStatus]]] = deque(
        (n, [n]) for n in _STEP_TRANSITIONS.get(start, frozenset())
    )
    visited: set[StepStatus] = {start}
    while queue:
        node, path = queue.popleft()
        if node == target:
            return path
        if node in visited:
            continue
        visited.add(node)
        for nxt in _STEP_TRANSITIONS.get(node, frozenset()):
            if nxt not in visited:
                queue.append((nxt, path + [nxt]))
    return None


def _slugify_action(text: str) -> str:
    """Turn a step title into a single-word action string.

    The BackwardCompatibilityAdapter maps actions like ``refactor``,
    ``review``, ``test`` to capabilities.  For titles that don't match
    any known action, the adapter falls back to ``planning``, so this
    function prefers action keywords that actually appear in the title.
    """
    if not text:
        return "execute"
    text_lower = text.lower()
    # Order matters: prefer the most specific action.
    for kw, action in (
        ("refactor", "refactor"),
        ("review", "review"),
        ("audit", "audit"),
        ("test", "test"),
        ("fix", "fix"),
        ("bug", "fix"),
        ("write", "write"),
        ("implement", "write"),
        ("code", "code"),
        ("commit", "commit"),
        ("push", "push"),
        ("branch", "branch"),
        ("merge", "merge"),
        ("search", "search"),
        ("research", "research"),
        ("gather", "gather"),
        ("analyze", "analyze"),
        ("summarize", "summarize"),
        ("document", "write"),
        ("execute", "execute"),
        ("run", "execute"),
        ("deploy", "execute"),
    ):
        if kw in text_lower:
            return action
    # Default: "plan" → capability "planning" via the adapter.
    return "plan"


def _safe_serialize(value: Any) -> Any:
    """Best-effort conversion of *value* to a JSON-friendly form."""
    if value is None:
        return None
    try:
        import json
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _row_refers_to_plan(row: dict[str, Any], plan_id: str) -> bool:
    """Check if an execution_history row references *plan_id* in its metadata."""
    meta = row.get("metadata")
    if isinstance(meta, dict):
        if meta.get("plan_id") == plan_id:
            return True
    return False


__all__ = [
    "PlannerExecutor",
    "PlanExecutionResult",
]
