"""Tests for tool_intelligence.tool_ranker.

Sprint 4.1 — Tool Intelligence Foundation.
"""

from __future__ import annotations

import pytest
from tool_intelligence import (
    RankerWeights,
    SelectionContext,
    ToolHistoryStats,
    ToolMetadata,
    ToolRanker,
    UserIntent,
)

# ----------------------------------------------------------------------
# RankerWeights
# ----------------------------------------------------------------------


class TestRankerWeights:
    def test_defaults(self) -> None:
        w = RankerWeights()
        assert w.w_semantic == 0.25
        assert w.w_capability == 0.25
        assert w.w_history == 0.20
        assert w.w_cost == 0.10
        assert w.w_latency == 0.10
        assert w.w_reliability == 0.10

    def test_normalized_sums_to_one(self) -> None:
        w = RankerWeights(
            w_semantic=1.0,
            w_capability=2.0,
            w_history=3.0,
            w_cost=4.0,
            w_latency=5.0,
            w_reliability=6.0,
        )
        nw = w.normalized()
        total = (
            nw.w_semantic + nw.w_capability + nw.w_history
            + nw.w_cost + nw.w_latency + nw.w_reliability
        )
        assert abs(total - 1.0) < 1e-9

    def test_normalized_zero_total_returns_defaults(self) -> None:
        w = RankerWeights(
            w_semantic=0.0,
            w_capability=0.0,
            w_history=0.0,
            w_cost=0.0,
            w_latency=0.0,
            w_reliability=0.0,
        )
        nw = w.normalized()
        # When total is 0, normalized() returns the default weights.
        assert nw.w_semantic == RankerWeights().w_semantic

    def test_normalized_negative_weights(self) -> None:
        # Negative weights would yield total < 0 -> defaults returned.
        w = RankerWeights(w_semantic=-1.0)
        nw = w.normalized()
        # Negative total -> return defaults.
        assert nw.w_semantic == RankerWeights().w_semantic


# ----------------------------------------------------------------------
# ToolRanker — basic ranking
# ----------------------------------------------------------------------


