"""Tool registry.

Holds references to all tools available to the orchestrator. Tools
are validated against the ``Tool`` Protocol before registration.

The PermissionService is consulted before any tool actually runs,
mediated by the ToolExecutor in the core package.
"""

from __future__ import annotations

import logging
from typing import Iterable

from src.base import BaseTool
from src.protocols import Tool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of available tools with validation."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool after validation.

        Args:
            tool: A tool instance.

        Raises:
            TypeError: If the tool does not satisfy the Tool protocol.
        """
        if not isinstance(tool, Tool):
            raise TypeError(
                f"Tool '{getattr(tool, 'name', '?')}' does not satisfy "
                f"the Tool protocol (needs 'name: str' and "
                f"'async execute(params) -> dict')"
            )
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

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools