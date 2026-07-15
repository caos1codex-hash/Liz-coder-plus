"""Agent selector — picks the best agent for each plan step.

Sprint 3.3 — Agent-Aware Planning Integration.

:class:`AgentSelector` scores and ranks candidate agents from an
:class:`AgentPlanningContext` based on:

1. **Capability match** — does the agent provide the needed capability?
2. **Agent state** — is the agent available (not busy/stopped/dead)?
3. **Priority** — higher-priority agents are preferred.
4. **Compatibility** — are the agent's required capabilities satisfied
   by the overall context?

The selector produces :class:`AgentAssignment` instances with confidence
scores and rationales.
"""

from __future__ import annotations

from .agent_context import AgentInfo, AgentPlanningContext
from .assignment import AgentAssignment
from .capability_mapper import CapabilityMapper
from .exceptions import (AgentNotFoundError, AssignmentError,
                         CapabilityMissingError)


class AgentSelector:
    """Select the best agent for a given capability requirement.

    Args:
        context: The agent planning context (snapshot of the registry).
        mapper: The capability mapper for translating needs.
        prefer_available: If ``True`` (default), unavailable agents receive
            a heavy score penalty.
    """

    def __init__(
        self,
        context: AgentPlanningContext,
        mapper: CapabilityMapper,
        *,
        prefer_available: bool = True,
    ) -> None:
        self._context = context
        self._mapper = mapper
        self._prefer_available = prefer_available

    # -- public API ----------------------------------------------------------

    def select_agent(
        self,
        capability: str,
        step_id: str = "",
        required_agents: list[str] | None = None,
    ) -> AgentAssignment:
        """Select the single best agent for a capability.

        Args:
            capability: The needed capability string.
            step_id: Optional step ID for the assignment record.
            required_agents: Optional list of agent names suggested by the
                planner's rule engine (from Sprint 3.2).

        Returns:
            An :class:`AgentAssignment` with the best match.

        Raises:
            CapabilityMissingError: If no agent provides the capability.
        """
        candidates = self._find_candidates(capability)

        # If the rule engine suggested specific agent names, boost them
        if required_agents:
            suggested_ids: set[str] = set()
            for name in required_agents:
                info = self._context.get_agent(name)
                if info is not None:
                    suggested_ids.add(info.id)

            if suggested_ids:
                # Re-sort: suggested first, then by score
                def _sort_key(item: tuple[AgentInfo, float]) -> tuple[int, float]:
                    info, score = item
                    is_suggested = 0 if info.id in suggested_ids else 1
                    return (is_suggested, -score)

                candidates.sort(key=_sort_key)

        if not candidates:
            raise CapabilityMissingError(capability)

        best_agent, best_score = candidates[0]
        return self._build_assignment(
            agent=best_agent,
            capability=capability,
            score=best_score,
            step_id=step_id,
        )

    def select_agents(
        self,
        capabilities: list[str],
        step_id: str = "",
        required_agents: list[str] | None = None,
    ) -> list[AgentAssignment]:
        """Select agents for multiple capabilities on a single step.

        Each capability is resolved independently.  The same agent may
        be selected for multiple capabilities if it is the best match
        for all of them.

        Args:
            capabilities: List of needed capability strings.
            step_id: Optional step ID.
            required_agents: Optional suggested agent names.

        Returns:
            List of :class:`AgentAssignment`, one per capability.
        """
        assignments: list[AgentAssignment] = []
        for cap in capabilities:
            assignment = self.select_agent(
                capability=cap,
                step_id=step_id,
                required_agents=required_agents,
            )
            assignments.append(assignment)
        return assignments

    def rank_agents(
        self,
        capability: str,
        limit: int | None = None,
    ) -> list[tuple[AgentInfo, float]]:
        """Rank all agents that could provide a capability.

        Args:
            capability: The capability to search for.
            limit: Maximum number of results (``None`` = no limit).

        Returns:
            List of ``(AgentInfo, score)`` tuples, sorted by score
            descending.
        """
        candidates = self._find_candidates(capability)
        if limit is not None:
            candidates = candidates[:limit]
        return candidates

    def score_agent(
        self,
        agent: AgentInfo,
        capability: str,
    ) -> float:
        """Compute a 0.0–1.0 selection score for an agent.

        The score combines four signals with equal weight:

        * **Capability match** (0.0 or 1.0): exact or hierarchical match.
        * **Availability** (0.0 or 1.0): agent is available.
        * **Priority factor** (0.0–1.0): normalized priority.
        * **Capability coverage** (0.0–1.0): how many of the agent's
          capabilities are relevant.

        Args:
            agent: The agent to score.
            capability: The needed capability.

        Returns:
            A score between 0.0 and 1.0.
        """
        # 1. Capability match (0 or 1)
        cap_match = 1.0 if agent.provides(capability) else 0.0

        # 2. Availability (0 or 1)
        availability = 1.0 if agent.available else 0.0

        # 3. Priority (normalize to 0-1, assume max priority 10)
        priority_factor = min(agent.priority / 10.0, 1.0)

        # 4. Coverage: how many of the agent's capabilities are relevant
        # (same category as the needed capability)
        category = capability.split(".")[0] if "." in capability else capability
        if agent.capabilities:
            relevant = sum(
                1
                for c in agent.capabilities
                if c == capability or c.startswith(category + ".")
            )
            coverage = min(relevant / max(len(agent.capabilities), 1), 1.0)
        else:
            coverage = 0.0

        # Weighted combination
        score = (
            0.40 * cap_match
            + 0.25 * availability
            + 0.15 * priority_factor
            + 0.20 * coverage
        )

        # Penalize unavailable agents if prefer_available is set
        if self._prefer_available and not agent.available:
            score *= 0.3

        return round(score, 4)

    # -- validation helpers --------------------------------------------------

    def validate_agent_exists(self, agent_id: str) -> AgentInfo:
        """Validate that an agent exists and return it.

        Args:
            agent_id: Agent name or UUID.

        Returns:
            The matching :class:`AgentInfo`.

        Raises:
            AgentNotFoundError: If the agent does not exist.
        """
        agent = self._context.get_agent(agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        return agent

    def validate_assignment(
        self,
        step_id: str,
        agent_id: str,
        capability: str,
    ) -> None:
        """Validate that an assignment is feasible.

        Checks that the agent exists, is available, and provides the
        required capability.

        Args:
            step_id: The step being assigned to.
            agent_id: The agent to assign.
            capability: The capability the agent must provide.

        Raises:
            AgentNotFoundError: If the agent does not exist.
            AssignmentError: If the agent is unavailable or doesn't
                provide the capability.
        """
        agent = self.validate_agent_exists(agent_id)

        if not agent.available:
            raise AssignmentError(
                step_id=step_id,
                agent_id=agent_id,
                reason=f"Agent is not available (status: {agent.status})",
            )

        if not agent.provides(capability):
            raise AssignmentError(
                step_id=step_id,
                agent_id=agent_id,
                reason=f"Agent does not provide capability '{capability}'",
            )

    # -- internals ------------------------------------------------------------

    def _find_candidates(
        self,
        capability: str,
    ) -> list[tuple[AgentInfo, float]]:
        """Find and score all candidate agents for a capability.

        Returns:
            List of ``(AgentInfo, score)`` tuples sorted by score desc.
        """
        candidates: list[tuple[AgentInfo, float]] = []
        for agent in self._context.agents:
            score = self.score_agent(agent, capability)
            if score > 0.0:
                candidates.append((agent, score))

        candidates.sort(key=lambda x: -x[1])
        return candidates

    def _build_assignment(
        self,
        agent: AgentInfo,
        capability: str,
        score: float,
        step_id: str = "",
    ) -> AgentAssignment:
        """Build an :class:`AgentAssignment` from a selected agent."""
        reason = (
            f"Selected '{agent.name}' for capability '{capability}' "
            f"(score: {score:.2f}, priority: {agent.priority})"
        )
        return AgentAssignment(
            step_id=step_id,
            agent_id=agent.id,
            capability=capability,
            confidence_score=score,
            reason=reason,
        )
