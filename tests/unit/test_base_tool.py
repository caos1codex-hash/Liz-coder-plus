"""Unit tests for BaseTool.

Sprint 1.6 - Phase 7.
Tests: creation, validation, lifecycle states, permission levels,
execution metrics, error handling.
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
    ToolError,
    ToolState,
)


class GoodTool(BaseTool):
    """A minimal working tool for testing."""

    name = "good_tool"
    description = "A good tool"
    parameters_schema = {
        "required": ["value"],
        "properties": {
            "value": {"type": "str", "description": "A value"},
            "optional": {"type": "int", "description": "Optional int"},
        },
    }

    async def _execute(self, params, context):
        return {"success": True, "output": params["value"]}


class FailingTool(BaseTool):
    """A tool that always fails."""

    name = "failing_tool"
    description = "Always fails"

    async def _execute(self, params, context):
        raise RuntimeError("intentional failure")


class BadOutputTool(BaseTool):
    """A tool that returns invalid output."""

    name = "bad_output_tool"
    description = "Invalid output"

    async def _execute(self, params, context):
        return {"no_success_key": True}


# ─── Creation ─────────────────────────────────────────────────────


def test_tool_creation_has_unique_id() -> None:
    tool = GoodTool()
    assert tool.tool_id is not None
    assert len(tool.tool_id) == 12
    assert isinstance(tool.tool_id, str)


def test_tool_creation_default_state() -> None:
    tool = GoodTool()
    assert tool.state == ToolState.AVAILABLE
    assert tool.is_available is True


def test_tool_creation_custom_name() -> None:
    tool = GoodTool(name="custom_name")
    assert tool.name == "custom_name"


def test_two_tools_have_different_ids() -> None:
    t1 = GoodTool()
    t2 = GoodTool()
    assert t1.tool_id != t2.tool_id


# ─── Metadata ─────────────────────────────────────────────────────


def test_metadata_includes_lifecycle_info() -> None:
    tool = GoodTool()
    meta = tool.metadata
    assert meta["name"] == "good_tool"
    assert meta["tool_id"] == tool.tool_id
    assert meta["state"] == "available"
    assert meta["permission_level"] == "low"
    assert meta["category"] == "custom"
    assert meta["executions"] == 0
    assert meta["failures"] == 0
    assert "parameters_schema" in meta


# ─── Validation ───────────────────────────────────────────────────


def test_validate_input_missing_required() -> None:
    tool = GoodTool()
    with pytest.raises(ToolError) as exc_info:
        tool.validate_input({})
    assert "value" in str(exc_info.value)
    assert exc_info.value.field == "value"


def test_validate_input_wrong_type() -> None:
    tool = GoodTool()
    with pytest.raises(ToolError) as exc_info:
        tool.validate_input({"value": 123})
    assert "str" in str(exc_info.value)


def test_validate_input_valid() -> None:
    tool = GoodTool()
    tool.validate_input({"value": "hello"})  # Should not raise


def test_validate_input_valid_with_optional() -> None:
    tool = GoodTool()
    tool.validate_input({"value": "hello", "optional": 42})


def test_validate_output_missing_success() -> None:
    tool = GoodTool()
    with pytest.raises(ToolError) as exc_info:
        tool.validate_output({"no_success": True})
    assert "success" in str(exc_info.value)


def test_validate_output_success_not_bool() -> None:
    tool = GoodTool()
    with pytest.raises(ToolError):
        tool.validate_output({"success": "yes"})


def test_validate_output_valid() -> None:
    tool = GoodTool()
    tool.validate_output({"success": True})


# ─── Execution ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_success() -> None:
    tool = GoodTool()
    result = await tool.execute({"value": "hello"})
    assert result["success"] is True
    assert result["output"] == "hello"
    assert result["tool_id"] == tool.tool_id
    assert "duration_ms" in result
    assert tool.state == ToolState.COMPLETED
    assert tool.executions == 1
    assert tool.failures == 0


@pytest.mark.asyncio
async def test_execute_passes_context() -> None:
    tool = GoodTool()
    ctx = {"session_id": "s1", "agent_name": "echo"}
    result = await tool.execute({"value": "test"}, context=ctx)
    assert result["success"] is True
    assert tool.state == ToolState.COMPLETED


@pytest.mark.asyncio
async def test_execute_failure_records_metrics() -> None:
    tool = FailingTool()
    with pytest.raises(RuntimeError, match="intentional failure"):
        await tool.execute({})
    assert tool.state == ToolState.ERROR
    assert tool.failures == 1
    assert tool.executions == 0
    assert tool.last_error == "intentional failure"


@pytest.mark.asyncio
async def test_execute_invalid_output_raises() -> None:
    tool = BadOutputTool()
    with pytest.raises(ToolError, match="success"):
        await tool.execute({})
    assert tool.state == ToolState.ERROR
    assert tool.failures == 1


@pytest.mark.asyncio
async def test_execute_invalid_input_raises() -> None:
    tool = GoodTool()
    with pytest.raises(ToolError, match="value"):
        await tool.execute({})
    # State should be ERROR after validation failure.
    assert tool.state == ToolState.ERROR


# ─── Lifecycle ────────────────────────────────────────────────────


def test_enable_disable() -> None:
    tool = GoodTool()
    assert tool.is_available is True

    tool.disable()
    assert tool.is_available is False
    assert tool.state == ToolState.DISABLED

    tool.enable()
    assert tool.is_available is True
    assert tool.state == ToolState.AVAILABLE


# ─── Repr ─────────────────────────────────────────────────────────


def test_repr() -> None:
    tool = GoodTool()
    r = repr(tool)
    assert "GoodTool" in r
    assert "good_tool" in r