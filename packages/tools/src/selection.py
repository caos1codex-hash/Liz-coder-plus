"""Tool Selection Engine — automatic best-tool picker.

Sprint 4.2 — Tool Intelligence: Tool Selection Engine.

This module provides :class:`ToolSelectionEngine`, the top-level
component responsible for:

1. Retrieving candidate tools from a :class:`ToolRegistry`.
2. Applying a chain of :class:`~src.filters.BaseFilter` instances.
3. Scoring and ranking the survivors via :class:`~src.ranking.ToolRanker`.
4. Returning the best tool (or a ranked list).

The engine **does not execute** any tool — it only *selects*.

Typical usage::

    engine = ToolSelectionEngine(registry)
    tool = engine.select_tool(
        task_name="read_file",
        capability_requirements=frozenset({"file"}),
    )
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from src.base import BaseTool, PermissionLevel, ToolCategory
from src.exceptions import (
    AmbiguousSelectionError,
    NoToolFoundError,
)
from src.filters import (
    DEFAULT_FILTER_CHAIN,
    BaseFilter,
    ExecutionMode,
    SelectionContext,
)
from src.ranking import RankingResult, RankingWeights, ToolRanker
from src.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Engine configuration
# ----------------------------------------------------------------------


@dataclass
class EngineConfig:
    """Configuration for :class:`ToolSelectionEngine`.

    Attributes:
        weights:         Ranking weights for all scoring factors.
        filters:         Ordered list of filters to apply.
        max_cost:        Ceiling for cost normalisation in ranking.
        max_latency_ms:  Ceiling for latency normalisation in ranking.
        allow_ties:      If ``False``, :meth:`select_tool` raises
                         :class:`AmbiguousSelectionError` when the
                         top two tools have identical scores.
        tie_epsilon:     Scores within this delta are considered tied.
    """

    weights: RankingWeights = field(default_factory=RankingWeights)
    filters: list[BaseFilter] = field(
        default_factory=lambda: list(DEFAULT_FILTER_CHAIN),
    )
    max_cost: float | None = None
    max_latency_ms: float | None = None
    allow_ties: bool = False
    tie_epsilon: float = 1e-6


# ----------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------


class ToolSelectionEngine:
    """Automatically selects the best tool for a given task.

    The engine is a thin orchestrator that delegates filtering to
    :mod:`src.filters` and scoring to :mod:`src.ranking`.

    Args:
        registry: The tool registry to select from.
        config:   Engine configuration (weights, filters, etc.).

    Example::

        engine = ToolSelectionEngine(registry)
        tool = engine.select_tool(
            task_name="list directory",
            capability_requirements=frozenset({"file"}),
        )
        print(tool.name)  # e.g. "file"
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        config: EngineConfig | None = None,
    ) -> None:
        self._registry = registry
        self._config = config or EngineConfig()
        self._ranker = ToolRanker(
            weights=self._config.weights,
            max_cost=self._config.max_cost,
            max_latency_ms=self._config.max_latency_ms,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> EngineConfig:
        """Read-only access to the engine configuration."""
        return self._config

    @property
    def ranker(self) -> ToolRanker:
        """Read-only access to the underlying ranker."""
        return self._ranker

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_tool(
        self,
        *,
        task_name: str = "",
        task_description: str = "",
        capability_requirements: frozenset[str] | None = None,
        agent_requirements: frozenset[str] | None = None,
        max_permission_level: PermissionLevel = PermissionLevel.HIGH,
        max_cost: float = float("inf"),
        max_latency_ms: float = float("inf"),
        min_safety_score: float = 0.0,
        execution_mode: ExecutionMode = ExecutionMode.AUTOMATIC,
        include_tools: frozenset[str] | None = None,
        exclude_tools: frozenset[str] | None = None,
        preferred_categories: frozenset[ToolCategory] | None = None,
        preferred_tools: frozenset[str] | None = None,
    ) -> BaseTool:
        """Select the single best tool for the given criteria.

        This is a convenience wrapper around :meth:`rank_tools` that
        returns only the top-ranked tool.

        Raises:
            NoToolFoundError: If no tool passes the filter chain.
            AmbiguousSelectionError: If multiple tools tie for the
                top rank and ``allow_ties`` is ``False``.
        """
        ranked = self.rank_tools(
            task_name=task_name,
            task_description=task_description,
            capability_requirements=capability_requirements,
            agent_requirements=agent_requirements,
            max_permission_level=max_permission_level,
            max_cost=max_cost,
            max_latency_ms=max_latency_ms,
            min_safety_score=min_safety_score,
            execution_mode=execution_mode,
            include_tools=include_tools,
            exclude_tools=exclude_tools,
            preferred_categories=preferred_categories,
            preferred_tools=preferred_tools,
        )

        if not ranked:
            raise NoToolFoundError(
                "No tool found matching the selection criteria",
                criteria={
                    "task_name": task_name,
                    "capabilities": str(capability_requirements or frozenset()),
                },
            )

        top = ranked[0]

        # Check for ties.
        if (
            not self._config.allow_ties
            and len(ranked) >= 2
            and abs(ranked[0].score - ranked[1].score) <= self._config.tie_epsilon
        ):
            tied = [
                r.tool.name
                for r in ranked
                if abs(r.score - top.score) <= self._config.tie_epsilon
            ]
            raise AmbiguousSelectionError(
                f"Multiple tools tied for top rank: {', '.join(tied)}",
                candidates=tied,
                score=top.score,
            )

        logger.info(
            "Selected tool '%s' (score=%.4f) for task '%s'",
            top.tool.name,
            top.score,
            task_name,
        )
        return top.tool

    def select_tools(
        self,
        *,
        top_n: int = 3,
        task_name: str = "",
        task_description: str = "",
        capability_requirements: frozenset[str] | None = None,
        agent_requirements: frozenset[str] | None = None,
        max_permission_level: PermissionLevel = PermissionLevel.HIGH,
        max_cost: float = float("inf"),
        max_latency_ms: float = float("inf"),
        min_safety_score: float = 0.0,
        execution_mode: ExecutionMode = ExecutionMode.AUTOMATIC,
        include_tools: frozenset[str] | None = None,
        exclude_tools: frozenset[str] | None = None,
        preferred_categories: frozenset[ToolCategory] | None = None,
        preferred_tools: frozenset[str] | None = None,
    ) -> list[BaseTool]:
        """Select the top *N* tools.

        Returns a list of up to *top_n* tools, ordered by score
        (best first).  If fewer than *top_n* tools are available,
        returns all of them.  Raises :class:`NoToolFoundError` if
        the ranked list is empty.
        """
        ranked = self.rank_tools(
            task_name=task_name,
            task_description=task_description,
            capability_requirements=capability_requirements,
            agent_requirements=agent_requirements,
            max_permission_level=max_permission_level,
            max_cost=max_cost,
            max_latency_ms=max_latency_ms,
            min_safety_score=min_safety_score,
            execution_mode=execution_mode,
            include_tools=include_tools,
            exclude_tools=exclude_tools,
            preferred_categories=preferred_categories,
            preferred_tools=preferred_tools,
        )

        if not ranked:
            raise NoToolFoundError(
                "No tool found matching the selection criteria",
                criteria={
                    "task_name": task_name,
                    "capabilities": str(capability_requirements or frozenset()),
                },
            )

        return [r.tool for r in ranked[:top_n]]

    def rank_tools(
        self,
        *,
        task_name: str = "",
        task_description: str = "",
        capability_requirements: frozenset[str] | None = None,
        agent_requirements: frozenset[str] | None = None,
        max_permission_level: PermissionLevel = PermissionLevel.HIGH,
        max_cost: float = float("inf"),
        max_latency_ms: float = float("inf"),
        min_safety_score: float = 0.0,
        execution_mode: ExecutionMode = ExecutionMode.AUTOMATIC,
        include_tools: frozenset[str] | None = None,
        exclude_tools: frozenset[str] | None = None,
        preferred_categories: frozenset[ToolCategory] | None = None,
        preferred_tools: frozenset[str] | None = None,
    ) -> list[RankingResult]:
        """Full pipeline: retrieve → filter → rank → return.

        Returns a list of :class:`RankingResult` sorted from best
        to worst.  The list may be empty if all tools are filtered
        out (the caller decides whether to raise an error).
        """
        t0 = time.monotonic()

        # 1. Build context.
        context = SelectionContext(
            task_name=task_name,
            task_description=task_description,
            capability_requirements=capability_requirements or frozenset(),
            agent_requirements=agent_requirements or frozenset(),
            max_permission_level=max_permission_level,
            max_cost=max_cost,
            max_latency_ms=max_latency_ms,
            min_safety_score=min_safety_score,
            execution_mode=execution_mode,
            include_tools=include_tools or frozenset(),
            exclude_tools=exclude_tools or frozenset(),
            preferred_categories=preferred_categories or frozenset(),
            preferred_tools=preferred_tools or frozenset(),
        )

        # 2. Retrieve candidates from registry.
        candidates = list(self._registry.all())
        logger.debug(
            "Retrieved %d candidate(s) from registry for task '%s'",
            len(candidates),
            task_name,
        )

        # 3. Apply filter chain.
        filtered = self._apply_filters(candidates, context)
        removed_count = len(candidates) - len(filtered)
        if removed_count > 0:
            removed_names = [t.name for t in candidates if t not in filtered]
            logger.info(
                "Filters removed %d tool(s): %s",
                removed_count,
                ", ".join(removed_names),
            )

        # 4. Rank.
        ranked = self._ranker.rank(filtered, context)

        # 5. Log final ranking.
        for r in ranked:
            logger.debug("  %s → %.4f  %s", r.tool.name, r.score, r.reason)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "Selection completed in %.1fms: %d candidate(s), "
            "%d passed filters, %d ranked",
            elapsed_ms,
            len(candidates),
            len(filtered),
            len(ranked),
        )

        return ranked

    def score_tool(
        self,
        tool_name: str,
        *,
        task_name: str = "",
        capability_requirements: frozenset[str] | None = None,
    ) -> float:
        """Score a single tool by name (convenience method).

        Returns the normalised score (0.0 – 1.0).

        Raises:
            NoToolFoundError: If the tool is not in the registry.
        """
        tool = self._registry.get(tool_name)
        if tool is None:
            raise NoToolFoundError(
                f"Tool '{tool_name}' not found in registry",
            )

        context = SelectionContext(
            task_name=task_name,
            capability_requirements=capability_requirements or frozenset(),
        )
        result = self._ranker.score_tool(tool, context)
        return result.score

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply_filters(
        self,
        tools: list[BaseTool],
        context: SelectionContext,
    ) -> list[BaseTool]:
        """Run every filter in the configured chain sequentially."""
        current = tools
        for filt in self._config.filters:
            current = filt.apply(current, context)
            if not current:
                break  # no tools left — short-circuit
        return current


__all__ = [
    "EngineConfig",
    "ToolSelectionEngine",
]
