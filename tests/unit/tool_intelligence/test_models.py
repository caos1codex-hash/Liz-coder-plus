"""Tests for tool_intelligence.models.

Sprint 4.1 — Tool Intelligence Foundation.
"""

from __future__ import annotations

import pytest
from tool_intelligence.models import (
    RankedCandidate,
    RiskLevel,
    SelectionContext,
    ToolCategory,
    ToolHistoryStats,
    ToolMetadata,
    UserIntent,
)

# ----------------------------------------------------------------------
# RiskLevel
# ----------------------------------------------------------------------


class TestRiskLevel:
    def test_values(self) -> None:
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"
        assert RiskLevel.CRITICAL.value == "critical"

    def test_numeric_ordering(self) -> None:
        assert RiskLevel.LOW.numeric() < RiskLevel.MEDIUM.numeric()
        assert RiskLevel.MEDIUM.numeric() < RiskLevel.HIGH.numeric()
        assert RiskLevel.HIGH.numeric() < RiskLevel.CRITICAL.numeric()

    def test_is_str_enum(self) -> None:
        assert isinstance(RiskLevel.LOW, str)
        assert RiskLevel.LOW == "low"


# ----------------------------------------------------------------------
# ToolCategory
# ----------------------------------------------------------------------


class TestToolCategory:
    def test_values(self) -> None:
        assert ToolCategory.SHELL.value == "shell"
        assert ToolCategory.FILE.value == "file"
        assert ToolCategory.WEB.value == "web"
        assert ToolCategory.LLM.value == "llm"

    def test_is_str_enum(self) -> None:
        assert isinstance(ToolCategory.FILE, str)


# ----------------------------------------------------------------------
# ToolMetadata
# ----------------------------------------------------------------------


class TestToolMetadataConstruction:
    def test_minimal_construction(self) -> None:
        meta = ToolMetadata(id="t1", name="t1")
        assert meta.id == "t1"
        assert meta.name == "t1"
        assert meta.category == ToolCategory.CUSTOM
        assert meta.risk_level == RiskLevel.LOW
        assert meta.estimated_cost == 0.0
        assert meta.reliability == 1.0
        assert meta.supported_platforms == ["linux", "macos", "windows"]

    def test_empty_id_raises(self) -> None:
        with pytest.raises(ValueError, match="id cannot be empty"):
            ToolMetadata(id="", name="t1")

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="name cannot be empty"):
            ToolMetadata(id="t1", name="")

    def test_invalid_cost_raises(self) -> None:
        with pytest.raises(ValueError, match="estimated_cost"):
            ToolMetadata(id="t1", name="t1", estimated_cost=2.0)

    def test_negative_cost_raises(self) -> None:
        with pytest.raises(ValueError, match="estimated_cost"):
            ToolMetadata(id="t1", name="t1", estimated_cost=-0.1)

    def test_invalid_reliability_raises(self) -> None:
        with pytest.raises(ValueError, match="reliability"):
            ToolMetadata(id="t1", name="t1", reliability=1.5)

    def test_negative_latency_raises(self) -> None:
        with pytest.raises(ValueError, match="estimated_latency"):
            ToolMetadata(id="t1", name="t1", estimated_latency=-1.0)

    def test_invalid_category_type_raises(self) -> None:
        with pytest.raises(TypeError, match="category must be a ToolCategory"):
            ToolMetadata(id="t1", name="t1", category="file")  # type: ignore[arg-type]

    def test_invalid_risk_level_type_raises(self) -> None:
        with pytest.raises(TypeError, match="risk_level must be a RiskLevel"):
            ToolMetadata(id="t1", name="t1", risk_level="low")  # type: ignore[arg-type]


