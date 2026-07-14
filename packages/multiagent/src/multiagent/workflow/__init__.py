"""Workflow engine subpackage (Sprint 2.2 / 2.4).

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

Sprint 2.4 additions:

- `TaskPipelineEngine` — motor basado en capacidades (via AgentRegistry).
- `TaskRequest`, `TaskResult` — modelos estandarizados de comunicación.
- `WorkflowStepDef`, `StepRetryPolicy` — definición de steps con capabilities.
- `PipelineWorkflow`, `PipelineExecutionResult` — workflow y resultado.
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
from .pipeline_models import (
    PipelineWorkflow,
    StepRetryPolicy,
    TaskRequest,
    TaskResult,
    WorkflowStepDef,
)
from .task_pipeline_engine import PipelineExecutionResult, TaskPipelineEngine

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
    "PipelineExecutionResult",
    "PipelineWorkflow",
    "Scheduler",
    "SharedMessage",
    "Step",
    "StepCriticality",
    "StepRetryPolicy",
    "StepStatus",
    "TaskPipelineEngine",
    "TaskRequest",
    "TaskResult",
    "ValidationResult",
    "Workflow",
    "WorkflowEngine",
    "WorkflowEvent",
    "WorkflowOrchestrator",
    "WorkflowStatus",
    "WorkflowStepDef",
    "can_transition_step",
]
