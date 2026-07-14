"""CapabilityMatcher — assigns agents to tasks (Sprint 2.8).

The :class:`CapabilityMatcher` integrates with the existing Sprint 2.2-2
``AgentRegistry`` / ``CapabilityResolver`` / ``AgentManifest`` stack
through a thin adapter (:class:`AgentRegistryAdapter`) so that the
planner package itself never imports the multiagent package directly.
This keeps the planner usable standalone (with a stub adapter).

Scoring signals (weighted, configurable):

- **specialty**     — how well the agent's capabilities match the task's
                      ``required_capabilities`` (exact > wildcard > parent).
- **availability**  — agent is healthy and not in a terminal state.
- **workload**      — inverse of current usage_count (less busy = better).
- **priority**      — priority alignment between agent and task.
- **affinity**      — task.metadata['agent_affinity'] hints (a list of
                      preferred agent ids; an exact match contributes 1.0).

The final score is a weighted sum in ``[0.0, 1.0]``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .exceptions import AgentAssignmentError
from .models import AgentAssignment, CapabilityScore
from .task import Task

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


@dataclass
class MatcherWeights:
    """Weights for :class:`CapabilityMatcher`. Normalized at use time.

    Defaults favour specialty (capability match) above all else.
    """

    w_specialty: float = 0.40
    w_availability: float = 0.20
    w_workload: float = 0.15
    w_priority: float = 0.10
    w_affinity: float = 0.15

    def normalized(self) -> MatcherWeights:
        total = (
            self.w_specialty
            + self.w_availability
            + self.w_workload
            + self.w_priority
            + self.w_affinity
        )
        if total <= 0:
            return MatcherWeights()
        return MatcherWeights(
            w_specialty=self.w_specialty / total,
            w_availability=self.w_availability / total,
            w_workload=self.w_workload / total,
            w_priority=self.w_priority / total,
            w_affinity=self.w_affinity / total,
        )


# ----------------------------------------------------------------------
# Agent info (plain dict for decoupling)
# ----------------------------------------------------------------------


@dataclass
class AgentInfo:
    """Plain agent description consumed by the matcher.

    This is intentionally a thin dataclass so that any external
    ``AgentRegistry`` can be adapted to it by copying fields, without
    the planner depending on the multiagent package.
    """

    id: str
    name: str = ""
    capabilities: list[str] = field(default_factory=list)
    priority: int = 0
    healthy: bool = True
    available: bool = True
    usage_count: int = 0
    success_rate: float = 1.0
    avg_latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Default name to id when not provided.
        if not self.name:
            self.name = self.id
        # Defensive copies.
        self.capabilities = list(self.capabilities)
        self.metadata = dict(self.metadata)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentInfo:
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            capabilities=list(data.get("capabilities", [])),
            priority=int(data.get("priority", 0)),
            healthy=bool(data.get("healthy", True)),
            available=bool(data.get("available", True)),
            usage_count=int(data.get("usage_count", 0)),
            success_rate=float(data.get("success_rate", 1.0)),
            avg_latency_ms=float(data.get("avg_latency_ms", 0.0)),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "capabilities": list(self.capabilities),
            "priority": self.priority,
            "healthy": self.healthy,
            "available": self.available,
            "usage_count": self.usage_count,
            "success_rate": self.success_rate,
            "avg_latency_ms": self.avg_latency_ms,
            "metadata": dict(self.metadata),
        }


# ----------------------------------------------------------------------
# Adapter base
# ----------------------------------------------------------------------


class AgentRegistryAdapter:
    """Default in-memory adapter.

    Concrete adapters wrapping
    :class:`multiagent.registry.AgentRegistry` should subclass this and
    override :meth:`list_agents` / :meth:`find_by_capability`.
    """

    def __init__(self, agents: list[AgentInfo] | None = None) -> None:
        self._agents: dict[str, AgentInfo] = {a.id: a for a in (agents or [])}

    def register(self, agent: AgentInfo) -> None:
        self._agents[agent.id] = agent

    def unregister(self, agent_id: str) -> bool:
        return self._agents.pop(agent_id, None) is not None

    def list_agents(self) -> list[AgentInfo]:
        return list(self._agents.values())

    def get_agent(self, agent_id: str) -> AgentInfo | None:
        return self._agents.get(agent_id)

    def find_by_capability(self, capability: str) -> list[AgentInfo]:
        """Return agents that provide ``capability`` (with wildcard support)."""
        result: list[AgentInfo] = []
        for agent in self._agents.values():
            for cap in agent.capabilities:
                if _cap_matches(cap, capability):
                    result.append(agent)
                    break
        return result

    def update_workload(self, agent_id: str, *, delta: int) -> None:
        """Increment/decrement an agent's usage_count (for live updates)."""
        a = self._agents.get(agent_id)
        if a is not None:
            a.usage_count = max(0, a.usage_count + delta)


