"""Tests for tool_intelligence.tool_selector.

Sprint 4.1 — Tool Intelligence Foundation.
"""

from __future__ import annotations

import pytest
from tool_intelligence import (
    CapabilityIndex,
    CapabilityMissing,
    RegistryCache,
    RiskLevel,
    SelectionContext,
    ToolCategory,
    ToolMetadata,
    ToolSelectionError,
    ToolSelector,
    UserIntent,
)

# ----------------------------------------------------------------------
# Selection result types
# ----------------------------------------------------------------------


class TestSelectionResult:
    def test_to_dict(
        self, file_tool_meta: ToolMetadata, read_intent: UserIntent
    ) -> None:
        from tool_intelligence import RankedCandidate, SelectionResult

        result = SelectionResult(
            intent=read_intent,
            candidates=[
                RankedCandidate(
                    metadata=file_tool_meta, score=0.5, rank=1
                )
            ],
            duration_ms=42.0,
            reason="ok",
        )
        d = result.to_dict()
        assert d["intent"]["message"] == read_intent.message
        assert d["candidate_count"] == 1
        assert d["duration_ms"] == 42.0
        assert d["reason"] == "ok"
        assert d["candidates"][0]["tool_name"] == "file_read"

    def test_best_returns_top(
        self, file_tool_meta: ToolMetadata, read_intent: UserIntent
    ) -> None:
        from tool_intelligence import RankedCandidate, SelectionResult

        result = SelectionResult(
            intent=read_intent,
            candidates=[
                RankedCandidate(metadata=file_tool_meta, score=0.5, rank=1),
            ],
        )
        assert result.best is not None
        assert result.best.metadata.id == file_tool_meta.id

    def test_best_returns_none_when_empty(self, read_intent: UserIntent) -> None:
        from tool_intelligence import SelectionResult

        result = SelectionResult(intent=read_intent)
        assert result.best is None


# ----------------------------------------------------------------------
# ToolSelector — basic selection
# ----------------------------------------------------------------------


