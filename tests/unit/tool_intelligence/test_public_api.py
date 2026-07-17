"""Tests for tool_intelligence public API surface.

Sprint 4.1 — Tool Intelligence Foundation.

These tests exercise small public methods that were not directly
covered by the per-module test files (``to_dict`` roundtrips,
property accessors, and the public ``__all__`` exports).
"""

from __future__ import annotations

import pytest
import tool_intelligence
from tool_intelligence import (
    CapabilityIndex,
    CompatibilityChecker,
    RankedCandidate,
    RankerWeights,
    RegistryCache,
    SelectionContext,
    SubTaskSelection,
    ToolMetadata,
    ToolRanker,
    ToolSelector,
    UserIntent,
)

# ----------------------------------------------------------------------
# Package-level surface
# ----------------------------------------------------------------------


class TestPackageExports:
    def test_version_string(self) -> None:
        assert isinstance(tool_intelligence.__version__, str)
        assert tool_intelligence.__version__ == "0.1.0"

    def test_all_exports_resolve(self) -> None:
        for name in tool_intelligence.__all__:
            assert hasattr(tool_intelligence, name), f"missing export: {name}"

    def test_all_exports_listed(self) -> None:
        # Sanity check: a few key names must be in __all__.
        required = {
            "ToolMetadata",
            "UserIntent",
            "SelectionContext",
            "CapabilityIndex",
            "RegistryCache",
            "CompatibilityChecker",
            "ToolRanker",
            "ToolSelector",
            "ToolNotCompatible",
            "CapabilityMissing",
            "ToolSelectionError",
            "RankingError",
        }
        assert required.issubset(set(tool_intelligence.__all__))


# ----------------------------------------------------------------------
# SubTaskSelection.to_dict
# ----------------------------------------------------------------------


class TestSubTaskSelectionDict:
    def test_to_dict_empty(self) -> None:
        sel = SubTaskSelection(subtask_name="step1")
        d = sel.to_dict()
        assert d["subtask_name"] == "step1"
        assert d["candidates"] == []
        assert d["candidate_count"] == 0
        assert d["reason"] == ""

    def test_to_dict_with_candidates(
        self, file_tool_meta: ToolMetadata
    ) -> None:
        cand = RankedCandidate(
            metadata=file_tool_meta, score=0.5, breakdown={"x": 0.1}, rank=1
        )
        sel = SubTaskSelection(
            subtask_name="step1",
            candidates=[cand],
            reason="matched",
        )
        d = sel.to_dict()
        assert d["subtask_name"] == "step1"
        assert d["candidate_count"] == 1
        assert d["reason"] == "matched"
        assert d["candidates"][0]["tool_name"] == file_tool_meta.name


# ----------------------------------------------------------------------
# Selector property accessors
# ----------------------------------------------------------------------


class TestToolSelectorProperties:
    def test_ranker_property(self, populated_index: CapabilityIndex) -> None:
        selector = ToolSelector(populated_index)
        assert isinstance(selector.ranker, ToolRanker)

    def test_checker_property(self, populated_index: CapabilityIndex) -> None:
        selector = ToolSelector(populated_index)
        assert isinstance(selector.checker, CompatibilityChecker)

    def test_custom_ranker_and_checker(self, populated_index: CapabilityIndex) -> None:
        custom_ranker = ToolRanker(weights=RankerWeights(w_semantic=1.0))
        custom_checker = CompatibilityChecker()
        selector = ToolSelector(
            populated_index, ranker=custom_ranker, checker=custom_checker
        )
        assert selector.ranker is custom_ranker
        assert selector.checker is custom_checker


# ----------------------------------------------------------------------
# Ranker weights property
# ----------------------------------------------------------------------


class TestRankerWeightsProperty:
    def test_weights_property(self) -> None:
        ranker = ToolRanker()
        assert isinstance(ranker.weights, RankerWeights)


# ----------------------------------------------------------------------
# Ranker error path
# ----------------------------------------------------------------------


class TestRankerErrorPath:
    def test_ranker_raises_ranking_error_on_internal_exception(
        self,
        file_tool_meta: ToolMetadata,
        read_intent: UserIntent,
        default_context: SelectionContext,
    ) -> None:
        from tool_intelligence import RankingError

        ranker = ToolRanker()

        # Monkey-patch _score_one to force an exception.
        original = ranker._score_one

        def boom(
            meta: ToolMetadata,
            intent: UserIntent,
            ctx: SelectionContext,
            max_cost: float,
            max_latency: float,
        ) -> tuple[float, dict[str, float]]:
            raise RuntimeError("forced failure")

        ranker._score_one = boom  # type: ignore[assignment]
        try:
            with pytest.raises(RankingError, match="Failed to score tool"):
                ranker.rank([file_tool_meta], intent=read_intent, ctx=default_context)
        finally:
            ranker._score_one = original  # type: ignore[assignment]

        # The error counter should have been incremented.
        assert ranker.metrics()["errors"] >= 1


