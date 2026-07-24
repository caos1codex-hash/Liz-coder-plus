"""File Search tool — search file contents with pattern matching.

Sprint 8 — Extended tools.

Provides grep-like functionality for searching through files and
directories. Supports regex patterns, file type filtering, and
result limiting.

Security:
  - search_files: LOW permission (read-only search).
  - Paths are validated to prevent directory traversal.
  - Output is limited to prevent excessive results.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from src.base import BaseTool, PermissionLevel, ToolCategory, ToolError

logger = logging.getLogger(__name__)

# Maximum number of search results to prevent excessive output.
_MAX_RESULTS = 50
# Maximum lines of context per match.
_MAX_CONTEXT_LINES = 3
# Maximum file size to search (skip files larger than this).
_MAX_FILE_SIZE = 2_048_576  # 2MB


class FileSearchTool(BaseTool):
    """Tool for searching file contents with pattern matching.

    Actions:
      - ``search``: Search for a pattern in files within a directory (LOW).
      - ``find``: Find files by name pattern (LOW).
    """

    name = "file_search"
    description = (
        "Search file contents for patterns and find files by name. "
        "Supports regex patterns, file extensions filtering, and "
        "case-sensitive/insensitive matching."
    )
    version = "1.0.0"
    category = ToolCategory.FILE

    actions = {
        "search": PermissionLevel.LOW,
        "find": PermissionLevel.LOW,
    }

    # Allowed root directories (empty = all paths allowed).
    _allowed_roots: list[str] = []

    def __init__(self, allowed_roots: list[str] | None = None) -> None:
        super().__init__()
        if allowed_roots is not None:
            self._allowed_roots = allowed_roots

    def _validate_path(self, path: str) -> Path:
        """Validate that a path is safe to access.

        Args:
            path: The path string to validate.

        Returns:
            Resolved absolute Path.

        Raises:
            ToolError: If the path is outside allowed roots or traverses up.
        """
        resolved = Path(path).resolve()

        # Check for directory traversal.
        if ".." in path.split(os.sep):
            raise ToolError(f"Path traversal not allowed: {path}")

        # Check against allowed roots.
        if self._allowed_roots:
            allowed = any(
                str(resolved).startswith(str(Path(r).resolve()))
                for r in self._allowed_roots
            )
            if not allowed:
                raise ToolError(
                    f"Path '{path}' is outside allowed directories"
                )

        return resolved

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a file search action.

        Args:
            params: Dictionary containing:
                - action: 'search' or 'find'
                - For 'search':
                    - pattern: Regex pattern to search for
                    - directory: Directory to search in (default: '.')
                    - extensions: File extensions to include (e.g. ['.py'])
                    - case_sensitive: Whether search is case-sensitive
                    - max_results: Maximum results to return
                - For 'find':
                    - pattern: Glob pattern for file names
                    - directory: Directory to search in (default: '.')

        Returns:
            Dictionary with search results.
        """
        action = params.get("action", "")

        if action == "search":
            return await self._search(params)
        elif action == "find":
            return await self._find(params)
        else:
            raise ToolError(
                f"Unknown action '{action}'. "
                f"Available: search, find"
            )

    async def _search(self, params: dict[str, Any]) -> dict[str, Any]:
        """Search file contents for a pattern.

        Args:
            params: Search parameters.

        Returns:
            Search results with file paths and matching lines.
        """
        pattern = params.get("pattern", "")
        if not pattern:
            raise ToolError("Missing required parameter: pattern")

        directory = params.get("directory", ".")
        extensions = params.get("extensions", [])
        case_sensitive = params.get("case_sensitive", False)
        max_results = min(params.get("max_results", _MAX_RESULTS), _MAX_RESULTS)

        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            raise ToolError(f"Invalid regex pattern: {exc}") from exc

        base_path = self._validate_path(directory)
        if not base_path.is_dir():
            raise ToolError(f"Directory not found: {directory}")

        ext_set = {e.lower() if e.startswith(".") else f".{e}".lower() for e in extensions}

        results: list[dict[str, Any]] = []
        files_searched = 0

        try:
            for root, _dirs, files in os.walk(base_path):
                for filename in files:
                    filepath = Path(root) / filename

                    # Filter by extension if specified.
                    if ext_set and filepath.suffix.lower() not in ext_set:
                        continue

                    # Skip files that are too large.
                    try:
                        if filepath.stat().st_size > _MAX_FILE_SIZE:
                            continue
                    except OSError:
                        continue

                    files_searched += 1

                    try:
                        content = filepath.read_text(
                            encoding="utf-8", errors="ignore"
                        )
                    except (OSError, PermissionError):
                        continue

                    # Search line by line.
                    for line_num, line in enumerate(content.splitlines(), 1):
                        if compiled.search(line):
                            # Get context lines.
                            lines = content.splitlines()
                            start = max(0, line_num - 1 - _MAX_CONTEXT_LINES)
                            end = min(len(lines), line_num + _MAX_CONTEXT_LINES)
                            context = lines[start:end]

                            results.append({
                                "file": str(filepath),
                                "line": line_num,
                                "match": line.strip(),
                                "context": context,
                            })

                            if len(results) >= max_results:
                                return {
                                    "action": "search",
                                    "pattern": pattern,
                                    "results": results,
                                    "total_matches": len(results),
                                    "files_searched": files_searched,
                                    "truncated": True,
                                }
        except PermissionError as exc:
            raise ToolError(f"Permission denied: {exc}") from exc

        return {
            "action": "search",
            "pattern": pattern,
            "results": results,
            "total_matches": len(results),
            "files_searched": files_searched,
            "truncated": False,
        }

    async def _find(self, params: dict[str, Any]) -> dict[str, Any]:
        """Find files by name pattern.

        Args:
            params: Find parameters.

        Returns:
            List of matching file paths.
        """
        pattern = params.get("pattern", "*")
        if not pattern:
            pattern = "*"

        directory = params.get("directory", ".")
        max_results = min(params.get("max_results", _MAX_RESULTS), _MAX_RESULTS)

        base_path = self._validate_path(directory)
        if not base_path.is_dir():
            raise ToolError(f"Directory not found: {directory}")

        results: list[str] = []

        try:
            for filepath in base_path.rglob(pattern):
                if filepath.is_file():
                    results.append(str(filepath))
                    if len(results) >= max_results:
                        return {
                            "action": "find",
                            "pattern": pattern,
                            "results": results,
                            "total_found": len(results),
                            "truncated": True,
                        }
        except PermissionError as exc:
            raise ToolError(f"Permission denied: {exc}") from exc

        return {
            "action": "find",
            "pattern": pattern,
            "results": results,
            "total_found": len(results),
            "truncated": False,
        }
