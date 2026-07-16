"""Plan Executor — bridges Planner output to Orchestrator execution.

Sprint 3.6.

The PlanExecutor takes the heuristic plan produced by the Planner,
wraps it in an ``ExecutablePlan`` with full lifecycle tracking,
validates dependencies, assigns agents, executes each task via the
Orchestrator's agent routing, handles errors with configurable
retries, and emits progress events through the EventBus.

Flow::

    User message
      → Planner.plan() → list of sub-task dicts
      → PlanExecutor.execute_plan() → ExecutablePlan
          → validate dependencies
          → for each ready task:
              → assign agent
              → execute via Orchestrator agent
              → on success: mark COMPLETED
              → on failure: retry or mark FAILED
              → emit progress events
          → emit plan completed/failed

The executor integrates with the existing memory system via
``ExecutionRepository`` to persist plan history without duplicating
functionality.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from src.events import (
    PLAN_CANCELLED,
    PLAN_COMPLETED,
    PLAN_FAILED,
    PLAN_READY,
    PLAN_STARTED,
    PLAN_TASK_ASSIGNED,
    PLAN_TASK_COMPLETED,
    PLAN_FAILED as PLAN_TASK_FAILED_EVT,
    PLAN_TASK_SKIPPED,
    PLAN_TASK_STARTED,
    PLAN_TASK_RETRY,
    PLAN_CREATED,
    PLAN_VALIDATING,
)
from src.plan_models import (
    ExecutablePlan,
    PlanState,
    PlanTask,
    PlanTaskStatus,
    can_transition_plan_task,
)
from src.recovery import ResilienceHelper

if TYPE_CHECKING:
    from src.event_bus import EventBus
    from src.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------


@dataclass
class PlanExecutorConfig:
    """Configuration for the PlanExecutor.

    Attributes:
        default_agent_timeout:  Timeout in seconds per task execution.
        max_retries:           Default max retries per task.
        retry_backoff_base:    Base seconds for exponential backoff.
        retry_backoff_cap:     Max seconds between retries.
        continue_on_error:     If True, skip failed non-critical tasks
                               and continue the plan.
        pause_between_tasks:   Seconds to wait between tasks (0 = none).
    """

    default_agent_timeout: float = 30.0
    max_retries: int = 3
    retry_backoff_base: float = 0.5
    retry_backoff_cap: float = 30.0
    continue_on_error: bool = True
    pause_between_tasks: float = 0.0


# ----------------------------------------------------------------------
# Plan Executor
# ----------------------------------------------------------------------


class PlanExecutor:
    """Executes plans produced by the Planner through the Orchestrator.

    The executor manages the full lifecycle of an ExecutablePlan:
    validation, agent assignment, sequential/dependency-ordered
    execution, error handling with retries, and progress events.

    Args:
        orchestrator: The core Orchestrator instance (for agent access).
        event_bus:    Optional EventBus for progress events.
        config:       Execution configuration.
    """

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        event_bus: EventBus | None = None,
        config: PlanExecutorConfig | None = None,
    ) -> None:
        self._orch = orchestrator
        self._bus = event_bus
        self._config = config or PlanExecutorConfig()

        # Active plans being executed.
        self._active_plans: dict[str, ExecutablePlan] = {}

        # Completed plans history (bounded).
        self._history: list[ExecutablePlan] = []
        self._MAX_HISTORY = 100

        # Execution repository for persistence (late binding).
        self._execution_repo: Any = None

        # Plan tracker for advanced tracking (Sprint 3.7).
        self._tracker: Any = None

        logger.info("PlanExecutor initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def attach_tracker(self, tracker: Any) -> None:
        """Attach a PlanExecutionTracker for advanced plan tracking.

        When attached, the executor will automatically bind each
        executed plan to the tracker and update step progress in
        real time.

        Args:
            tracker: A PlanExecutionTracker instance.
        """
        self._tracker = tracker
        logger.info("PlanExecutionTracker attached to PlanExecutor")

    def attach_execution_repository(self, repo: Any) -> None:
        """Attach an ExecutionRepository for plan history persistence."""
        self._execution_repo = repo
        logger.info("ExecutionRepository attached to PlanExecutor")

    async def execute_plan(
        self,
        subtasks: list[dict[str, Any]],
        *,
        original_message: str = "",
        strategy: str = "",
        rationale: str = "",
        context: dict[str, Any] | None = None,
    ) -> ExecutablePlan:
        """Create and execute an ExecutablePlan from Planner sub-tasks.

        This is the main entry point. It creates the plan, validates
        it, and runs all tasks to completion (or failure).

        Args:
            subtasks:        List of sub-task dicts from Planner.plan().
            original_message: The user message that triggered the plan.
            strategy:        Planning strategy label.
            rationale:       Why this plan was chosen.
            context:         Agent context for execution.

        Returns:
            The completed (or failed) ExecutablePlan.
        """
        ctx = context or {}

        # 1. Create plan.
        plan = self._build_executable_plan(
            subtasks,
            original_message=original_message,
            strategy=strategy,
            rationale=rationale,
        )

        # Sprint 3.7: Create tracker entry if tracker is attached.
        if self._tracker is not None:
            self._tracker.create_tracker(
                plan.id,
                strategy=plan.strategy,
                original_message=original_message,
            )
            self._tracker.bind_plan(plan.id, plan)

        plan.transition_to(PlanState.VALIDATING)
        await self._emit(PLAN_VALIDATING, {
            "plan_id": plan.id,
            "task_count": plan.task_count,
        })

        # 2. Validate dependencies.
        validation_errors = self._validate_dependencies(plan)
        if validation_errors:
            plan.error = "; ".join(validation_errors)
            plan.transition_to(PlanState.FAILED)
            await self._emit(PLAN_FAILED, {
                "plan_id": plan.id,
                "error": plan.error,
                "validation_errors": validation_errors,
            })
            self._archive_plan(plan)
            await self._persist_plan(plan)
            return plan

        # 3. Mark ready and start.
        plan.transition_to(PlanState.READY)
        await self._emit(PLAN_READY, {
            "plan_id": plan.id,
            "task_count": plan.task_count,
        })

        plan.transition_to(PlanState.RUNNING)
        self._active_plans[plan.id] = plan
        await self._emit(PLAN_STARTED, {
            "plan_id": plan.id,
            "task_count": plan.task_count,
            "strategy": strategy,
        })

        # 4. Execute tasks.
        try:
            await self._execute_all_tasks(plan, ctx)
        except asyncio.CancelledError:
            plan.error = "Plan execution was cancelled"
            if not plan.is_terminal:
                plan.transition_to(PlanState.CANCELLED)
                await self._emit(PLAN_CANCELLED, {
                    "plan_id": plan.id,
                    "error": plan.error,
                })
        except Exception as exc:  # noqa: BLE001
            plan.error = str(exc)
            if not plan.is_terminal:
                plan.transition_to(PlanState.FAILED)
                await self._emit(PLAN_FAILED, {
                    "plan_id": plan.id,
                    "error": plan.error,
                })

        # 5. Finalize.
        self._active_plans.pop(plan.id, None)

        if plan.state == PlanState.RUNNING:
            # All tasks processed — determine final state.
            if plan.failed_count > 0 and plan.completed_count == 0:
                plan.transition_to(PlanState.FAILED)
                await self._emit(PLAN_FAILED, {
                    "plan_id": plan.id,
                    "error": "All tasks failed",
                    "metrics": plan.metrics(),
                })
            else:
                plan.transition_to(PlanState.COMPLETED)
                await self._emit(PLAN_COMPLETED, {
                    "plan_id": plan.id,
                    "metrics": plan.metrics(),
                })

        self._archive_plan(plan)
        await self._persist_plan(plan)
        return plan

    async def cancel_plan(self, plan_id: str) -> bool:
        """Cancel an active plan.

        Returns:
            True if the plan was found and cancelled.
        """
        plan = self._active_plans.get(plan_id)
        if plan is None:
            return False
        if plan.is_terminal:
            return False

        plan.error = "Cancelled by user"
        plan.transition_to(PlanState.CANCELLED)
        await self._emit(PLAN_CANCELLED, {
            "plan_id": plan.id,
            "error": plan.error,
        })

        # Mark remaining non-terminal tasks as skipped.
        for t in plan.tasks:
            if not t.is_terminal:
                t.transition_to(PlanTaskStatus.SKIPPED)
                await self._emit(PLAN_TASK_SKIPPED, {
                    "plan_id": plan.id,
                    "task_id": t.id,
                    "task_name": t.name,
                    "reason": "plan_cancelled",
                })

        self._active_plans.pop(plan_id, None)
        self._archive_plan(plan)
        await self._persist_plan(plan)
        return True

    def get_plan(self, plan_id: str) -> ExecutablePlan | None:
        """Look up a plan by id (active or history)."""
        plan = self._active_plans.get(plan_id)
        if plan is not None:
            return plan
        for p in self._history:
            if p.id == plan_id:
                return p
        return None

    def list_active_plans(self) -> list[ExecutablePlan]:
        """Return all currently executing plans."""
        return list(self._active_plans.values())

    def plan_history(self, limit: int = 50) -> list[ExecutablePlan]:
        """Return the most recent completed/failed plans."""
        return list(self._history[-limit:])

    def summary(self) -> dict[str, Any]:
        """Return aggregate stats."""
        active_states: dict[str, int] = {}
        for p in self._active_plans.values():
            active_states[p.state.value] = active_states.get(p.state.value, 0) + 1
        return {
            "active_plans": len(self._active_plans),
            "active_by_state": active_states,
            "history_size": len(self._history),
        }

    # ------------------------------------------------------------------
    # Plan construction
    # ------------------------------------------------------------------

    def _build_executable_plan(
        self,
        subtasks: list[dict[str, Any]],
        *,
        original_message: str = "",
        strategy: str = "",
        rationale: str = "",
    ) -> ExecutablePlan:
        """Convert Planner sub-task dicts into an ExecutablePlan."""
        plan = ExecutablePlan(
            strategy=strategy or "unknown",
            original_message=original_message,
            rationale=rationale,
        )

        for st in subtasks:
            task = PlanTask(
                name=st.get("name", "unnamed"),
                description=st.get("description", ""),
                agent_assigned=st.get("suggested_agent", ""),
                dependencies=list(st.get("depends_on", [])),
                max_retries=self._config.max_retries,
                metadata={
                    "suggested_tools": list(st.get("suggested_tools", [])),
                },
            )
            plan.tasks.append(task)

        # Emit creation event (plan is already in CREATED state).
        # Note: we use create_task because _build_executable_plan is sync.
        # The event is best-effort; if the loop isn't running yet it will
        # be a no-op.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._emit(PLAN_CREATED, {
                    "plan_id": plan.id,
                    "strategy": plan.strategy,
                    "task_count": plan.task_count,
                    "original_message": original_message[:200],
                })
            )
        except RuntimeError:
            pass  # No running loop yet
        return plan

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_dependencies(self, plan: ExecutablePlan) -> list[str]:
        """Validate that all task dependencies reference existing tasks.

        Returns:
            A list of validation error strings (empty if valid).
        """
        errors: list[str] = []
        task_names: set[str] = {t.name for t in plan.tasks}

        for t in plan.tasks:
            for dep in t.dependencies:
                if dep not in task_names:
                    errors.append(
                        f"Task '{t.name}' depends on unknown task '{dep}'"
                    )

        # Check for circular dependencies (simple DFS).
        if not errors:
            cycle = self._detect_cycle(plan)
            if cycle:
                errors.append(f"Circular dependency detected: {' -> '.join(cycle)}")

        return errors

    @staticmethod
    def _detect_cycle(plan: ExecutablePlan) -> list[str] | None:
        """Detect a circular dependency in the plan's task graph.

        Returns the cycle path if found, or None.
        """
        name_to_task: dict[str, PlanTask] = {t.name: t for t in plan.tasks}
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {name: WHITE for name in name_to_task}
        path: list[str] = []

        def dfs(node: str) -> list[str] | None:
            color[node] = GRAY
            path.append(node)
            task = name_to_task.get(node)
            if task:
                for dep in task.dependencies:
                    if color.get(dep) == GRAY:
                        # Found cycle — extract it.
                        cycle_start = path.index(dep)
                        return path[cycle_start:] + [dep]
                    if color.get(dep, WHITE) == WHITE:
                        result = dfs(dep)
                        if result:
                            return result
            path.pop()
            color[node] = BLACK
            return None

        for name in name_to_task:
            if color[name] == WHITE:
                result = dfs(name)
                if result:
                    return result
        return None

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    async def _execute_all_tasks(
        self, plan: ExecutablePlan, context: dict[str, Any]
    ) -> None:
        """Execute all tasks in the plan respecting dependencies.

        Tasks are executed in dependency order. When multiple tasks
        are ready simultaneously, they execute sequentially (future
        enhancement: parallel execution).
        """
        while not plan.is_terminal:
            ready = plan.ready_tasks()
            if not ready:
                # No more tasks can run.
                break

            # Execute ready tasks (one at a time for now).
            for task in ready:
                if plan.state != PlanState.RUNNING:
                    return
                await self._execute_single_task(plan, task, context)

                # Optional pause between tasks.
                if self._config.pause_between_tasks > 0:
                    await asyncio.sleep(self._config.pause_between_tasks)

    async def _execute_single_task(
        self, plan: ExecutablePlan, task: PlanTask, context: dict[str, Any]
    ) -> None:
        """Execute a single plan task with retry handling."""
        # Assign agent.
        agent_name = self._resolve_agent(task)
        task.agent_assigned = agent_name
        task.transition_to(PlanTaskStatus.ASSIGNED)

        # Sprint 3.7: Update tracker step.
        if self._tracker is not None:
            self._tracker.update_step(
                plan.id, task.id,
                status="assigned",
                agent_assigned=agent_name,
            )

        await self._emit(PLAN_TASK_ASSIGNED, {
            "plan_id": plan.id,
            "task_id": task.id,
            "task_name": task.name,
            "agent_assigned": agent_name,
        })

        # Execute with retries.
        while True:
            task.transition_to(PlanTaskStatus.RUNNING)
            task.started_at = task.started_at or plan.tasks[0].created_at

            # Sprint 3.7: Update tracker step.
            if self._tracker is not None:
                self._tracker.update_step(
                    plan.id, task.id,
                    status="running",
                    agent_assigned=task.agent_assigned,
                )

            await self._emit(PLAN_TASK_STARTED, {
                "plan_id": plan.id,
                "task_id": task.id,
                "task_name": task.name,
                "agent_assigned": task.agent_assigned,
                "attempt": task.retry_count + 1,
            })

            start = time.monotonic()
            try:
                result = await self._run_agent(task, context)
                duration_ms = (time.monotonic() - start) * 1000

                task.result = result
                task.execution_time_ms = duration_ms
                task.transition_to(PlanTaskStatus.COMPLETED)

                # Sprint 3.7: Update tracker step.
                if self._tracker is not None:
                    self._tracker.update_step(
                        plan.id, task.id,
                        status="completed",
                        result=result,
                        execution_time_ms=round(duration_ms, 2),
                        retry_count=task.retry_count,
                    )

                await self._emit(PLAN_TASK_COMPLETED, {
                    "plan_id": plan.id,
                    "task_id": task.id,
                    "task_name": task.name,
                    "agent_assigned": task.agent_assigned,
                    "duration_ms": round(duration_ms, 2),
                    "attempt": task.retry_count + 1,
                })

                # Record in execution repository.
                await self._record_execution(
                    plan, task, "success", duration_ms=round(duration_ms),
                )
                return  # success

            except asyncio.CancelledError:
                raise  # propagate cancellation

            except Exception as exc:  # noqa: BLE001
                duration_ms = (time.monotonic() - start) * 1000
                task.error = str(exc)
                task.execution_time_ms = duration_ms
                task.retry_count += 1

                # Transition to FAILED first so can_retry is correct.
                task.transition_to(PlanTaskStatus.FAILED)

                # Sprint 3.7: Update tracker step.
                if self._tracker is not None:
                    self._tracker.update_step(
                        plan.id, task.id,
                        status="failed",
                        error=str(exc),
                        execution_time_ms=round(duration_ms, 2),
                        retry_count=task.retry_count,
                    )

                logger.warning(
                    "PlanTask '%s' failed (attempt %d/%d): %s",
                    task.name, task.retry_count, task.max_retries, exc,
                )

                await self._record_execution(
                    plan, task, "failed",
                    duration_ms=round(duration_ms),
                    error=str(exc),
                )

                if task.can_retry:
                    # Retry with backoff.
                    backoff = min(
                        self._config.retry_backoff_cap,
                        self._config.retry_backoff_base * (2 ** (task.retry_count - 1)),
                    )
                    await self._emit(PLAN_TASK_RETRY, {
                        "plan_id": plan.id,
                        "task_id": task.id,
                        "task_name": task.name,
                        "attempt": task.retry_count + 1,
                        "max_retries": task.max_retries,
                        "backoff_seconds": backoff,
                        "error": task.error,
                    })
                    logger.info(
                        "Retrying PlanTask '%s' in %.1fs (attempt %d/%d)",
                        task.name, backoff, task.retry_count + 1, task.max_retries,
                    )
                    await asyncio.sleep(backoff)
                    task.transition_to(PlanTaskStatus.ASSIGNED)
                    continue

                # Exhausted retries (task is already FAILED from above).
                await self._emit(PLAN_TASK_FAILED_EVT, {
                    "plan_id": plan.id,
                    "task_id": task.id,
                    "task_name": task.name,
                    "agent_assigned": task.agent_assigned,
                    "error": task.error,
                    "attempts": task.retry_count,
                })

                if not self._config.continue_on_error:
                    plan.error = (
                        f"Task '{task.name}' failed after {task.retry_count} "
                        f"attempts: {task.error}"
                    )
                    return  # will cause plan to be marked FAILED

                # Skip failed task and continue with the rest.
                logger.info(
                    "Skipping failed PlanTask '%s' (continue_on_error=True)",
                    task.name,
                )
                task.transition_to(PlanTaskStatus.SKIPPED)
                await self._emit(PLAN_TASK_SKIPPED, {
                    "plan_id": plan.id,
                    "task_id": task.id,
                    "task_name": task.name,
                    "reason": "exhausted_retries",
                })
                return

    def _resolve_agent(self, task: PlanTask) -> str:
        """Resolve the agent for a task.

        Uses the pre-assigned agent from the Planner. Falls back to
        the Orchestrator's default agent if none is assigned or if
        the assigned agent is not registered.
        """
        if task.agent_assigned:
            # Check if the agent exists in the orchestrator.
            if hasattr(self._orch, "_agents") and task.agent_assigned in self._orch._agents:
                return task.agent_assigned
            logger.warning(
                "Agent '%s' not registered; using default",
                task.agent_assigned,
            )

        # Fall back to the router's default agent.
        if hasattr(self._orch, "_router"):
            default = self._orch._router.default_agent
            return default.name
        return "echo"

    async def _run_agent(
        self, task: PlanTask, context: dict[str, Any]
    ) -> Any:
        """Run the assigned agent for a task.

        Uses the Orchestrator's agent to execute the task. The task's
        description is sent as the message, and the agent's response
        is returned as the result.
        """
        agent = self._orch._agents.get(task.agent_assigned)
        if agent is None:
            raise RuntimeError(
                f"Agent '{task.agent_assigned}' is not registered "
                f"in the Orchestrator"
            )

        # Build a message from the task's context.
        message = task.description or task.name
        tools = task.metadata.get("suggested_tools", [])
        if tools:
            message = f"[Tools: {', '.join(tools)}] {message}"

        # Execute with timeout.
        timeout = self._config.default_agent_timeout
        try:
            result = await asyncio.wait_for(
                agent.handle(message, context),
                timeout=timeout,
            )
            return result
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Agent '{task.agent_assigned}' exceeded {timeout}s "
                f"for task '{task.name}'"
            ) from None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _record_execution(
        self,
        plan: ExecutablePlan,
        task: PlanTask,
        status: str,
        *,
        duration_ms: float = 0,
        error: str | None = None,
    ) -> None:
        """Record a task execution event in the ExecutionRepository."""
        if self._execution_repo is None:
            return
        try:
            await self._execution_repo.record(
                event_type=f"plan.task.{status}",
                task_id=task.id,
                workflow_id=plan.id,
                agent_name=task.agent_assigned,
                duration_ms=int(duration_ms),
                status=status,
                error=error,
                result=task.result,
                metadata={
                    "plan_id": plan.id,
                    "task_name": task.name,
                    "strategy": plan.strategy,
                    "attempt": task.retry_count + 1,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to record execution for plan task '%s'", task.name
            )

    async def _persist_plan(self, plan: ExecutablePlan) -> None:
        """Persist plan completion to the ExecutionRepository."""
        if self._execution_repo is None:
            return
        try:
            await self._execution_repo.record(
                event_type=f"plan.{plan.state.value}",
                workflow_id=plan.id,
                status=plan.state.value,
                error=plan.error,
                result=plan.metrics(),
                metadata={
                    "strategy": plan.strategy,
                    "original_message": plan.original_message[:500],
                    "rationale": plan.rationale,
                    "task_count": plan.task_count,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist plan '%s'", plan.id[:8])

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def _archive_plan(self, plan: ExecutablePlan) -> None:
        """Move a terminal plan to history."""
        if plan.is_terminal:
            self._history.append(plan)
            if len(self._history) > self._MAX_HISTORY:
                overflow = len(self._history) - self._MAX_HISTORY
                del self._history[:overflow]

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------

    async def _emit(self, event_type: str, payload: Any) -> None:
        """Emit an event, isolating failures."""
        if self._bus is None:
            return
        try:
            await self._bus.publish(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit event '%s'", event_type)


__all__ = [
    "PlanExecutor",
    "PlanExecutorConfig",
]