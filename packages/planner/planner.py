"""Task Planner — main entry point.

Sprint 3.1 + 3.2 — Intelligent Task Planning Engine.

:class:`TaskPlanner` orchestrates plan creation, validation, cloning,
estimation, and (de)serialization by delegating to the respective
sub-components (:class:`PlanValidator`, :class:`PlanSerializer`,
:class:`PlannerMetrics`).

As of Sprint 3.2 the planner can also **generate** plans from natural-
language goals via :meth:`create_from_goal`, which delegates to
:class:`PlanGenerator`.

The planner **never** executes tasks — it only builds and manages plan
structures.
"""

from __future__ import annotations

import copy
import time
from typing import Any

from .enums import PlanStatus, Priority
from .interfaces import IMetrics, IPlanner, ISerializer, IValidator
from .metrics import PlanMetrics, PlannerMetrics
from .models import PlanStep, TaskPlan
from .serializer import PlanSerializer
from .validator import PlanValidator


class TaskPlanner(IPlanner):
    """Create, validate, clone, estimate, and serialize execution plans.

    The class follows a composition-over-inheritance design: each capability
    is provided by an injectable component that implements the corresponding
    ``I*`` interface.

    Args:
        validator: Optional custom validator. Defaults to :class:`PlanValidator`.
        serializer: Optional custom serializer. Defaults to :class:`PlanSerializer`.
        metrics: Optional custom metrics calculator. Defaults to :class:`PlannerMetrics`.

    Example::

        planner = TaskPlanner()
        plan = planner.create_plan(
            goal="Deploy to production",
            steps=[PlanStep(title="Run tests"), PlanStep(title="Push to registry")],
        )
        planner.validate(plan)
        json_blob = planner.serialize(plan, format="json")
    """

    def __init__(
        self,
        validator: IValidator | None = None,
        serializer: ISerializer | None = None,
        metrics: IMetrics | None = None,
    ) -> None:
        self._validator = validator or PlanValidator()
        self._serializer = serializer or PlanSerializer()
        self._metrics = metrics or PlannerMetrics()

    # -- properties ----------------------------------------------------------

    @property
    def validator(self) -> IValidator:
        """The validator instance."""
        return self._validator

    @property
    def serializer(self) -> ISerializer:
        """The serializer instance."""
        return self._serializer

    @property
    def metrics(self) -> IMetrics:
        """The metrics calculator instance."""
        return self._metrics

    # -- IPlanner ------------------------------------------------------------

    def create_plan(
        self,
        goal: str,
        steps: list[PlanStep],
        description: str = "",
        priority: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskPlan:
        """Create a new :class:`TaskPlan`.

        The plan is created in :attr:`PlanStatus.NOT_STARTED` state.

        Args:
            goal: One-sentence objective.
            steps: Steps that compose the plan.
            description: Optional elaborated description.
            priority: Optional priority. Defaults to :attr:`Priority.NORMAL`.
            metadata: Optional extensible metadata.

        Returns:
            A fully-constructed :class:`TaskPlan`.
        """
        plan = TaskPlan(
            goal=goal,
            description=description,
            priority=Priority(priority) if priority is not None else Priority.NORMAL,
            status=PlanStatus.NOT_STARTED,
            steps=list(steps),
            metadata=metadata or {},
        )
        return plan

    def clone(self, plan: TaskPlan) -> TaskPlan:
        """Deep-copy *plan* with a new ID and fresh timestamps.

        The cloned plan's status is reset to :attr:`PlanStatus.NOT_STARTED`
        and all steps are reset to :attr:`StepStatus.PENDING`.

        Args:
            plan: The plan to clone.

        Returns:
            A new :class:`TaskPlan` that is independent of *plan*.
        """
        from .enums import StepStatus

        new_plan = copy.deepcopy(plan)
        # Assign new identity
        import uuid
        from datetime import datetime, timezone

        new_plan.id = uuid.uuid4().hex[:12]
        new_plan.created_at = datetime.now(timezone.utc)
        new_plan.updated_at = new_plan.created_at
        new_plan.status = PlanStatus.NOT_STARTED
        # Reset all steps
        for step in new_plan.steps:
            step.id = uuid.uuid4().hex[:12]
            step.status = StepStatus.PENDING
        return new_plan

    # -- validation ----------------------------------------------------------

    def validate(self, plan: TaskPlan) -> list[str]:
        """Validate *plan* and return a list of error messages.

        Delegates to :attr:`validator`.
        An empty list means the plan is structurally valid.
        """
        return self._validator.validate(plan)

    def validate_or_raise(self, plan: TaskPlan) -> None:
        """Validate and raise on the first error.

        Delegates to :meth:`PlanValidator.validate_or_raise`.
        """
        if isinstance(self._validator, PlanValidator):
            self._validator.validate_or_raise(plan)
        else:
            errors = self._validator.validate(plan)
            if errors:
                from .exceptions import PlanValidationError

                raise PlanValidationError(
                    f"Plan validation failed with {len(errors)} error(s)",
                    errors=errors,
                )

    # -- estimation ----------------------------------------------------------

    def estimate(self, plan: TaskPlan) -> PlanMetrics:
        """Compute structural metrics for *plan*.

        This is a thin wrapper that records planning time around the
        :meth:`IMetrics.compute` call.

        Args:
            plan: The plan to estimate.

        Returns:
            A populated :class:`PlanMetrics` instance.
        """
        start = time.monotonic()
        result = self._metrics.compute(plan)
        elapsed = time.monotonic() - start
        result.planning_time = elapsed
        return result

    # -- serialization -------------------------------------------------------

    def serialize(self, plan: TaskPlan, format: str = "dict") -> Any:  # noqa: A002
        """Serialize *plan* to the requested format.

        Delegates to :attr:`serializer`.
        """
        return self._serializer.serialize(plan, format=format)

    def deserialize(self, data: Any, format: str = "dict") -> TaskPlan:  # noqa: A002
        """Deserialize *data* back into a :class:`TaskPlan`.

        Delegates to :attr:`serializer`.
        """
        return self._serializer.deserialize(data, format=format)

    # -- goal-based generation (Sprint 3.2) ------------------------------------

    def create_from_goal(
        self,
        goal: str,
        description: str = "",
        priority: Any | None = None,
        **context_kwargs: Any,
    ) -> TaskPlan:
        """Generate a complete plan from a natural-language goal.

        This is the main entry point for Sprint 3.2's plan generation
        pipeline.  It analyses the goal, selects a template, generates
        steps, wires dependencies, and validates the result.

        Args:
            goal: The user-provided objective (e.g. "Crear API REST").
            description: Optional elaborated description.
            priority: Optional priority override. Auto-detected if not set.
            **context_kwargs: Forwarded to :class:`PlanningContext`
                (``user``, ``available_agents``, ``constraints``, etc.).

        Returns:
            A validated :class:`TaskPlan` ready for execution.

        Example::

            planner = TaskPlanner()
            plan = planner.create_from_goal("Crear una página web para una tienda")
            print(plan.steps[0].title)  # "Analizar requisitos"
        """
        from .generator import PlanGenerator

        generator = PlanGenerator(
            analyzer=self._get_or_create_analyzer(),
            validator=self._validator if isinstance(self._validator, PlanValidator) else None,
        )
        plan = generator.generate(
            goal=goal,
            description=description,
            priority=priority,
            **context_kwargs,
        )
        return plan

    def _get_or_create_analyzer(self):
        """Lazy-create a :class:`TaskAnalyzer` if one doesn't exist."""
        if not hasattr(self, "_analyzer"):
            from .analyzer import TaskAnalyzer

            self._analyzer = TaskAnalyzer()
        return self._analyzer
