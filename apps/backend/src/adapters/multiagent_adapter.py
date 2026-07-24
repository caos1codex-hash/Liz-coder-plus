"""Multiagent-to-Core Adapter.

Wraps the specialized multiagent agents (Coder, Git, Memory, Planner,
Research, Reviewer, Terminal) as core Protocol-compatible agents so they
can be registered directly in the core Orchestrator alongside LLMAgent.

Sprint 8 — Multiagent Integration.
"""

from __future__ import annotations

import logging
from typing import Any

from src.protocols import Agent

logger = logging.getLogger(__name__)


class MultiagentAdapter(Agent):
    """Adapts a multiagent BaseAgent to the core Agent Protocol.

    The core Orchestrator expects agents with a `handle(message, context)`
    method. The multiagent BaseAgent instead uses `execute(task)`. This
    adapter bridges the gap by:
      1. Wrapping the incoming message/context into a task dict.
      2. Calling the multiagent's execute method.
      3. Extracting and formatting the result as a string response.

    Args:
        multiagent: The multiagent BaseAgent instance to wrap.
    """

    def __init__(self, multiagent: Any) -> None:
        self._agent = multiagent

    @property
    def name(self) -> str:
        """Return the wrapped agent's name."""
        return self._agent.name

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        """Handle a message by delegating to the multiagent's execute.

        Args:
            message: The user message text.
            context: Additional context (session_id, tools, etc.).

        Returns:
            A string response from the agent.
        """
        task = {
            "name": self._agent.name,
            "payload": {
                "message": message,
                "request": message,
                **context,
            },
        }

        try:
            result = await self._agent.execute(task)
            # The multiagent execute returns a dict with various fields.
            # Extract the most useful response text.
            if isinstance(result, dict):
                # Prefer explicit 'response' or 'result' field.
                response = result.get("response") or result.get("result") or ""
                if not response:
                    # Fall back to formatted summary of the result dict.
                    parts = []
                    for k, v in result.items():
                        if k in ("response", "result", "tools_used", "metadata"):
                            continue
                        if v and str(v).strip():
                            parts.append(f"{k}: {v}")
                    response = "\n".join(parts) if parts else str(result)
                return response
            return str(result)
        except Exception as exc:
            logger.error(
                "MultiagentAdapter[%s] failed: %s", self.name, exc,
                exc_info=True,
            )
            return f"[Error en agente {self.name}: {exc}]"

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes to the wrapped agent."""
        return getattr(self._agent, name)


def create_multiagent_adapters(
    agents: list[Any],
) -> list[MultiagentAdapter]:
    """Create Protocol-compatible adapters for a list of multiagent agents.

    Args:
        agents: List of multiagent BaseAgent instances.

    Returns:
        List of MultiagentAdapter instances.
    """
    return [MultiagentAdapter(agent) for agent in agents]
