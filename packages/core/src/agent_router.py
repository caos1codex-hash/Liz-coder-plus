"""Agent router.

Decides which registered agent should handle a given user message.
Sprint 1 ships a simple keyword-based router plus a default fallback
agent that echoes a friendly Spanish greeting. Sprint 2 will swap in
a smarter routing strategy (intent classification via the LLM).

Agents are validated against the ``Agent`` Protocol at registration
time to prevent runtime AttributeError.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from src.protocols import Agent

logger = logging.getLogger(__name__)

# A routing rule is an async predicate: (message, context) -> bool
RoutePredicate = Callable[[str, dict[str, Any]], Awaitable[bool]]


class EchoAgent:
    """Built-in default agent.

    Used until real agents land in Sprint 2. It produces a friendly
    Spanish acknowledgement so the end-to-end pipeline can be demoed.
    The agent implements the ``Agent`` protocol.

    This agent uses the context to personalize the response, serving
    as a reference implementation for future agent developers.
    """

    name = "echo"
    description = "Default fallback agent that acknowledges messages"
    version = "0.1.0"
    capabilities = ["greeting", "echo"]

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        session_id = context.get("session_id", "unknown")
        language = context.get("user_language", "es")
        mode = context.get("mode", "confirmation")

        logger.debug(
            "EchoAgent handling message for session %s (lang=%s, mode=%s)",
            session_id,
            language,
            mode,
        )

        history_count = len(context.get("history", []))

        if history_count > 0:
            return (
                f"Hola, soy Liz. Recibí tu mensaje: «{message}». "
                f"Vamos {history_count} intercambios en esta conversación. "
                f"Modo actual: {mode}."
            )

        return f"Hola, soy Liz. Recibí tu mensaje: «{message}»"


class AgentRouter:
    """Routes a user message to the appropriate agent.

    Routing strategy (Sprint 1):
      1. Iterate registered rules in order; first match wins.
      2. If no rule matches, fall back to the default agent.

    A "rule" is a tuple ``(agent_name, predicate)``. Predicates are
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
        """Register an agent after protocol validation.

        Args:
            agent: Must satisfy the Agent protocol (name + async handle).

        Raises:
            TypeError: If the agent does not satisfy the Agent protocol.
        """
        if not isinstance(agent, Agent):
            raise TypeError(
                f"Object {agent!r} does not satisfy the Agent protocol "
                f"(needs 'name: str' and 'async handle(message, context) -> str')"
            )
        self._agents[agent.name] = agent
        logger.debug("Registered agent '%s' with router", agent.name)

    def add_rule(self, agent_name: str, predicate: RoutePredicate) -> None:
        """Add a routing rule.

        Args:
            agent_name: Name of the agent to dispatch to when the
                predicate returns True.
            predicate: Async callable (message, context) -> bool.

        Raises:
            KeyError: If agent_name is not registered.
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
        start = time.monotonic()
        for agent_name, predicate in self._rules:
            try:
                if await predicate(message, context):
                    elapsed = (time.monotonic() - start) * 1000
                    logger.debug(
                        "Message routed to '%s' by rule (%.1fms)",
                        agent_name,
                        elapsed,
                    )
                    return self._agents[agent_name]
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Routing predicate for '%s' raised; skipping", agent_name
                )

        elapsed = (time.monotonic() - start) * 1000
        logger.debug(
            "Message routed to default agent '%s' (%.1fms)",
            self._default.name,
            elapsed,
        )
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

    def __len__(self) -> int:
        return len(self._agents)