class TestToolMetadataCreate:
    def test_create_auto_generates_id(self) -> None:
        meta = ToolMetadata.create("mytool")
        assert meta.name == "mytool"
        assert meta.id.startswith("tool_")
        assert len(meta.id) > 5

    def test_create_accepts_str_category(self) -> None:
        meta = ToolMetadata.create("mytool", category="file")
        assert meta.category == ToolCategory.FILE

    def test_create_accepts_str_risk_level(self) -> None:
        meta = ToolMetadata.create("mytool", risk_level="high")
        assert meta.risk_level == RiskLevel.HIGH

    def test_create_accepts_capabilities(self) -> None:
        meta = ToolMetadata.create(
            "mytool", capabilities=["filesystem.read"]
        )
        assert meta.capabilities == ["filesystem.read"]

    def test_create_passes_through_extra_kwargs(self) -> None:
        meta = ToolMetadata.create(
            "mytool",
            estimated_cost=0.5,
            estimated_latency=100.0,
            tags=["x", "y"],
        )
        assert meta.estimated_cost == 0.5
        assert meta.estimated_latency == 100.0
        assert meta.tags == ["x", "y"]

    def test_create_invalid_str_category_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolMetadata.create("mytool", category="not_a_category")

    def test_create_invalid_str_risk_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolMetadata.create("mytool", risk_level="not_a_risk")


class TestToolMetadataQueries:
    def test_has_capability_exact(self) -> None:
        meta = ToolMetadata.create("t", capabilities=["filesystem.read"])
        assert meta.has_capability("filesystem.read")

    def test_has_capability_parent_matches_child(self) -> None:
        meta = ToolMetadata.create("t", capabilities=["filesystem.read"])
        assert meta.has_capability("filesystem")

    def test_has_capability_child_matches_parent(self) -> None:
        meta = ToolMetadata.create("t", capabilities=["filesystem"])
        assert meta.has_capability("filesystem.read")

    def test_has_capability_no_match(self) -> None:
        meta = ToolMetadata.create("t", capabilities=["filesystem.read"])
        assert not meta.has_capability("web.search")

    def test_has_capability_empty_list(self) -> None:
        meta = ToolMetadata.create("t")
        assert not meta.has_capability("anything")

    def test_supports_platform(self) -> None:
        meta = ToolMetadata.create("t", supported_platforms=["linux", "macos"])
        assert meta.supports_platform("linux")
        assert meta.supports_platform("macos")
        assert not meta.supports_platform("windows")

    def test_supports_platform_case_insensitive(self) -> None:
        meta = ToolMetadata.create("t", supported_platforms=["Linux"])
        assert meta.supports_platform("LINUX")

    def test_supports_platform_empty_means_all(self) -> None:
        meta = ToolMetadata.create("t", supported_platforms=[])
        assert meta.supports_platform("linux")
        assert meta.supports_platform("anything")

    def test_matches_keyword_in_name(self) -> None:
        meta = ToolMetadata.create("file_reader")
        assert meta.matches_keyword("file")

    def test_matches_keyword_in_description(self) -> None:
        meta = ToolMetadata.create("t", description="reads files from disk")
        assert meta.matches_keyword("reads")

    def test_matches_keyword_in_aliases(self) -> None:
        meta = ToolMetadata.create("t", aliases=["cat"])
        assert meta.matches_keyword("cat")

    def test_matches_keyword_in_tags(self) -> None:
        meta = ToolMetadata.create("t", tags=["io"])
        assert meta.matches_keyword("io")

    def test_matches_keyword_in_keywords(self) -> None:
        meta = ToolMetadata.create("t", keywords=["disk"])
        assert meta.matches_keyword("disk")

    def test_matches_keyword_no_match(self) -> None:
        meta = ToolMetadata.create("t", description="foo bar")
        assert not meta.matches_keyword("baz")


