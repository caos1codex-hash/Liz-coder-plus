"""Tests for the Tool Selection Engine — filters module.

Sprint 4.2
"""

from __future__ import annotations

import sys

sys.path.insert(0, "packages/tools")
from tests.conftest import clear_src_cache  # noqa: E402
clear_src_cache()

from dataclasses import replace  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402

from src.base import (  # noqa: E402
    BaseTool,
    PermissionLevel,
    ToolCategory,
    ToolState,
)
from src.exceptions import FilterError  # noqa: E402
from src.filters import (  # noqa: E402
    AvailabilityFilter,
    BaseFilter,
    CapabilityFilter,
    CostFilter,
    DEFAULT_FILTER_CHAIN,
    ExecutionMode,
    ExecutionModeFilter,
    LatencyFilter,
    PermissionFilter,
    SafetyFilter,
    SelectionContext,
    UserPreferenceFilter,
)


# ======================================================================
# Helpers — concrete tool for testing
# ======================================================================


class _FakeTool(BaseTool):
    """Minimal concrete tool for filter tests."""

    def __init__(
        self,
        name: str = "fake",
        category: ToolCategory = ToolCategory.CUSTOM,
        permission_level: PermissionLevel = PermissionLevel.LOW,
        state: ToolState = ToolState.AVAILABLE,
        metadata_extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(name=name)
        self.category = category
        self.permission_level = permission_level
        self._state = state
        if metadata_extra:
            # Merge into the parent's metadata via tool.metadata
            self._meta_extra = metadata_extra
        else:
            self._meta_extra = {}

    @property
    def metadata(self) -> dict[str, Any]:  # type: ignore[override]
        base = super().metadata
        base.update(self._meta_extra)
        return base

    async def _execute(
        self,
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {"success": True}


def _ctx(**overrides: Any) -> SelectionContext:
    defaults: dict[str, Any] = {}
    for f in SelectionContext.__dataclass_fields__:
        defaults[f] = getattr(SelectionContext(), f)
    defaults.update(overrides)
    return SelectionContext(**defaults)


# ======================================================================
# SelectionContext
# ======================================================================


class TestSelectionContext:
    def test_defaults(self) -> None:
        ctx = SelectionContext()
        assert ctx.task_name == ""
        assert ctx.capability_requirements == frozenset()
        assert ctx.max_permission_level == PermissionLevel.HIGH
        assert ctx.max_cost == float("inf")
        assert ctx.max_latency_ms == float("inf")

    def test_frozen(self) -> None:
        ctx = SelectionContext()
        with pytest.raises(AttributeError):
            ctx.task_name = "new"  # type: ignore[misc]


# ======================================================================
# ExecutionMode
# ======================================================================


class TestExecutionMode:
    def test_values(self) -> None:
        assert ExecutionMode.AUTOMATIC.value == "automatic"
        assert ExecutionMode.MANUAL_CONFIRM.value == "manual_confirm"
        assert ExecutionMode.BACKGROUND.value == "background"
        assert ExecutionMode.INTERACTIVE.value == "interactive"


# ======================================================================
# BaseFilter
# ======================================================================


class TestBaseFilter:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            BaseFilter()  # type: ignore[abstract]

    def test_subclass_must_implement_name(self) -> None:
        class Bad(BaseFilter):
            def _do_apply(self, tools, context):
                return tools

        with pytest.raises(TypeError):
            Bad()


# ======================================================================
# AvailabilityFilter
# ======================================================================


class TestAvailabilityFilter:
    def setup_method(self) -> None:
        self.filt = AvailabilityFilter()
        self.available = _FakeTool("a", state=ToolState.AVAILABLE)
        self.executing = _FakeTool("b", state=ToolState.EXECUTING)
        self.disabled = _FakeTool("c", state=ToolState.DISABLED)
        self.error = _FakeTool("d", state=ToolState.ERROR)

    def test_name(self) -> None:
        assert self.filt.name == "availability"

    def test_keeps_available(self) -> None:
        result = self.filt.apply([self.available], _ctx())
        assert len(result) == 1
        assert result[0].name == "a"

    def test_removes_non_available(self) -> None:
        tools = [self.available, self.executing, self.disabled, self.error]
        result = self.filt.apply(tools, _ctx())
        assert len(result) == 1
        assert result[0].name == "a"

    def test_empty_input(self) -> None:
        assert self.filt.apply([], _ctx()) == []


# ======================================================================
# CapabilityFilter
# ======================================================================


class TestCapabilityFilter:
    def setup_method(self) -> None:
        self.filt = CapabilityFilter()
        self.file_tool = _FakeTool(
            "file", category=ToolCategory.FILE
        )
        self.shell_tool = _FakeTool(
            "shell", category=ToolCategory.SHELL
        )
        self.capable_tool = _FakeTool(
            "capable",
            category=ToolCategory.CUSTOM,
            metadata_extra={
                "capabilities": ["file_read", "file_write", "search"]
            },
        )

    def test_name(self) -> None:
        assert self.filt.name == "capability"

    def test_no_requirements_passes_all(self) -> None:
        result = self.filt.apply(
            [self.file_tool, self.shell_tool],
            _ctx(),
        )
        assert len(result) == 2

    def test_category_match(self) -> None:
        result = self.filt.apply(
            [self.file_tool, self.shell_tool],
            _ctx(capability_requirements=frozenset({"file"})),
        )
        assert len(result) == 1
        assert result[0].name == "file"

    def test_metadata_capabilities_match(self) -> None:
        result = self.filt.apply(
            [self.file_tool, self.capable_tool],
            _ctx(capability_requirements=frozenset({"file_read"})),
        )
        assert len(result) == 1
        assert result[0].name == "capable"

    def test_no_match_removes_all(self) -> None:
        result = self.filt.apply(
            [self.file_tool],
            _ctx(capability_requirements=frozenset({"web"})),
        )
        assert result == []


# ======================================================================
# ExecutionModeFilter
# ======================================================================


class TestExecutionModeFilter:
    def setup_method(self) -> None:
        self.filt = ExecutionModeFilter()
        self.low = _FakeTool(
            "low", permission_level=PermissionLevel.LOW
        )
        self.high = _FakeTool(
            "high", permission_level=PermissionLevel.HIGH
        )
        self.high_safe = _FakeTool(
            "high_safe",
            permission_level=PermissionLevel.HIGH,
            metadata_extra={"auto_safe": True},
        )
        self.medium = _FakeTool(
            "medium", permission_level=PermissionLevel.MEDIUM
        )

    def test_name(self) -> None:
        assert self.filt.name == "execution_mode"

    def test_automatic_removes_high_unless_safe(self) -> None:
        tools = [self.low, self.high, self.high_safe]
        result = self.filt.apply(
            tools, _ctx(execution_mode=ExecutionMode.AUTOMATIC)
        )
        names = {t.name for t in result}
        assert names == {"low", "high_safe"}

    def test_manual_confirm_passes_all(self) -> None:
        tools = [self.low, self.high, self.medium]
        result = self.filt.apply(
            tools, _ctx(execution_mode=ExecutionMode.MANUAL_CONFIRM)
        )
        assert len(result) == 3

    def test_interactive_passes_all(self) -> None:
        tools = [self.low, self.high]
        result = self.filt.apply(
            tools, _ctx(execution_mode=ExecutionMode.INTERACTIVE)
        )
        assert len(result) == 2

    def test_background_removes_high_and_medium(self) -> None:
        tools = [self.low, self.medium, self.high]
        result = self.filt.apply(
            tools, _ctx(execution_mode=ExecutionMode.BACKGROUND)
        )
        assert len(result) == 1
        assert result[0].name == "low"


# ======================================================================
# PermissionFilter
# ======================================================================


class TestPermissionFilter:
    def setup_method(self) -> None:
        self.filt = PermissionFilter()
        self.low = _FakeTool("low", permission_level=PermissionLevel.LOW)
        self.medium = _FakeTool(
            "medium", permission_level=PermissionLevel.MEDIUM
        )
        self.high = _FakeTool(
            "high", permission_level=PermissionLevel.HIGH
        )

    def test_name(self) -> None:
        assert self.filt.name == "permission"

    def test_high_ceiling_passes_all(self) -> None:
        result = self.filt.apply(
            [self.low, self.medium, self.high],
            _ctx(max_permission_level=PermissionLevel.HIGH),
        )
        assert len(result) == 3

    def test_medium_ceiling_removes_high(self) -> None:
        result = self.filt.apply(
            [self.low, self.medium, self.high],
            _ctx(max_permission_level=PermissionLevel.MEDIUM),
        )
        assert len(result) == 2
        assert all(t.name != "high" for t in result)

    def test_low_ceiling_keeps_only_low(self) -> None:
        result = self.filt.apply(
            [self.low, self.medium, self.high],
            _ctx(max_permission_level=PermissionLevel.LOW),
        )
        assert len(result) == 1
        assert result[0].name == "low"


# ======================================================================
# CostFilter
# ======================================================================


class TestCostFilter:
    def setup_method(self) -> None:
        self.filt = CostFilter()
        self.free = _FakeTool("free", metadata_extra={"cost": 0})
        self.cheap = _FakeTool("cheap", metadata_extra={"cost": 5.0})
        self.expensive = _FakeTool(
            "expensive", metadata_extra={"cost": 100.0}
        )
        self.no_cost = _FakeTool("no_cost")  # no cost metadata

    def test_name(self) -> None:
        assert self.filt.name == "cost"

    def test_infinite_budget_passes_all(self) -> None:
        result = self.filt.apply(
            [self.free, self.expensive],
            _ctx(max_cost=float("inf")),
        )
        assert len(result) == 2

    def test_budget_removes_expensive(self) -> None:
        result = self.filt.apply(
            [self.free, self.cheap, self.expensive],
            _ctx(max_cost=10.0),
        )
        names = {t.name for t in result}
        assert names == {"free", "cheap"}

    def test_no_cost_metadata_assumed_free(self) -> None:
        result = self.filt.apply(
            [self.no_cost],
            _ctx(max_cost=0.0),
        )
        assert len(result) == 1

    def test_exact_boundary(self) -> None:
        result = self.filt.apply(
            [self.cheap],
            _ctx(max_cost=5.0),
        )
        assert len(result) == 1


# ======================================================================
# LatencyFilter
# ======================================================================


class TestLatencyFilter:
    def setup_method(self) -> None:
        self.filt = LatencyFilter()
        self.fast = _FakeTool(
            "fast",
            metadata_extra={"avg_latency_ms": 10, "executions": 100},
        )
        self.slow = _FakeTool(
            "slow",
            metadata_extra={"avg_latency_ms": 500, "executions": 50},
        )
        self.no_meta = _FakeTool("no_meta")

    def test_name(self) -> None:
        assert self.filt.name == "latency"

    def test_infinite_threshold_passes_all(self) -> None:
        result = self.filt.apply(
            [self.fast, self.slow],
            _ctx(max_latency_ms=float("inf")),
        )
        assert len(result) == 2

    def test_removes_slow(self) -> None:
        result = self.filt.apply(
            [self.fast, self.slow],
            _ctx(max_latency_ms=100),
        )
        assert len(result) == 1
        assert result[0].name == "fast"

    def test_fallback_to_metadata_total_time(self) -> None:
        tool = _FakeTool(
            "calc",
            metadata_extra={"total_time_ms": 600, "executions": 3},
        )
        result = self.filt.apply(
            [tool], _ctx(max_latency_ms=250)
        )
        # avg = 600/3 = 200 <= 250 → passes
        assert len(result) == 1

    def test_no_executions_defaults_to_zero(self) -> None:
        result = self.filt.apply(
            [self.no_meta],
            _ctx(max_latency_ms=0),
        )
        # 0/1 = 0 <= 0 → passes
        assert len(result) == 1


# ======================================================================
# SafetyFilter
# ======================================================================


class TestSafetyFilter:
    def setup_method(self) -> None:
        self.filt = SafetyFilter()
        self.low = _FakeTool(
            "low", permission_level=PermissionLevel.LOW
        )
        self.medium = _FakeTool(
            "medium", permission_level=PermissionLevel.MEDIUM
        )
        self.high = _FakeTool(
            "high", permission_level=PermissionLevel.HIGH
        )
        self.explicit = _FakeTool(
            "explicit",
            permission_level=PermissionLevel.HIGH,
            metadata_extra={"safety_score": 0.9},
        )

    def test_name(self) -> None:
        assert self.filt.name == "safety"

    def test_zero_threshold_passes_all(self) -> None:
        result = self.filt.apply(
            [self.low, self.high],
            _ctx(min_safety_score=0.0),
        )
        assert len(result) == 2

    def test_removes_high_by_default_safety(self) -> None:
        # HIGH default safety = 0.4
        result = self.filt.apply(
            [self.low, self.high],
            _ctx(min_safety_score=0.5),
        )
        assert len(result) == 1
        assert result[0].name == "low"

    def test_explicit_safety_overrides_default(self) -> None:
        result = self.filt.apply(
            [self.high, self.explicit],
            _ctx(min_safety_score=0.8),
        )
        assert len(result) == 1
        assert result[0].name == "explicit"

    def test_boundary_inclusive(self) -> None:
        # MEDIUM default safety = 0.7
        result = self.filt.apply(
            [self.medium],
            _ctx(min_safety_score=0.7),
        )
        assert len(result) == 1


# ======================================================================
# UserPreferenceFilter
# ======================================================================


class TestUserPreferenceFilter:
    def setup_method(self) -> None:
        self.filt = UserPreferenceFilter()
        self.a = _FakeTool("a")
        self.b = _FakeTool("b")
        self.c = _FakeTool("c")

    def test_name(self) -> None:
        assert self.filt.name == "user_preference"

    def test_no_preferences_passes_all(self) -> None:
        result = self.filt.apply([self.a, self.b], _ctx())
        assert len(result) == 2

    def test_exclude_removes(self) -> None:
        result = self.filt.apply(
            [self.a, self.b, self.c],
            _ctx(exclude_tools=frozenset({"b"})),
        )
        names = {t.name for t in result}
        assert names == {"a", "c"}

    def test_include_keeps(self) -> None:
        # include_tools doesn't force-include; this filter only
        # removes excludes.  With no excludes, all pass.
        result = self.filt.apply(
            [self.a, self.b],
            _ctx(include_tools=frozenset({"a"})),
        )
        assert len(result) == 2

    def test_exclude_and_include(self) -> None:
        result = self.filt.apply(
            [self.a, self.b, self.c],
            _ctx(
                include_tools=frozenset({"a"}),
                exclude_tools=frozenset({"b"}),
            ),
        )
        names = {t.name for t in result}
        assert names == {"a", "c"}


# ======================================================================
# FilterError propagation
# ======================================================================


class TestFilterErrorPropagation:
    def test_internal_error_becomes_filter_error(self) -> None:
        class Broken(BaseFilter):
            @property
            def name(self) -> str:
                return "broken"

            def _do_apply(self, tools, context):
                raise RuntimeError("internal boom")

        filt = Broken()
        with pytest.raises(FilterError) as exc_info:
            filt.apply([_FakeTool("x")], _ctx())

        assert "broken" in str(exc_info.value)
        assert exc_info.value.filter_name == "broken"

    def test_filter_error_propagated_directly(self) -> None:
        class Raising(BaseFilter):
            @property
            def name(self) -> str:
                return "raising"

            def _do_apply(self, tools, context):
                raise FilterError("intentional", filter_name="raising")

        filt = Raising()
        with pytest.raises(FilterError) as exc_info:
            filt.apply([_FakeTool("x")], _ctx())
        assert exc_info.value.filter_name == "raising"


# ======================================================================
# DEFAULT_FILTER_CHAIN
# ======================================================================


class TestDefaultFilterChain:
    def test_is_list(self) -> None:
        assert isinstance(DEFAULT_FILTER_CHAIN, list)

    def test_all_are_filters(self) -> None:
        for f in DEFAULT_FILTER_CHAIN:
            assert isinstance(f, BaseFilter)

    def test_has_all_eight(self) -> None:
        assert len(DEFAULT_FILTER_CHAIN) == 8

    def test_order(self) -> None:
        names = [f.name for f in DEFAULT_FILTER_CHAIN]
        expected = [
            "availability",
            "capability",
            "execution_mode",
            "permission",
            "cost",
            "latency",
            "safety",
            "user_preference",
        ]
        assert names == expected