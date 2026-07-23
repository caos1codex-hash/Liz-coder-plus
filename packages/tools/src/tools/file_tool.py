"""File tool — read, write, and list files and directories.

Sprint 1.6 — Core operational tool.

Security:
  - read_file: LOW permission (read-only).
  - write_file: MEDIUM permission (controlled modification).
  - list_directory: LOW permission (read-only).
  - Paths are validated to prevent directory traversal.
  - File size limit on reads (default 1MB).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from src.base import BaseTool, PermissionLevel, ToolCategory, ToolError

logger = logging.getLogger(__name__)

# Maximum file size for reads (1MB).
_MAX_READ_SIZE = 1_048_576


class FileTool(BaseTool):
    """Tool for file system operations: read, write, list.

    Actions:
      - ``read_file``: Read the contents of a file (LOW).
      - ``write_file``: Write content to a file (MEDIUM).
      - ``list_directory``: List files in a directory (LOW).
    """

    name = "file"
    description = "Read, write, and list files and directories."
    version = "1.0.0"
    category = ToolCategory.FILE
    permission_level = PermissionLevel.MEDIUM
    parameters_schema = {
        "required": ["action"],
        "properties": {
            "action": {
                "type": "str",
                "description": "One of: read_file, write_file, list_directory",
            },
            "path": {
                "type": "str",
                "description": "File or directory path.",
            },
            "content": {
                "type": "str",
                "description": "Content to write (for write_file action).",
            },
        },
    }

    def validate_input(self, params: dict[str, Any]) -> None:
        """Validate action and paths."""
        super().validate_input(params)
        action = params.get("action", "")
        if action not in ("read_file", "write_file", "list_directory"):
            raise ToolError(
                f"Invalid action '{action}'. "
                f"Must be one of: read_file, write_file, list_directory",
                field="action",
            )

        path = params.get("path", "")
        if not path:
            raise ToolError(
                f"Parameter 'path' is required for action '{action}'",
                field="path",
            )

        # Prevent directory traversal.
        resolved = Path(path).resolve()
        if ".." in Path(path).parts:
            raise ToolError(
                "Directory traversal ('..') is not allowed in paths",
                field="path",
            )

    async def _execute(
        self,
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a file operation."""
        action = params["action"]
        path = params["path"]

        if action == "read_file":
            return await self._read_file(path)
        elif action == "write_file":
            return await self._write_file(path, params.get("content", ""))
        elif action == "list_directory":
            return await self._list_directory(path)

        # Should not reach here due to validation.
        return {"success": False, "error": f"Unknown action: {action}"}

    async def _read_file(self, path: str) -> dict[str, Any]:
        """Read file contents."""
        file_path = Path(path)
        if not file_path.exists():
            return {"success": False, "error": f"File not found: {path}"}
        if not file_path.is_file():
            return {"success": False, "error": f"Not a file: {path}"}

        try:
            size = file_path.stat().st_size
            if size > _MAX_READ_SIZE:
                return {
                    "success": False,
                    "error": (
                        f"File too large: {size} bytes "
                        f"(max {_MAX_READ_SIZE}). "
                        f"Use a smaller file or increase the limit."
                    ),
                }
            content = file_path.read_text(encoding="utf-8")
            return {
                "success": True,
                "output": content,
                "file_path": str(file_path.resolve()),
                "size_bytes": size,
            }
        except UnicodeDecodeError:
            return {
                "success": False,
                "error": f"Cannot read file as UTF-8: {path}",
            }
        except OSError as exc:
            return {"success": False, "error": f"OS error reading file: {exc}"}

    async def _write_file(
        self, path: str, content: str
    ) -> dict[str, Any]:
        """Write content to a file."""
        file_path = Path(path)

        # Create parent directories if needed.
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return {
                "success": True,
                "output": f"File written: {file_path.resolve()}",
                "file_path": str(file_path.resolve()),
                "size_bytes": len(content.encode("utf-8")),
            }
        except OSError as exc:
            return {"success": False, "error": f"OS error writing file: {exc}"}

    async def _list_directory(self, path: str) -> dict[str, Any]:
        """List files and directories."""
        dir_path = Path(path)
        if not dir_path.exists():
            return {"success": False, "error": f"Directory not found: {path}"}
        if not dir_path.is_dir():
            return {"success": False, "error": f"Not a directory: {path}"}

        try:
            entries = []
            for entry in sorted(dir_path.iterdir()):
                entry_info = {
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else 0,
                }
                entries.append(entry_info)

            return {
                "success": True,
                "output": f"Listed {len(entries)} entries in {dir_path.resolve()}",
                "path": str(dir_path.resolve()),
                "entries": entries,
                "count": len(entries),
            }
        except PermissionError:
            return {
                "success": False,
                "error": f"Permission denied: {path}",
            }
        except OSError as exc:
            return {"success": False, "error": f"OS error listing directory: {exc}"}