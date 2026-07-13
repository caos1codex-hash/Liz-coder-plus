"""Base class for all agents.

Every agent in Liz Coder Plus inherits from BaseAgent. Subclasses must
implement `handle`. The base class provides identity, logging and a
shared `name` property required by the orchestrator's Agent protocol.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base class for agents.

    Subclasses must:
      - Set the `name` attribute.
      - Implement `handle(message, context) -> str`.
    """

    name: str = "base"

    def __init__(self, *, name: str | None = None) -> None:
        if name is not None:
            self.name = name
        logger.debug("Agent '%s' instantiated", self.name)

    @abstractmethod
    async def handle(self, message: str, context: dict[str, Any]) -> str:
        """Process a user message and return a response.

        Args:
            message: The raw user input.
            context: Additional context (memory, session, etc.).

        Returns:
            The agent's response as a string.
        """
        raise NotImplementedError
