"""Planner enumerations.

Sprint 3.1 — Intelligent Task Planning Engine.

Defines status and priority enums used throughout the planner package.
Follows the project convention of ``str, Enum`` with explicit transition maps
for state-machine-like validation.
"""

from __future__ import annotations

from enum import Enum

# ---------------------------------------------------------------------------
# Plan-level status
# ---------------------------------------------------------------------------


class PlanStatus(str, Enum):
    """Lifecycle status of a :class:`TaskPlan`.

    Transitions are constrained by :data:`_ALLOWED_PLAN_TRANSITIONS`.
    Use :func:`can_transition_plan` to validate before mutating.
    """

    NOT_STARTED = "not_started"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_PLAN_TRANSITIONS: dict[PlanStatus, frozenset[PlanStatus]] = {
    PlanStatus.NOT_STARTED: frozenset({PlanStatus.PLANNING, PlanStatus.CANCELLED}),
    PlanStatus.PLANNING: frozenset({PlanStatus.READY, PlanStatus.FAILED, PlanStatus.CANCELLED}),
    PlanStatus.READY: frozenset({PlanStatus.RUNNING, PlanStatus.CANCELLED}),
    PlanStatus.RUNNING: frozenset({PlanStatus.COMPLETED, PlanStatus.FAILED, PlanStatus.CANCELLED}),
    PlanStatus.COMPLETED: frozenset(),
    PlanStatus.FAILED: frozenset({PlanStatus.PLANNING, PlanStatus.CANCELLED}),
    PlanStatus.CANCELLED: frozenset(),
}


def can_transition_plan(current: PlanStatus, target: PlanStatus) -> bool:
    """Return ``True`` if transitioning from *current* to *target* is allowed."""
    return target in _ALLOWED_PLAN_TRANSITIONS.get(current, frozenset())


# ---------------------------------------------------------------------------
# Step-level status
# ---------------------------------------------------------------------------


class StepStatus(str, Enum):
    """Lifecycle status of a single :class:`PlanStep`.

    Transitions are constrained by :data:`_ALLOWED_STEP_TRANSITIONS`.
    """

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


_ALLOWED_STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.READY, StepStatus.SKIPPED}),
    StepStatus.READY: frozenset({StepStatus.RUNNING, StepStatus.SKIPPED}),
    StepStatus.RUNNING: frozenset({StepStatus.SUCCESS, StepStatus.FAILED}),
    StepStatus.SUCCESS: frozenset(),
    StepStatus.FAILED: frozenset({StepStatus.READY, StepStatus.SKIPPED}),
    StepStatus.SKIPPED: frozenset(),
}


def can_transition_step(current: StepStatus, target: StepStatus) -> bool:
    """Return ``True`` if transitioning from *current* to *target* is allowed."""
    return target in _ALLOWED_STEP_TRANSITIONS.get(current, frozenset())


# ---------------------------------------------------------------------------
# Priority levels
# ---------------------------------------------------------------------------


class Priority(str, Enum):
    """Priority levels for a :class:`TaskPlan`.

    Ordered from lowest to highest urgency.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"
