"""Custom exception hierarchy for the planner package (Sprint 2.8).

All planner exceptions inherit from :class:`PlannerError`, allowing
callers to catch any planner-related failure with a single ``except``
clause. The hierarchy is intentionally granular so that error-handling
code can discriminate between recoverable failures (e.g. a task that
can be retried) and structural failures (e.g. a cycle in the DAG that
makes a plan un-executable).

Exception map::

    PlannerError
    ├── TaskNotFoundError          (lookup miss)
    ├── TaskAlreadyExistsError     (duplicate id)
    ├── InvalidTaskError           (bad field / state)
    ├── CycleDetectedError         (DAG has cycle)
    ├── DependencyError            (broken / unsatisfiable dep)
    ├── PlanValidationError        (plan failed validate())
    ├── SchedulerError             (generic scheduler fault)
    ├── SchedulerPausedError       (scheduler is paused)
    ├── AgentAssignmentError       (no agent available)
    └── EstimationError            (estimator misconfigured)
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# ----------------------------------------------------------------------
# Base
# ----------------------------------------------------------------------


class PlannerError(Exception):
    """Base class for every planner-related exception.

    Attributes:
        message: Human-readable description.
        details: Optional structured payload (task id, plan id, etc.).
    """

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": type(self).__name__,
            "message": self.message,
            "details": dict(self.details),
        }

    def __str__(self) -> str:
        if not self.message:
            return type(self).__name__
        if self.details:
            return f"{self.message} :: {self.details}"
        return self.message


# ----------------------------------------------------------------------
# Task errors
# ----------------------------------------------------------------------


class TaskNotFoundError(PlannerError):
    """Raised when a task id is not present in a plan or scheduler."""

    def __init__(self, task_id: str, *, message: str = "") -> None:
        super().__init__(
            message or f"Task '{task_id}' not found",
            details={"task_id": task_id},
        )
        self.task_id = task_id


class TaskAlreadyExistsError(PlannerError):
    """Raised when adding a task with an id that already exists."""

    def __init__(self, task_id: str, *, message: str = "") -> None:
        super().__init__(
            message or f"Task '{task_id}' already exists",
            details={"task_id": task_id},
        )
        self.task_id = task_id


class InvalidTaskError(PlannerError):
    """Raised when a task field or transition is invalid."""

    def __init__(self, task_id: str, reason: str) -> None:
        super().__init__(
            f"Invalid task '{task_id}': {reason}",
            details={"task_id": task_id, "reason": reason},
        )
        self.task_id = task_id
        self.reason = reason


# ----------------------------------------------------------------------
# Graph errors
# ----------------------------------------------------------------------


class CycleDetectedError(PlannerError):
    """Raised when adding an edge would create a cycle in the DAG.

    Attributes:
        cycle: List of task ids that form the cycle (in order).
    """

    def __init__(self, cycle: Iterable[str], *, message: str = "") -> None:
        cycle_list = list(cycle)
        cycle_repr = " -> ".join(cycle_list + [cycle_list[0] if cycle_list else ""])
        super().__init__(
            message or f"Cycle detected: {cycle_repr}",
            details={"cycle": cycle_list},
        )
        self.cycle = cycle_list


class DependencyError(PlannerError):
    """Raised when a dependency is broken or cannot be satisfied."""

    def __init__(self, task_id: str, dependency_id: str, *, reason: str = "") -> None:
        super().__init__(
            reason or f"Broken dependency: '{task_id}' -> '{dependency_id}'",
            details={"task_id": task_id, "dependency_id": dependency_id},
        )
        self.task_id = task_id
        self.dependency_id = dependency_id


# ----------------------------------------------------------------------
# Plan errors
# ----------------------------------------------------------------------


class PlanValidationError(PlannerError):
    """Raised when :meth:`ExecutionPlan.validate` finds structural issues.

    Attributes:
        errors: List of human-readable error strings.
    """

    def __init__(self, errors: Iterable[str], *, plan_id: str | None = None) -> None:
        errors_list = list(errors)
        msg = "; ".join(errors_list) if errors_list else "plan validation failed"
        super().__init__(
            msg,
            details={"plan_id": plan_id, "errors": errors_list},
        )
        self.errors = errors_list
        self.plan_id = plan_id


# ----------------------------------------------------------------------
# Scheduler errors
# ----------------------------------------------------------------------


class SchedulerError(PlannerError):
    """Generic scheduler fault (invalid state, unexpected transition)."""


class SchedulerPausedError(SchedulerError):
    """Raised when an operation is attempted on a paused scheduler."""

    def __init__(self) -> None:
        super().__init__("Scheduler is paused; call resume() before scheduling")


# ----------------------------------------------------------------------
# Capability / Estimation errors
# ----------------------------------------------------------------------


class AgentAssignmentError(PlannerError):
    """Raised when no agent can be assigned to a task."""

    def __init__(self, task_id: str, *, reason: str = "") -> None:
        super().__init__(
            reason or f"No agent available for task '{task_id}'",
            details={"task_id": task_id},
        )
        self.task_id = task_id


class EstimationError(PlannerError):
    """Raised when the estimator cannot produce a numeric estimate."""


__all__ = [
    "AgentAssignmentError",
    "CycleDetectedError",
    "DependencyError",
    "EstimationError",
    "InvalidTaskError",
    "PlanValidationError",
    "PlannerError",
    "SchedulerError",
    "SchedulerPausedError",
    "TaskAlreadyExistsError",
    "TaskNotFoundError",
]
