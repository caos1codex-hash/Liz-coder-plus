"""Planner-specific exceptions.

Sprint 3.1 — Intelligent Task Planning Engine.

Provides a hierarchy of exception classes so that callers can catch
broad categories (:class:`PlannerException`) or narrow ones
(:class:`CircularDependencyError`, etc.).
"""

from __future__ import annotations


class PlannerException(Exception):
    """Base exception for all planner-related errors."""

    pass


class PlanValidationError(PlannerException):
    """Raised when a plan fails structural validation.

    Attributes:
        errors: Human-readable list of individual validation issues.
    """

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        self.errors: list[str] = errors or []
        super().__init__(message)


class SerializationError(PlannerException):
    """Raised when serialization or deserialization fails."""

    pass


class DependencyError(PlannerException):
    """Raised when a step references a dependency that does not exist."""

    def __init__(self, step_id: str, missing_dep: str) -> None:
        self.step_id = step_id
        self.missing_dep = missing_dep
        super().__init__(
            f"Step '{step_id}' depends on '{missing_dep}' which does not exist in the plan"
        )


class CircularDependencyError(PlannerException):
    """Raised when a cycle is detected among step dependencies.

    Attributes:
        cycle: Ordered list of step IDs forming the cycle.
    """

    def __init__(self, cycle: list[str]) -> None:
        self.cycle: list[str] = cycle
        display = " -> ".join(cycle)
        super().__init__(f"Circular dependency detected: {display}")


class DuplicateStepError(PlanValidationError):
    """Raised when duplicate step IDs are found."""

    def __init__(self, step_id: str) -> None:
        self.step_id = step_id
        super().__init__(
            f"Duplicate step ID: '{step_id}'", errors=[f"Duplicate step ID: '{step_id}'"]
        )


class InvalidStatusError(PlanValidationError):
    """Raised when a status transition is not allowed."""

    def __init__(self, current: str, target: str, entity: str = "plan") -> None:
        self.current = current
        self.target = target
        self.entity = entity
        super().__init__(
            f"Invalid {entity} status transition: '{current}' -> '{target}'",
            errors=[f"Invalid {entity} status transition: '{current}' -> '{target}'"],
        )
