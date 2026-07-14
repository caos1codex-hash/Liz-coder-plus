"""Unit tests for FileTool, SystemTool, TerminalTool.

Sprint 1.6 - Phase 7.
Tests: creation, execution, validation, error handling.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS_SRC = ROOT / "packages" / "tools"
sys.path.insert(0, str(TOOLS_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.base import PermissionLevel
from src.tools.file_tool import FileTool
from src.tools.system_tool import SystemTool
from src.tools.terminal_tool import TerminalTool


# ═══════════════════════════════════════════════════════════════════
# FileTool
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_file_read() -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write("hello world")
        f.flush()
        path = f.name

    try:
        tool = FileTool()
        result = await tool.execute({
            "action": "read_file",
            "path": path,
        })
        assert result["success"] is True
        assert result["output"] == "hello world"
        assert result["size_bytes"] == 11
    finally:
        Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_read_not_found() -> None:
    tool = FileTool()
    result = await tool.execute({
        "action": "read_file",
        "path": "/nonexistent/file.txt",
    })
    assert result["success"] is False
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_file_write_and_read() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = str(Path(tmpdir) / "test.txt")
        tool = FileTool()

        write_result = await tool.execute({
            "action": "write_file",
            "path": path,
            "content": "written content",
        })
        assert write_result["success"] is True

        read_result = await tool.execute({
            "action": "read_file",
            "path": path,
        })
        assert read_result["success"] is True
        assert read_result["output"] == "written content"


@pytest.mark.asyncio
async def test_file_list_directory() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some files.
        Path(tmpdir, "a.txt").write_text("a")
        Path(tmpdir, "b.txt").write_text("b")
        Path(tmpdir, "subdir").mkdir()

        tool = FileTool()
        result = await tool.execute({
            "action": "list_directory",
            "path": tmpdir,
        })
        assert result["success"] is True
        assert result["count"] == 3
        names = [e["name"] for e in result["entries"]]
        assert "a.txt" in names
        assert "b.txt" in names
        assert "subdir" in names


@pytest.mark.asyncio
async def test_file_invalid_action() -> None:
    tool = FileTool()
    with pytest.raises(Exception):  # ToolError
        await tool.execute({"action": "delete_all"})


@pytest.mark.asyncio
async def test_file_missing_path() -> None:
    tool = FileTool()
    with pytest.raises(Exception):  # ToolError (missing required 'path')
        await tool.execute({"action": "read_file"})


@pytest.mark.asyncio
async def test_file_traversal_blocked() -> None:
    tool = FileTool()
    with pytest.raises(Exception):  # ToolError
        await tool.execute({
            "action": "read_file",
            "path": "/tmp/../../../etc/passwd",
        })


# ═══════════════════════════════════════════════════════════════════
# SystemTool
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_system_info() -> None:
    tool = SystemTool()
    result = await tool.execute({"action": "info"})
    assert result["success"] is True
    assert "system" in result
    assert "os_name" in result["system"]
    assert "python_version" in result["system"]
    assert result["system"]["pid"] > 0


@pytest.mark.asyncio
async def test_system_status() -> None:
    tool = SystemTool()
    result = await tool.execute({"action": "status"})
    assert result["success"] is True
    assert "status" in result
    assert "platform" in result["status"]


@pytest.mark.asyncio
async def test_system_invalid_action() -> None:
    tool = SystemTool()
    with pytest.raises(Exception):
        await tool.execute({"action": "reboot"})


# ═══════════════════════════════════════════════════════════════════
# TerminalTool
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_terminal_run_echo() -> None:
    tool = TerminalTool()
    result = await tool.execute({
        "action": "run",
        "command": "echo hello",
    })
    assert result["success"] is True
    assert "hello" in result["output"]


@pytest.mark.asyncio
async def test_terminal_run_ls() -> None:
    tool = TerminalTool()
    result = await tool.execute({
        "action": "run",
        "command": "ls /tmp",
    })
    assert result["success"] is True


@pytest.mark.asyncio
async def test_terminal_invalid_command() -> None:
    tool = TerminalTool()
    result = await tool.execute({
        "action": "run",
        "command": "nonexistent_command_xyz_123",
    })
    assert result["success"] is False


@pytest.mark.asyncio
async def test_terminal_empty_command_rejected() -> None:
    tool = TerminalTool()
    with pytest.raises(Exception):
        await tool.execute({
            "action": "run",
            "command": "",
        })


@pytest.mark.asyncio
async def test_terminal_invalid_action() -> None:
    tool = TerminalTool()
    with pytest.raises(Exception):
        await tool.execute({
            "action": "delete_all",
            "command": "rm -rf /",
        })


@pytest.mark.asyncio
async def test_terminal_has_high_permission() -> None:
    tool = TerminalTool()
    assert tool.permission_level == PermissionLevel.HIGH


# ═══════════════════════════════════════════════════════════════════
# Cross-tool metadata
# ═══════════════════════════════════════════════════════════════════


def test_file_tool_metadata() -> None:
    tool = FileTool()
    meta = tool.metadata
    assert meta["name"] == "file"
    assert meta["category"] == "file"
    assert meta["permission_level"] == "medium"
    assert "parameters_schema" in meta


def test_system_tool_metadata() -> None:
    tool = SystemTool()
    meta = tool.metadata
    assert meta["name"] == "system"
    assert meta["permission_level"] == "low"


def test_terminal_tool_metadata() -> None:
    tool = TerminalTool()
    meta = tool.metadata
    assert meta["name"] == "terminal"
    assert meta["category"] == "shell"
    assert meta["permission_level"] == "high"


def test_all_tools_have_unique_ids() -> None:
    t1 = FileTool()
    t2 = SystemTool()
    t3 = TerminalTool()
    ids = {t1.tool_id, t2.tool_id, t3.tool_id}
    assert len(ids) == 3