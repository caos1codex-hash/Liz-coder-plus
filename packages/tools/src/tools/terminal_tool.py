"""Terminal tool — execute shell commands safely.

Sprint 1.6 — Core operational tool.

Security:
  - HIGH permission level (can execute arbitrary commands).
  - Must pass through PermissionService before execution.
  - Timeout enforced by ToolExecutor (default 30s).
  - Output is captured and limited (max 64KB).
  - No interactive terminal support.
  - Dangerous patterns logged as warnings.

NEVER execute a tool without passing through permissions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.base import BaseTool, PermissionLevel, ToolCategory, ToolError

logger = logging.getLogger(__name__)

# Maximum output size (64KB).
_MAX_OUTPUT_SIZE = 65_536

# Dangerous command patterns (additional to the deny list in config).
_DANGEROUS_PATTERNS = (
    "rm -rf /",
    "mkfs.",
    "dd if=",
    ":(){ :|:& };:",
    "> /dev/sd",
    "chmod -R 777 /",
)


class TerminalTool(BaseTool):
    """Tool for executing shell commands.

    Actions:
      - ``run``: Execute a command and capture output.

    All commands are subject to:
      1. PermissionService evaluation (HIGH level → confirm by default).
      2. ToolExecutor timeout.
      3. Output size limit.
    """

    name = "terminal"
    description = "Execute shell commands with permission gating."
    version = "1.0.0"
    category = ToolCategory.SHELL
    permission_level = PermissionLevel.HIGH
    parameters_schema = {
        "required": ["action", "command"],
        "properties": {
            "action": {
                "type": "str",
                "description": "Must be: run",
            },
            "command": {
                "type": "str",
                "description": "The shell command to execute.",
            },
            "working_dir": {
                "type": "str",
                "description": "Working directory for the command.",
            },
        },
    }

    def validate_input(self, params: dict[str, Any]) -> None:
        """Validate action and command."""
        super().validate_input(params)
        action = params.get("action", "")
        if action != "run":
            raise ToolError(
                f"Invalid action '{action}'. Must be: run",
                field="action",
            )

        command = params.get("command", "")
        if not command or not command.strip():
            raise ToolError(
                "Parameter 'command' must not be empty",
                field="command",
            )

        # Check for known dangerous patterns — BLOCK execution.
        cmd_lower = command.lower()
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.lower() in cmd_lower:
                raise ToolError(
                    f"Dangerous command pattern detected and blocked: '{pattern}'",
                    field="command",
                )

    async def _execute(
        self,
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute the terminal command."""
        command = params["command"]
        working_dir = params.get("working_dir", None)

        session_id = context.get("session_id", "unknown")
        agent_name = context.get("agent_name", "unknown")

        logger.info(
            "Terminal executing command (session=%s, agent=%s): %s",
            session_id,
            agent_name,
            command,
        )

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
            )

            stdout, stderr = await process.communicate()

            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            # Truncate if too large.
            truncated = False
            if len(stdout_str) > _MAX_OUTPUT_SIZE:
                stdout_str = stdout_str[:_MAX_OUTPUT_SIZE]
                truncated = True
            if len(stderr_str) > _MAX_OUTPUT_SIZE:
                stderr_str = stderr_str[:_MAX_OUTPUT_SIZE]
                truncated = True

            output_parts = []
            if stdout_str:
                output_parts.append(stdout_str)
            if stderr_str:
                output_parts.append(f"[stderr] {stderr_str}")
            if truncated:
                output_parts.append(
                    "[output truncated — exceeded limit]"
                )

            full_output = "\n".join(output_parts) if output_parts else "(no output)"

            success = process.returncode == 0

            result: dict[str, Any] = {
                "success": success,
                "output": full_output,
                "return_code": process.returncode,
            }

            if not success:
                result["error"] = (
                    f"Command exited with code {process.returncode}"
                )

            return result

        except FileNotFoundError:
            return {
                "success": False,
                "error": "Command not found or shell not available.",
            }
        except PermissionError:
            return {
                "success": False,
                "error": "Permission denied executing command.",
            }
        except OSError as exc:
            return {
                "success": False,
                "error": f"OS error: {exc}",
            }