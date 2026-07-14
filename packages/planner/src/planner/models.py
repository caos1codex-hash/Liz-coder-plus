"""Common enumerations and lightweight dataclasses for the planner (Sprint 2.8).

This module centralizes the canonical enums (``TaskStatus``,
``TaskPriority``, ``Difficulty``) and the small ``PlanStatistics``
dataclass that is referenced by :class:`~planner.execution_plan.ExecutionPlan`.

Keeping these in a separate module avoids circular imports between
``task.py``, ``graph.py`` and ``execution_plan.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ----------------------------------------------------------------------
# Task status
# ----------------------------------------------------------------------


class TaskStatus(str, Enum):  # noqa: UP042
    """Lifecycle state of a :class:`~planner.task.Task`.

    State machine::

        PENDING ──▶ READY ──▶ RUNNING ──▶ COMPLETED
           │           │         │
           │           │         ├──▶ FAILED ──▶ READY (retry)
           │           │         ├──▶ WAITING
           │           │         └──▶ CANCELLED
           │           │
           │           └──▶ CANCELLED / SKIPPED
           └──▶ CANCELLED / SKIPPED

    - ``PENDING``: created but not all dependencies satisfied.
    - ``READY``: dependencies satisfied; ready for an agent.
    - ``RUNNING``: an agent is executing it.
    - ``WAITING``: paused mid-execution (e.g. waiting on a sub-task).
    - ``COMPLETED``: finished successfully.
    - ``FAILED``: finished with errors; may be retried.
    - ``CANCELLED``: aborted by the user or scheduler.
    - ``SKIPPED``: intentionally not executed (e.g. condition false).
    """

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"

    @classmethod
    def is_terminal(cls, status: TaskStatus) -> bool:
        """True if ``status`` is a terminal state."""
        return status in {cls.COMPLETED, cls.FAILED, cls.CANCELLED, cls.SKIPPED}

    @classmethod
    def is_active(cls, status: TaskStatus) -> bool:
        """True if ``status`` is an active (non-terminal) state."""
        return not cls.is_terminal(status)


# Allowed transitions for state-machine validation.
_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset(
        {
            TaskStatus.READY,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        }
    ),
    TaskStatus.READY: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.WAITING,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.WAITING,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.WAITING: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.FAILED: frozenset(
        {
            TaskStatus.READY,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        }
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.SKIPPED: frozenset(),
}


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """True if ``current`` -> ``target`` is a valid state transition."""
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


# ----------------------------------------------------------------------
# Task priority
# ----------------------------------------------------------------------


class TaskPriority(str, Enum):  # noqa: UP042
    """Priority of a task (higher = more urgent).

    The integer weight is exposed via :func:`priority_weight` so that
    schedulers can sort with a stable, numeric key while users interact
    with the symbolic enum.

    This enum is intentionally aligned with the existing
    :class:`multiagent.enums.Priority` (CRITICAL/HIGH/NORMAL/LOW) but is
    declared separately to avoid a hard cross-package dependency at
    import time. Adapters translate between the two when needed.
    """

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    BACKGROUND = "background"


_PRIORITY_WEIGHT: dict[TaskPriority, int] = {
    TaskPriority.CRITICAL: 100,
    TaskPriority.HIGH: 75,
    TaskPriority.NORMAL: 50,
    TaskPriority.LOW: 25,
    TaskPriority.BACKGROUND: 10,
}


def priority_weight(p: TaskPriority) -> int:
    """Numeric weight for ``p`` (higher = more urgent)."""
    return _PRIORITY_WEIGHT[p]


# ----------------------------------------------------------------------
# Difficulty
# ----------------------------------------------------------------------


class Difficulty(str, Enum):  # noqa: UP042
    """Estimated difficulty of a task.

    Used by :class:`~planner.estimator.CostEstimator` to scale token and
    time estimates.
    """

    TRIVIAL = "trivial"
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXPERT = "expert"


_DIFFICULTY_MULTIPLIER: dict[Difficulty, float] = {
    Difficulty.TRIVIAL: 0.5,
    Difficulty.EASY: 0.8,
    Difficulty.MEDIUM: 1.0,
    Difficulty.HARD: 1.5,
    Difficulty.EXPERT: 2.5,
}


def difficulty_multiplier(d: Difficulty) -> float:
    """Linear multiplier used to scale estimates by difficulty."""
    return _DIFFICULTY_MULTIPLIER[d]


# ----------------------------------------------------------------------
# Plan statistics
# ----------------------------------------------------------------------


@dataclass
class PlanStatistics:
    """Aggregate statistics about an :class:`ExecutionPlan`.

    All fields default to zero so the dataclass is safe to instantiate
    before any tasks have been added.

    Attributes:
        total_tasks:        Total number of tasks in the plan.
        pending:            Tasks in PENDING state.
        ready:              Tasks in READY state.
        running:            Tasks in RUNNING state.
        waiting:            Tasks in WAITING state.
        completed:          Tasks in COMPLETED state.
        failed:             Tasks in FAILED state.
        cancelled:          Tasks in CANCELLED state.
        skipped:            Tasks in SKIPPED state.
        total_estimated_duration: Sum of estimated durations (seconds).
        total_estimated_tokens:   Sum of estimated LLM tokens.
        total_estimated_cost:     Sum of estimated costs (USD).
        parallel_groups:   Number of parallel execution layers.
        critical_path_len:  Number of tasks on the critical path.
    """

    total_tasks: int = 0
    pending: int = 0
    ready: int = 0
    running: int = 0
    waiting: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    skipped: int = 0
    total_estimated_duration: float = 0.0
    total_estimated_tokens: int = 0
    total_estimated_cost: float = 0.0
    parallel_groups: int = 0
    critical_path_len: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tasks": self.total_tasks,
            "by_status": {
                "pending": self.pending,
                "ready": self.ready,
                "running": self.running,
                "waiting": self.waiting,
                "completed": self.completed,
                "failed": self.failed,
                "cancelled": self.cancelled,
                "skipped": self.skipped,
            },
            "total_estimated_duration": round(self.total_estimated_duration, 3),
            "total_estimated_tokens": self.total_estimated_tokens,
            "total_estimated_cost": round(self.total_estimated_cost, 6),
            "parallel_groups": self.parallel_groups,
            "critical_path_len": self.critical_path_len,
        }


# ----------------------------------------------------------------------
# Capability scoring result
# ----------------------------------------------------------------------


@dataclass
class CapabilityScore:
    """Score breakdown for a single (task, agent) candidate.

    Attributes:
        agent_id:   Candidate agent id.
        score:      Final composite score in ``[0.0, 1.0]``.
        specialty:  Capability match score (0..1).
        availability: Agent availability score (0..1).
        workload:   Inverse workload score (0..1; higher = less loaded).
        priority:   Priority alignment score (0..1).
        affinity:   Affinity score (0..1).
        reason:     Optional human-readable explanation.
    """

    agent_id: str
    score: float = 0.0
    specialty: float = 0.0
    availability: float = 0.0
    workload: float = 0.0
    priority: float = 0.0
    affinity: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "score": round(self.score, 4),
            "specialty": round(self.specialty, 4),
            "availability": round(self.availability, 4),
            "workload": round(self.workload, 4),
            "priority": round(self.priority, 4),
            "affinity": round(self.affinity, 4),
            "reason": self.reason,
        }


@dataclass
class AgentAssignment:
    """Result of assigning an agent to a task.

    Attributes:
        task_id:    The task being assigned.
        agent_id:   Selected agent (None if unassigned).
        score:      Composite capability score.
        candidates: All considered (agent_id, score) pairs, sorted desc.
        assigned:   True if an agent was selected.
        reason:     Human-readable explanation.
    """

    task_id: str
    agent_id: str | None = None
    score: float = 0.0
    candidates: list[tuple[str, float]] = field(default_factory=list)
    assigned: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "assigned": self.assigned,
            "score": round(self.score, 4),
            "candidates": [{"agent_id": a, "score": round(s, 4)} for a, s in self.candidates],
            "reason": self.reason,
        }


__all__ = [
    "AgentAssignment",
    "CapabilityScore",
    "Difficulty",
    "PlanStatistics",
    "TaskPriority",
    "TaskStatus",
    "can_transition",
    "difficulty_multiplier",
    "priority_weight",
]
