"""Independent, reusable filters for the Tool Selection Engine.

Sprint 4.2 — Tool Intelligence: Tool Selection Engine.

Each filter is a callable class with a descriptive ``name`` attribute
and an ``apply()`` method.  Filters receive a list of :class:`BaseTool`
instances and a :class:`SelectionContext`, and return the subset that
passes the filter's criteria.

Filters are designed to be **composable**: the selection engine chains
them in sequence, each one narrowing the candidate set further.

Usage::

    from src.filters import AvailabilityFilter

    available = AvailabilityFilter().apply(candidates, context)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from src.base import BaseTool, PermissionLevel, ToolCategory, ToolState
from src.exceptions import FilterError

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Selection context — shared input for all filters
# ----------------------------------------------------------------------


class ExecutionMode(str, Enum):
    """How the tool will be invoked."""

    AUTOMATIC = "automatic"
    MANUAL_CONFIRM = "manual_confirm"
    BACKGROUND = "background"
    INTERACTIVE = "interactive"


@dataclass(frozen=True)
class SelectionContext:
    """All contextual information a filter may need.

    Attributes:
        task_name: Short name of the task being planned.
        task_description: Longer description of what the task does.
        capability_requirements: Set of capability tags the tool must
            provide (e.g. ``{"file_read", "file_write"}``).
        agent_requirements: Set of capability tags the *agent* needs
            the tool to satisfy.
        max_permission_level: Highest permission level allowed.
        max_cost: Maximum acceptable execution cost (arbitrary units).
        max_latency_ms: Maximum acceptable latency in milliseconds.
        min_safety_score: Minimum safety score (0.0 – 1.0).
        execution_mode: Desired execution mode.
        include_tools: Explicit tool names to always include
            (bypasses most filters).
        exclude_tools: Explicit tool names to always remove.
        preferred_categories: Categories to prefer (used by scoring,
            not by filters directly).
        preferred_tools: Tool names to boost in ranking.
    """

    task_name: str = ""
    task_description: str = ""
    capability_requirements: frozenset[str] = frozenset()
    agent_requirements: frozenset[str] = frozenset()
    max_permission_level: PermissionLevel = PermissionLevel.HIGH
    max_cost: float = float("inf")
    max_latency_ms: float = float("inf")
    min_safety_score: float = 0.0
    execution_mode: ExecutionMode = ExecutionMode.AUTOMATIC
    include_tools: frozenset[str] = frozenset()
    exclude_tools: frozenset[str] = frozenset()
    preferred_categories: frozenset[ToolCategory] = frozenset()
    preferred_tools: frozenset[str] = frozenset()


# ----------------------------------------------------------------------
# Base filter
# ----------------------------------------------------------------------


class BaseFilter(ABC):
    """Abstract base for all tool-selection filters.

    Subclasses must implement :meth:`_do_apply` which receives the
    candidate list and context, and returns the filtered list.

    The public :meth:`apply` method adds logging and error handling
    around the subclass logic.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this filter."""

    def apply(
        self,
        tools: list[BaseTool],
        context: SelectionContext,
    ) -> list[BaseTool]:
        """Run this filter on *tools*.

        Returns the subset of *tools* that pass the filter.

        Errors inside the filter are caught, logged, and re-raised
        as :class:`FilterError` so the selection engine can report
        which filter failed.
        """
        before = len(tools)
        try:
            result = self._do_apply(tools, context)
        except FilterError:
            raise
        except Exception as exc:
            raise FilterError(
                f"Filter '{self.name}' failed: {exc}",
                filter_name=self.name,
            ) from exc
        removed = before - len(result)
        if removed > 0:
            removed_names = [t.name for t in tools if t not in result]
            logger.debug(
                "Filter '%s' removed %d tool(s): %s",
                self.name,
                removed,
                ", ".join(removed_names),
            )
        return result

    @abstractmethod
    def _do_apply(
        self,
        tools: list[BaseTool],
        context: SelectionContext,
    ) -> list[BaseTool]:
        """Subclass-specific filtering logic."""


# ----------------------------------------------------------------------
# Concrete filters
# ----------------------------------------------------------------------


class AvailabilityFilter(BaseFilter):
    """Remove tools that are not in the AVAILABLE state.

    A tool in EXECUTING, ERROR, DISABLED, or COMPLETED state is
    considered unavailable for new selections.
    """

    @property
    def name(self) -> str:
        return "availability"

    def _do_apply(
        self,
        tools: list[BaseTool],
        context: SelectionContext,
    ) -> list[BaseTool]:
        return [t for t in tools if t.state == ToolState.AVAILABLE]


class CapabilityFilter(BaseFilter):
    """Remove tools whose category or metadata tags do not satisfy
    the capability requirements in the context.

    Matching strategy:
      - If ``capability_requirements`` is empty, all tools pass.
      - Otherwise a tool passes if its **category** (as a string)
        is in the requirements set, OR if any of its
        ``metadata.get("capabilities", [])`` entries intersect with
        the requirements.
    """

    @property
    def name(self) -> str:
        return "capability"

    def _do_apply(
        self,
        tools: list[BaseTool],
        context: SelectionContext,
    ) -> list[BaseTool]:
        if not context.capability_requirements:
            return list(tools)

        result: list[BaseTool] = []
        for tool in tools:
            tool_caps: set[str] = {tool.category.value}
            caps_from_meta = tool.metadata.get("capabilities")
            if isinstance(caps_from_meta, list):
                tool_caps.update(str(c) for c in caps_from_meta)
            if tool_caps & context.capability_requirements:
                result.append(tool)
        return result


class ExecutionModeFilter(BaseFilter):
    """Remove tools that are incompatible with the requested
    execution mode.

    Rules:
      - ``AUTOMATIC``: tools with ``PermissionLevel.HIGH`` are removed
        unless the tool metadata explicitly declares
        ``"auto_safe": True``.
      - ``MANUAL_CONFIRM``: all tools pass (user will confirm).
      - ``BACKGROUND``: tools with ``PermissionLevel.HIGH`` or
        ``PermissionLevel.MEDIUM`` are removed.
      - ``INTERACTIVE``: all tools pass.
    """

    @property
    def name(self) -> str:
        return "execution_mode"

    def _do_apply(
        self,
        tools: list[BaseTool],
        context: SelectionContext,
    ) -> list[BaseTool]:
        mode = context.execution_mode

        if mode in (ExecutionMode.MANUAL_CONFIRM, ExecutionMode.INTERACTIVE):
            return list(tools)

        if mode == ExecutionMode.AUTOMATIC:
            return [
                t
                for t in tools
                if t.permission_level != PermissionLevel.HIGH
                or t.metadata.get("auto_safe") is True
            ]

        if mode == ExecutionMode.BACKGROUND:
            return [t for t in tools if t.permission_level == PermissionLevel.LOW]

        return list(tools)


class PermissionFilter(BaseFilter):
    """Remove tools whose permission level exceeds the maximum
    allowed by the context.

    Permission ordering: LOW < MEDIUM < HIGH.
    """

    @property
    def name(self) -> str:
        return "permission"

    _LEVEL_ORDER: dict[PermissionLevel, int] = {
        PermissionLevel.LOW: 0,
        PermissionLevel.MEDIUM: 1,
        PermissionLevel.HIGH: 2,
    }

    def _do_apply(
        self,
        tools: list[BaseTool],
        context: SelectionContext,
    ) -> list[BaseTool]:
        max_ord = self._LEVEL_ORDER[context.max_permission_level]
        return [t for t in tools if self._LEVEL_ORDER[t.permission_level] <= max_ord]


class CostFilter(BaseFilter):
    """Remove tools whose declared execution cost exceeds the budget.

    Cost is read from ``tool.metadata["cost"]`` (float).  Tools that
    do not declare a cost are assumed to be free (cost = 0).
    """

    @property
    def name(self) -> str:
        return "cost"

    def _do_apply(
        self,
        tools: list[BaseTool],
        context: SelectionContext,
    ) -> list[BaseTool]:
        if context.max_cost == float("inf"):
            return list(tools)
        return [
            t for t in tools if float(t.metadata.get("cost", 0)) <= context.max_cost
        ]


class LatencyFilter(BaseFilter):
    """Remove tools whose average latency exceeds the threshold.

    Latency is derived from ``tool.metadata["avg_latency_ms"]`` if
    present.  When not declared, the filter falls back to computing
    the average from ``total_time_ms / max(1, executions)``.
    """

    @property
    def name(self) -> str:
        return "latency"

    def _do_apply(
        self,
        tools: list[BaseTool],
        context: SelectionContext,
    ) -> list[BaseTool]:
        if context.max_latency_ms == float("inf"):
            return list(tools)
        result: list[BaseTool] = []
        for tool in tools:
            avg = self._effective_latency(tool)
            if avg <= context.max_latency_ms:
                result.append(tool)
        return result

    @staticmethod
    def _effective_latency(tool: BaseTool) -> float:
        """Return the effective average latency for *tool*."""
        declared = tool.metadata.get("avg_latency_ms")
        if declared is not None:
            return float(declared)
        total = float(tool.metadata.get("total_time_ms", 0))
        execs = max(1, int(tool.metadata.get("executions", 0)))
        return total / execs


class SafetyFilter(BaseFilter):
    """Remove tools whose safety score is below the minimum threshold.

    Safety score is read from ``tool.metadata["safety_score"]`` (float
    0.0 – 1.0).  If not declared, the tool is assigned a default
    safety score based on its permission level:
      - LOW → 1.0
      - MEDIUM → 0.7
      - HIGH → 0.4
    """

    @property
    def name(self) -> str:
        return "safety"

    _DEFAULT_SAFETY: dict[PermissionLevel, float] = {
        PermissionLevel.LOW: 1.0,
        PermissionLevel.MEDIUM: 0.7,
        PermissionLevel.HIGH: 0.4,
    }

    def _do_apply(
        self,
        tools: list[BaseTool],
        context: SelectionContext,
    ) -> list[BaseTool]:
        if context.min_safety_score <= 0.0:
            return list(tools)
        result: list[BaseTool] = []
        for tool in tools:
            score = tool.metadata.get("safety_score")
            if score is None:
                score = self._DEFAULT_SAFETY[tool.permission_level]
            if float(score) >= context.min_safety_score:
                result.append(tool)
        return result


class UserPreferenceFilter(BaseFilter):
    """Apply user-level include / exclude preferences.

    - Tools in ``context.exclude_tools`` are always removed.
    - Tools in ``context.include_tools`` are always kept (even if
      they would otherwise be filtered by *this* filter — other
      filters in the chain may still remove them).
    """

    @property
    def name(self) -> str:
        return "user_preference"

    def _do_apply(
        self,
        tools: list[BaseTool],
        context: SelectionContext,
    ) -> list[BaseTool]:
        if not context.exclude_tools and not context.include_tools:
            return list(tools)

        result: list[BaseTool] = []
        for tool in tools:
            if tool.name in context.exclude_tools:
                continue
            result.append(tool)
        return result


# ----------------------------------------------------------------------
# Convenience: default filter chain
# ----------------------------------------------------------------------


DEFAULT_FILTER_CHAIN: list[BaseFilter] = [
    AvailabilityFilter(),
    CapabilityFilter(),
    ExecutionModeFilter(),
    PermissionFilter(),
    CostFilter(),
    LatencyFilter(),
    SafetyFilter(),
    UserPreferenceFilter(),
]
"""Sensible default ordering of all built-in filters.

The selection engine applies them in this exact sequence unless the
caller provides a custom chain."""


__all__ = [
    "AvailabilityFilter",
    "BaseFilter",
    "CapabilityFilter",
    "CostFilter",
    "DEFAULT_FILTER_CHAIN",
    "ExecutionMode",
    "ExecutionModeFilter",
    "LatencyFilter",
    "PermissionFilter",
    "SafetyFilter",
    "SelectionContext",
    "UserPreferenceFilter",
]
