"""Agent registry.

Holds references to all agents available to the orchestrator. Agents
register themselves at startup; the orchestrator queries the registry
to find an appropriate handler for a given message.

This registry validates that agents satisfy the ``Agent`` Protocol
(name + async handle) before accepting them.
"""

from __future__ import annotations

import logging
from typing import Iterable

from src.base import BaseAgent
from src.protocols import Agent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Registry of available agents with validation."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """Register an agent after validation.

        Args:
            agent: An agent instance.

        Raises:
            TypeError: If the agent does not satisfy the Agent protocol.
        """
        if not isinstance(agent, Agent):
            raise TypeError(
                f"Agent '{getattr(agent, 'name', '?')}' does not satisfy "
                f"the Agent protocol (needs 'name: str' and "
                f"'async handle(message, context) -> str')"
            )
        if agent.name in self._agents:
            logger.warning("Overwriting existing agent '%s'", agent.name)
        self._agents[agent.name] = agent
        logger.debug("Registered agent '%s'", agent.name)

    def get(self, name: str) -> BaseAgent | None:
        """Look up an agent by name."""
        return self._agents.get(name)

    def all(self) -> Iterable[BaseAgent]:
        """Return all registered agents."""
        return self._agents.values()

    def names(self) -> list[str]:
        """Return the list of registered agent names."""
        return list(self._agents.keys())

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: str) -> bool:
        return name in self._agents