class TestToolMetadataSerialization:
    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        meta = ToolMetadata(
            id="t1",
            name="reader",
            description="reads files",
            category=ToolCategory.FILE,
            capabilities=["filesystem.read"],
            risk_level=RiskLevel.LOW,
            estimated_cost=0.1,
            estimated_latency=20.0,
            aliases=["read_file"],
            tags=["io"],
            keywords=["read"],
            permissions=["fs:read"],
            dependencies=["bash"],
            required_model="gpt-4",
            plugins=["x"],
            reliability=0.95,
            metadata={"custom": "value"},
        )
        d = meta.to_dict()
        restored = ToolMetadata.from_dict(d)
        assert restored.id == meta.id
        assert restored.name == meta.name
        assert restored.category == meta.category
        assert restored.risk_level == meta.risk_level
        assert restored.capabilities == meta.capabilities
        assert restored.estimated_cost == meta.estimated_cost
        assert restored.estimated_latency == meta.estimated_latency
        assert restored.aliases == meta.aliases
        assert restored.tags == meta.tags
        assert restored.keywords == meta.keywords
        assert restored.permissions == meta.permissions
        assert restored.dependencies == meta.dependencies
        assert restored.required_model == meta.required_model
        assert restored.plugins == meta.plugins
        assert restored.reliability == meta.reliability
        assert restored.metadata == meta.metadata

    def test_from_dict_with_string_enums(self) -> None:
        d = {
            "id": "t1",
            "name": "t1",
            "category": "file",
            "risk_level": "high",
        }
        meta = ToolMetadata.from_dict(d)
        assert meta.category == ToolCategory.FILE
        assert meta.risk_level == RiskLevel.HIGH

    def test_from_dict_defaults(self) -> None:
        d = {"id": "t1", "name": "t1"}
        meta = ToolMetadata.from_dict(d)
        assert meta.category == ToolCategory.CUSTOM
        assert meta.risk_level == RiskLevel.LOW
        assert meta.estimated_cost == 0.0
        assert meta.reliability == 1.0


class TestToolMetadataFromBaseTool:
    def test_from_base_tool_extracts_fields(self) -> None:
        class FakeTool:
            name = "stub"
            description = "a stub tool"
            version = "1.2.3"
            tool_id = "abc123"
            category = type("C", (), {"value": "file"})()
            permission_level = type("P", (), {"value": "medium"})()
            capabilities = ["filesystem.read"]
            metadata = {
                "parameters_schema": {"required": ["path"]},
            }

        meta = ToolMetadata.from_base_tool(FakeTool())
        assert meta.id == "abc123"
        assert meta.name == "stub"
        assert meta.description == "a stub tool"
        assert meta.version == "1.2.3"
        assert meta.category == ToolCategory.FILE
        assert meta.risk_level == RiskLevel.MEDIUM
        assert meta.capabilities == ["filesystem.read"]
        assert meta.input_schema == {"required": ["path"]}

    def test_from_base_tool_unknown_category_falls_back(self) -> None:
        class FakeTool:
            name = "stub"
            description = ""
            category = type("C", (), {"value": "unknown"})()
            permission_level = type("P", (), {"value": "low"})()

        meta = ToolMetadata.from_base_tool(FakeTool())
        assert meta.category == ToolCategory.CUSTOM

    def test_from_base_tool_missing_tool_id_generates_one(self) -> None:
        class FakeTool:
            name = "stub"
            description = ""
            category = type("C", (), {"value": "file"})()
            permission_level = type("P", (), {"value": "low"})()

        meta = ToolMetadata.from_base_tool(FakeTool())
        assert meta.id.startswith("tool_")

    def test_from_base_tool_no_capabilities_uses_category(self) -> None:
        class FakeTool:
            name = "stub"
            description = ""
            category = type("C", (), {"value": "shell"})()
            permission_level = type("P", (), {"value": "high"})()

        meta = ToolMetadata.from_base_tool(FakeTool())
        assert meta.capabilities == ["shell"]

    def test_from_base_tool_unknown_risk_level_falls_back_to_low(self) -> None:
        class FakeTool:
            name = "stub"
            description = ""
            category = type("C", (), {"value": "file"})()
            permission_level = type("P", (), {"value": "unknown_risk"})()

        meta = ToolMetadata.from_base_tool(FakeTool())
        assert meta.risk_level == RiskLevel.LOW


# ----------------------------------------------------------------------
# UserIntent
# ----------------------------------------------------------------------


