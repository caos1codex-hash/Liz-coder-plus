"""System tool — system information and status.

Sprint 1.6 — Core operational tool.

Security:
  - All actions are LOW permission (read-only information).
  - No modifications to the system.
"""

from __future__ import annotations

import logging
import platform
import sys
from typing import Any

from src.base import BaseTool, PermissionLevel, ToolCategory, ToolError

logger = logging.getLogger(__name__)


class SystemTool(BaseTool):
    """Tool for querying system information.

    Actions:
      - ``info``: General system information (OS, CPU, memory, Python).
      - ``status``: Quick status check (hostname, platform, PID).
    """

    name = "system"
    description = "Get system information and status."
    version = "1.0.0"
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.LOW
    parameters_schema = {
        "required": ["action"],
        "properties": {
            "action": {
                "type": "str",
                "description": "One of: info, status",
            },
        },
    }

    def validate_input(self, params: dict[str, Any]) -> None:
        """Validate action."""
        super().validate_input(params)
        action = params.get("action", "")
        if action not in ("info", "status"):
            raise ToolError(
                f"Invalid action '{action}'. "
                f"Must be one of: info, status",
                field="action",
            )

    async def _execute(
        self,
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a system info action."""
        action = params["action"]

        if action == "info":
            return await self._system_info()
        elif action == "status":
            return await self._system_status()

        return {"success": False, "error": f"Unknown action: {action}"}

    async def _system_info(self) -> dict[str, Any]:
        """Gather general system information."""
        try:
            import os

            info = {
                "os_name": platform.system(),
                "os_release": platform.release(),
                "os_version": platform.version(),
                "architecture": platform.machine(),
                "processor": platform.processor() or "N/A",
                "hostname": platform.node(),
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "pid": os.getpid(),
                "cwd": os.getcwd(),
            }

            # Memory info (Linux/macOS).
            try:
                if platform.system() == "Linux":
                    with open("/proc/meminfo", encoding="utf-8") as f:
                        meminfo = {}
                        for line in f:
                            parts = line.split(":")
                            if len(parts) == 2:
                                key = parts[0].strip()
                                val = parts[1].strip().replace(" kB", "")
                                meminfo[key] = int(val)
                        total_mb = meminfo.get("MemTotal", 0) // 1024
                        available_mb = meminfo.get(
                            "MemAvailable", 0
                        ) // 1024
                        info["memory_total_mb"] = total_mb
                        info["memory_available_mb"] = available_mb
            except (OSError, ValueError, KeyError):
                pass

            return {
                "success": True,
                "output": (
                    f"System: {info['os_name']} {info['os_release']} "
                    f"({info['architecture']}), "
                    f"Python {info['python_version']}, "
                    f"PID {info['pid']}"
                ),
                "system": info,
            }
        except Exception as exc:
            return {"success": False, "error": f"Error gathering system info: {exc}"}

    async def _system_status(self) -> dict[str, Any]:
        """Quick status check."""
        try:
            import os

            status = {
                "hostname": platform.node(),
                "platform": platform.platform(),
                "pid": os.getpid(),
                "python_version": sys.version,
            }
            return {
                "success": True,
                "output": (
                    f"Status: {status['hostname']} | "
                    f"{status['platform']} | PID {status['pid']}"
                ),
                "status": status,
            }
        except Exception as exc:
            return {"success": False, "error": f"Error gathering status: {exc}"}