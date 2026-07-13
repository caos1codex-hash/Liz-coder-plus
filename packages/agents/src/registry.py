"""Agent registry.

Holds references to all agents available to the orchestrator. Agents
register themselves at startup; the orchestrator queries the registry
to find an appropriate handler for a given message.
"""

from __future__ import annotations

import logging
from typing import Iterable

from src.base import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Registry of available agents."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """Register an agent."""
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
