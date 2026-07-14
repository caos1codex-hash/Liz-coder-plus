"""Scheduler — executes an :class:`ExecutionPlan` respecting the DAG (Sprint 2.8).

The :class:`Scheduler` is the runtime component that:

- Picks the next task(s) to execute (priority + readiness).
- Tracks task lifecycle (RUNNING / WAITING / COMPLETED / FAILED).
- Retries failed tasks up to ``max_retries``.
- Supports ``pause`` / ``resume`` / ``cancel``.
- Records execution history for recovery and observability.

Design notes:

- The scheduler is **synchronous and stateful**: it does not call
  agents itself. Callers (e.g. the orchestrator) consume
  :meth:`next_tasks`, dispatch them to agents, then call
  :meth:`mark_started` / :meth:`mark_completed` / :meth:`mark_failed`.
- The scheduler is **crash-recoverable**: its full state can be
  serialized via :meth:`to_state` / :meth:`from_state`, allowing a
  plan to be resumed on a different process.
- Retries: when :meth:`mark_failed` is called and the task still has
  retries left, the scheduler automatically transitions it back to
  READY and bumps ``retry_count``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .exceptions import (
    InvalidTaskError,
    SchedulerError,
    SchedulerPausedError,
)
from .execution_plan import ExecutionPlan
from .models import TaskPriority, TaskStatus, priority_weight
from .task import Task
from .utils import now_epoch, now_iso

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


@dataclass
class SchedulerConfig:
    """Configuration for :class:`Scheduler`.

    Attributes:
        max_parallel:       Maximum number of tasks allowed in RUNNING
                            state at the same time.
        default_max_retries: Default ``max_retries`` for tasks that
                            don't override it.
        retry_backoff_s:    Base backoff (seconds) between retries.
                            Actual backoff is ``retry_backoff_s *
                            2**attempt``.
        enable_replan_on_failure: If True, calling :meth:`mark_failed`
                            on a task with no retries left will trigger
                            the registered ``on_replan`` callback.
        pause_on_critical_failure: If True, the scheduler auto-pauses
                            when a CRITICAL task fails terminally.
    """

    max_parallel: int = 4
    default_max_retries: int = 3
    retry_backoff_s: float = 0.0
    enable_replan_on_failure: bool = True
    pause_on_critical_failure: bool = True


# ----------------------------------------------------------------------
# Scheduler
# ----------------------------------------------------------------------


class Scheduler:
    """Stateful scheduler for an :class:`ExecutionPlan`.

    Usage::

        plan = planner.create_plan("Build editor")
        sched = Scheduler(plan)
        while not sched.is_done():
            batch = sched.next_tasks(max_tasks=2)
            for task in batch:
                # ... dispatch `task` to an agent ...
                pass
            # ... once agent reports back ...
            sched.mark_completed(task.id)
    """

    def __init__(
        self,
        plan: ExecutionPlan,
        *,
        config: SchedulerConfig | None = None,
        on_replan: Any | None = None,
    ) -> None:
        self._plan = plan
        self._config = config or SchedulerConfig()
        self._on_replan = on_replan
        # Track tasks that have been started but not yet completed.
        self._running: set[str] = set()
        # Track tasks that have been completed (terminal successful).
        self._completed: set[str] = set()
        # Track tasks that have failed terminally.
        self._failed: set[str] = set()
        # Track tasks that have been cancelled or skipped.
        self._cancelled: set[str] = set()
        # Track tasks that are paused (waiting for an external event).
        self._waiting: set[str] = set()
        # History of events for observability / recovery.
        self._history: list[dict[str, Any]] = []
        # Pause flag.
        self._paused: bool = False
        # Apply default max_retries to tasks that don't override it.
        for t in plan.tasks.values():
            if t.max_retries <= 0:
                t.max_retries = self._config.default_max_retries
            # Promote PENDING tasks to READY if their deps are already
            # satisfied (e.g. when resuming a recovered plan).
            self._maybe_promote_to_ready(t)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def plan(self) -> ExecutionPlan:
        return self._plan

    @property
    def config(self) -> SchedulerConfig:
        return self._config

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def running_count(self) -> int:
        return len(self._running)

    @property
    def completed_count(self) -> int:
        return len(self._completed)

    @property
    def failed_count(self) -> int:
        return len(self._failed)

    # ------------------------------------------------------------------
    # Lifecycle queries
    # ------------------------------------------------------------------

    def is_done(self) -> bool:
        """True when every task is in a terminal state."""
        return all(
            t.status
            in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.SKIPPED,
            }
            for t in self._plan.tasks.values()
        )

    def progress(self) -> float:
        """Return completion ratio in ``[0.0, 1.0]``."""
        total = len(self._plan.tasks)
        if total == 0:
            return 1.0
        done = sum(1 for t in self._plan.tasks.values() if t.status == TaskStatus.COMPLETED)
        return done / total

    # ------------------------------------------------------------------
    # Task selection
    # ------------------------------------------------------------------

    def next_tasks(self, *, max_tasks: int = 1) -> list[Task]:
        """Return the next batch of tasks ready to be dispatched.

        Selection criteria (in order):

        1. Status is READY.
        2. All dependencies are COMPLETED.
        3. Not currently running.
        4. Sorted by priority (desc) then by creation time (asc).

        The number of returned tasks is bounded by ``max_tasks`` and
        by ``config.max_parallel - running_count``.

        Raises:
            SchedulerPausedError: if the scheduler is paused.
        """
        if self._paused:
            raise SchedulerPausedError()
        if max_tasks <= 0:
            return []
        # First, promote any PENDING tasks whose deps are now complete.
        for t in self._plan.tasks.values():
            if t.status == TaskStatus.PENDING:
                self._maybe_promote_to_ready(t)
        # Compute the budget.
        budget = min(max_tasks, self._config.max_parallel - len(self._running))
        if budget <= 0:
            return []
        candidates: list[Task] = []
        for t in self._plan.tasks.values():
            if t.status != TaskStatus.READY:
                continue
            if t.id in self._running:
                continue
            if not self._deps_satisfied(t):
                continue
            candidates.append(t)
        # Sort by priority desc, then by created_at asc, then by id.
        candidates.sort(key=lambda t: (-priority_weight(t.priority), t.created_at, t.id))
        return candidates[:budget]

    def _deps_satisfied(self, task: Task) -> bool:
        """True if all dependencies of ``task`` are COMPLETED (or SKIPPED)."""
        for dep in task.dependencies:
            dep_task = self._plan.tasks.get(dep)
            if dep_task is None:
                return False
            if dep_task.status not in {TaskStatus.COMPLETED, TaskStatus.SKIPPED}:
                return False
        return True

    def _maybe_promote_to_ready(self, task: Task) -> None:
        """PENDING -> READY if all deps are satisfied."""
        if task.status != TaskStatus.PENDING:
            return
        if self._deps_satisfied(task):
            try:
                task.mark_ready()
                self._record_event("task.ready", task_id=task.id)
            except InvalidTaskError:
                pass

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def mark_started(self, task_id: str) -> None:
        """Mark ``task_id`` as RUNNING.

        Raises:
            TaskNotFoundError, SchedulerPausedError, InvalidTaskError.
        """
        self._require_paused()
        task = self._plan.require_task(task_id)
        if task.id in self._running:
            return  # idempotent
        if not self._deps_satisfied(task):
            raise InvalidTaskError(task_id, "dependencies not satisfied; cannot start")
        # If the task is PENDING (never promoted), promote it now.
        if task.status == TaskStatus.PENDING:
            task.mark_ready()
        task.mark_running()
        self._running.add(task.id)
        self._record_event("task.started", task_id=task.id)

    def mark_completed(self, task_id: str, *, result: Any = None) -> None:
        """Mark ``task_id`` as COMPLETED.

        Stores ``result`` in ``task.metadata['result']`` if provided.
        """
        task = self._plan.require_task(task_id)
        if task.id not in self._running:
            raise InvalidTaskError(task_id, "task is not running; cannot complete")
        task.mark_completed()
        if result is not None:
            task.metadata["result"] = result
        self._running.discard(task.id)
        self._completed.add(task.id)
        self._record_event("task.completed", task_id=task.id, has_result=result is not None)
        # Promote newly-ready tasks.
        self._promote_all_ready()

    def mark_failed(self, task_id: str, *, error: str = "") -> None:
        """Mark ``task_id`` as FAILED.

        If retries are available, the task is automatically transitioned
        back to READY (via :meth:`retry`). Otherwise the failure is
        terminal; if ``config.enable_replan_on_failure`` is set, the
        ``on_replan`` callback is invoked.
        """
        task = self._plan.require_task(task_id)
        if task.id not in self._running and task.status != TaskStatus.READY:
            raise InvalidTaskError(
                task_id,
                f"task is in state {task.status.value}; cannot mark failed",
            )
        task.mark_failed(error=error)
        self._running.discard(task.id)
        self._record_event(
            "task.failed",
            task_id=task.id,
            error=error,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
        )
        # Try retry if possible.
        if task.can_retry:
            self._schedule_retry(task)
            return
        # Terminal failure.
        self._failed.add(task.id)
        if self._config.pause_on_critical_failure and task.priority == TaskPriority.CRITICAL:
            self._paused = True
            self._record_event("scheduler.paused", reason="critical task failed terminally")
        if self._config.enable_replan_on_failure and self._on_replan is not None:
            try:
                self._on_replan(self._plan, task, error=error)
                self._record_event("planner.replan_triggered", task_id=task.id, error=error)
            except Exception:  # noqa: BLE001
                logger.exception("on_replan callback raised")

    def mark_waiting(self, task_id: str, *, reason: str = "") -> None:
        """RUNNING -> WAITING (the task is paused mid-execution)."""
        task = self._plan.require_task(task_id)
        if task.id not in self._running:
            raise InvalidTaskError(task_id, "task is not running; cannot mark waiting")
        task.mark_waiting()
        self._running.discard(task.id)
        self._waiting.add(task.id)
        self._record_event("task.waiting", task_id=task.id, reason=reason)

    def resume_task(self, task_id: str) -> None:
        """WAITING -> RUNNING (resume a paused task)."""
        self._require_paused()
        task = self._plan.require_task(task_id)
        if task.id not in self._waiting:
            raise InvalidTaskError(task_id, "task is not waiting; cannot resume")
        task.mark_running()
        self._waiting.discard(task.id)
        self._running.add(task.id)
        self._record_event("task.resumed", task_id=task.id)

    # ------------------------------------------------------------------
    # Retry
    # ------------------------------------------------------------------

    def retry(self, task_id: str) -> int:
        """Manually retry a FAILED task.

        Returns the new ``retry_count``.

        Raises:
            InvalidTaskError: if the task cannot be retried (not FAILED
                or max retries exhausted).
        """
        task = self._plan.require_task(task_id)
        if not task.can_retry:
            raise InvalidTaskError(
                task_id,
                f"task cannot be retried (status={task.status.value}, "
                f"retries={task.retry_count}/{task.max_retries})",
            )
        return self._schedule_retry(task)

    def _schedule_retry(self, task: Task) -> int:
        """Internal: schedule a retry for ``task``.

        Increments ``retry_count``, transitions FAILED -> READY, and
        records the backoff in ``metadata['next_retry_at']``.
        """
        attempt = task.increment_retry()
        # Back to READY.
        task.status = TaskStatus.READY
        task.updated_at = now_iso()
        # Compute backoff.
        backoff = self._config.retry_backoff_s * (2 ** (attempt - 1))
        if backoff > 0:
            task.metadata["next_retry_at"] = now_epoch() + backoff
        self._record_event(
            "task.retry_scheduled",
            task_id=task.id,
            retry_count=attempt,
            backoff_s=backoff,
        )
        return attempt

    # ------------------------------------------------------------------
    # Pause / Resume / Cancel
    # ------------------------------------------------------------------

    def pause(self) -> None:
        """Pause the scheduler.

        Already-running tasks continue (the scheduler cannot preempt
        them), but :meth:`next_tasks` will refuse to return new ones
        until :meth:`resume` is called.
        """
        if self._paused:
            return
        self._paused = True
        self._record_event("scheduler.paused", reason="manual")

    def resume(self) -> None:
        """Resume the scheduler after :meth:`pause`."""
        if not self._paused:
            return
        self._paused = False
        self._record_event("scheduler.resumed")
        # Promote any tasks that became ready while paused.
        self._promote_all_ready()

    def cancel(self, task_id: str, *, reason: str = "") -> None:
        """Cancel a single task.

        Works on any non-terminal task.
        """
        task = self._plan.require_task(task_id)
        if task.is_terminal:
            return
        # Force-cancel regardless of current state.
        if task.status == TaskStatus.RUNNING:
            self._running.discard(task.id)
        if task.id in self._waiting:
            self._waiting.discard(task.id)
        task.cancel()
        self._cancelled.add(task.id)
        self._record_event("task.cancelled", task_id=task.id, reason=reason)

    def cancel_all(self, *, reason: str = "bulk cancel") -> None:
        """Cancel every non-terminal task in the plan."""
        for t in list(self._plan.tasks.values()):
            if not t.is_terminal:
                self.cancel(t.id, reason=reason)

    def skip(self, task_id: str, *, reason: str = "") -> None:
        """Mark a task as SKIPPED (intentionally not executed)."""
        task = self._plan.require_task(task_id)
        if task.is_terminal:
            return
        if task.status == TaskStatus.RUNNING:
            self._running.discard(task.id)
        if task.id in self._waiting:
            self._waiting.discard(task.id)
        task.skip(reason=reason)
        self._record_event("task.skipped", task_id=task.id, reason=reason)
        # Skipping may unblock dependents.
        self._promote_all_ready()

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def to_state(self) -> dict[str, Any]:
        """Serialize the scheduler's runtime state.

        Useful for crash recovery: persist the plan + this state, then
        on restart reconstruct the scheduler and resume execution.
        """
        return {
            "plan_id": self._plan.id,
            "paused": self._paused,
            "running": sorted(self._running),
            "completed": sorted(self._completed),
            "failed": sorted(self._failed),
            "cancelled": sorted(self._cancelled),
            "waiting": sorted(self._waiting),
            "history": list(self._history),
            "config": {
                "max_parallel": self._config.max_parallel,
                "default_max_retries": self._config.default_max_retries,
                "retry_backoff_s": self._config.retry_backoff_s,
                "enable_replan_on_failure": self._config.enable_replan_on_failure,
                "pause_on_critical_failure": self._config.pause_on_critical_failure,
            },
            "saved_at": now_iso(),
        }

    @classmethod
    def from_state(
        cls,
        plan: ExecutionPlan,
        state: dict[str, Any],
        *,
        on_replan: Any | None = None,
    ) -> Scheduler:
        """Reconstruct a scheduler from :meth:`to_state` output.

        ``plan`` must be the same plan (by id) that was serialized.
        """
        if state.get("plan_id") != plan.id:
            raise SchedulerError(
                f"plan id mismatch: state has {state.get('plan_id')!r}, " f"plan has {plan.id!r}"
            )
        cfg = SchedulerConfig(
            max_parallel=state.get("config", {}).get("max_parallel", 4),
            default_max_retries=state.get("config", {}).get("default_max_retries", 3),
            retry_backoff_s=state.get("config", {}).get("retry_backoff_s", 0.0),
            enable_replan_on_failure=state.get("config", {}).get("enable_replan_on_failure", True),
            pause_on_critical_failure=state.get("config", {}).get(
                "pause_on_critical_failure", True
            ),
        )
        sched = cls(plan, config=cfg, on_replan=on_replan)
        sched._paused = bool(state.get("paused", False))
        sched._running = set(state.get("running", []))
        sched._completed = set(state.get("completed", []))
        sched._failed = set(state.get("failed", []))
        sched._cancelled = set(state.get("cancelled", []))
        sched._waiting = set(state.get("waiting", []))
        sched._history = list(state.get("history", []))
        return sched

    # ------------------------------------------------------------------
    # History / observability
    # ------------------------------------------------------------------

    def history(self) -> list[dict[str, Any]]:
        """Return a copy of the event history."""
        return list(self._history)

    def metrics(self) -> dict[str, Any]:
        """Return a summary of scheduler state."""
        return {
            "total_tasks": len(self._plan.tasks),
            "running": len(self._running),
            "completed": len(self._completed),
            "failed": len(self._failed),
            "cancelled": len(self._cancelled),
            "waiting": len(self._waiting),
            "paused": self._paused,
            "progress": round(self.progress(), 4),
            "is_done": self.is_done(),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_paused(self) -> None:
        if self._paused:
            raise SchedulerPausedError()

    def _promote_all_ready(self) -> None:
        """Re-evaluate every PENDING task; promote those whose deps are now done."""
        for t in self._plan.tasks.values():
            if t.status == TaskStatus.PENDING:
                self._maybe_promote_to_ready(t)

    def _record_event(self, event: str, **fields: Any) -> None:
        entry: dict[str, Any] = {
            "event": event,
            "ts": now_iso(),
            "epoch": now_epoch(),
        }
        entry.update(fields)
        self._history.append(entry)
        # Cap history to prevent unbounded growth.
        if len(self._history) > 1000:
            self._history = self._history[-500:]

    def __repr__(self) -> str:
        return (
            f"Scheduler(plan={self._plan.id!r}, "
            f"running={len(self._running)}, "
            f"completed={len(self._completed)}, "
            f"paused={self._paused})"
        )


__all__ = ["Scheduler", "SchedulerConfig"]
