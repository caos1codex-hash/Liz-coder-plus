"""PlannerAgent — divide tareas en subtareas y prioriza (Sprint 2.1 + 2.2).

Responsable de:

- dividir tareas
- crear subtareas
- estimar dificultad
- priorizar ejecución
- **(Sprint 2.2)** crear workflows completos con DAG
- **(Sprint 2.2)** estimar tiempo y costo (tokens)

Estrategia: heurística basada en el contenido del payload. Si el
payload trae `text` o `request`, se inspecciona para detectar
patrones (file ops, terminal, research, etc.) y producir un plan.

Sprint 2.2: con `payload['op'] = 'plan_workflow'`, el agente genera
un `Workflow` completo con DAG, dependencias y estimaciones.
"""

from __future__ import annotations

import re
from typing import Any

from ..base import BaseAgent
from ..enums import Permission, Priority
from ..workflow.planner_ext import plan_workflow


_PATTERNS: list[tuple[str, str, list[str]]] = [
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

    Sprint 2.2: con `payload['op'] = 'plan_workflow'`, devuelve además
    un `Workflow` completo con DAG y estimaciones en `result['workflow']`.
    """

    default_permissions = frozenset({Permission.MEMORY_READ, Permission.MEMORY_WRITE})
    default_objectives = ["plan", "decompose", "estimate", "prioritize", "workflow"]
    default_tools = ["planner"]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="planner", description="Divides tasks into subtasks, builds workflows with DAG, estimates time and cost", **kwargs)

    async def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("payload", {}) or {}
        op = (payload.get("op") or "plan").lower()
        request = (
            payload.get("request")
            or payload.get("text")
            or task.get("name")
            or ""
        )

        # Sprint 2.2: generar workflow completo.
        if op == "plan_workflow":
            priority_str = payload.get("priority", "normal")
            try:
                priority = Priority(priority_str)
            except ValueError:
                priority = Priority.NORMAL
            wf, plan_meta = plan_workflow(str(request), priority=priority)
            # Persistir.
            self._memory.store(
                key=f"workflow_plan:{task.get('id', 'unknown')}",
                value={
                    "workflow_id": wf.id,
                    "workflow_name": wf.name,
                    "plan": plan_meta,
                    "steps": [s.to_dict() for s in wf.steps.values()],
                },
                tags=["workflow_plan", plan_meta["strategy"]],
            )
            return {
                "op": "plan_workflow",
                "workflow": wf.to_dict(),
                "plan": plan_meta,
                "request": request,
                "tools_used": ["planner", "dag_builder"],
                "tokens_prompt": max(1, len(request) // 4),
                "tokens_completion": max(
                    1,
                    sum(len(s.name) for s in wf.steps.values()) // 4 + 50,
                ),
            }

        # Sprint 2.1: plan simple (sin workflow).
        plan = self._build_plan(str(request))
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