class TestUserIntent:
    def test_defaults(self) -> None:
        intent = UserIntent()
        assert intent.message == ""
        assert intent.capabilities == []
        assert intent.keywords == []
        assert intent.preferred_category is None
        assert intent.max_risk_level == RiskLevel.HIGH
        assert intent.language == "en"

    def test_to_dict(self) -> None:
        intent = UserIntent(
            message="read file",
            capabilities=["filesystem.read"],
            keywords=["read"],
            preferred_category=ToolCategory.FILE,
            max_risk_level=RiskLevel.LOW,
            language="es",
        )
        d = intent.to_dict()
        assert d["message"] == "read file"
        assert d["capabilities"] == ["filesystem.read"]
        assert d["preferred_category"] == "file"
        assert d["max_risk_level"] == "low"
        assert d["language"] == "es"


# ----------------------------------------------------------------------
# SelectionContext
# ----------------------------------------------------------------------


class TestSelectionContext:
    def test_defaults(self) -> None:
        ctx = SelectionContext()
        assert ctx.platform == "linux"
        assert ctx.available_models == []
        assert ctx.granted_permissions == []
        assert ctx.loaded_plugins == []
        assert ctx.max_cost == 1.0
        assert ctx.max_latency == float("inf")
        assert ctx.require_parallel is False
        assert ctx.history == {}

    def test_to_dict(self) -> None:
        ctx = SelectionContext(
            platform="windows",
            available_models=["gpt-4"],
            granted_permissions=["fs:read"],
            loaded_plugins=["pg"],
            max_cost=0.5,
            max_latency=1000.0,
            require_parallel=True,
            history={"t": ToolHistoryStats(executions=10, successes=8)},
        )
        d = ctx.to_dict()
        assert d["platform"] == "windows"
        assert d["available_models"] == ["gpt-4"]
        assert d["granted_permissions"] == ["fs:read"]
        assert d["loaded_plugins"] == ["pg"]
        assert d["max_cost"] == 0.5
        assert d["max_latency"] == 1000.0
        assert d["require_parallel"] is True
        assert "t" in d["history"]
        assert d["history"]["t"]["success_rate"] == 0.8


# ----------------------------------------------------------------------
# ToolHistoryStats
# ----------------------------------------------------------------------


class TestToolHistoryStats:
    def test_success_rate_with_executions(self) -> None:
        stats = ToolHistoryStats(executions=10, successes=8, failures=2)
        assert stats.success_rate == 0.8

    def test_success_rate_zero_executions(self) -> None:
        stats = ToolHistoryStats()
        assert stats.success_rate == 0.0

    def test_to_dict(self) -> None:
        stats = ToolHistoryStats(
            executions=10, successes=8, failures=2,
            avg_latency_ms=50.0, avg_cost=0.1,
        )
        d = stats.to_dict()
        assert d["executions"] == 10
        assert d["successes"] == 8
        assert d["failures"] == 2
        assert d["avg_latency_ms"] == 50.0
        assert d["avg_cost"] == 0.1
        assert d["success_rate"] == 0.8


# ----------------------------------------------------------------------
# RankedCandidate
# ----------------------------------------------------------------------


class TestRankedCandidate:
    def test_default_construction(self) -> None:
        meta = ToolMetadata(id="t1", name="t1")
        cand = RankedCandidate(metadata=meta)
        assert cand.metadata is meta
        assert cand.score == 0.0
        assert cand.breakdown == {}
        assert cand.rank == 0

    def test_to_dict(self) -> None:
        meta = ToolMetadata(id="t1", name="t1")
        cand = RankedCandidate(
            metadata=meta,
            score=0.8765,
            breakdown={"semantic": 0.5},
            rank=2,
        )
        d = cand.to_dict()
        assert d["tool_id"] == "t1"
        assert d["tool_name"] == "t1"
        assert d["score"] == 0.8765  # rounded to 4 decimals
        assert d["rank"] == 2
        assert d["breakdown"] == {"semantic": 0.5}
        assert "metadata" in d
