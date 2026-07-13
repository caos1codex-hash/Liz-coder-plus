"""Base class for all agents.

Every agent in Liz Coder Plus inherits from BaseAgent. Subclasses must
implement `handle`. The base class provides identity, logging, state
tracking, capabilities declaration and a shared `name` property required
by the orchestrator's Agent protocol.

The class is designed to satisfy the ``Agent`` Protocol defined in
``packages/core/src/protocols.py`` (duck-typed: ``name: str`` +
``async handle(message, context) -> str``).
"""

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class AgentState(str, Enum):
    """Lifecycle states for an agent.

    Transitions:
        CREATED -> READY -> RUNNING -> COMPLETED
                                    -> FAILED
                                    -> CANCELLED
        READY   -> SHUTDOWN
        Any non-terminal -> SHUTDOWN (safe cleanup)
    """

    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SHUTDOWN = "shutdown"


class BaseAgent(ABC):
    """Abstract base class for agents.

    Subclasses must:
      - Set the ``name`` attribute (or pass it to ``__init__``).
      - Implement ``handle(message, context) -> str``.

    The class tracks its own lifecycle state and provides structured
    metadata for the orchestrator to inspect capabilities before
    dispatching a message.
    """

    name: str = "base"
    description: str = ""
    version: str = "0.1.0"
    capabilities: list[str] = []

    def __init__(self, *, name: str | None = None) -> None:
        if name is not None:
            self.name = name
        self._state = AgentState.CREATED
        self._id: str = str(uuid.uuid4())[:8]
        self._tasks_completed: int = 0
        self._tasks_failed: int = 0
        self._last_error: str | None = None
        self._created_at: float = time.monotonic()
        logger.info(
            "Agent '%s' (id=%s) instantiated", self.name, self._id
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> AgentState:
        """Current lifecycle state."""
        return self._state

    @property
    def agent_id(self) -> str:
        """Unique short identifier for this agent instance."""
        return self._id

    @property
    def tasks_completed(self) -> int:
        """Number of tasks successfully completed."""
        return self._tasks_completed

    @property
    def tasks_failed(self) -> int:
        """Number of tasks that resulted in failure."""
        return self._tasks_failed

    @property
    def last_error(self) -> str | None:
        """Last error message, if any."""
        return self._last_error

    @property
    def is_ready(self) -> bool:
        """Whether the agent is ready to accept tasks."""
        return self._state == AgentState.READY

    @property
    def metadata(self) -> dict[str, Any]:
        """Serializable metadata about this agent."""
        return {
            "name": self.name,
            "agent_id": self._id,
            "state": self._state.value,
            "description": self.description,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "tasks_completed": self._tasks_completed,
            "tasks_failed": self._tasks_failed,
            "last_error": self._last_error,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Prepare the agent for receiving tasks.

        Called by the orchestrator after registration. Subclasses can
        override to load resources, connect to services, etc.

        Transitions: CREATED -> READY.
        Raises:
            RuntimeError: If the agent is not in CREATED state.
        """
        if self._state != AgentState.CREATED:
            raise RuntimeError(
                f"Agent '{self.name}' cannot initialize from "
                f"state '{self._state.value}'"
            )
        self._state = AgentState.READY
        logger.info("Agent '%s' (id=%s) is now READY", self.name, self._id)

    async def shutdown(self) -> None:
        """Release resources and mark the agent as shut down.

        Safe to call from any non-terminal state. Subclasses should
        override to close connections, flush caches, etc.

        Transitions: Any non-terminal -> SHUTDOWN.
        """
        prev = self._state
        self._state = AgentState.SHUTDOWN
        logger.info(
            "Agent '%s' (id=%s) shut down (was %s)",
            self.name,
            self._id,
            prev.value,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    @abstractmethod
    async def handle(self, message: str, context: dict[str, Any]) -> str:
        """Process a user message and return a response.

        Args:
            message: The raw user input.
            context: Additional context (session, history, language, mode).

        Returns:
            The agent's response as a string.

        Raises:
            Exception: Any error during processing. The orchestrator
                will catch and handle it.
        """
        raise NotImplementedError

    async def safe_handle(
        self, message: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute handle with lifecycle tracking and error isolation.

        This is the recommended entry point for the orchestrator to call.
        It manages state transitions and records success/failure metrics.

        Returns:
            A dict with keys:
                - ``content``: The response string (empty on failure).
                - ``agent_id``: This agent's id.
                - ``state``: Final agent state.
                - ``error``: Error message if failed, else None.
                - ``duration_ms``: Execution time in milliseconds.
        """
        if self._state not in (AgentState.READY, AgentState.COMPLETED):
            return {
                "content": "",
                "agent_id": self._id,
                "state": self._state.value,
                "error": f"Agent '{self.name}' is not ready (state={self._state.value})",
                "duration_ms": 0,
            }

        self._state = AgentState.RUNNING
        start = time.monotonic()
        try:
            result = await self.handle(message, context)
            self._state = AgentState.COMPLETED
            self._tasks_completed += 1
            self._last_error = None
            return {
                "content": result,
                "agent_id": self._id,
                "state": self._state.value,
                "error": None,
                "duration_ms": int((time.monotonic() - start) * 1000),
            }
        except Exception as exc:
            self._state = AgentState.FAILED
            self._tasks_failed += 1
            self._last_error = str(exc)
            logger.exception(
                "Agent '%s' (id=%s) failed: %s", self.name, self._id, exc
            )
            return {
                "content": "",
                "agent_id": self._id,
                "state": self._state.value,
                "error": str(exc),
                "duration_ms": int((time.monotonic() - start) * 1000),
            }

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} name={self.name!r} "
            f"id={self._id} state={self._state.value}>"
        )