def _cap_matches(cap: str, target: str) -> bool:
    """True if ``cap`` covers ``target`` (exact, wildcard, or parent)."""
    if cap == target:
        return True
    if cap.endswith(".*"):
        prefix = cap[:-2]
        return target == prefix or target.startswith(prefix + ".")
    if target.startswith(cap + "."):
        return True
    return False


# ----------------------------------------------------------------------
# CapabilityMatcher
# ----------------------------------------------------------------------


class CapabilityMatcher:
    """Matches tasks to agents using a weighted multi-signal score.

    Usage::

        adapter = AgentRegistryAdapter(agents=[...])
        matcher = CapabilityMatcher(adapter=adapter)
        assignment = matcher.match(task)
        if assignment.assigned:
            task.assigned_agent = assignment.agent_id
    """

    def __init__(
        self,
        *,
        adapter: AgentRegistryAdapter | None = None,
        weights: MatcherWeights | None = None,
        fallback_agent_id: str | None = None,
    ) -> None:
        self._adapter = adapter or AgentRegistryAdapter()
        self._weights = (weights or MatcherWeights()).normalized()
        self._fallback_agent_id = fallback_agent_id
        # Metrics.
        self._metrics = {
            "matches_total": 0,
            "assigned_count": 0,
            "miss_count": 0,
        }

    @property
    def adapter(self) -> AgentRegistryAdapter:
        return self._adapter

    @property
    def weights(self) -> MatcherWeights:
        return self._weights

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match(self, task: Task) -> AgentAssignment:
        """Return an :class:`AgentAssignment` for ``task``.

        If no agent can be assigned, ``assignment.assigned`` is False
        and ``assignment.agent_id`` is None (or the fallback if set).
        """
        self._metrics["matches_total"] += 1
        candidates = self._gather_candidates(task)
        if not candidates:
            self._metrics["miss_count"] += 1
            return AgentAssignment(
                task_id=task.id,
                agent_id=self._fallback_agent_id,
                assigned=self._fallback_agent_id is not None,
                score=0.0,
                candidates=[],
                reason="no candidate agents available",
            )
        # Score each candidate.
        max_priority = max((a.priority for a in candidates), default=1) or 1
        max_usage = max((a.usage_count for a in candidates), default=1) or 1
        scored: list[tuple[AgentInfo, CapabilityScore]] = []
        for agent in candidates:
            score = self._score(agent, task, max_priority=max_priority, max_usage=max_usage)
            scored.append((agent, score))
        # Sort by score desc.
        scored.sort(key=lambda x: x[1].score, reverse=True)
        best_agent, best_score = scored[0]
        self._metrics["assigned_count"] += 1
        reason = best_score.reason or (f"selected '{best_agent.id}' (score={best_score.score:.4f})")
        return AgentAssignment(
            task_id=task.id,
            agent_id=best_agent.id,
            assigned=True,
            score=best_score.score,
            candidates=[(a.id, s.score) for a, s in scored],
            reason=reason,
        )

    def match_many(self, tasks: list[Task]) -> list[AgentAssignment]:
        """Match a batch of tasks (independent calls)."""
        return [self.match(t) for t in tasks]

    def assign(self, task: Task) -> str:
        """Match and write the agent id onto ``task`` in place.

        Raises :class:`AgentAssignmentError` if no agent can be assigned
        and no fallback is configured.
        """
        a = self.match(task)
        if not a.assigned or a.agent_id is None:
            raise AgentAssignmentError(task.id, reason=a.reason)
        task.assigned_agent = a.agent_id
        # Bump the agent's workload.
        self._adapter.update_workload(a.agent_id, delta=+1)
        return a.agent_id

    def assign_many(self, tasks: list[Task]) -> dict[str, str]:
        """Assign agents to multiple tasks; return ``{task_id: agent_id}``.

        Tasks that cannot be assigned are skipped (their ``assigned_agent``
        is left as None).
        """
        result: dict[str, str] = {}
        for t in tasks:
            try:
                result[t.id] = self.assign(t)
            except AgentAssignmentError:
                continue
        return result

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _gather_candidates(self, task: Task) -> list[AgentInfo]:
        """Return agents that could plausibly serve ``task``.

        Strategy: if the task declares ``required_capabilities``, only
        return agents that provide at least one of them. Otherwise,
        return all available agents.
        """
        if task.required_capabilities:
            seen: set[str] = set()
            candidates: list[AgentInfo] = []
            for cap in task.required_capabilities:
                for agent in self._adapter.find_by_capability(cap):
                    if agent.id not in seen:
                        seen.add(agent.id)
                        candidates.append(agent)
            # Filter by availability.
            candidates = [a for a in candidates if a.healthy and a.available]
            return candidates
        # No required caps: return all healthy + available agents.
        return [a for a in self._adapter.list_agents() if a.healthy and a.available]

    def _score(
        self,
        agent: AgentInfo,
        task: Task,
        *,
        max_priority: int,
        max_usage: int,
    ) -> CapabilityScore:
        w = self._weights

        specialty = self._specialty_score(agent, task)
        availability = self._availability_score(agent)
        workload = 1.0 - (agent.usage_count / max_usage) if max_usage > 0 else 1.0
        priority = agent.priority / max_priority if max_priority > 0 else 0.0
        affinity = self._affinity_score(agent, task)

        score = (
            w.w_specialty * specialty
            + w.w_availability * availability
            + w.w_workload * workload
            + w.w_priority * priority
            + w.w_affinity * affinity
        )
        reason = (
            f"agent='{agent.id}' spec={specialty:.2f} avail={availability:.2f} "
            f"work={workload:.2f} prio={priority:.2f} affin={affinity:.2f}"
        )
        return CapabilityScore(
            agent_id=agent.id,
            score=score,
            specialty=specialty,
            availability=availability,
            workload=workload,
            priority=priority,
            affinity=affinity,
            reason=reason,
        )

    def _specialty_score(self, agent: AgentInfo, task: Task) -> float:
        """Average capability match across the task's required_capabilities.

        Per-capability scoring:
        - 1.0 exact match
        - 0.85 wildcard match (``code.*`` matches ``code.generate``)
        - 0.7 hierarchical match (``code`` matches ``code.generate``)
        - 0.0 no match
        """
        if not task.required_capabilities:
            # No requirement: neutral score so other signals decide.
            return 0.5
        scores: list[float] = []
        for cap in task.required_capabilities:
            best = 0.0
            for ac in agent.capabilities:
                if ac == cap:
                    best = max(best, 1.0)
                elif ac.endswith(".*"):
                    prefix = ac[:-2]
                    if cap == prefix or cap.startswith(prefix + "."):
                        best = max(best, 0.85)
                elif cap.startswith(ac + "."):
                    best = max(best, 0.7)
            scores.append(best)
        return sum(scores) / len(scores) if scores else 0.0

    def _availability_score(self, agent: AgentInfo) -> float:
        """0.0 if unhealthy/unavailable; scale by success_rate otherwise."""
        if not agent.healthy or not agent.available:
            return 0.0
        return max(0.0, min(1.0, agent.success_rate))

    def _affinity_score(self, agent: AgentInfo, task: Task) -> float:
        """1.0 if task.metadata['agent_affinity'] contains ``agent.id``."""
        affinity = task.metadata.get("agent_affinity")
        if not affinity:
            return 0.0
        if isinstance(affinity, str):
            affinity = [affinity]
        if agent.id in affinity:
            return 1.0
        # Partial credit if the agent's name matches.
        if agent.name and agent.name in affinity:
            return 0.8
        return 0.0

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        total = self._metrics["matches_total"]
        return {
            "matches_total": total,
            "assigned_count": self._metrics["assigned_count"],
            "miss_count": self._metrics["miss_count"],
            "assign_rate": (
                round(self._metrics["assigned_count"] / total, 4) if total > 0 else 0.0
            ),
            "agents_tracked": len(self._adapter.list_agents()),
        }


__all__ = [
    "AgentInfo",
    "AgentRegistryAdapter",
    "CapabilityMatcher",
    "MatcherWeights",
]
