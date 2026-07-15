"""Agent assignment model.

Sprint 3.3 — Agent-Aware Planning Integration.

:class:`AgentAssignment` represents the binding of an agent to a plan step,
along with the rationale for the selection.  Assignments are stored in
:attr:`PlanStep.metadata["agent_assignments"]` and can also be collected
into a plan-level list in :attr:`TaskPlan.metadata["agent_assignments"]`.

This model is purely declarative — it does **not** execute agents or
communicate with the registry at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentAssignment:
    """Binds an agent to a plan step with a confidence score and rationale.

    Attributes:
        step_id: The :class:`PlanStep` identifier this assignment targets.
        agent_id: The agent's identifier (UUID or name) from the registry.
        capability: The primary capability that motivated the selection.
        confidence_score: How confident the selector is (0.0–1.0).
            ``1.0`` = exact match, lower values indicate partial or
            heuristic matches.
        reason: Human-readable explanation of why this agent was chosen.
        metadata: Extensible key-value store for additional data
            (e.g. alternative candidates, scoring breakdown).
    """

    step_id: str = ""
    agent_id: str = ""
    capability: str = ""
    confidence_score: float = 0.0
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        return {
            "step_id": self.step_id,
            "agent_id": self.agent_id,
            "capability": self.capability,
            "confidence_score": self.confidence_score,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentAssignment:
        """Construct an :class:`AgentAssignment` from a plain dict."""
        return cls(
            step_id=data.get("step_id", ""),
            agent_id=data.get("agent_id", ""),
            capability=data.get("capability", ""),
            confidence_score=float(data.get("confidence_score", 0.0)),
            reason=data.get("reason", ""),
            metadata=dict(data.get("metadata", {})),
        )
