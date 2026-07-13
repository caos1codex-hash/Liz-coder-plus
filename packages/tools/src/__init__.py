"""Tools package: external tools for Liz Coder Plus.

Sprint 1.6 — Full tool system with lifecycle, permission levels,
validation, and structured metadata.

Public API:
  - ``BaseTool`` — abstract base class with lifecycle and validation.
  - ``ToolCategory`` — SHELL, FILE, WEB, APP, SYSTEM, CUSTOM.
  - ``ToolState`` — AVAILABLE, EXECUTING, COMPLETED, ERROR, DISABLED.
  - ``PermissionLevel`` — LOW, MEDIUM, HIGH.
  - ``ToolError`` — raised on validation failures.
  - ``ToolRegistry`` — tool registration with validation.
"""

from src.base import (
    BaseTool,
    PermissionLevel,
    ToolCategory,
    ToolError,
    ToolState,
)
from src.registry import ToolRegistry

__version__ = "0.2.0"

__all__ = [
    "BaseTool",
    "ToolCategory",
    "ToolState",
    "PermissionLevel",
    "ToolError",
    "ToolRegistry",
]