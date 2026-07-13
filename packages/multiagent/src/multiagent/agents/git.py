"""GitAgent — commits, ramas, merges, conflictos, push (Sprint 2.1).

Responsable de:

- commits
- ramas
- merges
- conflictos
- push

**Seguridad**: requiere `Permission.GIT_OPERATIONS`. Ejecuta `git`
vía `TerminalAgent` interno o vía subprocess directo. La implementación
base es no-destructiva: nunca hace `push --force` ni borra ramas sin
flag explícito.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..base import BaseAgent
from ..enums import Permission

logger = logging.getLogger(__name__)


class GitAgent(BaseAgent):
    """Agente git."""

    default_permissions = frozenset({
        Permission.GIT_OPERATIONS,
        Permission.EXECUTE_COMMANDS,
        Permission.MEMORY_WRITE,
    })
    default_objectives = ["git", "commit", "branch", "merge", "push"]
    default_tools = ["git"]

    def __init__(self, *, repo_path: str = ".", **kwargs: Any) -> None:
        super().__init__(name="git", description="Manages git operations: commits, branches, merges, push", **kwargs)
        self._repo_path = repo_path

    async def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("payload", {}) or {}
        op = (payload.get("op") or "status").lower()
        self.require_permission(Permission.GIT_OPERATIONS)

        if op == "status":
            return await self._git(["status", "--porcelain"], task)
        if op == "diff":
            return await self._git(["diff", "--stat"], task)
        if op == "log":
            n = str(payload.get("limit", 10))
            return await self._git(["log", f"-n{n}", "--oneline"], task)
        if op == "add":
            paths = payload.get("paths", ["."])
            return await self._git(["add", *paths], task)
        if op == "commit":
            msg = payload.get("message", "automated commit")
            return await self._git(["commit", "-m", msg], task)
        if op == "branch":
            name = payload.get("name")
            if not name:
                return {"error": "branch name required", "op": op}
            return await self._git(["branch", name], task)
        if op == "checkout":
            name = payload.get("name")
            if not name:
                return {"error": "branch name required", "op": op}
            return await self._git(["checkout", name], task)
        if op == "merge":
            name = payload.get("name")
            if not name:
                return {"error": "branch name required", "op": op}
            return await self._git(["merge", name], task)
        if op == "push":
            remote = payload.get("remote", "origin")
            branch = payload.get("branch", "")
            args = ["push", remote]
            if branch:
                args.append(branch)
            if payload.get("force"):
                if not self.has_permission(Permission.UNRESTRICTED):
                    return {"error": "force push requires UNRESTRICTED", "op": op}
                args.insert(1, "--force")
            return await self._git(args, task)
        if op == "pull":
            remote = payload.get("remote", "origin")
            return await self._git(["pull", remote], task)
        return {"error": f"unknown op: {op}", "op": op}

    async def _git(self, args: list[str], task: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta `git <args>` en el repo_path."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args,
                cwd=self._repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=30.0
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "exit_code": -3,
                    "stdout": "",
                    "stderr": "timeout",
                    "args": args,
                }
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            exit_code = proc.returncode or 0
            await self._logger.log_tool(
                self._name, task.get("id", ""), "git",
                duration_ms=0.0, success=(exit_code == 0),
                error=stderr if exit_code != 0 else None,
            )
            self._memory.add_short(
                {
                    "event": "git_op",
                    "args": args,
                    "exit": exit_code,
                },
                tags=["git"],
            )
            return {
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "args": args,
                "tools_used": ["git"],
                "tokens_prompt": max(1, len(" ".join(args)) // 4),
                "tokens_completion": max(1, (len(stdout) + len(stderr)) // 4),
            }
        except FileNotFoundError:
            return {
                "exit_code": -4,
                "stdout": "",
                "stderr": "git not installed",
                "args": args,
            }


__all__ = ["GitAgent"]
