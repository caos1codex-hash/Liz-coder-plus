"""Plan execution models.

Sprint 3.6.

Defines the state machines and data models for the Planner execution
flow. An ``ExecutablePlan`` tracks its own lifecycle (PlanState) and
contains ``PlanTask`` items, each with their own lifecycle
(PlanTaskStatus).

Flow::

    User → Planner → ExecutablePlan (CREATED) → VALIDATING → READY
           → RUNNING → COMPLETED / FAILED / CANCELLED

    Each PlanTask:
        PENDING → ASSIGNED → RUNNING → COMPLETED / FAILED / SKIPPED
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Plan-level state machine
# ----------------------------------------------------------------------


class PlanState(str, Enum):
    """Lifecycle states of an ExecutablePlan.

    Transitions::

        CREATED → VALIDATING → READY → RUNNING → COMPLETED
                              ↘         ↘→ FAILED
                                → CANCELLED
                              RUNNING → PAUSED → RUNNING
    """

    CREATED = "created"
    VALIDATING = "validating"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_PLAN_TRANSITIONS: dict[PlanState, frozenset[PlanState]] = {
    PlanState.CREATED: frozenset({
        PlanState.VALIDATING, PlanState.CANCELLED,
    }),
    PlanState.VALIDATING: frozenset({
        PlanState.READY, PlanState.FAILED, PlanState.CANCELLED,
    }),
    PlanState.READY: frozenset({
        PlanState.RUNNING, PlanState.CANCELLED,
    }),
    PlanState.RUNNING: frozenset({
        PlanState.COMPLETED, PlanState.FAILED,
        PlanState.PAUSED, PlanState.CANCELLED,
    }),
    PlanState.PAUSED: frozenset({
        PlanState.RUNNING, PlanState.CANCELLED,
    }),
    PlanState.COMPLETED: frozenset(),  # terminal
    PlanState.FAILED: frozenset({PlanState.VALIDATING}),  # can re-plan
    PlanState.CANCELLED: frozenset(),  # terminal
}


def can_transition_plan(current: PlanState, target: PlanState) -> bool:
    """Return True if a plan can transition from *current* to *target*."""
    return target in _ALLOWED_PLAN_TRANSITIONS.get(current, frozenset())


# ----------------------------------------------------------------------
# Task-level state machine
# ----------------------------------------------------------------------


class PlanTaskStatus(str, Enum):
    """Lifecycle states of a PlanTask within an ExecutablePlan.

    Transitions::

        PENDING → ASSIGNED → RUNNING → COMPLETED
                                    ↘→ FAILED → ASSIGNED (retry)
                                    ↘→ SKIPPED
    """

    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


_ALLOWED_TASK_TRANSITIONS: dict[PlanTaskStatus, frozenset[PlanTaskStatus]] = {
    PlanTaskStatus.PENDING: frozenset({
        PlanTaskStatus.ASSIGNED, PlanTaskStatus.SKIPPED,
    }),
    PlanTaskStatus.ASSIGNED: frozenset({
        PlanTaskStatus.RUNNING, PlanTaskStatus.SKIPPED,
    }),
    PlanTaskStatus.RUNNING: frozenset({
        PlanTaskStatus.COMPLETED, PlanTaskStatus.FAILED, PlanTaskStatus.SKIPPED,
    }),
    PlanTaskStatus.FAILED: frozenset({
        PlanTaskStatus.ASSIGNED, PlanTaskStatus.SKIPPED,
    }),
    PlanTaskStatus.COMPLETED: frozenset(),  # terminal
    PlanTaskStatus.SKIPPED: frozenset(),  # terminal
}


def can_transition_plan_task(
    current: PlanTaskStatus, target: PlanTaskStatus
) -> bool:
    """Return True if a plan task can transition."""
    return target in _ALLOWED_TASK_TRANSITIONS.get(current, frozenset())


# ----------------------------------------------------------------------
# PlanTask data model
# ----------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _jsonable(value: Any) -> bool:
    try:
        import json
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


@dataclass
class PlanTask:
    """A single task within an ExecutablePlan.

    Attributes:
        id:              Unique identifier (UUID4).
        name:            Short name (from the Planner's SubTask).
        description:     What this task should accomplish.
        agent_assigned:  Name of the agent assigned to execute this task.
        dependencies:    Names of sibling tasks that must complete first.
        status:          Current PlanTaskStatus.
        result:          Output of the task execution (any).
        error:           Last error message if the task failed.
        retry_count:     Number of retry attempts so far.
        max_retries:     Maximum allowed retries.
        started_at:      ISO timestamp when execution started.
        completed_at:    ISO timestamp when execution finished.
        created_at:      ISO timestamp when the task was created.
        execution_time_ms: Duration of the last execution attempt in ms.
        metadata:        Free-form metadata bag.
    """

    name: str
    description: str = ""
    agent_assigned: str = ""
    dependencies: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: PlanTaskStatus = PlanTaskStatus.PENDING
    result: Any = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str = field(default_factory=_now_iso)
    execution_time_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            PlanTaskStatus.COMPLETED,
            PlanTaskStatus.FAILED,
            PlanTaskStatus.SKIPPED,
        )

    @property
    def can_retry(self) -> bool:
        return (
            self.status == PlanTaskStatus.FAILED
            and self.retry_count < self.max_retries
        )

    def transition_to(self, new_status: PlanTaskStatus) -> None:
        """Transition to a new status with validation.

        Raises:
            ValueError: If the transition is not allowed.
        """
        if not can_transition_plan_task(self.status, new_status):
            raise ValueError(
                f"PlanTask '{self.name}' cannot transition from "
                f"{self.status.value} to {new_status.value}"
            )
        old = self.status
        self.status = new_status

        if new_status == PlanTaskStatus.RUNNING and self.started_at is None:
            self.started_at = _now_iso()
        if new_status in (
            PlanTaskStatus.COMPLETED,
            PlanTaskStatus.FAILED,
            PlanTaskStatus.SKIPPED,
        ):
            self.completed_at = _now_iso()

        logger.debug(
            "PlanTask '%s' (%s) %s -> %s",
            self.name, self.id[:8], old.value, new_status.value,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "agent_assigned": self.agent_assigned,
            "dependencies": list(self.dependencies),
            "status": self.status.value,
            "result": self.result if _jsonable(self.result) else str(self.result),
            "error": self.error,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
            "execution_time_ms": self.execution_time_ms,
            "metadata": dict(self.metadata),
        }


# ----------------------------------------------------------------------
# ExecutablePlan data model
# ----------------------------------------------------------------------


@dataclass
class ExecutablePlan:
    """A plan that can be validated, executed, and tracked.

    Wraps the Planner's output with full lifecycle management.

    Attributes:
        id:              Unique identifier (UUID4).
        original_message: The user message that triggered this plan.
        strategy:        Planning strategy label (from Planner).
        rationale:       Why this plan was chosen.
        state:           Current PlanState.
        tasks:           Ordered list of PlanTask instances.
        error:           Last error if the plan failed.
        created_at:      ISO timestamp.
        updated_at:      ISO timestamp.
        started_at:      ISO timestamp when execution began.
        completed_at:    ISO timestamp when execution finished.
        metadata:        Free-form metadata.
    """

    strategy: str
    original_message: str = ""
    rationale: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: PlanState = PlanState.CREATED
    tasks: list[PlanTask] = field(default_factory=list)
    error: str | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def transition_to(self, new_state: PlanState) -> None:
        """Transition the plan to a new state.

        Raises:
            ValueError: If the transition is not allowed.
        """
        if not can_transition_plan(self.state, new_state):
            raise ValueError(
                f"Plan '{self.id[:8]}' cannot transition from "
                f"{self.state.value} to {new_state.value}"
            )
        old = self.state
        self.state = new_state
        self._touch()

        if new_state == PlanState.RUNNING and self.started_at is None:
            self.started_at = _now_iso()
        if new_state in (
            PlanState.COMPLETED,
            PlanState.FAILED,
            PlanState.CANCELLED,
        ):
            self.completed_at = _now_iso()

        logger.debug(
            "Plan '%s' %s -> %s",
            self.id[:8], old.value, new_state.value,
        )

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            PlanState.COMPLETED,
            PlanState.FAILED,
            PlanState.CANCELLED,
        )

    # ------------------------------------------------------------------
    # Task queries
    # ------------------------------------------------------------------

    @property
    def task_count(self) -> int:
        return len(self.tasks)

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == PlanTaskStatus.COMPLETED)

    @property
    def failed_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == PlanTaskStatus.FAILED)

    @property
    def skipped_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == PlanTaskStatus.SKIPPED)

    @property
    def progress(self) -> float:
        """Progress ratio 0.0..1.0."""
        if not self.tasks:
            return 0.0
        done = sum(
            1 for t in self.tasks
            if t.status in (
                PlanTaskStatus.COMPLETED,
                PlanTaskStatus.SKIPPED,
            )
        )
        return done / len(self.tasks)

    def get_task(self, task_id: str) -> PlanTask | None:
        """Look up a task by id."""
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def get_task_by_name(self, name: str) -> PlanTask | None:
        """Look up a task by name."""
        for t in self.tasks:
            if t.name == name:
                return t
        return None

    def ready_tasks(self) -> list[PlanTask]:
        """Return tasks whose dependencies are all completed and are PENDING or ASSIGNED."""
        completed_names: set[str] = set()
        for t in self.tasks:
            if t.status == PlanTaskStatus.COMPLETED:
                completed_names.add(t.name)

        ready: list[PlanTask] = []
        for t in self.tasks:
            if t.status not in (
                PlanTaskStatus.PENDING,
                PlanTaskStatus.ASSIGNED,
            ):
                continue
            if all(dep in completed_names for dep in t.dependencies):
                ready.append(t)
        return ready

    def next_task(self) -> PlanTask | None:
        """Return the first ready task, or None."""
        ready = self.ready_tasks()
        return ready[0] if ready else None

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """Return aggregate metrics for this plan."""
        total_duration = sum(t.execution_time_ms for t in self.tasks)
        total_retries = sum(t.retry_count for t in self.tasks)
        return {
            "plan_id": self.id,
            "strategy": self.strategy,
            "state": self.state.value,
            "progress": round(self.progress, 4),
            "task_count": self.task_count,
            "completed": self.completed_count,
            "failed": self.failed_count,
            "skipped": self.skipped_count,
            "total_duration_ms": round(total_duration, 2),
            "total_retries": total_retries,
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize the plan to a JSON-compatible dict."""
        return {
            "id": self.id,
            "original_message": self.original_message,
            "strategy": self.strategy,
            "rationale": self.rationale,
            "state": self.state.value,
            "tasks": [t.to_dict() for t in self.tasks],
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metrics": self.metrics(),
            "metadata": dict(self.metadata),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _touch(self) -> None:
        self.updated_at = _now_iso()


__all__ = [
    "ExecutablePlan",
    "PlanState",
    "PlanTask",
    "PlanTaskStatus",
    "can_transition_plan",
    "can_transition_plan_task",
]