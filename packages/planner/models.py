"""Planner domain models.

Sprint 3.1 — Intelligent Task Planning Engine.

Uses plain ``@dataclass`` with manual ``to_dict`` / ``from_dict`` serialization,
consistent with the existing codebase convention (see ``packages/core/src/planner.py``
and ``packages/multiagent/src/multiagent/workflow/models.py``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .enums import PlanStatus, Priority, StepStatus

# ---------------------------------------------------------------------------
# PlanStep
# ---------------------------------------------------------------------------


@dataclass
class PlanStep:
    """A single step inside a :class:`TaskPlan`.

    Attributes:
        id: Unique identifier for the step.
        title: Short human-readable title.
        description: Detailed description of what the step accomplishes.
        estimated_duration: Expected duration in seconds (``0`` = unknown).
        dependencies: List of step IDs that must complete before this step.
        required_agents: Agent names required to execute this step.
        status: Current lifecycle status.
        metadata: Extensible key-value store for custom attributes.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    description: str = ""
    estimated_duration: float = 0.0
    dependencies: list[str] = field(default_factory=list)
    required_agents: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "estimated_duration": self.estimated_duration,
            "dependencies": list(self.dependencies),
            "required_agents": list(self.required_agents),
            "status": self.status.value,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanStep:
        """Construct a ``PlanStep`` from a plain dict."""
        raw = dict(data)
        raw["status"] = StepStatus(raw.get("status", "pending"))
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# TaskPlan
# ---------------------------------------------------------------------------


@dataclass
class TaskPlan:
    """Top-level execution plan produced by the planner.

    Attributes:
        id: Unique plan identifier.
        goal: One-sentence goal the plan tries to achieve.
        description: Elaborated description of the plan's purpose.
        priority: Urgency level.
        status: Current lifecycle status.
        steps: Ordered collection of :class:`PlanStep` instances.
        metadata: Extensible key-value store for custom attributes.
        created_at: UTC timestamp of plan creation.
        updated_at: UTC timestamp of last mutation.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    goal: str = ""
    description: str = ""
    priority: Priority = Priority.NORMAL
    status: PlanStatus = PlanStatus.NOT_STARTED
    steps: list[PlanStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # -- convenience ---------------------------------------------------------

    def step_by_id(self, step_id: str) -> PlanStep | None:
        """Look up a step by its *step_id*. Returns ``None`` if not found."""
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def step_ids(self) -> list[str]:
        """Return a list of all step IDs in order."""
        return [s.id for s in self.steps]

    def touch(self) -> None:
        """Update :attr:`updated_at` to the current UTC timestamp."""
        self.updated_at = datetime.now(timezone.utc)

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        return {
            "id": self.id,
            "goal": self.goal,
            "description": self.description,
            "priority": self.priority.value,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskPlan:
        """Construct a ``TaskPlan`` from a plain dict."""
        raw: dict[str, Any] = {
            **data,
            "priority": Priority(data.get("priority", "normal")),
            "status": PlanStatus(data.get("status", "not_started")),
            "steps": [PlanStep.from_dict(s) for s in data.get("steps", [])],
        }
        # Parse ISO timestamps
        for ts_key in ("created_at", "updated_at"):
            if ts_key in raw and isinstance(raw[ts_key], str):
                raw[ts_key] = datetime.fromisoformat(raw[ts_key])
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# PlanMetrics
# ---------------------------------------------------------------------------


@dataclass
class PlanMetrics:
    """Computed metrics about a :class:`TaskPlan`.

    These values are derived from the plan's structure and do not include
    runtime execution data.
    """

    planning_time: float = 0.0
    estimated_total_duration: float = 0.0
    total_steps: int = 0
    sequential_steps: int = 0
    parallel_steps: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        return {
            "planning_time": self.planning_time,
            "estimated_total_duration": self.estimated_total_duration,
            "total_steps": self.total_steps,
            "sequential_steps": self.sequential_steps,
            "parallel_steps": self.parallel_steps,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanMetrics:
        """Construct a ``PlanMetrics`` from a plain dict."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
