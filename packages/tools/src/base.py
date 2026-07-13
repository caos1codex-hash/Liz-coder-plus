"""Base class for all tools.

Every tool inherits from BaseTool. Subclasses must:
  - Set the `name` attribute.
  - Implement `execute(params) -> dict`.

All tool executions are mediated by the PermissionService to decide
whether the user must confirm the action first.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class BaseTool(ABC):
    """Abstract base class for tools."""

    name: str = "base"

    def __init__(self, *, name: str | None = None) -> None:
        if name is not None:
            self.name = name
        logger.debug("Tool '%s' instantiated", self.name)

    @abstractmethod
    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool with the given parameters.

        Args:
            params: Tool-specific parameters.

        Returns:
            A dictionary describing the execution result. Always
            includes a "success" boolean.
        """
        raise NotImplementedError
