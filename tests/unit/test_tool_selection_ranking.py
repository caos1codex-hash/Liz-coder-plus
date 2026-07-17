"""Tests for the Tool Selection Engine — ranking module.

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
    PermissionLevel,
    ToolCategory,
    ToolState,
)
from src.exceptions import RankingError  # noqa: E402
from src.filters import SelectionContext  # noqa: E402
from src.ranking import (  # noqa: E402
    RankingResult,
    RankingWeights,
    ToolRanker,
)


# ======================================================================
# Helpers
# ======================================================================


class _FakeTool:
    """Lightweight stand-in for BaseTool (duck-typed for ranking)."""

    def __init__(
        self,
        name: str = "fake",
        category: ToolCategory = ToolCategory.CUSTOM,
        permission_level: PermissionLevel = PermissionLevel.LOW,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.category = category
        self.permission_level = permission_level
        self._meta: dict[str, Any] = meta or {}
        self.state = ToolState.AVAILABLE

    @property
    def metadata(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "executions": 0,
            "failures": 0,
            "total_time_ms": 0,
            "state": self.state.value,
            "category": self.category.value,
            "permission_level": self.permission_level.value,
        }
        base.update(self._meta)
        return base


def _ctx(**overrides: Any) -> SelectionContext:
    defaults: dict[str, Any] = {
        "task_name": "",
        "task_description": "",
        "capability_requirements": frozenset(),
        "agent_requirements": frozenset(),
        "max_permission_level": PermissionLevel.HIGH,
        "max_cost": float("inf"),
        "max_latency_ms": float("inf"),
        "min_safety_score": 0.0,
        "execution_mode": "automatic",
        "include_tools": frozenset(),
        "exclude_tools": frozenset(),
        "preferred_categories": frozenset(),
        "preferred_tools": frozenset(),
    }
    defaults.update(overrides)
    return SelectionContext(**defaults)


# ======================================================================
# RankingWeights
# ======================================================================


class TestRankingWeights:
    def test_default_weights_positive(self) -> None:
        w = RankingWeights()
        for v in w.as_tuple:
            assert v >= 0

    def test_total_is_one_after_normalise(self) -> None:
        w = RankingWeights()
        n = w.normalised()
        assert abs(n.total - 1.0) < 1e-9

    def test_custom_weights_normalise(self) -> None:
        w = RankingWeights(
            capability_weight=1.0,
            priority_weight=1.0,
            latency_weight=0.0,
            cost_weight=0.0,
            reliability_weight=0.0,
            history_weight=0.0,
            safety_weight=0.0,
            manual_boost_weight=0.0,
        )
        n = w.normalised()
        assert abs(n.capability_weight - 0.5) < 1e-9
        assert abs(n.priority_weight - 0.5) < 1e-9
        # Others should be 0
        assert n.latency_weight == 0.0

    def test_all_zero_falls_back_to_uniform(self) -> None:
        w = RankingWeights(
            capability_weight=0,
            priority_weight=0,
            latency_weight=0,
            cost_weight=0,
            reliability_weight=0,
            history_weight=0,
            safety_weight=0,
            manual_boost_weight=0,
        )
        n = w.normalised()
        assert abs(n.total - 1.0) < 1e-9
        # All 8 factors should be 0.125
        for v in n.as_tuple:
            assert abs(v - 0.125) < 1e-9

    def test_negative_weight_raises(self) -> None:
        with pytest.raises(ValueError):
            RankingWeights(capability_weight=-0.1)

    def test_frozen(self) -> None:
        w = RankingWeights()
        with pytest.raises(AttributeError):
            w.capability_weight = 0.99  # type: ignore[misc]


# ======================================================================
# RankingResult
# ======================================================================


class TestRankingResult:
    def test_comparison(self) -> None:
        tool = _FakeTool("a")
        high = RankingResult(tool, 0.9, "good")
        low = RankingResult(tool, 0.3, "bad")
        assert high > low
        assert low < high
        assert high >= low
        assert low <= high
        assert high == RankingResult(tool, 0.9, "good")

    def test_comparison_with_non_ranking(self) -> None:
        r = RankingResult(_FakeTool("a"), 0.5, "")
        assert r.__gt__(42) is NotImplemented  # type: ignore[operator]

    def test_details_default(self) -> None:
        r = RankingResult(_FakeTool("a"), 0.5, "ok")
        assert r.details == {}

    def test_details_custom(self) -> None:
        r = RankingResult(
            _FakeTool("a"), 0.5, "ok", details={"x": 0.3}
        )
        assert r.details == {"x": 0.3}


# ======================================================================
# ToolRanker — basic scoring
# ======================================================================


class TestToolRankerBasic:
    def setup_method(self) -> None:
        self.ranker = ToolRanker()

    def test_rank_empty_list(self) -> None:
        assert self.ranker.rank([], _ctx()) == []

    def test_single_tool_gets_score(self) -> None:
        tool = _FakeTool("only")
        results = self.ranker.rank([tool], _ctx())
        assert len(results) == 1
        assert 0.0 <= results[0].score <= 1.0

    def test_multiple_tools_sorted(self) -> None:
        good = _FakeTool(
            "good",
            meta={"priority": 1.0, "safety_score": 1.0},
        )
        bad = _FakeTool(
            "bad",
            category=ToolCategory.SHELL,
            permission_level=PermissionLevel.HIGH,
            meta={"priority": 0.0, "safety_score": 0.1},
        )
        results = self.ranker.rank([bad, good], _ctx())
        assert results[0].tool.name == "good"
        assert results[0].score > results[1].score

    def test_score_tool_returns_ranking_result(self) -> None:
        tool = _FakeTool("x")
        result = self.ranker.score_tool(tool, _ctx())
        assert isinstance(result, RankingResult)
        assert 0.0 <= result.score <= 1.0

    def test_reason_contains_tool_name(self) -> None:
        tool = _FakeTool("my_tool")
        result = self.ranker.score_tool(tool, _ctx())
        assert "my_tool" in result.reason


# ======================================================================
# ToolRanker — individual factors
# ======================================================================


class TestFactorCapabilityMatch:
    def test_no_requirements_perfect_score(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("x")
        r = ranker.score_tool(tool, _ctx())
        assert r.details["capability_match"] == 1.0

    def test_exact_match(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("x", category=ToolCategory.FILE)
        r = ranker.score_tool(
            tool,
            _ctx(capability_requirements=frozenset({"file"})),
        )
        assert r.details["capability_match"] == 1.0

    def test_partial_match(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("x", category=ToolCategory.FILE)
        r = ranker.score_tool(
            tool,
            _ctx(capability_requirements=frozenset({"file", "web"})),
        )
        # intersection=1, union=2 → 0.5
        assert r.details["capability_match"] == 0.5

    def test_no_match(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("x", category=ToolCategory.FILE)
        r = ranker.score_tool(
            tool,
            _ctx(capability_requirements=frozenset({"web"})),
        )
        assert r.details["capability_match"] == 0.0


class TestFactorReliability:
    def test_no_executions_neutral(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("x")
        r = ranker.score_tool(tool, _ctx())
        assert r.details["reliability"] == 0.5

    def test_all_success(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("x", meta={"executions": 10, "failures": 0})
        r = ranker.score_tool(tool, _ctx())
        assert r.details["reliability"] == 1.0

    def test_mixed(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("x", meta={"executions": 7, "failures": 3})
        r = ranker.score_tool(tool, _ctx())
        assert abs(r.details["reliability"] - 0.7) < 1e-9


class TestFactorHistory:
    def test_no_executions(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("x")
        r = ranker.score_tool(tool, _ctx())
        assert r.details["history"] == 0.3

    def test_many_executions(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("x", meta={"executions": 10000})
        r = ranker.score_tool(tool, _ctx())
        # log10(10001) / 3 ≈ 1.33
        assert r.details["history"] > 0.9


class TestFactorSafety:
    def test_explicit_score(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool(
            "x",
            permission_level=PermissionLevel.HIGH,
            meta={"safety_score": 0.95},
        )
        r = ranker.score_tool(tool, _ctx())
        assert r.details["safety"] == 0.95

    def test_default_by_level(self) -> None:
        ranker = ToolRanker()
        low = _FakeTool("low", permission_level=PermissionLevel.LOW)
        r = ranker.score_tool(low, _ctx())
        assert r.details["safety"] == 1.0

        high = _FakeTool(
            "high", permission_level=PermissionLevel.HIGH
        )
        r = ranker.score_tool(high, _ctx())
        assert r.details["safety"] == 0.4


class TestFactorManualBoost:
    def test_no_boost(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("x")
        r = ranker.score_tool(tool, _ctx())
        assert r.details["manual_boost"] == 0.0

    def test_metadata_boost(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("x", meta={"manual_boost": 0.8})
        r = ranker.score_tool(tool, _ctx())
        assert r.details["manual_boost"] == 0.8

    def test_preferred_tool_boost(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("fav")
        r = ranker.score_tool(
            tool,
            _ctx(preferred_tools=frozenset({"fav"})),
        )
        assert r.details["manual_boost"] == 0.5

    def test_preferred_overrides_lower_metadata(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool(
            "fav", meta={"manual_boost": 0.2}
        )
        r = ranker.score_tool(
            tool,
            _ctx(preferred_tools=frozenset({"fav"})),
        )
        assert r.details["manual_boost"] == 0.5

    def test_boost_capped_at_one(self) -> None:
        ranker = ToolRanker()
        tool = _FakeTool("x", meta={"manual_boost": 2.0})
        r = ranker.score_tool(tool, _ctx())
        assert r.details["manual_boost"] == 1.0


# ======================================================================
# ToolRanker — edge cases
# ======================================================================


class TestToolRankerEdgeCases:
    def test_all_tools_identical_score(self) -> None:
        ranker = ToolRanker()
        t1 = _FakeTool("a")
        t2 = _FakeTool("b")
        results = ranker.rank([t1, t2], _ctx())
        assert len(results) == 2
        assert abs(results[0].score - results[1].score) < 1e-9

    def test_custom_weights(self) -> None:
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
        ranker = ToolRanker(weights=w)
        file_tool = _FakeTool(
            "file", category=ToolCategory.FILE
        )
        web_tool = _FakeTool(
            "web", category=ToolCategory.WEB
        )
        results = ranker.rank(
            [file_tool, web_tool],
            _ctx(capability_requirements=frozenset({"file"})),
        )
        assert results[0].tool.name == "file"

    def test_max_cost_normalisation(self) -> None:
        ranker = ToolRanker(max_cost=100.0)
        cheap = _FakeTool("cheap", meta={"cost": 10})
        expensive = _FakeTool("expensive", meta={"cost": 90})
        results = ranker.rank([cheap, expensive], _ctx())
        assert results[0].tool.name == "cheap"

    def test_max_latency_normalisation(self) -> None:
        ranker = ToolRanker(max_latency_ms=1000.0)
        fast = _FakeTool(
            "fast",
            meta={"avg_latency_ms": 50, "executions": 10},
        )
        slow = _FakeTool(
            "slow",
            meta={"avg_latency_ms": 900, "executions": 10},
        )
        results = ranker.rank([slow, fast], _ctx())
        assert results[0].tool.name == "fast"

    def test_failed_score_skipped_gracefully(self) -> None:
        """A tool that causes a ranking error is skipped, not raised."""

        class BrokenTool:
            name = "broken"
            category = ToolCategory.CUSTOM
            permission_level = PermissionLevel.LOW
            state = ToolState.AVAILABLE

            @property
            def metadata(self) -> dict[str, Any]:
                raise RuntimeError("corrupt metadata")

        ranker = ToolRanker()
        results = ranker.rank([BrokenTool()], _ctx())  # type: ignore[arg-type]
        assert results == []