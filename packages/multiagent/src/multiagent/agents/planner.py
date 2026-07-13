"""PlannerAgent — divide tareas en subtareas y prioriza (Sprint 2.1).

Responsable de:

- dividir tareas
- crear subtareas
- estimar dificultad
- priorizar ejecución

Estrategia: heurística basada en el contenido del payload. Si el
payload trae `text` o `request`, se inspecciona para detectar
patrones (file ops, terminal, research, etc.) y producir un plan.
"""

from __future__ import annotations

import re
from typing import Any

from ..base import BaseAgent
from ..enums import Permission, Priority


_PATTERNS: list[tuple[str, list[str], list[str]]] = [
    # (id, regex, subtask_names)
    ("file_op", r"\b(lee|leer|le\xe9|escrib\w*|guarda\w*|lista\w*)\w*\s+.+\s+archivo", ["read", "process", "write"]),
    ("terminal", r"\b(ejecut\w*|corr\w*|run|terminal|shell|bash|cmd)\b", ["validate", "execute", "respond"]),
    ("research", r"\b(busca\w*|investiga\w*|analiza\w*)\b.*\b(y|luego|despu\xe9s)\b", ["gather", "analyze", "summarize"]),
    ("system", r"\b(informaci\xf3n|info|estado)\s+del\s+sistema\b", ["query", "respond"]),
]

_COMPILED: list[tuple[str, re.Pattern[str], list[str]]] = [
    (pid, re.compile(p, re.IGNORECASE), subs)
    for pid, p, subs in _PATTERNS
]

_DIFFICULTY_HINTS = {
    "file_op": 2,
    "terminal": 3,
    "research": 4,
    "system": 1,
}


class PlannerAgent(BaseAgent):
    """Agente planificador.

    Recibe una tarea con `payload['request']` o `payload['text']` y
    devuelve un plan en `result['plan']` con una lista de subtareas,
    cada una con `name`, `description`, `priority` y `difficulty`.
    """

    default_permissions = frozenset({Permission.MEMORY_READ, Permission.MEMORY_WRITE})
    default_objectives = ["plan", "decompose", "estimate", "prioritize"]
    default_tools = ["planner"]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="planner", description="Divide tasks into subtasks and prioritizes", **kwargs)

    async def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        request = (
            task.get("payload", {}).get("request")
            or task.get("payload", {}).get("text")
            or task.get("name")
            or ""
        )
        plan = self._build_plan(str(request))
        # Persistir en memoria larga para consulta futura.
        self._memory.store(
            key=f"plan:{task.get('id', 'unknown')}",
            value=plan,
            tags=["plan"],
        )
        return {
            "plan": plan,
            "request": request,
            "tools_used": ["planner"],
            "tokens_prompt": max(1, len(request) // 4),
            "tokens_completion": max(1, sum(len(s["description"]) for s in plan["subtasks"]) // 4),
        }

    def _build_plan(self, request: str) -> dict[str, Any]:
        """Construye un plan heurístico a partir del request."""
        request_lower = request.lower()
        for pid, pattern, subtask_names in _COMPILED:
            if pattern.search(request_lower):
                subtasks = [
                    {
                        "name": name,
                        "description": f"{name} step for {pid} request",
                        "priority": Priority.NORMAL.value,
                        "difficulty": _DIFFICULTY_HINTS[pid],
                        "depends_on": (
                            [subtask_names[i - 1]] if i > 0 else []
                        ),
                    }
                    for i, name in enumerate(subtask_names)
                ]
                return {
                    "strategy": pid,
                    "request": request,
                    "subtasks": subtasks,
                    "estimated_difficulty": _DIFFICULTY_HINTS[pid],
                    "rationale": f"Pattern '{pid}' matched; producing {len(subtasks)} subtasks.",
                }
        # Fallback
        return {
            "strategy": "single",
            "request": request,
            "subtasks": [
                {
                    "name": "handle",
                    "description": "Handle the request directly.",
                    "priority": Priority.NORMAL.value,
                    "difficulty": 1,
                    "depends_on": [],
                }
            ],
            "estimated_difficulty": 1,
            "rationale": "No specific pattern matched; single subtask.",
        }


__all__ = ["PlannerAgent"]
