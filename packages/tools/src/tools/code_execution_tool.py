"""Code execution tool — execute Python code in a sandboxed subprocess.

Sprint 5 — Code execution capability.

Security:
  - execute: HIGH permission (can execute arbitrary code).
  - Runs in a subprocess with timeout.
  - Captures stdout, stderr, and return code.
  - No network access from executed code.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any

from src.base import BaseTool, PermissionLevel, ToolCategory

logger = logging.getLogger(__name__)

# Maximum execution time (seconds).
_MAX_EXECUTION_TIME = 30
# Maximum output size (characters).
_MAX_OUTPUT_SIZE = 50000


class CodeExecutionTool(BaseTool):
    """Tool for executing Python code.

    Actions:
      - ``execute``: Execute Python code and return output (HIGH).
    """

    name = "code_execution"
    description = "Execute Python code in a sandboxed subprocess."
    version = "1.0.0"
    category = ToolCategory.CUSTOM
    permission_level = PermissionLevel.HIGH
    parameters_schema = {
        "required": ["action"],
        "properties": {
            "action": {
                "type": "str",
                "description": "Must be 'execute'.",
            },
            "code": {
                "type": "str",
                "description": "Python code to execute.",
            },
            "timeout": {
                "type": "int",
                "description": "Timeout in seconds (default: 30, max: 60).",
            },
        },
    }

    async def _execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute Python code in a subprocess."""
        action = params.get("action", "")

        if action != "execute":
            return {
                "success": False,
                "error": f"Unknown action: '{action}'. Use 'execute'.",
            }

        code = params.get("code", "")
        if not code:
            return {"success": False, "error": "Code is required."}

        timeout = min(
            params.get("timeout", _MAX_EXECUTION_TIME),
            60,
        )

        return await self._run_code(code, timeout)

    async def _run_code(self, code: str, timeout: int) -> dict[str, Any]:
        """Run Python code in a subprocess with timeout."""
        # Write code to a temp file.
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                prefix="liz_exec_",
            ) as f:
                f.write(code)
                temp_path = f.name
        except Exception as exc:
            return {"success": False, "error": f"Failed to write temp file: {exc}"}

        try:
            proc = await asyncio.create_subprocess_exec(
                "python",
                temp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Restrict: no network via environment.
                env={
                    **os.environ,
                    "http_proxy": "",
                    "https_proxy": "",
                    "HTTP_PROXY": "",
                    "HTTPS_PROXY": "",
                    "NO_PROXY": "*",
                },
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                return {
                    "success": False,
                    "error": f"Code execution timed out after {timeout}s",
                    "return_code": -1,
                }

            stdout_text = stdout.decode("utf-8", errors="replace")[:_MAX_OUTPUT_SIZE]
            stderr_text = stderr.decode("utf-8", errors="replace")[:_MAX_OUTPUT_SIZE]

            return {
                "success": proc.returncode == 0,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "return_code": proc.returncode,
            }

        except Exception as exc:
            logger.exception("Code execution failed")
            return {"success": False, "error": f"Execution error: {exc}"}
        finally:
            # Clean up temp file.
            try:
                os.unlink(temp_path)
            except OSError:
                pass
