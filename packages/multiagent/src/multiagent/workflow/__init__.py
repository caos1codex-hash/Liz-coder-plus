"""Workflow engine subpackage (Sprint 2.2).

Public API:

- `Workflow`, `Step` — modelos de datos.
- `DAG`, `ValidationResult` — grafo de dependencias con validación.
- `EventBus`, `Event` — bus pub/sub de eventos del workflow.
- `ContextManager` — contexto compartido entre agentes.
- `WorkflowEngine` — motor que ejecuta workflows respetando el DAG.
- `Scheduler` — scheduler inteligente con balanceo.
- `LoadBalancer` — distribución de carga por estado/CPU/RAM/cola.
- `WorkflowOrchestrator` — combina el AgentOrchestrator de Sprint 2.1
  con el WorkflowEngine.
"""

from __future__ import annotations

from .context import CodeSnippet, ContextManager, FileSnapshot, SharedMessage
from .dag import DAG, ValidationResult
from .engine import WorkflowEngine
from .enums import (
    FailoverAction,
    StepCriticality,
    StepStatus,
    WorkflowEvent,
    WorkflowStatus,
    can_transition_step,
)
from .event_bus import Event, EventBus, EventHandler, EventFilter
from .load_balancer import LoadBalancer
from .models import Step, Workflow
from .scheduler import Scheduler
from .orchestrator import WorkflowOrchestrator

__all__ = [
    "CodeSnippet",
    "ContextManager",
    "DAG",
    "Event",
    "EventBus",
    "EventHandler",
    "EventFilter",
    "FailoverAction",
    "FileSnapshot",
    "LoadBalancer",
    "Scheduler",
    "SharedMessage",
    "Step",
    "StepCriticality",
    "StepStatus",
    "ValidationResult",
    "Workflow",
    "WorkflowEngine",
    "WorkflowEvent",
    "WorkflowOrchestrator",
    "WorkflowStatus",
    "can_transition_step",
]
