"""Planner-specific exceptions.

Sprint 3.1 — Intelligent Task Planning Engine.
Sprint 3.3 — Agent-Aware Planning (new: AgentPlanningError branch).
Sprint 3.5 — Advanced Planner (new: DecompositionError, StrategyError,
            GraphError, PlanningMemoryError).

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
            f"Duplicate step ID: '{step_id}'",
            errors=[f"Duplicate step ID: '{step_id}'"],
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


# ---------------------------------------------------------------------------
# Sprint 3.3 — Agent-Aware Planning exceptions
# ---------------------------------------------------------------------------


class AgentPlanningError(PlannerException):
    """Base exception for agent-aware planning errors (Sprint 3.3).

    All agent-related planning failures inherit from this class,
    enabling callers to catch the entire category with a single handler.
    """

    pass


class AgentNotFoundError(AgentPlanningError):
    """Raised when a referenced agent does not exist in the registry.

    Attributes:
        agent_id: The identifier (name or UUID) that was not found.
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"Agent '{agent_id}' not found in the registry")


class CapabilityMissingError(AgentPlanningError):
    """Raised when a required capability is not provided by any registered agent.

    Attributes:
        capability: The capability name that is missing.
    """

    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(f"No agent provides the required capability: '{capability}'")


class AssignmentError(AgentPlanningError):
    """Raised when an agent-to-step assignment cannot be performed.

    This covers cases such as:
    - The agent is not available (busy, stopped, or dead).
    - A capability conflict exists between required and provided capabilities.
    - The step already has an incompatible assignment.

    Attributes:
        step_id: The step that could not be assigned.
        agent_id: The agent that was rejected.
        reason: Human-readable explanation of the failure.
    """

    def __init__(self, step_id: str, agent_id: str, reason: str) -> None:
        self.step_id = step_id
        self.agent_id = agent_id
        self.reason = reason
        super().__init__(
            f"Cannot assign agent '{agent_id}' to step '{step_id}': {reason}"
        )


# ---------------------------------------------------------------------------
# Sprint 3.5 — Advanced Planner exceptions
# ---------------------------------------------------------------------------


class DecompositionError(PlannerException):
    """Raised when a goal cannot be decomposed into subtasks.

    Attributes:
        goal: The original goal that failed decomposition.
        reason: Human-readable explanation.
    """

    def __init__(self, goal: str, reason: str) -> None:
        self.goal = goal
        self.reason = reason
        super().__init__(f"Cannot decompose goal '{goal}': {reason}")


class StrategyError(PlannerException):
    """Raised when an execution strategy cannot be applied.

    Attributes:
        reason: Human-readable explanation.
        step_id: Optional step ID that triggered the failure.
    """

    def __init__(self, reason: str, step_id: str | None = None) -> None:
        self.reason = reason
        self.step_id = step_id
        msg = f"Execution strategy error: {reason}"
        if step_id:
            msg += f" (step: {step_id})"
        super().__init__(msg)


class GraphError(PlannerException):
    """Raised when the dependency graph is structurally invalid.

    Distinct from :class:`CircularDependencyError` (which is a specific
    kind of graph error): ``GraphError`` covers cases such as missing
    nodes, duplicate edges, or attempts to mutate a frozen graph.

    Attributes:
        reason: Human-readable explanation.
        node_id: Optional node ID that triggered the failure.
    """

    def __init__(self, reason: str, node_id: str | None = None) -> None:
        self.reason = reason
        self.node_id = node_id
        msg = f"Dependency graph error: {reason}"
        if node_id:
            msg += f" (node: {node_id})"
        super().__init__(msg)


class PlanningMemoryError(PlannerException):
    """Raised when planner memory operations fail.

    Attributes:
        operation: The memory operation that failed (e.g. ``"store"``,
            ``"retrieve"``, ``"match"``).
        reason: Human-readable explanation.
    """

    def __init__(self, operation: str, reason: str) -> None:
        self.operation = operation
        self.reason = reason
        super().__init__(f"Planning memory '{operation}' failed: {reason}")


class EscalationError(PlannerException):
    """Raised when an escalation path cannot be satisfied.

    Attributes:
        step_id: The step whose failure triggered the escalation.
        reason: Why the escalation could not be applied.
    """

    def __init__(self, step_id: str, reason: str) -> None:
        self.step_id = step_id
        self.reason = reason
        super().__init__(
            f"Cannot escalate failure for step '{step_id}': {reason}"
        )
