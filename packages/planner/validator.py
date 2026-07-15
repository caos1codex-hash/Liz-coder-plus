"""Plan validator.

Sprint 3.1 — Intelligent Task Planning Engine.

Validates structural integrity of a :class:`TaskPlan` before it is
considered ready for execution.  Checks include:

* Unique step IDs
* No duplicate steps
* All dependency references exist
* No circular dependencies (DFS-based cycle detection)
* Valid metadata types
* Valid status transitions (via enum transition maps)
"""

from __future__ import annotations

from .enums import PlanStatus, StepStatus, can_transition_plan, can_transition_step
from .exceptions import (
    CircularDependencyError,
    DependencyError,
    DuplicateStepError,
    InvalidStatusError,
    PlanValidationError,
)
from .interfaces import IValidator
from .models import TaskPlan


class PlanValidator(IValidator):
    """Concrete implementation of :class:`IValidator`.

    All validation methods are public so they can be called individually
    or composed via :meth:`validate`.
    """

    # -- IValidator ----------------------------------------------------------

    def validate(self, plan: TaskPlan) -> list[str]:
        """Run all checks and return accumulated error messages."""
        errors: list[str] = []
        errors.extend(self._check_unique_step_ids(plan))
        errors.extend(self._check_valid_plan_status(plan))
        errors.extend(self._check_valid_step_statuses(plan))
        errors.extend(self._check_existing_dependencies(plan))
        errors.extend(self._check_no_cycles(plan))
        errors.extend(self._check_metadata_types(plan))
        return errors

    # -- individual checks ---------------------------------------------------

    @staticmethod
    def _check_unique_step_ids(plan: TaskPlan) -> list[str]:
        """Ensure every step has a unique ID."""
        seen: set[str] = set()
        errors: list[str] = []
        for step in plan.steps:
            if step.id in seen:
                errors.append(f"Duplicate step ID: '{step.id}'")
            seen.add(step.id)
        return errors

    @staticmethod
    def _check_valid_plan_status(plan: TaskPlan) -> list[str]:
        """Ensure the plan status is a valid :class:`PlanStatus` value."""
        if not isinstance(plan.status, PlanStatus):
            return [f"Invalid plan status: {plan.status!r}"]
        return []

    @staticmethod
    def _check_valid_step_statuses(plan: TaskPlan) -> list[str]:
        """Ensure every step status is a valid :class:`StepStatus` value."""
        errors: list[str] = []
        for step in plan.steps:
            if not isinstance(step.status, StepStatus):
                errors.append(f"Step '{step.id}' has invalid status: {step.status!r}")
        return errors

    @staticmethod
    def _check_existing_dependencies(plan: TaskPlan) -> list[str]:
        """Ensure every dependency reference points to an existing step."""
        step_ids = {s.id for s in plan.steps}
        errors: list[str] = []
        for step in plan.steps:
            for dep in step.dependencies:
                if dep not in step_ids:
                    errors.append(f"Step '{step.id}' depends on '{dep}' which does not exist")
        return errors

    @staticmethod
    def _check_no_cycles(plan: TaskPlan) -> list[str]:
        """Detect cycles among step dependencies using DFS with 3-colour marking.

        Returns a list containing at most one error message describing the
        first cycle found.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        colour: dict[str, int] = {s.id: WHITE for s in plan.steps}
        parent: dict[str, str | None] = {s.id: None for s in plan.steps}
        adj: dict[str, list[str]] = {s.id: list(s.dependencies) for s in plan.steps}

        def _dfs(node: str) -> list[str] | None:
            colour[node] = GRAY
            for neighbour in adj.get(node, []):
                if colour.get(neighbour, WHITE) == GRAY:
                    # Reconstruct cycle
                    cycle = [neighbour, node]
                    cur = parent[node]
                    while cur is not None and cur != neighbour:
                        cycle.append(cur)
                        cur = parent[cur]
                    cycle.append(neighbour)
                    cycle.reverse()
                    return [f"Circular dependency detected: {' -> '.join(cycle)}"]
                if colour.get(neighbour, WHITE) == WHITE:
                    parent[neighbour] = node
                    result = _dfs(neighbour)
                    if result:
                        return result
            colour[node] = BLACK
            return None

        for step in plan.steps:
            if colour[step.id] == WHITE:
                result = _dfs(step.id)
                if result:
                    return result
        return []

    @staticmethod
    def _check_metadata_types(plan: TaskPlan) -> list[str]:
        """Ensure plan-level metadata values are JSON-serialisable types."""
        errors: list[str] = []
        allowed_types = (str, int, float, bool, list, dict, type(None))
        for key, value in plan.metadata.items():
            if not isinstance(value, allowed_types):
                errors.append(
                    f"Plan metadata key '{key}' has non-serializable type: {type(value).__name__}"
                )
        # Also check step metadata
        for step in plan.steps:
            for key, value in step.metadata.items():
                if not isinstance(value, allowed_types):
                    errors.append(
                        f"Step '{step.id}' metadata key '{key}' has non-serializable type: "
                        f"{type(value).__name__}"
                    )
        return errors

    # -- exception-raising convenience methods --------------------------------

    def validate_or_raise(self, plan: TaskPlan) -> None:
        """Validate and raise the first specific exception on failure.

        Raises:
            PlanValidationError: When validation fails with no specific exception type.
            DuplicateStepError: When duplicate step IDs are found.
            DependencyError: When a dependency reference is broken.
            CircularDependencyError: When a cycle is detected.
        """
        # Check duplicates first (specific exception)
        dup_errors = self._check_unique_step_ids(plan)
        if dup_errors:
            # Extract first duplicate ID
            seen: set[str] = set()
            for step in plan.steps:
                if step.id in seen:
                    raise DuplicateStepError(step.id)
                seen.add(step.id)

        # Check dependencies
        dep_errors = self._check_existing_dependencies(plan)
        if dep_errors:
            for step in plan.steps:
                for dep in step.dependencies:
                    step_ids = {s.id for s in plan.steps}
                    if dep not in step_ids:
                        raise DependencyError(step.id, dep)

        # Check cycles
        cycle_errors = self._check_no_cycles(plan)
        if cycle_errors:
            # Extract the cycle IDs from the error message
            cycle = self._extract_cycle(plan)
            raise CircularDependencyError(cycle)

        # General validation
        all_errors = self.validate(plan)
        if all_errors:
            raise PlanValidationError(
                f"Plan validation failed with {len(all_errors)} error(s)",
                errors=all_errors,
            )

    @staticmethod
    def _extract_cycle(plan: TaskPlan) -> list[str]:
        """Extract the actual cycle IDs from the plan's dependency graph."""
        WHITE, GRAY, BLACK = 0, 1, 2
        colour: dict[str, int] = {s.id: WHITE for s in plan.steps}
        parent: dict[str, str | None] = {s.id: None for s in plan.steps}
        adj: dict[str, list[str]] = {s.id: list(s.dependencies) for s in plan.steps}

        def _dfs(node: str) -> list[str] | None:
            colour[node] = GRAY
            for neighbour in adj.get(node, []):
                if colour.get(neighbour, WHITE) == GRAY:
                    cycle = [neighbour, node]
                    cur = parent[node]
                    while cur is not None and cur != neighbour:
                        cycle.append(cur)
                        cur = parent[cur]
                    cycle.append(neighbour)
                    cycle.reverse()
                    return cycle
                if colour.get(neighbour, WHITE) == WHITE:
                    parent[neighbour] = node
                    result = _dfs(neighbour)
                    if result:
                        return result
            colour[node] = BLACK
            return None

        for step in plan.steps:
            if colour[step.id] == WHITE:
                result = _dfs(step.id)
                if result:
                    return result
        return []

    # -- status transition validation ----------------------------------------

    @staticmethod
    def validate_plan_status_transition(current: PlanStatus, target: PlanStatus) -> None:
        """Raise :class:`InvalidStatusError` if the transition is not allowed."""
        if not can_transition_plan(current, target):
            raise InvalidStatusError(current.value, target.value, entity="plan")

    @staticmethod
    def validate_step_status_transition(current: StepStatus, target: StepStatus) -> None:
        """Raise :class:`InvalidStatusError` if the transition is not allowed."""
        if not can_transition_step(current, target):
            raise InvalidStatusError(current.value, target.value, entity="step")
