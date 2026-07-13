"""Orchestrator stub for the core package.

Sprint 1 - Prompt 1 (Foundation). The orchestrator wires together agents,
memory, and tools. The full implementation arrives in Sprint 2 (memory)
and Sprint 3 (tools).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class Agent(Protocol):
    """Protocol every agent must satisfy."""

    name: str

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        """Handle a user message and return a response."""


class Memory(Protocol):
    """Protocol every memory backend must satisfy."""

    async def store(self, key: str, value: Any) -> None: ...

    async def retrieve(self, key: str) -> Any | None: ...


class Tool(Protocol):
    """Protocol every tool must satisfy."""

    name: str

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]: ...


class Orchestrator:
    """Central coordinator.

    Coordinates the lifecycle of a conversation: receives a message,
    decides which agent should handle it, optionally reads/writes memory,
    and may invoke tools. This is a stub - real behaviour lands in
    Sprint 2 and Sprint 3.
    """

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._tools: dict[str, Tool] = {}
        self._memory: Memory | None = None
        logger.info("Core orchestrator initialized (stage: scaffolding)")

    def register_agent(self, agent: Agent) -> None:
        """Register an agent with the orchestrator."""
        self._agents[agent.name] = agent
        logger.debug("Registered agent '%s'", agent.name)

    def register_tool(self, tool: Tool) -> None:
        """Register a tool with the orchestrator."""
        self._tools[tool.name] = tool
        logger.debug("Registered tool '%s'", tool.name)

    def attach_memory(self, memory: Memory) -> None:
        """Attach a memory backend."""
        self._memory = memory
        logger.debug("Memory backend attached")

    async def handle_message(self, message: str) -> dict[str, Any]:
        """Handle an incoming user message (stub)."""
        return {
            "status": "not_implemented",
            "message": "Core orchestrator is in scaffolding stage.",
        }
