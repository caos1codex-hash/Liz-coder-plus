"""Workflow enums (Sprint 2.2).

Estados y constantes para el motor de workflows. Mantengo estos enums
separados del módulo `multiagent.enums` para que la capa de workflows
sea una extensión independiente y pueda evolucionar sin romper la
API pública de Sprint 2.1.
"""

from __future__ import annotations

from enum import Enum


class WorkflowStatus(str, Enum):
    """Estados de un workflow.

    Lifecycle::

        PENDING → RUNNING ─┬─→ COMPLETED
                            ├─→ FAILED
                            ├─→ CANCELLED
                            └─→ PAUSED → RUNNING
    """

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    """Estados de un step dentro de un workflow.

    Lifecycle::

        PENDING → READY → RUNNING ─┬─→ COMPLETED
                                   ├─→ FAILED → READY (retry)
                                   ├─→ SKIPPED (dependencia fallida opcional)
                                   └─→ CANCELLED
    """

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


# Transiciones permitidas para steps.
_ALLOWED_STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({
        StepStatus.READY, StepStatus.CANCELLED, StepStatus.SKIPPED,
    }),
    StepStatus.READY: frozenset({
        StepStatus.RUNNING, StepStatus.CANCELLED, StepStatus.SKIPPED,
    }),
    StepStatus.RUNNING: frozenset({
        StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.CANCELLED,
    }),
    StepStatus.FAILED: frozenset({StepStatus.READY, StepStatus.CANCELLED}),  # retry
    StepStatus.COMPLETED: frozenset(),
    StepStatus.SKIPPED: frozenset(),
    StepStatus.CANCELLED: frozenset(),
}


def can_transition_step(current: StepStatus, target: StepStatus) -> bool:
    """Devuelve True si el step puede transicionar."""
    return target in _ALLOWED_STEP_TRANSITIONS.get(current, frozenset())


# Tipos de failover disponibles.
class FailoverAction(str, Enum):
    """Acciones que el engine puede tomar cuando un step falla."""

    RETRY = "retry"          # reintentar el mismo step
    REASSIGN = "reassign"    # asignar a otro agente
    SKIP = "skip"            # saltar (si es opcional)
    CANCEL = "cancel"        # cancelar todo el workflow
    NONE = "none"            # propagar el fallo sin acción especial


# Niveles de criticidad de un step (afectan el failover).
class StepCriticality(str, Enum):
    """Criticidad de un step dentro del workflow.

    - `REQUIRED`: si falla (sin retry exitoso), el workflow falla.
    - `OPTIONAL`: si falla, el workflow puede continuar.
    """

    REQUIRED = "required"
    OPTIONAL = "optional"


# Tipos de evento emitidos por el EventBus del workflow engine.
class WorkflowEvent(str, Enum):
    """Eventos canónicos del workflow engine."""

    WORKFLOW_CREATED = "workflow.created"
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_PAUSED = "workflow.paused"
    WORKFLOW_RESUMED = "workflow.resumed"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"
    WORKFLOW_CANCELLED = "workflow.cancelled"

    STEP_READY = "step.ready"
    STEP_STARTED = "step.started"
    STEP_FINISHED = "step.finished"
    STEP_FAILED = "step.failed"
    STEP_SKIPPED = "step.skipped"
    STEP_RETRY = "step.retry"
    STEP_CANCELLED = "step.cancelled"

    AGENT_BUSY = "agent.busy"
    AGENT_IDLE = "agent.idle"
    AGENT_REASSIGNED = "agent.reassigned"

    MEMORY_UPDATED = "memory.updated"
    TOOL_EXECUTED = "tool.executed"

    CONTEXT_SHARED = "context.shared"
    CONTEXT_TRANSFERRED = "context.transferred"

    METRIC_RECORDED = "metric.recorded"


__all__ = [
    "FailoverAction",
    "StepCriticality",
    "StepStatus",
    "WorkflowEvent",
    "WorkflowStatus",
    "can_transition_step",
]