# ----------------------------------------------------------------------
# RegistryCache upsert fallback
# ----------------------------------------------------------------------


class TestRegistryCacheUpsertFallback:
    def test_upsert_falls_back_to_add_when_update_raises(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        # Pre-populate the cache with file_tool_meta.
        cache = RegistryCache(loader=lambda: [file_tool_meta])
        cache.get()

        # Now upsert a NEW metadata with a different id. ``update``
        # raises ToolIntelligenceError because the id is unknown, and
        # the fallback should call ``add`` instead.
        new_meta = ToolMetadata(
            id="different_id",
            name="different_name",
            description="new",
        )
        cache.upsert(new_meta)
        idx = cache.get()
        assert idx.get("different_id") is not None


# ----------------------------------------------------------------------
# ToolMetadata edge cases
# ----------------------------------------------------------------------


class TestToolMetadataEdgeCases:
    def test_from_base_tool_with_no_metadata_attribute(self) -> None:
        # A truly minimal duck-typed tool with no `metadata` attribute.
        class BareTool:
            name = "bare"
            description = ""
            category = type("C", (), {"value": "file"})()
            permission_level = type("P", (), {"value": "low"})()

        meta = ToolMetadata.from_base_tool(BareTool())
        assert meta.name == "bare"
        assert meta.input_schema == {}

    def test_from_base_tool_with_non_dict_metadata(self) -> None:
        class WeirdTool:
            name = "weird"
            description = ""
            category = type("C", (), {"value": "file"})()
            permission_level = type("P", (), {"value": "low"})()
            metadata = "not a dict"  # type: ignore[assignment]

        meta = ToolMetadata.from_base_tool(WeirdTool())
        assert meta.input_schema == {}


# ----------------------------------------------------------------------
# CapabilityIndex edge cases
# ----------------------------------------------------------------------


class TestCapabilityIndexEdgeCases:
    def test_iter_yields_all(self, populated_index: CapabilityIndex) -> None:
        tools = list(iter(populated_index))
        assert len(tools) == 6

    def test_search_with_only_stopwords_returns_empty(
        self, populated_index: CapabilityIndex
    ) -> None:
        # "the" is a stopword.
        results = populated_index.search("the")
        assert results == []

    def test_search_token_substring_in_name(
        self, populated_index: CapabilityIndex
    ) -> None:
        # "file" appears in both file_read and file_write names.
        results = populated_index.search("file")
        names = {m.name for m in results}
        assert "file_read" in names
        assert "file_write" in names


# ----------------------------------------------------------------------
# CompatibilityChecker caching edge cases
# ----------------------------------------------------------------------


class TestCompatibilityCheckerCachingEdgeCases:
    def test_cache_invalidated_on_different_permissions(
        self,
        file_tool_meta: ToolMetadata,
    ) -> None:
        checker = CompatibilityChecker()
        ctx1 = SelectionContext(granted_permissions=["fs:read"])
        ctx2 = SelectionContext(granted_permissions=["other"])
        r1 = checker.check(file_tool_meta, ctx1)
        r2 = checker.check(file_tool_meta, ctx2)
        assert r1 is not r2

    def test_cache_invalidated_on_different_models(
        self,
        llm_tool_meta: ToolMetadata,
    ) -> None:
        checker = CompatibilityChecker()
        ctx1 = SelectionContext(available_models=["gpt-4"])
        ctx2 = SelectionContext(available_models=["gpt-3.5"])
        r1 = checker.check(llm_tool_meta, ctx1)
        r2 = checker.check(llm_tool_meta, ctx2)
        assert r1 is not r2


# ----------------------------------------------------------------------
# ToolSelector — default context (no ctx argument)
# ----------------------------------------------------------------------


class TestToolSelectorDefaultContext:
    def test_select_without_context_uses_default(
        self,
        populated_index: CapabilityIndex,
        read_intent: UserIntent,
    ) -> None:
        selector = ToolSelector(populated_index)
        # Should not raise — default context is created internally.
        result = selector.select(read_intent)
        assert result.duration_ms >= 0.0


# ----------------------------------------------------------------------
# Integration smoke test
# ----------------------------------------------------------------------


class TestIntegrationSmoke:
    def test_full_pipeline(
        self,
        populated_index: CapabilityIndex,
        default_context: SelectionContext,
    ) -> None:
        """End-to-end: index -> selector -> ranked candidate."""
        selector = ToolSelector(populated_index, top_k=3)
        intent = UserIntent(
            message="read the file from disk",
            capabilities=["filesystem.read"],
            keywords=["read", "file"],
        )
        result = selector.select(intent, default_context)
        assert result.best is not None
        assert result.best.metadata.name == "file_read"
        assert result.best.score > 0.0
        # Breakdown should be populated.
        assert "semantic" in result.best.breakdown
        assert "capability" in result.best.breakdown
