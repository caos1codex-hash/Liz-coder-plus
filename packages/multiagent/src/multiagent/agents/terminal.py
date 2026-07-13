"""TerminalAgent — ejecuta comandos de shell (Sprint 2.1).

Responsable de:

- ejecutar comandos
- administrar procesos
- monitorear ejecución

**Seguridad**: sólo ejecuta comandos si tiene `Permission.EXECUTE_COMMANDS`
Y el comando está en una allowlist configurable. La allowlist por
defecto es muy restrictiva (sólo comandos de inspección). El agente
rechaza comandos con `&&`, `;`, `|`, redirecciones o backticks salvo
que se invoque con `payload['allow_shell'] = True` (y el agente tenga
UNRESTRICTED).
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Any

from ..base import BaseAgent
from ..enums import Permission

logger = logging.getLogger(__name__)


_DEFAULT_ALLOWLIST = frozenset({
    "ls", "pwd", "whoami", "date", "echo", "cat", "head", "tail",
    "wc", "grep", "find", "python", "python3", "pip", "pytest",
    "git", "ruff", "mypy", "test",
})

_DANGEROUS_TOKENS = ("&&", ";", "|", ">", "<", "`", "$(", "${")


class TerminalAgent(BaseAgent):
    """Agente terminal."""

    default_permissions = frozenset({
        Permission.EXECUTE_COMMANDS, Permission.MEMORY_WRITE,
    })
    default_objectives = ["execute", "process", "monitor", "shell"]
    default_tools = ["terminal_tool", "process_manager"]

    def __init__(
        self,
        *,
        allowlist: frozenset[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name="terminal", description="Executes shell commands with permission gating", **kwargs)
        self._allowlist = allowlist if allowlist is not None else _DEFAULT_ALLOWLIST

    async def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("payload", {}) or {}
        command = payload.get("command") or payload.get("cmd") or ""
        cwd = payload.get("cwd")
        timeout = float(payload.get("timeout", 30.0))
        allow_shell = bool(payload.get("allow_shell", False))

        if not command:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "no command provided",
                "tools_used": ["terminal_tool"],
                "tokens_prompt": 0,
                "tokens_completion": 0,
            }

        # Validación de seguridad.
        validation = self._validate(command, allow_shell)
        if validation is not None:
            await self._logger.log_tool(
                self._name, task.get("id", ""), "terminal_tool",
                duration_ms=0.0, success=False, error=validation,
            )
            return {
                "exit_code": -2,
                "stdout": "",
                "stderr": validation,
                "command": command,
                "tools_used": ["terminal_tool"],
                "tokens_prompt": max(1, len(command) // 4),
                "tokens_completion": 0,
            }

        self.require_permission(Permission.EXECUTE_COMMANDS)

        # Ejecutar
        try:
            proc = await asyncio.create_subprocess_exec(
                *shlex.split(command),
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                await self._logger.log_tool(
                    self._name, task.get("id", ""), "terminal_tool",
                    duration_ms=timeout * 1000, success=False, error="timeout",
                )
                return {
                    "exit_code": -3,
                    "stdout": "",
                    "stderr": f"timeout after {timeout}s",
                    "command": command,
                    "tools_used": ["terminal_tool"],
                    "tokens_prompt": max(1, len(command) // 4),
                    "tokens_completion": 0,
                }
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            exit_code = proc.returncode if proc.returncode is not None else -1
            await self._logger.log_tool(
                self._name, task.get("id", ""), "terminal_tool",
                duration_ms=0.0, success=(exit_code == 0),
            )
            self._memory.add_short(
                {"event": "command_executed", "command": command, "exit": exit_code},
                tags=["terminal"],
            )
            return {
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "command": command,
                "tools_used": ["terminal_tool"],
                "tokens_prompt": max(1, len(command) // 4),
                "tokens_completion": max(1, (len(stdout) + len(stderr)) // 4),
            }
        except FileNotFoundError as exc:
            return {
                "exit_code": -4,
                "stdout": "",
                "stderr": f"command not found: {exc}",
                "command": command,
                "tools_used": ["terminal_tool"],
                "tokens_prompt": max(1, len(command) // 4),
                "tokens_completion": 0,
            }

    def _validate(self, command: str, allow_shell: bool) -> str | None:
        """Valida el comando contra la allowlist. Devuelve None si OK."""
        if not allow_shell:
            for tok in _DANGEROUS_TOKENS:
                if tok in command:
                    return (
                        f"command contains forbidden token '{tok}'; "
                        f"set allow_shell=True to override (requires UNRESTRICTED)"
                    )
        if allow_shell and not self.has_permission(Permission.UNRESTRICTED):
            return "allow_shell=True requires UNRESTRICTED permission"

        # Parsear el primer token (comando base).
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            return f"invalid shell syntax: {exc}"
        if not tokens:
            return "empty command"
        base = tokens[0]
        # Si es path, tomar el basename.
        if "/" in base:
            base = base.rsplit("/", 1)[-1]
        if base not in self._allowlist:
            return (
                f"command '{base}' not in allowlist "
                f"(allowed: {sorted(self._allowlist)[:10]}...)"
            )
        return None


__all__ = ["TerminalAgent"]
