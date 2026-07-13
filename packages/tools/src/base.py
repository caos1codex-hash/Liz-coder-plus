"""Base class for all tools.

Every tool inherits from BaseTool. Subclasses must:
  - Set the ``name`` attribute.
  - Implement ``execute(params) -> dict``.

All tool executions are mediated by the ToolExecutor which consults
the PermissionService to decide whether the user must confirm the
action first.

The class provides metadata, permission category, and a description
for the orchestrator and UI to inspect before execution.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ToolCategory(str, Enum):
    """Categories of tools, used for permission grouping."""

    SHELL = "shell"
    FILE = "file"
    WEB = "web"
    APP = "app"
    SYSTEM = "system"
    CUSTOM = "custom"


class BaseTool(ABC):
    """Abstract base class for tools.

    Subclasses must set ``name`` and implement ``execute(params) -> dict``.
    The returned dict should include at minimum a ``"success"`` boolean.
    """

    name: str = "base"
    description: str = ""
    version: str = "0.1.0"
    category: ToolCategory = ToolCategory.CUSTOM

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
            A dictionary describing the execution result. Should
            include a ``"success"`` boolean. May include ``"output"``,
            ``"error"``, and other tool-specific fields.
        """
        raise NotImplementedError

    @property
    def metadata(self) -> dict[str, Any]:
        """Serializable metadata about this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "category": self.category.value,
            "type": self.__class__.__name__,
        }

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} name={self.name!r} "
            f"category={self.category.value}>"
        )