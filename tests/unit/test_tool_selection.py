"""Tests for the Tool Selection Engine — selection module.

Sprint 4.2
"""

from __future__ import annotations

import sys
from typing import Any

sys.path.insert(0, "packages/tools")
from tests.conftest import clear_src_cache  # noqa: E402
clear_src_cache()

import pytest  # noqa: E402

from src.base import (  # noqa: E402
    BaseTool,
    PermissionLevel,
    ToolCategory,
    ToolState,
)
from src.exceptions import (  # noqa: E402
    AmbiguousSelectionError,
    NoToolFoundError,
)
from src.filters import (  # noqa: E402
    ExecutionMode,
    SelectionContext,
)
from src.ranking import RankingWeights  # noqa: E402
from src.registry import ToolRegistry  # noqa: E402
from src.selection import (  # noqa: E402
    EngineConfig,
    ToolSelectionEngine,
)


# ======================================================================
# Helpers
# ======================================================================


class _FakeTool(BaseTool):
    def __init__(
        self,
        name: str = "fake",
        category: ToolCategory = ToolCategory.CUSTOM,
        permission_level: PermissionLevel = PermissionLevel.LOW,
        state: ToolState = ToolState.AVAILABLE,
        meta: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(name=name)
        self.category = category
        self.permission_level = permission_level
        self._state = state
        self._extra = meta or {}

    @property
    def metadata(self) -> dict[str, Any]:  # type: ignore[override]
        base = super().metadata
        base.update(self._extra)
        return base

    async def _execute(
        self,
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {"success": True}


def _make_registry(
    *tools: _FakeTool,
) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


# ======================================================================
# EngineConfig
# ======================================================================


class TestEngineConfig:
    def test_defaults(self) -> None:
        cfg = EngineConfig()
        assert isinstance(cfg.weights, RankingWeights)
        assert len(cfg.filters) == 8
        assert cfg.allow_ties is False
        assert cfg.tie_epsilon == 1e-6


# ======================================================================
# ToolSelectionEngine — basic
# ======================================================================


class TestSelectionEngineBasic:
    def setup_method(self) -> None:
        self.file_tool = _FakeTool(
            "file", category=ToolCategory.FILE
        )
        self.sys_tool = _FakeTool(
            "system", category=ToolCategory.SYSTEM
        )
        self.registry = _make_registry(self.file_tool, self.sys_tool)
        self.engine = ToolSelectionEngine(self.registry)

    def test_select_tool_returns_base_tool(self) -> None:
        tool = self.engine.select_tool(
            capability_requirements=frozenset({"file"}),
        )
        assert isinstance(tool, BaseTool)
        assert tool.name == "file"

    def test_select_tools_returns_list(self) -> None:
        tools = self.engine.select_tools(top_n=2)
        assert isinstance(tools, list)
        assert len(tools) == 2

    def test_rank_tools_returns_ranking_results(self) -> None:
        from src.ranking import RankingResult

        results = self.engine.rank_tools()
        assert all(isinstance(r, RankingResult) for r in results)

    def test_score_tool(self) -> None:
        score = self.engine.score_tool("file")
        assert 0.0 <= score <= 1.0

    def test_score_tool_not_found(self) -> None:
        with pytest.raises(NoToolFoundError):
            self.engine.score_tool("nonexistent")

    def test_config_property(self) -> None:
        assert isinstance(self.engine.config, EngineConfig)

    def test_ranker_property(self) -> None:
        from src.ranking import ToolRanker

        assert isinstance(self.engine.ranker, ToolRanker)


# ======================================================================
# NoToolFoundError
# ======================================================================


class TestSelectionNoToolFound:
    def test_empty_registry(self) -> None:
        engine = ToolSelectionEngine(ToolRegistry())
        with pytest.raises(NoToolFoundError):
            engine.select_tool()

    def test_all_filtered_out(self) -> None:
        high_tool = _FakeTool(
            "high", permission_level=PermissionLevel.HIGH
        )
        reg = _make_registry(high_tool)
        engine = ToolSelectionEngine(reg)
        # AUTOMATIC mode removes HIGH tools
        with pytest.raises(NoToolFoundError):
            engine.select_tool(
                execution_mode=ExecutionMode.AUTOMATIC
            )

    def test_select_tools_empty(self) -> None:
        engine = ToolSelectionEngine(ToolRegistry())
        with pytest.raises(NoToolFoundError):
            engine.select_tools()


# ======================================================================
# AmbiguousSelectionError (ties)
# ======================================================================


class TestSelectionTies:
    def test_tie_raises_by_default(self) -> None:
        a = _FakeTool("a")
        b = _FakeTool("b")
        reg = _make_registry(a, b)
        engine = ToolSelectionEngine(reg)
        with pytest.raises(AmbiguousSelectionError) as exc_info:
            engine.select_tool()
        assert "a" in exc_info.value.candidates
        assert "b" in exc_info.value.candidates

    def test_tie_allowed(self) -> None:
        a = _FakeTool("a")
        b = _FakeTool("b")
        reg = _make_registry(a, b)
        cfg = EngineConfig(allow_ties=True)
        engine = ToolSelectionEngine(reg, config=cfg)
        tool = engine.select_tool()
        assert tool.name in ("a", "b")

    def test_tie_epsilon(self) -> None:
        """Scores within epsilon are considered tied."""
        a = _FakeTool("a", meta={"priority": 1.0})
        b = _FakeTool("b", meta={"priority": 0.999})
        reg = _make_registry(a, b)
        # Default epsilon is 1e-6 — these might not tie depending
        # on the weighted sum.  Use a very large epsilon instead.
        cfg = EngineConfig(allow_ties=False, tie_epsilon=10.0)
        engine = ToolSelectionEngine(reg, config=cfg)
        with pytest.raises(AmbiguousSelectionError):
            engine.select_tool()

    def test_different_scores_no_tie(self) -> None:
        good = _FakeTool("good", meta={"priority": 1.0, "safety_score": 1.0})
        bad = _FakeTool("bad", meta={"priority": 0.0, "safety_score": 0.1})
        reg = _make_registry(good, bad)
        engine = ToolSelectionEngine(reg)
        tool = engine.select_tool()
        assert tool.name == "good"


# ======================================================================
# Filtering integration
# ======================================================================


class TestSelectionFiltering:
    def test_exclude_tools(self) -> None:
        a = _FakeTool("a")
        b = _FakeTool("b")
        reg = _make_registry(a, b)
        engine = ToolSelectionEngine(reg)
        cfg = EngineConfig(allow_ties=True)
        engine2 = ToolSelectionEngine(reg, config=cfg)
        # Both pass, but we exclude "b"
        tool = engine2.select_tool(
            exclude_tools=frozenset({"b"}),
        )
        assert tool.name == "a"

    def test_execution_mode_filters(self) -> None:
        low = _FakeTool("low", permission_level=PermissionLevel.LOW)
        high = _FakeTool(
            "high", permission_level=PermissionLevel.HIGH
        )
        reg = _make_registry(low, high)
        engine = ToolSelectionEngine(reg)
        tool = engine.select_tool(
            execution_mode=ExecutionMode.AUTOMATIC,
        )
        assert tool.name == "low"

    def test_capability_filtering(self) -> None:
        file_t = _FakeTool("file", category=ToolCategory.FILE)
        shell_t = _FakeTool("shell", category=ToolCategory.SHELL)
        reg = _make_registry(file_t, shell_t)
        engine = ToolSelectionEngine(reg)
        tool = engine.select_tool(
            capability_requirements=frozenset({"file"}),
        )
        assert tool.name == "file"

    def test_max_permission(self) -> None:
        low = _FakeTool("low", permission_level=PermissionLevel.LOW)
        high = _FakeTool("high", permission_level=PermissionLevel.HIGH)
        reg = _make_registry(low, high)
        engine = ToolSelectionEngine(reg)
        tool = engine.select_tool(
            max_permission_level=PermissionLevel.LOW,
        )
        assert tool.name == "low"


# ======================================================================
# Ranking integration
# ======================================================================


class TestSelectionRanking:
    def test_preferred_tool_boosted(self) -> None:
        a = _FakeTool("a")
        b = _FakeTool("b")
        reg = _make_registry(a, b)
        engine = ToolSelectionEngine(reg)
        # Without preference, could be either (tie)
        # With preference, "b" should win
        cfg = EngineConfig(allow_ties=True)
        engine2 = ToolSelectionEngine(reg, config=cfg)
        tool = engine2.select_tool(
            preferred_tools=frozenset({"b"}),
        )
        assert tool.name == "b"

    def test_custom_weights(self) -> None:
        file_t = _FakeTool("file", category=ToolCategory.FILE)
        sys_t = _FakeTool("system", category=ToolCategory.SYSTEM)
        reg = _make_registry(file_t, sys_t)
        w = RankingWeights(
            capability_weight=1.0,
            priority_weight=0.0,
            latency_weight=0.0,
            cost_weight=0.0,
            reliability_weight=0.0,
            history_weight=0.0,
            safety_weight=0.0,
            manual_boost_weight=0.0,
        )
        cfg = EngineConfig(weights=w)
        engine = ToolSelectionEngine(reg, config=cfg)
        tool = engine.select_tool(
            capability_requirements=frozenset({"file"}),
        )
        assert tool.name == "file"

    def test_select_tools_top_n(self) -> None:
        tools = [_FakeTool(f"t{i}") for i in range(5)]
        reg = _make_registry(*tools)
        cfg = EngineConfig(allow_ties=True)
        engine = ToolSelectionEngine(reg, config=cfg)
        result = engine.select_tools(top_n=3)
        assert len(result) == 3


# ======================================================================
# Logging / observability
# ======================================================================


class TestSelectionObservability:
    def test_rank_tools_logs_timing(self, caplog: Any) -> None:
        import logging

        tool = _FakeTool("x")
        reg = _make_registry(tool)
        engine = ToolSelectionEngine(reg)
        with caplog.at_level(logging.INFO, logger="src.selection"):
            engine.rank_tools()
        assert any(
            "Selection completed" in r.message for r in caplog.records
        )

    def test_removed_tools_logged(self, caplog: Any) -> None:
        import logging

        high = _FakeTool(
            "high", permission_level=PermissionLevel.HIGH
        )
        reg = _make_registry(high)
        engine = ToolSelectionEngine(reg)
        with caplog.at_level(logging.INFO, logger="src.selection"):
            engine.rank_tools(
                execution_mode=ExecutionMode.AUTOMATIC,
            )
        # The HIGH tool should be removed by ExecutionModeFilter
        assert any(
            "removed" in r.message for r in caplog.records
        )