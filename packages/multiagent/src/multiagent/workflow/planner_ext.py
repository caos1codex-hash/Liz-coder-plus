"""Extensión del PlannerAgent para generar workflows (Sprint 2.2).

Añade una nueva operación `plan_workflow` que produce un `Workflow`
completo con steps, dependencias (DAG), estimaciones de tiempo y
costo (tokens), y prioridades.

La heurística mejora la de Sprint 2.1:

- Detecta el patrón (file_op, terminal, research, system, multi_step).
- Para patrones multi-step, genera el DAG con dependencias correctas.
- Estima tokens por step en base al tamaño del input.
- Estima duración basándose en herramientas usadas.
- Estima costo en "tokens equivalentes" (prompt + completion).

Mantiene 100% de compatibilidad con la API de Sprint 2.1: el método
`_execute_impl` sigue respondiendo a `payload['request']` con un plan
simple. La nueva funcionalidad se activa con `payload['op'] =
'plan_workflow'`.
"""

from __future__ import annotations

import re
from typing import Any

from ..enums import Priority
from .enums import StepCriticality
from .models import Step, Workflow

# Reutilizamos los patrones de Sprint 2.1.
_PATTERNS: list[tuple[str, str, list[str], list[str]]] = [
    # (id, regex, subtask_names, suggested_agents)
    ("file_op", r"\b(lee|leer|le\xe9|escrib\w*|guarda\w*|lista\w*)\w*\s+.+\s+archivo",
     ["read", "process", "write"], ["coder", "coder", "coder"]),
    ("terminal", r"\b(ejecut\w*|corr\w*|run|terminal|shell|bash|cmd)\b",
     ["validate", "execute", "respond"], ["planner", "terminal", "coder"]),
    ("research", r"\b(busca\w*|investiga\w*|analiza\w*)\b.*\b(y|luego|despu\xe9s)\b",
     ["gather", "analyze", "summarize"], ["research", "planner", "coder"]),
    ("system", r"\b(informaci\xf3n|info|estado)\s+del\s+sistema\b",
     ["query", "respond"], ["terminal", "coder"]),
    ("review", r"\b(revisa\w*|audita\w*|inspeccion\w*)\b.*\b(c\xf3digo|code|archivo)\b",
     ["read", "review", "report"], ["coder", "reviewer", "coder"]),
]

_COMPILED: list[tuple[str, re.Pattern[str], list[str], list[str]]] = [
    (pid, re.compile(p, re.IGNORECASE), subs, agents)
    for pid, p, subs, agents in _PATTERNS
]

_DIFFICULTY_HINTS = {
    "file_op": 2,
    "terminal": 3,
    "research": 4,
    "system": 1,
    "review": 3,
}

# Estimación por defecto de tokens y duración por step type.
_STEP_ESTIMATES = {
    "read":      {"tokens_prompt": 200, "tokens_completion": 300, "duration_ms": 50},
    "process":   {"tokens_prompt": 400, "tokens_completion": 600, "duration_ms": 100},
    "write":     {"tokens_prompt": 300, "tokens_completion": 800, "duration_ms": 150},
    "validate":  {"tokens_prompt": 100, "tokens_completion": 100, "duration_ms": 30},
    "execute":   {"tokens_prompt": 100, "tokens_completion": 200, "duration_ms": 500},
    "respond":   {"tokens_prompt": 200, "tokens_completion": 300, "duration_ms": 80},
    "gather":    {"tokens_prompt": 300, "tokens_completion": 500, "duration_ms": 800},
    "analyze":   {"tokens_prompt": 500, "tokens_completion": 700, "duration_ms": 200},
    "summarize": {"tokens_prompt": 400, "tokens_completion": 400, "duration_ms": 100},
    "query":     {"tokens_prompt": 100, "tokens_completion": 200, "duration_ms": 50},
    "review":    {"tokens_prompt": 600, "tokens_completion": 400, "duration_ms": 120},
    "report":    {"tokens_prompt": 300, "tokens_completion": 500, "duration_ms": 80},
    "handle":    {"tokens_prompt": 200, "tokens_completion": 300, "duration_ms": 50},
}


def _estimate_step(name: str) -> dict[str, int]:
    """Devuelve estimaciones {tokens_prompt, tokens_completion, duration_ms}."""
    return _STEP_ESTIMATES.get(name, _STEP_ESTIMATES["handle"])


