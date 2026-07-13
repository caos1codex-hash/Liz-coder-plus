"""Agent router.

Decides which registered agent should handle a given user message.
Sprint 1 ships a simple keyword-based router plus a default fallback
agent that echoes a friendly Spanish greeting. Sprint 2 will swap in
a smarter routing strategy (intent classification via the LLM).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from src.protocols import Agent

logger = logging.getLogger(__name__)

# A routing rule is an async predicate: (message, context) -> bool
RoutePredicate = Callable[[str, dict[str, Any]], Awaitable[bool]]


class EchoAgent:
    """Built-in default agent.

    Used until real agents land in Sprint 2. It produces a friendly
    Spanish acknowledgement so the end-to-end pipeline can be demoed.
    The agent implements the `Agent` protocol from `orchestrator.py`.
    """

    name = "echo"

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        session_id = context.get("session_id", "unknown")
        logger.debug("EchoAgent handling message for session %s", session_id)
        # Friendly Spanish acknowledgement - matches the user-facing
        # language requirement (UI/docs in Spanish, code in English).
        return f"Hola, soy Liz. Recibí tu mensaje: «{message}»"


class AgentRouter:
    """Routes a user message to the appropriate agent.

    Routing strategy (Sprint 1):
      1. Iterate registered rules in order; first match wins.
      2. If no rule matches, fall back to the default agent.

    A "rule" is a tuple `(agent_name, predicate)`. Predicates are
    async callables so they can consult memory or external state
    before deciding.
    """

    def __init__(self, default_agent: Agent | None = None) -> None:
        self._agents: dict[str, Agent] = {}
        self._rules: list[tuple[str, RoutePredicate]] = []
        self._default = default_agent or EchoAgent()
        # Register the default under its own name so it can be looked up.
        self._agents[self._default.name] = self._default
        logger.info("AgentRouter initialized (default=%s)", self._default.name)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_agent(self, agent: Agent) -> None:
        """Register an agent so it can be referenced by routing rules."""
        self._agents[agent.name] = agent
        logger.debug("Registered agent '%s' with router", agent.name)

    def add_rule(self, agent_name: str, predicate: RoutePredicate) -> None:
        """Add a routing rule.

        Args:
            agent_name: Name of the agent to dispatch to when the
                predicate returns True.
            predicate: Async callable (message, context) -> bool.
        """
        if agent_name not in self._agents:
            raise KeyError(
                f"Cannot add rule for unknown agent '{agent_name}'. "
                f"Register the agent first."
            )
        self._rules.append((agent_name, predicate))
        logger.debug("Routing rule added -> '%s'", agent_name)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def route(self, message: str, context: dict[str, Any]) -> Agent:
        """Return the agent that should handle the message.

        Args:
            message: The raw user message.
            context: Conversation context (session, history, etc.).

        Returns:
            The selected Agent instance.
        """
        for agent_name, predicate in self._rules:
            try:
                if await predicate(message, context):
                    logger.debug("Message routed to '%s' by rule", agent_name)
                    return self._agents[agent_name]
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Routing predicate for '%s' raised; skipping", agent_name
                )

        logger.debug("Message routed to default agent '%s'", self._default.name)
        return self._default

    def get_agent(self, name: str) -> Agent | None:
        """Look up an agent by name."""
        return self._agents.get(name)

    @property
    def default_agent(self) -> Agent:
        """Return the default (fallback) agent."""
        return self._default

    @property
    def agent_names(self) -> list[str]:
        """List names of all registered agents."""
        return list(self._agents.keys())
