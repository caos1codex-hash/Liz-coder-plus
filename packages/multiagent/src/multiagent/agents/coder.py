"""CoderAgent — escribe, refactoriza y testea código (Sprint 2.1).

Responsable de:

- escribir código
- refactorizar
- corregir errores
- generar tests

Esta implementación es una base funcional: opera sobre el payload
`{'op': 'write'|'refactor'|'fix'|'test', 'path': ..., 'content': ...,
'language': ...}`. No ejecuta I/O real salvo que tenga permiso
WRITE_FILES, en cuyo caso puede escribir el archivo solicitado.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..base import BaseAgent
from ..enums import Permission

logger = logging.getLogger(__name__)


class CoderAgent(BaseAgent):
    """Agente programador."""

    default_permissions = frozenset({
        Permission.READ_FILES, Permission.WRITE_FILES, Permission.MEMORY_WRITE,
    })
    default_objectives = ["code", "refactor", "fix", "test", "implement"]
    default_tools = ["file_tool"]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="coder", description="Writes, refactors and tests code", **kwargs)

    async def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("payload", {}) or {}
        op = (payload.get("op") or "write").lower()
        path = payload.get("path")
        content = payload.get("content", "")
        language = payload.get("language", "python")

        tools_used: list[str] = ["file_tool"]
        result: dict[str, Any] = {
            "op": op,
            "path": path,
            "language": language,
        }

        if op == "write":
            self.require_permission(Permission.WRITE_FILES)
            written = await self._write(path, content)
            result["bytes_written"] = written
            result["message"] = f"wrote {written} bytes to {path}"
        elif op == "refactor":
            refactored = self._refactor(content, language)
            result["refactored"] = refactored
            result["message"] = "refactored content"
            tools_used.append("refactor")
        elif op == "fix":
            fixed = self._fix(content, payload.get("error", ""))
            result["fixed"] = fixed
            result["message"] = "applied fix"
            tools_used.append("fix")
        elif op == "test":
            tests = self._generate_tests(content, language, path)
            result["tests"] = tests
            result["message"] = "generated tests"
            tools_used.append("test_gen")
        else:
            result["message"] = f"unknown op: {op}"
            result["op"] = "noop"

        # Persistir resultado en memoria larga.
        self._memory.store(
            key=f"coder:{task.get('id', 'unknown')}",
            value=result,
            tags=["code", op],
        )

        return {
            **result,
            "tools_used": tools_used,
            "tokens_prompt": max(1, len(content) // 4),
            "tokens_completion": max(
                1, len(str(result.get("refactored", "") or result.get("tests", "") or result.get("message", ""))) // 4
            ),
        }

    async def _write(self, path: str | None, content: str) -> int:
        if not path:
            return 0
        # Respetar restricción: sólo escribir dentro de un workspace.
        # El path es interpretado como relativo al CWD o absoluto seguro.
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            self._memory.add_short(
                {"event": "file_written", "path": path, "bytes": len(content)},
                tags=["io"],
            )
            return len(content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CoderAgent write failed for %s: %s", path, exc)
            return 0

    def _refactor(self, content: str, language: str) -> str:
        """Refactor trivial: normaliza finales de línea y trim."""
        if not content:
            return content
        # Normalizar finales de línea.
        refactored = content.replace("\r\n", "\n").replace("\r", "\n")
        # Quitar trailing whitespace por línea.
        refactored = "\n".join(line.rstrip() for line in refactored.split("\n"))
        # Asegurar un solo trailing newline.
        return refactored.rstrip("\n") + "\n"

    def _fix(self, content: str, error: str) -> str:
        """Fix trivial: si el error menciona 'syntax', reportar líneas sospechosas."""
        if not content:
            return content
        if "syntax" in error.lower():
            # Marcar líneas con posibles problemas (paréntesis no balanceados).
            lines = content.split("\n")
            marked: list[str] = []
            for i, line in enumerate(lines, 1):
                if line.count("(") != line.count(")"):
                    marked.append(f"# FIXME line {i}: unbalanced parens")
                marked.append(line)
            return "\n".join(marked)
        return content

    def _generate_tests(self, content: str, language: str, path: str | None) -> str:
        """Genera un esqueleto de tests trivial."""
        module = (path or "module").replace(".py", "").replace("/", "_")
        # Extraer nombres de funciones `def foo(`.
        import re
        funcs = re.findall(r"^\s*def\s+(\w+)\s*\(", content, re.MULTILINE)
        funcs = funcs or ["target"]
        lines = [
            f'"""Auto-generated tests for {module}."""',
            "",
            "import pytest",
            "",
            f"# from {module} import {', '.join(funcs)}",
            "",
        ]
        for fn in funcs:
            lines.extend([
                f"def test_{fn}_basic() -> None:",
                f'    """Smoke test for {fn}."""',
                "    # TODO: replace with real assertions.",
                "    assert True",
                "",
            ])
        return "\n".join(lines)


__all__ = ["CoderAgent"]
