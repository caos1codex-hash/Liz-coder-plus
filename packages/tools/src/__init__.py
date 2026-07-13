"""Tools package: external tools for Liz Coder Plus.

Sprint 1.6 — Full tool system with lifecycle, permission levels,
validation, structured metadata, and built-in operational tools.

Public API:
  - ``BaseTool`` — abstract base class with lifecycle and validation.
  - ``ToolCategory`` — SHELL, FILE, WEB, APP, SYSTEM, CUSTOM.
  - ``ToolState`` — AVAILABLE, EXECUTING, COMPLETED, ERROR, DISABLED.
  - ``PermissionLevel`` — LOW, MEDIUM, HIGH.
  - ``ToolError`` — raised on validation failures.
  - ``ToolRegistry`` — tool registration with validation.
  - ``FileTool`` — read, write, list files and directories.
  - ``SystemTool`` — system information and status.
  - ``TerminalTool`` — execute shell commands safely.
"""

from src.base import (
    BaseTool,
    PermissionLevel,
    ToolCategory,
    ToolError,
    ToolState,
)
from src.registry import ToolRegistry
from src.tools.file_tool import FileTool
from src.tools.system_tool import SystemTool
from src.tools.terminal_tool import TerminalTool

__version__ = "0.2.0"

__all__ = [
    "BaseTool",
    "ToolCategory",
    "ToolState",
    "PermissionLevel",
    "ToolError",
    "ToolRegistry",
    "FileTool",
    "SystemTool",
    "TerminalTool",
]