class TestToolSelectorBasic:
    def test_select_returns_candidates(
        self,
        populated_index: CapabilityIndex,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        result = selector.select(read_intent, default_context)
        assert len(result.candidates) > 0
        assert result.best is not None
        assert result.best.metadata.name == "file_read"

    def test_select_with_no_matching_capability(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        intent = UserIntent(capabilities=["nonexistent.capability"])
        result = selector.select(intent, default_context)
        assert result.candidates == []
        assert "no tools matched" in result.reason

    def test_select_with_no_matching_keyword(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        intent = UserIntent(message="zzznonexistent")
        result = selector.select(intent, default_context)
        assert result.candidates == []

    def test_select_with_empty_index(
        self,
        default_context: SelectionContext,
        read_intent: UserIntent,
    ) -> None:
        selector = ToolSelector(CapabilityIndex())
        result = selector.select(read_intent, default_context)
        assert result.candidates == []

    def test_select_with_top_k_limit(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index, top_k=1)
        # Use an intent that matches multiple tools.
        intent = UserIntent(message="file disk")  # matches file_read, file_write
        result = selector.select(intent, default_context)
        assert len(result.candidates) <= 1

    def test_select_with_min_score(
        self,
        populated_index: CapabilityIndex,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        # Set a high min_score so nothing qualifies.
        selector = ToolSelector(populated_index, min_score=0.99)
        result = selector.select(read_intent, default_context)
        # Most realistic scores will be below 0.99.
        # We just check the filter is applied (length may be 0 or small).
        for cand in result.candidates:
            assert cand.score >= 0.99


# ----------------------------------------------------------------------
# ToolSelector — risk filtering
# ----------------------------------------------------------------------


class TestToolSelectorRiskFilter:
    def test_select_filters_by_max_risk_level(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        # Intent asking for terminal execution, but max risk = LOW.
        selector = ToolSelector(populated_index)
        intent = UserIntent(
            message="execute command",
            capabilities=["terminal.execute"],
            keywords=["execute", "command"],
            max_risk_level=RiskLevel.LOW,
        )
        result = selector.select(intent, default_context)
        # shell_exec is HIGH risk, so it should be filtered out.
        for cand in result.candidates:
            assert cand.metadata.risk_level != RiskLevel.HIGH
        assert "exceeds max risk" in result.reason or len(result.candidates) == 0


# ----------------------------------------------------------------------
# ToolSelector — runtime constraints
# ----------------------------------------------------------------------


class TestToolSelectorRuntimeConstraints:
    def test_select_filters_by_max_cost(
        self,
        populated_index: CapabilityIndex,
        read_intent: UserIntent,
    ) -> None:
        # max_cost = 0 -> filters out everything that costs > 0.
        ctx = SelectionContext(
            platform="linux",
            granted_permissions=["fs:read"],
            max_cost=0.0,
        )
        selector = ToolSelector(populated_index)
        result = selector.select(read_intent, ctx)
        # file_read.estimated_cost = 0.05 > 0.0, so excluded.
        assert result.candidates == []
        assert "runtime constraints" in result.reason

    def test_select_filters_by_max_latency(
        self,
        populated_index: CapabilityIndex,
        read_intent: UserIntent,
    ) -> None:
        # max_latency = 1.0 ms -> filters out file_read (latency 20).
        ctx = SelectionContext(
            platform="linux",
            granted_permissions=["fs:read"],
            max_latency=1.0,
        )
        selector = ToolSelector(populated_index)
        result = selector.select(read_intent, ctx)
        assert result.candidates == []

    def test_select_filters_by_require_parallel(
        self,
        populated_index: CapabilityIndex,
        read_intent: UserIntent,
    ) -> None:
        # file_write.requires_confirmation=True but supports_parallel=True
        # by default. Set require_parallel=True and use a tool with supports_parallel=False.
        meta_no_parallel = ToolMetadata(
            id="serial", name="serial",
            capabilities=["filesystem.read"],
            supports_parallel=False,
            permissions=["fs:read"],
        )
        idx = CapabilityIndex()
        idx.add(meta_no_parallel)
        ctx = SelectionContext(
            platform="linux",
            granted_permissions=["fs:read"],
            require_parallel=True,
        )
        selector = ToolSelector(idx)
        result = selector.select(read_intent, ctx)
        assert result.candidates == []
        assert "runtime constraints" in result.reason


# ----------------------------------------------------------------------
# ToolSelector — compatibility filtering
# ----------------------------------------------------------------------


class TestToolSelectorCompatibility:
    def test_select_filters_incompatible_tools(
        self,
        populated_index: CapabilityIndex,
        read_intent: UserIntent,
    ) -> None:
        # llm_tool_meta requires gpt-4 model. Make it match the intent.
        intent = UserIntent(
            message="summarize text with llm",
            capabilities=["text.summarize"],
            keywords=["summarize", "text"],
        )
        # Context without gpt-4 in available_models.
        ctx = SelectionContext(
            platform="linux",
            available_models=["gpt-3.5"],
            granted_permissions=["llm:call"],
        )
        selector = ToolSelector(populated_index)
        result = selector.select(intent, ctx)
        # llm_summarize should be rejected.
        rejected_names = [m.name for m, _ in result.rejected]
        assert "llm_summarize" in rejected_names
        assert result.candidates == [] or all(
            c.metadata.name != "llm_summarize" for c in result.candidates
        )

    def test_select_all_compatible(
        self,
        populated_index: CapabilityIndex,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        result = selector.select(read_intent, default_context)
        # No rejections in the default context.
        assert result.rejected == []


# ----------------------------------------------------------------------
# ToolSelector — plan-based selection
# ----------------------------------------------------------------------


class TestToolSelectorPlan:
    def test_select_for_plan_uses_suggested_tools(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        plan = [
            {
                "name": "read",
                "description": "read the file",
                "suggested_tools": ["file_read"],
            },
            {
                "name": "respond",
                "description": "format response",
                "suggested_tools": [],
            },
        ]
        results = selector.select_for_plan(plan, default_context)
        assert len(results) == 2
        assert results[0].subtask_name == "read"
        assert len(results[0].candidates) > 0
        assert results[0].candidates[0].metadata.name == "file_read"

    def test_select_for_plan_falls_back_to_select(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        plan = [
            {
                "name": "research",
                "description": "search the web for information",
                "suggested_tools": [],  # no suggestions -> fall back
            }
        ]
        results = selector.select_for_plan(plan, default_context)
        assert len(results) == 1
        # web_search should be selected based on keyword match.
        assert any(
            c.metadata.name == "web_search"
            for c in results[0].candidates
        ) or "no tools matched" in results[0].reason

    def test_select_for_plan_empty_plan(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        results = selector.select_for_plan([], default_context)
        assert results == []

    def test_select_for_plan_suggested_tool_not_in_index(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        plan = [
            {
                "name": "step1",
                "description": "do something unrelated",
                "suggested_tools": ["nonexistent_tool"],
            }
        ]
        results = selector.select_for_plan(plan, default_context)
        # Should fall back to a full select() since no direct matches.
        assert len(results) == 1


# ----------------------------------------------------------------------
# ToolSelector — required capability
# ----------------------------------------------------------------------


class TestToolSelectorRequiredCapability:
    def test_select_required_capability_succeeds(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        candidate = selector.select_required_capability(
            "filesystem.read", default_context
        )
        assert candidate.metadata.name == "file_read"

    def test_select_required_capability_raises_on_missing(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        with pytest.raises(CapabilityMissing) as exc_info:
            selector.select_required_capability(
                "nonexistent.capability", default_context
            )
        assert exc_info.value.required_capability == "nonexistent.capability"


# ----------------------------------------------------------------------
# ToolSelector — with RegistryCache
# ----------------------------------------------------------------------


class TestToolSelectorWithCache:
    def test_selector_works_with_cache(
        self,
        file_tool_meta: ToolMetadata,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        selector = ToolSelector(cache)
        result = selector.select(read_intent, default_context)
        assert len(result.candidates) > 0
        assert result.best is not None
        assert result.best.metadata.id == file_tool_meta.id


# ----------------------------------------------------------------------
# ToolSelector — error handling
# ----------------------------------------------------------------------


class TestToolSelectorErrors:
    def test_index_resolution_failure_raises_selection_error(
        self,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        def bad_loader() -> list[ToolMetadata]:
            raise RuntimeError("loader broken")

        cache = RegistryCache(loader=bad_loader)
        selector = ToolSelector(cache)
        with pytest.raises(ToolSelectionError, match="Failed to resolve index"):
            selector.select(read_intent, default_context)


# ----------------------------------------------------------------------
# ToolSelector — metrics
# ----------------------------------------------------------------------


class TestToolSelectorMetrics:
    def test_metrics_initial(self, populated_index: CapabilityIndex) -> None:
        selector = ToolSelector(populated_index)
        m = selector.metrics()
        assert m["selections_total"] == 0
        assert m["candidates_returned"] == 0
        assert m["no_candidate_count"] == 0
        assert m["avg_selection_time_ms"] == 0.0

    def test_metrics_after_selections(
        self,
        populated_index: CapabilityIndex,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        selector.select(read_intent, default_context)
        selector.select(read_intent, default_context)
        m = selector.metrics()
        assert m["selections_total"] == 2
        assert m["candidates_returned"] >= 2

    def test_metrics_tracks_no_candidate(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        intent = UserIntent(capabilities=["nonexistent.cap"])
        selector.select(intent, default_context)
        m = selector.metrics()
        assert m["no_candidate_count"] == 1


# ----------------------------------------------------------------------
# ToolSelector — preferred category
# ----------------------------------------------------------------------


class TestToolSelectorPreferredCategory:
    def test_preferred_category_restricts(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        # Intent with broad message, but preferred_category = FILE.
        intent = UserIntent(
            message="file",  # matches file_read, file_write
            preferred_category=ToolCategory.FILE,
        )
        result = selector.select(intent, default_context)
        for cand in result.candidates:
            assert cand.metadata.category == ToolCategory.FILE

    def test_preferred_category_only_no_other_matches(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        """preferred_category with no capabilities/keywords should still return tools."""
        selector = ToolSelector(populated_index)
        intent = UserIntent(
            preferred_category=ToolCategory.SHELL,
        )
        result = selector.select(intent, default_context)
        assert len(result.candidates) > 0
        for cand in result.candidates:
            assert cand.metadata.category == ToolCategory.SHELL


# ----------------------------------------------------------------------
# ToolSelector — keyword-only selection
# ----------------------------------------------------------------------


class TestToolSelectorKeywordOnly:
    def test_keyword_only_selection(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        selector = ToolSelector(populated_index)
        intent = UserIntent(keywords=["shell", "bash"])
        result = selector.select(intent, default_context)
        # shell_exec should be in the results.
        names = {c.metadata.name for c in result.candidates}
        assert "shell_exec" in names
