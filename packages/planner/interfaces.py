"""Planner interfaces (abstract base classes).

Sprint 3.1 — Intelligent Task Planning Engine.

All concrete implementations **must** satisfy these contracts.
The interfaces use ``abc.ABC`` / ``abc.abstractmethod`` so that ``isinstance``
checks work at runtime, consistent with the broader project's use of
``typing.Protocol`` but providing stricter enforcement for the planner's
public contracts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import PlanMetrics, PlanStep, TaskPlan


class IPlanner(ABC):
    """Contract for creating and managing execution plans.

    The planner is **not** responsible for executing tasks — it only builds
    the plan structure.
    """

    @abstractmethod
    def create_plan(
        self,
        goal: str,
        steps: list[PlanStep],
        description: str = "",
        priority: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskPlan:
        """Create a new :class:`TaskPlan`.

        Args:
            goal: One-sentence objective.
            steps: Steps that compose the plan.
            description: Optional elaborated description.
            priority: Optional priority override.
            metadata: Optional extensible metadata.

        Returns:
            A fully-constructed :class:`TaskPlan`.
        """
        ...

    @abstractmethod
    def clone(self, plan: TaskPlan) -> TaskPlan:
        """Deep-copy a plan so the original is not mutated.

        The cloned plan receives a new ID and fresh timestamps.
        """
        ...


class IValidator(ABC):
    """Contract for validating plan integrity."""

    @abstractmethod
    def validate(self, plan: TaskPlan) -> list[str]:
        """Validate *plan* and return a list of error messages.

        An empty list means the plan is valid.
        """
        ...


class ISerializer(ABC):
    """Contract for (de)serializing plans."""

    @abstractmethod
    def serialize(self, plan: TaskPlan, format: str = "dict") -> Any:
        """Serialize *plan* to the requested *format*.

        Args:
            plan: The plan to serialize.
            format: One of ``"dict"``, ``"json"``, ``"yaml"``.

        Returns:
            The serialized representation (dict, JSON string, or YAML string).
        """
        ...

    @abstractmethod
    def deserialize(self, data: Any, format: str = "dict") -> TaskPlan:
        """Deserialize *data* in the given *format* back into a :class:`TaskPlan`.

        Args:
            data: Serialized data (dict, JSON string, or YAML string).
            format: One of ``"dict"``, ``"json"``, ``"yaml"``.
        """
        ...


class IMetrics(ABC):
    """Contract for computing plan metrics."""

    @abstractmethod
    def compute(self, plan: TaskPlan, planning_time: float = 0.0) -> PlanMetrics:
        """Compute structural metrics for *plan*.

        Args:
            plan: The plan to analyze.
            planning_time: Wall-clock seconds spent building the plan.

        Returns:
            A populated :class:`PlanMetrics`.
        """
        ...
