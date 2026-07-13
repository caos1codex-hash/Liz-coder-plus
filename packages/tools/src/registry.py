"""Tool registry.

Holds references to all tools available to the orchestrator. The
PermissionService is consulted before any tool actually runs.
"""

from __future__ import annotations

import logging
from typing import Iterable

from src.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool."""
        if tool.name in self._tools:
            logger.warning("Overwriting existing tool '%s'", tool.name)
        self._tools[tool.name] = tool
        logger.debug("Registered tool '%s'", tool.name)

    def get(self, name: str) -> BaseTool | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def all(self) -> Iterable[BaseTool]:
        """Return all registered tools."""
        return self._tools.values()

    def names(self) -> list[str]:
        """Return the list of registered tool names."""
        return list(self._tools.keys())