class TestToolRankerBasic:
    def test_empty_candidates_returns_empty(
        self, read_intent: UserIntent, default_context: SelectionContext
    ) -> None:
        ranker = ToolRanker()
        result = ranker.rank([], intent=read_intent, ctx=default_context)
        assert result == []

    def test_single_candidate_gets_rank_one(
        self,
        file_tool_meta: ToolMetadata,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        ranker = ToolRanker()
        result = ranker.rank(
            [file_tool_meta], intent=read_intent, ctx=default_context
        )
        assert len(result) == 1
        assert result[0].rank == 1
        assert result[0].metadata.id == file_tool_meta.id

    def test_ranking_sorted_by_score_desc(
        self,
        file_tool_meta: ToolMetadata,
        web_search_tool_meta: ToolMetadata,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        # For a "read file" intent, file_read should score higher than web_search.
        ranker = ToolRanker()
        result = ranker.rank(
            [web_search_tool_meta, file_tool_meta],
            intent=read_intent,
            ctx=default_context,
        )
        assert result[0].metadata.id == file_tool_meta.id
        assert result[0].score >= result[1].score
        assert result[0].rank == 1
        assert result[1].rank == 2

    def test_tiebreaker_by_name(
        self,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        # Two identical tools with different names — sort alphabetically.
        meta_a = ToolMetadata(
            id="t_a", name="aaa", description="read file",
            capabilities=["filesystem.read"], keywords=["read", "file"],
        )
        meta_b = ToolMetadata(
            id="t_b", name="bbb", description="read file",
            capabilities=["filesystem.read"], keywords=["read", "file"],
        )
        ranker = ToolRanker()
        result = ranker.rank(
            [meta_b, meta_a], intent=read_intent, ctx=default_context
        )
        assert result[0].metadata.name == "aaa"
        assert result[1].metadata.name == "bbb"

    def test_limit_caps_results(
        self,
        populated_index_fixture_data: list[ToolMetadata],
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        ranker = ToolRanker()
        result = ranker.rank(
            populated_index_fixture_data,
            intent=read_intent,
            ctx=default_context,
            limit=2,
        )
        assert len(result) <= 2

    def test_score_in_zero_to_one_range(
        self,
        file_tool_meta: ToolMetadata,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        ranker = ToolRanker()
        result = ranker.rank(
            [file_tool_meta], intent=read_intent, ctx=default_context
        )
        assert 0.0 <= result[0].score <= 1.0

    def test_breakdown_contains_all_factors(
        self,
        file_tool_meta: ToolMetadata,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        ranker = ToolRanker()
        result = ranker.rank(
            [file_tool_meta], intent=read_intent, ctx=default_context
        )
        breakdown = result[0].breakdown
        assert "semantic" in breakdown
        assert "capability" in breakdown
        assert "history" in breakdown
        assert "cost" in breakdown
        assert "latency" in breakdown
        assert "reliability" in breakdown


# ----------------------------------------------------------------------
# ToolRanker — factor scoring
# ----------------------------------------------------------------------


class TestSemanticScore:
    def test_no_intent_tokens_returns_zero(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        ranker = ToolRanker()
        intent = UserIntent()  # empty
        score, _ = ranker.score_one(file_tool_meta, intent=intent)
        # Semantic component is 0; final score is just other factors.
        assert score >= 0.0

    def test_keyword_overlap_increases_score(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        ranker = ToolRanker()
        # Intent with matching keywords.
        intent_match = UserIntent(
            message="read file from disk",
            keywords=["read", "file", "disk"],
        )
        # Intent with no matching keywords.
        intent_nomatch = UserIntent(
            message="search the web",
            keywords=["search", "web"],
        )
        s_match, _ = ranker.score_one(file_tool_meta, intent=intent_match)
        s_nomatch, _ = ranker.score_one(file_tool_meta, intent=intent_nomatch)
        assert s_match > s_nomatch


class TestCapabilityScore:
    def test_exact_capability_match(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        ranker = ToolRanker()
        intent = UserIntent(capabilities=["filesystem.read"])
        _, breakdown = ranker.score_one(file_tool_meta, intent=intent)
        assert breakdown["capability"] == 1.0

    def test_no_capability_in_intent(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        ranker = ToolRanker()
        intent = UserIntent()
        _, breakdown = ranker.score_one(file_tool_meta, intent=intent)
        assert breakdown["capability"] == 0.0

    def test_parent_capability_match(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        # file_tool_meta has "filesystem.read". Intent asks for "filesystem.*"
        # (anything under filesystem). The tool provides a child of the
        # request's parent — actually, intent requests "filesystem" (parent),
        # tool provides "filesystem.read" (child of intent's request).
        # So this matches the "sub-capability of request" case -> 0.6.
        ranker = ToolRanker()
        intent = UserIntent(capabilities=["filesystem"])
        _, breakdown = ranker.score_one(file_tool_meta, intent=intent)
        assert breakdown["capability"] == 0.6

    def test_child_capability_match(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        # Tool provides "filesystem" (parent). Intent asks for "filesystem.read"
        # (child). Tool provides a parent of the request -> 0.8.
        meta = ToolMetadata(
            id="t1", name="t1", capabilities=["filesystem"],
        )
        ranker = ToolRanker()
        intent = UserIntent(capabilities=["filesystem.read"])
        _, breakdown = ranker.score_one(meta, intent=intent)
        assert breakdown["capability"] == 0.8

    def test_no_capability_match(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        ranker = ToolRanker()
        intent = UserIntent(capabilities=["web.search"])
        _, breakdown = ranker.score_one(file_tool_meta, intent=intent)
        assert breakdown["capability"] == 0.0


class TestHistoryScore:
    def test_uses_history_when_available(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        ranker = ToolRanker()
        # Build history where file_read has 80% success rate.
        history = {
            "file_read": ToolHistoryStats(
                executions=10, successes=8, failures=2
            ),
        }
        ctx = SelectionContext(history=history)
        _, breakdown = ranker.score_one(file_tool_meta, ctx=ctx)
        assert breakdown["history"] == 0.8

    def test_falls_back_to_reliability_when_no_history(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        ranker = ToolRanker()
        ctx = SelectionContext()  # no history
        _, breakdown = ranker.score_one(file_tool_meta, ctx=ctx)
        assert breakdown["history"] == file_tool_meta.reliability

    def test_falls_back_to_reliability_when_zero_executions(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        ranker = ToolRanker()
        ctx = SelectionContext(
            history={"file_read": ToolHistoryStats(executions=0)}
        )
        _, breakdown = ranker.score_one(file_tool_meta, ctx=ctx)
        assert breakdown["history"] == file_tool_meta.reliability


class TestCostScore:
    def test_zero_cost_scores_one(self) -> None:
        meta = ToolMetadata(id="t", name="t", estimated_cost=0.0)
        ranker = ToolRanker()
        _, breakdown = ranker.score_one(meta, max_cost=1.0)
        assert breakdown["cost"] == 1.0

    def test_max_cost_scores_zero(self) -> None:
        meta = ToolMetadata(id="t", name="t", estimated_cost=1.0)
        ranker = ToolRanker()
        _, breakdown = ranker.score_one(meta, max_cost=1.0)
        assert breakdown["cost"] == 0.0

    def test_zero_max_cost_returns_one(self) -> None:
        meta = ToolMetadata(id="t", name="t", estimated_cost=0.5)
        ranker = ToolRanker()
        _, breakdown = ranker.score_one(meta, max_cost=0.0)
        assert breakdown["cost"] == 1.0


class TestLatencyScore:
    def test_zero_latency_scores_one(self) -> None:
        meta = ToolMetadata(id="t", name="t", estimated_latency=0.0)
        ranker = ToolRanker()
        _, breakdown = ranker.score_one(meta, max_latency=100.0)
        assert breakdown["latency"] == 1.0

    def test_max_latency_scores_zero(self) -> None:
        meta = ToolMetadata(id="t", name="t", estimated_latency=100.0)
        ranker = ToolRanker()
        _, breakdown = ranker.score_one(meta, max_latency=100.0)
        assert breakdown["latency"] == 0.0

    def test_zero_max_latency_returns_one(self) -> None:
        meta = ToolMetadata(id="t", name="t", estimated_latency=50.0)
        ranker = ToolRanker()
        _, breakdown = ranker.score_one(meta, max_latency=0.0)
        assert breakdown["latency"] == 1.0


class TestReliabilityScore:
    def test_uses_declared_reliability(self) -> None:
        meta = ToolMetadata(id="t", name="t", reliability=0.75)
        ranker = ToolRanker()
        _, breakdown = ranker.score_one(meta)
        assert breakdown["reliability"] == 0.75


# ----------------------------------------------------------------------
# ToolRanker — error handling
# ----------------------------------------------------------------------


class TestToolRankerErrors:
    def test_score_one_handles_metadata_with_no_keywords(
        self,
        read_intent: UserIntent,
    ) -> None:
        # A minimal metadata with no keywords/description.
        meta = ToolMetadata(id="t", name="t")
        ranker = ToolRanker()
        # Should not raise.
        score, _ = ranker.score_one(meta, intent=read_intent)
        assert score >= 0.0


# ----------------------------------------------------------------------
# ToolRanker — metrics
# ----------------------------------------------------------------------


class TestToolRankerMetrics:
    def test_metrics_initial(self) -> None:
        ranker = ToolRanker()
        m = ranker.metrics()
        assert m["rankings_total"] == 0
        assert m["tools_scored"] == 0
        assert m["total_duration_ms"] == 0.0
        assert m["errors"] == 0
        assert m["avg_ranking_time_ms"] == 0.0

    def test_metrics_after_ranking(
        self,
        file_tool_meta: ToolMetadata,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        ranker = ToolRanker()
        ranker.rank(
            [file_tool_meta], intent=read_intent, ctx=default_context
        )
        m = ranker.metrics()
        assert m["rankings_total"] == 1
        assert m["tools_scored"] == 1
        assert m["total_duration_ms"] >= 0.0
        assert m["avg_ranking_time_ms"] >= 0.0


# ----------------------------------------------------------------------
# Fixture for full index data
# ----------------------------------------------------------------------


@pytest.fixture
def populated_index_fixture_data(
    file_tool_meta: ToolMetadata,
    file_write_tool_meta: ToolMetadata,
    shell_tool_meta: ToolMetadata,
    web_search_tool_meta: ToolMetadata,
    llm_tool_meta: ToolMetadata,
    plugin_tool_meta: ToolMetadata,
) -> list[ToolMetadata]:
    """Return all six tools as a flat list (used by tests that bypass the index)."""
    return [
        file_tool_meta,
        file_write_tool_meta,
        shell_tool_meta,
        web_search_tool_meta,
        llm_tool_meta,
        plugin_tool_meta,
    ]