def _build_workflow_from_pattern(
    pattern_id: str,
    subtask_names: list[str],
    agents: list[str],
    request: str,
    priority: Priority = Priority.NORMAL,
) -> Workflow:
    """Construye un Workflow con DAG a partir de un patrón."""
    wf = Workflow(
        name=f"{pattern_id}_workflow",
        description=f"Auto-generated workflow for pattern '{pattern_id}': {request[:120]}",
        priority=priority,
        metadata={"pattern": pattern_id, "request": request},
    )
    prev_step_id: str | None = None
    for i, (name, agent) in enumerate(zip(subtask_names, agents, strict=True)):
        est = _estimate_step(name)
        step = Step(
            name=name,
            agent=agent,
            action=name,
            input={"request": request, "step_index": i, "pattern": pattern_id},
            depends_on=[prev_step_id] if prev_step_id else [],
            timeout=60.0,
            criticality=StepCriticality.REQUIRED,
        )
        # Estimaciones en metadata para que el planner las exponga.
        step.metadata = est  # type: ignore[attr-defined]
        wf.add_step(step, priority=i)
        prev_step_id = step.id
    return wf


def _estimate_workflow(wf: Workflow) -> dict[str, Any]:
    """Calcula estimaciones totales de un workflow."""
    total_tokens_p = 0
    total_tokens_c = 0
    total_duration_ms = 0.0
    for step in wf.steps.values():
        est = step.metadata if hasattr(step, "metadata") and isinstance(step.metadata, dict) else _estimate_step(step.name)  # type: ignore[attr-defined]
        total_tokens_p += est.get("tokens_prompt", 0)
        total_tokens_c += est.get("tokens_completion", 0)
        total_duration_ms += est.get("duration_ms", 0)
    # El parallelismo efectivo reduce la duración si hay steps paralelos.
    # Para una cadena lineal, parallelism = 1.
    # Para un DAG con N ramas paralelas, parallelism = N.
    # Aquí estimamos parallelism como (steps / critical_path_length).
    try:
        # Heurística: critical_path = número de niveles en el DAG.
        levels = _count_levels(wf)
        parallelism = len(wf.steps) / max(levels, 1)
    except Exception:  # noqa: BLE001
        parallelism = 1.0
    estimated_duration_ms = total_duration_ms / max(parallelism, 1.0)
    return {
        "total_tokens_prompt": total_tokens_p,
        "total_tokens_completion": total_tokens_c,
        "total_tokens": total_tokens_p + total_tokens_c,
        "total_duration_ms_sequential": round(total_duration_ms, 2),
        "estimated_duration_ms_parallel": round(estimated_duration_ms, 2),
        "parallelism_factor": round(parallelism, 2),
        "step_count": len(wf.steps),
    }


def _count_levels(wf: Workflow) -> int:
    """Cuenta el número de niveles del DAG (longitud del critical path)."""
    if not wf.steps:
        return 0
    # BFS por niveles.
    completed: set[str] = set()
    # Nodos sin dependencias.
    current_level = [
        sid for sid, step in wf.steps.items()
        if not step.depends_on
    ]
    levels = 0
    while current_level:
        levels += 1
        completed.update(current_level)
        next_level: list[str] = []
        for sid, step in wf.steps.items():
            if sid in completed:
                continue
            if all(d in completed for d in step.depends_on if d in wf.steps):
                next_level.append(sid)
        current_level = next_level
    return levels


def _match_pattern(request: str) -> tuple[str, list[str], list[str]] | None:
    """Devuelve (pattern_id, subtask_names, agents) o None."""
    request_lower = request.lower()
    for pid, pattern, subs, agents in _COMPILED:
        if pattern.search(request_lower):
            return pid, subs, agents
    return None


def plan_workflow(
    request: str,
    *,
    priority: Priority = Priority.NORMAL,
) -> tuple[Workflow, dict[str, Any]]:
    """Genera un Workflow completo con DAG y estimaciones.

    Devuelve:
        (workflow, plan_metadata)

    `plan_metadata` incluye:
        - strategy: patrón detectado.
        - rationale: explicación.
        - estimated_difficulty: 1..5.
        - estimates: tokens y duración.
    """
    match = _match_pattern(request)
    if match is None:
        # Fallback: workflow de 1 step.
        wf = Workflow(
            name="single_workflow",
            description=f"Single-step workflow: {request[:120]}",
            priority=priority,
            metadata={"strategy": "single", "request": request},
        )
        step = Step(
            name="handle",
            agent="planner",
            action="handle",
            input={"request": request},
        )
        est = _estimate_step("handle")
        step.metadata = est  # type: ignore[attr-defined]
        wf.add_step(step)
        estimates = _estimate_workflow(wf)
        return wf, {
            "strategy": "single",
            "rationale": "No specific pattern matched; single-step workflow.",
            "estimated_difficulty": 1,
            "estimates": estimates,
        }

    pid, subs, agents = match
    wf = _build_workflow_from_pattern(pid, subs, agents, request, priority)
    estimates = _estimate_workflow(wf)
    return wf, {
        "strategy": pid,
        "rationale": f"Pattern '{pid}' matched; producing {len(subs)}-step workflow.",
        "estimated_difficulty": _DIFFICULTY_HINTS[pid],
        "estimates": estimates,
    }


__all__ = [
    "plan_workflow",
]
