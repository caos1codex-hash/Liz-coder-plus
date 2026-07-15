"""Planning context model.

Sprint 3.2 — Planner Engine Core.

Stores all contextual information needed during plan generation:
the original goal, user identity, available agents, constraints,
and preferences.  Extensible for future memory / semantic layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanningContext:
    """Context bag passed through the planning pipeline.

    Every component in the ``Analyzer → Template → Generator → Validator``
    chain can read from and write to this context, making it the single
    source of truth for a planning session.

    Attributes:
        goal: The original user-provided objective.
        user: Optional user identifier.
        metadata: Extensible key-value store.
        available_agents: Agent names that can be assigned to steps.
        constraints: Hard restrictions (e.g. max steps, forbidden agents).
        preferences: Soft preferences (e.g. preferred agents, language).
    """

    goal: str = ""
    user: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    available_agents: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    preferences: dict[str, Any] = field(default_factory=dict)

    # -- derived fields (populated by Analyzer) --------------------------------

    task_type: str = "general"
    complexity: str = "normal"
    estimated_steps: int = 0
    suggested_agents: list[str] = field(default_factory=list)
    detected_keywords: list[str] = field(default_factory=list)

    # -- serialization ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        return {
            "goal": self.goal,
            "user": self.user,
            "metadata": dict(self.metadata),
            "available_agents": list(self.available_agents),
            "constraints": dict(self.constraints),
            "preferences": dict(self.preferences),
            "task_type": self.task_type,
            "complexity": self.complexity,
            "estimated_steps": self.estimated_steps,
            "suggested_agents": list(self.suggested_agents),
            "detected_keywords": list(self.detected_keywords),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanningContext:
        """Construct a ``PlanningContext`` from a plain dict."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
