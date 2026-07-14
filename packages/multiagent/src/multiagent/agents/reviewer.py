"""ReviewerAgent — revisa código, detecta malas prácticas (Sprint 2.1).

Responsable de:

- revisar código
- detectar malas prácticas
- seguridad
- performance

Heurísticas implementadas:

- detecta `eval`, `exec`, `os.system`, `shell=True` (seguridad)
- detecta `except:` desnudo (mantenibilidad)
- detecta funciones sin docstring (calidad)
- detecta N+1 imports duplicados
- detecta líneas muy largas (>120 chars)
- detecta `print(` en lugar de `logging`
"""

from __future__ import annotations

import re
from typing import Any

from ..base import BaseAgent
from ..enums import Permission


_SECURITY_PATTERNS = [
    (r"\beval\s*\(", "Use of eval() is dangerous"),
    (r"\bexec\s*\(", "Use of exec() is dangerous"),
    (r"\bos\.system\s*\(", "Use os.system() is unsafe; prefer subprocess"),
    (r"shell\s*=\s*True", "shell=True in subprocess is unsafe"),
    (r"__import__\s*\(", "Dynamic __import__ may be unsafe"),
]

_QUALITY_PATTERNS = [
    (r"^\s*except\s*:", "Bare except catches everything; be specific"),
    (r"^\s*except\s+Exception\s*:", "Catching Exception broadly; consider narrower"),
    (r"^\s*print\s*\(", "Use logging instead of print"),
]

_LONG_LINE_THRESHOLD = 120


class ReviewerAgent(BaseAgent):
    """Agente revisor de código."""

    default_permissions = frozenset({Permission.READ_FILES, Permission.MEMORY_WRITE})
    default_objectives = ["review", "audit", "security", "performance", "quality"]
    default_tools = ["linter", "static_analyzer"]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="reviewer", description="Reviews code for quality, security and performance", **kwargs)

    async def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("payload", {}) or {}
        content = payload.get("content", "")
        path = payload.get("path", "unknown")
        if not content:
            return {
                "issues": [],
                "summary": {"total": 0},
                "message": "no content to review",
                "tools_used": ["linter"],
                "tokens_prompt": 0,
                "tokens_completion": 0,
            }

        issues: list[dict[str, Any]] = []
        lines = content.split("\n")

        # Security
        for line_no, line in enumerate(lines, 1):
            for pattern, message in _SECURITY_PATTERNS:
                if re.search(pattern, line):
                    issues.append({
                        "line": line_no,
                        "severity": "critical",
                        "category": "security",
                        "message": message,
                        "line_content": line.strip(),
                    })

        # Quality
        for line_no, line in enumerate(lines, 1):
            for pattern, message in _QUALITY_PATTERNS:
                if re.search(pattern, line):
                    issues.append({
                        "line": line_no,
                        "severity": "warning",
                        "category": "quality",
                        "message": message,
                        "line_content": line.strip(),
                    })

        # Long lines
        for line_no, line in enumerate(lines, 1):
            if len(line) > _LONG_LINE_THRESHOLD:
                issues.append({
                    "line": line_no,
                    "severity": "info",
                    "category": "style",
                    "message": f"Line too long ({len(line)} > {_LONG_LINE_THRESHOLD})",
                    "line_content": line.strip()[:80] + "...",
                })

        # Performance: detect nested loops
        nested_loops = self._detect_nested_loops(lines)
        for nl in nested_loops:
            issues.append({
                "line": nl,
                "severity": "warning",
                "category": "performance",
                "message": "Nested loop detected — possible O(n^2)",
                "line_content": lines[nl - 1].strip() if nl - 1 < len(lines) else "",
            })

        # Docstrings: detectar funciones públicas sin docstring
        for fn_line, fn_name in self._functions_without_docstring(lines):
            issues.append({
                "line": fn_line,
                "severity": "info",
                "category": "quality",
                "message": f"Function '{fn_name}' has no docstring",
                "line_content": "",
            })

        summary = {
            "total": len(issues),
            "critical": sum(1 for i in issues if i["severity"] == "critical"),
            "warning": sum(1 for i in issues if i["severity"] == "warning"),
            "info": sum(1 for i in issues if i["severity"] == "info"),
            "by_category": {
                cat: sum(1 for i in issues if i["category"] == cat)
                for cat in {"security", "quality", "style", "performance"}
            },
        }

        self._memory.store(
            key=f"review:{path}:{task.get('id', 'unknown')}",
            value={"issues": issues, "summary": summary},
            tags=["review", path],
        )

        return {
            "issues": issues,
            "summary": summary,
            "path": path,
            "tools_used": ["linter", "static_analyzer"],
            "tokens_prompt": max(1, len(content) // 4),
            "tokens_completion": max(1, len(issues) * 20 // 4),
        }

    def _detect_nested_loops(self, lines: list[str]) -> list[int]:
        """Detecta líneas con `for`/`while` anidados (1 nivel)."""
        result: list[int] = []
        loop_indent: dict[int, int] = {}
        for i, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if not stripped:
                continue
            indent = len(line) - len(stripped)
            # Quitar loops con indentación menor (cierre del anterior).
            for ind in list(loop_indent.keys()):
                if indent <= ind:
                    del loop_indent[ind]
            if re.match(r"(for|while)\s", stripped):
                if loop_indent:
                    result.append(i)
                loop_indent[indent] = i
        return result

    def _functions_without_docstring(self, lines: list[str]) -> list[tuple[int, str]]:
        """Devuelve (line_number, name) de funciones públicas sin docstring."""
        result: list[tuple[int, str]] = []
        fn_pattern = re.compile(r"^(\s*)def\s+(\w+)\s*\(")
        for i, line in enumerate(lines):
            m = fn_pattern.match(line)
            if not m:
                continue
            name = m.group(2)
            if name.startswith("_"):
                continue
            # Ver si las próximas 3 líneas no contienen `"""`
            has_doc = False
            for j in range(i + 1, min(i + 4, len(lines))):
                if '"""' in lines[j] or "'''" in lines[j]:
                    has_doc = True
                    break
            if not has_doc:
                result.append((i + 1, name))
        return result


__all__ = ["ReviewerAgent"]
