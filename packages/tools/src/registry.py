"""Tool registry.

Holds references to all tools available to the orchestrator. Tools
are validated against the ``Tool`` Protocol before registration.

Supports:
  - Registration with source tracking (internal, external, plugin).
  - Activation/deactivation of individual tools.
  - Search by name, category, and permission level.
  - Duplicate prevention (configurable).
  - Metadata inspection for all registered tools.

Sprint 1.6 — Enhanced with source tracking, activation control,
category filtering, and permission-level queries.
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any, Iterable

from src.base import BaseTool, PermissionLevel, ToolCategory

logger = logging.getLogger(__name__)


def _satisfies_tool_protocol(obj: Any) -> bool:
    """Check if obj satisfies the Tool protocol (duck-typing).

    Avoids importing from packages/core at module level, which breaks
    tests that only have packages/tools on sys.path.
    """
    return (
        hasattr(obj, "name")
        and isinstance(getattr(obj, "name", None), str)
        and hasattr(obj, "execute")
        and callable(getattr(obj, "execute", None))
    )


class ToolSource(str, Enum):
    """Origin of a registered tool."""

    INTERNAL = "internal"
    EXTERNAL = "external"
    PLUGIN = "plugin"


class DuplicatePolicy(str, Enum):
    """Policy for handling duplicate tool names on registration."""

    OVERWRITE = "overwrite"
    RAISE = "raise"
    SKIP = "skip"


class ToolRegistration:
    """Record for a registered tool with metadata."""

    __slots__ = (
        "tool",
        "source",
        "registered_at",
        "enabled",
    )

    def __init__(
        self,
        tool: BaseTool,
        *,
        source: ToolSource = ToolSource.INTERNAL,
    ) -> None:
        self.tool = tool
        self.source = source
        self.registered_at = time.monotonic()
        self.enabled = True

    @property
    def metadata(self) -> dict[str, Any]:
        """Combined metadata for this registration."""
        base = self.tool.metadata
        base["source"] = self.source.value
        base["enabled"] = self.enabled
        base["registered_at"] = self.registered_at
        return base


class ToolRegistry:
    """Registry of available tools with validation and lifecycle control.

    Args:
        duplicate_policy: How to handle duplicate tool names.
            Default is OVERWRITE (warns and replaces).

    Sprint 1.6 — Added source tracking, enable/disable, category
    filtering, and permission-level queries.
    """

    def __init__(
        self,
        *,
        duplicate_policy: DuplicatePolicy = DuplicatePolicy.OVERWRITE,
    ) -> None:
        self._tools: dict[str, ToolRegistration] = {}
        self._policy = duplicate_policy

    def register(
        self,
        tool: BaseTool,
        *,
        source: ToolSource = ToolSource.INTERNAL,
    ) -> None:
        """Register a tool after validation.

        Args:
            tool: A tool instance satisfying the Tool protocol.
            source: Where the tool comes from.

        Raises:
            TypeError: If the tool does not satisfy the Tool protocol.
            ValueError: If duplicate_policy is RAISE and name exists.
        """
        if not _satisfies_tool_protocol(tool):
            raise TypeError(
                f"Tool '{getattr(tool, 'name', '?')}' does not satisfy "
                f"the Tool protocol (needs 'name: str' and "
                f"'async execute(params) -> dict')"
            )

        if tool.name in self._tools:
            if self._policy is DuplicatePolicy.RAISE:
                raise ValueError(
                    f"Tool '{tool.name}' is already registered "
                    f"(policy=RAISE). Unregister it first."
                )
            if self._policy is DuplicatePolicy.SKIP:
                logger.info(
                    "Skipping duplicate tool '%s' (policy=SKIP)",
                    tool.name,
                )
                return
            # OVERWRITE: warn and replace.
            logger.warning("Overwriting existing tool '%s'", tool.name)

        self._tools[tool.name] = ToolRegistration(tool, source=source)
        logger.debug(
            "Registered tool '%s' (source=%s)", tool.name, source.value
        )

    def unregister(self, name: str) -> bool:
        """Remove a tool from the registry.

        Args:
            name: Name of the tool to remove.

        Returns:
            True if the tool was found and removed, False otherwise.
        """
        reg = self._tools.pop(name, None)
        if reg is not None:
            logger.info("Unregistered tool '%s'", name)
            return True
        return False

    def get(self, name: str) -> BaseTool | None:
        """Look up a tool by name.

        Returns None if the tool is not registered or is disabled.
        """
        reg = self._tools.get(name)
        if reg is None:
            return None
        if not reg.enabled:
            logger.debug("Tool '%s' is disabled", name)
            return None
        return reg.tool

    def get_all(self, name: str) -> BaseTool | None:
        """Look up a tool by name regardless of enabled state.

        Returns the tool even if disabled, or None if not registered.
        """
        reg = self._tools.get(name)
        return reg.tool if reg is not None else None

    def enable(self, name: str) -> bool:
        """Enable a registered tool.

        Returns:
            True if the tool was found and enabled, False otherwise.
        """
        reg = self._tools.get(name)
        if reg is None:
            return False
        if not reg.enabled:
            reg.enabled = True
            try:
                reg.tool.enable()
            except (RuntimeError, AttributeError):
                pass
            logger.info("Enabled tool '%s'", name)
        return True

    def disable(self, name: str) -> bool:
        """Disable a registered tool so it cannot be executed.

        Returns:
            True if the tool was found and disabled, False otherwise.
        """
        reg = self._tools.get(name)
        if reg is None:
            return False
        if reg.enabled:
            reg.enabled = False
            try:
                reg.tool.disable()
            except (RuntimeError, AttributeError):
                pass
            logger.info("Disabled tool '%s'", name)
        return True

    def all(self) -> Iterable[BaseTool]:
        """Return all enabled registered tools."""
        return (
            reg.tool for reg in self._tools.values() if reg.enabled
        )

    def all_registrations(self) -> Iterable[ToolRegistration]:
        """Return all registrations (including disabled)."""
        return self._tools.values()

    def names(self) -> list[str]:
        """Return the list of all registered tool names."""
        return list(self._tools.keys())

    def enabled_names(self) -> list[str]:
        """Return the list of enabled tool names."""
        return [n for n, r in self._tools.items() if r.enabled]

    def by_category(
        self, category: ToolCategory
    ) -> list[BaseTool]:
        """Return enabled tools matching a category."""
        return [
            reg.tool
            for reg in self._tools.values()
            if reg.enabled and reg.tool.category == category
        ]

    def by_permission_level(
        self, level: PermissionLevel
    ) -> list[BaseTool]:
        """Return enabled tools matching a permission level."""
        return [
            reg.tool
            for reg in self._tools.values()
            if reg.enabled and reg.tool.permission_level == level
        ]

    def by_source(self, source: ToolSource) -> list[BaseTool]:
        """Return tools from a specific source."""
        return [
            reg.tool
            for reg in self._tools.values()
            if reg.source == source
        ]

    def summary(self) -> dict[str, Any]:
        """Return a summary of the registry state."""
        total = len(self._tools)
        enabled = sum(1 for r in self._tools.values() if r.enabled)
        by_cat: dict[str, int] = {}
        by_level: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for reg in self._tools.values():
            if reg.enabled:
                cat = reg.tool.category.value
                lvl = reg.tool.permission_level.value
                src = reg.source.value
                by_cat[cat] = by_cat.get(cat, 0) + 1
                by_level[lvl] = by_level.get(lvl, 0) + 1
                by_source[src] = by_source.get(src, 0) + 1
        return {
            "total": total,
            "enabled": enabled,
            "disabled": total - enabled,
            "by_category": by_cat,
            "by_permission_level": by_level,
            "by_source": by_source,
        }

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools