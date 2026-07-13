"""Unit tests for ToolRegistry.

Sprint 1.6 - Phase 7.
Tests: registration, search, duplicates, enable/disable,
category/level filtering, source tracking, summary.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS_SRC = ROOT / "packages" / "tools"
sys.path.insert(0, str(TOOLS_SRC))

# Clear cached src modules.
for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.base import (
    BaseTool,
    PermissionLevel,
    ToolCategory,
    ToolState,
)
from src.registry import (
    DuplicatePolicy,
    ToolRegistry,
    ToolSource,
)


class StubTool(BaseTool):
    name = "stub"
    description = "stub"
    permission_level = PermissionLevel.LOW
    category = ToolCategory.CUSTOM

    async def _execute(self, params, context):
        return {"success": True}


class HighTool(BaseTool):
    name = "high_stub"
    description = "high"
    permission_level = PermissionLevel.HIGH
    category = ToolCategory.SHELL

    async def _execute(self, params, context):
        return {"success": True}


class FileTool2(BaseTool):
    name = "file_op"
    description = "file"
    permission_level = PermissionLevel.MEDIUM
    category = ToolCategory.FILE

    async def _execute(self, params, context):
        return {"success": True}


# ─── Registration ─────────────────────────────────────────────────


def test_register_tool() -> None:
    reg = ToolRegistry()
    tool = StubTool()
    reg.register(tool)
    assert "stub" in reg
    assert len(reg) == 1


def test_register_non_tool_raises() -> None:
    reg = ToolRegistry()

    class NotATool:
        name = "bad"
        async def execute(self, params):
            return {}

    # NotATool satisfies the Tool protocol (name + async execute)
    # so this actually passes. Let's test with a truly non-conforming object.
    class TrulyBad:
        pass

    with pytest.raises(TypeError):
        reg.register(TrulyBad())


def test_register_duplicate_overwrite() -> None:
    reg = ToolRegistry(duplicate_policy=DuplicatePolicy.OVERWRITE)
    t1 = StubTool()
    t2 = StubTool()
    reg.register(t1)
    reg.register(t2)
    assert len(reg) == 1


def test_register_duplicate_raise() -> None:
    reg = ToolRegistry(duplicate_policy=DuplicatePolicy.RAISE)
    t1 = StubTool()
    t2 = StubTool()
    reg.register(t1)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(t2)


def test_register_duplicate_skip() -> None:
    reg = ToolRegistry(duplicate_policy=DuplicatePolicy.SKIP)
    t1 = StubTool()
    t2 = StubTool()
    reg.register(t1)
    reg.register(t2)
    assert len(reg) == 1
    # t1 is still the registered tool.
    assert reg.get("stub") is t1


# ─── Lookup ───────────────────────────────────────────────────────


def test_get_existing() -> None:
    reg = ToolRegistry()
    tool = StubTool()
    reg.register(tool)
    assert reg.get("stub") is tool


def test_get_missing_returns_none() -> None:
    reg = ToolRegistry()
    assert reg.get("nonexistent") is None


def test_get_all_returns_disabled() -> None:
    reg = ToolRegistry()
    tool = StubTool()
    reg.register(tool)
    reg.disable("stub")
    assert reg.get("stub") is None  # disabled
    assert reg.get_all("stub") is tool  # even if disabled


# ─── Enable / Disable ─────────────────────────────────────────────


def test_disable_tool() -> None:
    reg = ToolRegistry()
    reg.register(StubTool())
    assert reg.get("stub") is not None

    reg.disable("stub")
    assert reg.get("stub") is None

    reg.enable("stub")
    assert reg.get("stub") is not None


def test_enable_nonexistent_returns_false() -> None:
    reg = ToolRegistry()
    assert reg.enable("nope") is False


def test_disable_nonexistent_returns_false() -> None:
    reg = ToolRegistry()
    assert reg.disable("nope") is False


# ─── Unregister ───────────────────────────────────────────────────


def test_unregister() -> None:
    reg = ToolRegistry()
    reg.register(StubTool())
    assert reg.unregister("stub") is True
    assert "stub" not in reg
    assert reg.unregister("stub") is False


# ─── Filtering ────────────────────────────────────────────────────


def test_by_category() -> None:
    reg = ToolRegistry()
    reg.register(StubTool())      # CUSTOM
    reg.register(HighTool())      # SHELL
    reg.register(FileTool2())     # FILE

    file_tools = reg.by_category(ToolCategory.FILE)
    assert len(file_tools) == 1
    assert file_tools[0].name == "file_op"


def test_by_permission_level() -> None:
    reg = ToolRegistry()
    reg.register(StubTool())      # LOW
    reg.register(HighTool())      # HIGH
    reg.register(FileTool2())     # MEDIUM

    high = reg.by_permission_level(PermissionLevel.HIGH)
    assert len(high) == 1
    assert high[0].name == "high_stub"


def test_by_source() -> None:
    reg = ToolRegistry()
    reg.register(StubTool(), source=ToolSource.INTERNAL)
    reg.register(HighTool(), source=ToolSource.PLUGIN)

    plugins = reg.by_source(ToolSource.PLUGIN)
    assert len(plugins) == 1
    assert plugins[0].name == "high_stub"


# ─── Names ────────────────────────────────────────────────────────


def test_names() -> None:
    reg = ToolRegistry()
    reg.register(StubTool())
    reg.register(HighTool())
    assert sorted(reg.names()) == ["high_stub", "stub"]


def test_enabled_names() -> None:
    reg = ToolRegistry()
    reg.register(StubTool())
    reg.register(HighTool())
    reg.disable("stub")
    assert "stub" not in reg.enabled_names()
    assert "high_stub" in reg.enabled_names()


# ─── Summary ──────────────────────────────────────────────────────


def test_summary() -> None:
    reg = ToolRegistry()
    reg.register(StubTool())      # CUSTOM
    reg.register(HighTool())      # SHELL
    reg.register(FileTool2())     # FILE
    reg.disable("stub")

    s = reg.summary()
    assert s["total"] == 3
    assert s["enabled"] == 2
    assert s["disabled"] == 1
    # Only enabled tools are counted in by_category.
    assert s["by_category"]["shell"] == 1
    assert s["by_category"]["file"] == 1
    assert s["by_permission_level"]["high"] == 1
    assert s["by_permission_level"]["medium"] == 1