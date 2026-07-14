"""Pipeline Models — TaskRequest, TaskResult, WorkflowStepDef (Sprint 2.4).

Modelos estandarizados para la comunicación entre el TaskPipelineEngine
y los agentes resueltos vía CapabilityResolver.

TaskRequest: Lo que el engine envía a un agente.
TaskResult:  Lo que el agente devuelve al engine.
WorkflowStepDef: Definición de un step con capability_required (en lugar
                 de agent directo).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# TaskRequest
# ----------------------------------------------------------------------


@dataclass
class TaskRequest:
    """Solicitud de tarea estandarizada para el pipeline.

    El TaskPipelineEngine crea un TaskRequest por cada WorkflowStep
    y lo envía al agente resuelto vía CapabilityResolver.

    Attributes:
        task_id:      UUID único de la tarea.
        description:  Descripción humana de la tarea.
        capability:   Capability requerida (e.g. "code.generate").
        context:      Contexto compartido del workflow (output de steps previos).
        metadata:     Metadata libre (prioridad, workflow_id, step_id, etc.).
        payload:      Datos adicionales para el agente.
    """

    description: str
    capability: str
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_agent_task(self) -> dict[str, Any]:
        """Convierte al formato que espera BaseAgent.execute(task)."""
        return {
            "id": self.task_id,
            "name": self.description,
            "description": self.description,
            "payload": {**self.payload, "capability": self.capability},
            "priority": self.metadata.get("priority", 0),
            "workflow_id": self.metadata.get("workflow_id", ""),
            "step_id": self.metadata.get("step_id", ""),
            "context": self.context,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "capability": self.capability,
            "context": dict(self.context),
            "metadata": dict(self.metadata),
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskRequest:
        return cls(
            task_id=data.get("task_id", str(uuid.uuid4())),
            description=data.get("description", ""),
            capability=data.get("capability", ""),
            context=data.get("context", {}),
            metadata=data.get("metadata", {}),
            payload=data.get("payload", {}),
        )


# ----------------------------------------------------------------------
# TaskResult
# ----------------------------------------------------------------------


@dataclass
class TaskResult:
    """Resultado de ejecutar una tarea vía el pipeline.

    Cada WorkflowStep produce exactamente un TaskResult.

    Attributes:
        success:        True si la ejecución fue exitosa.
        output:         Salida del agente (resultado de la tarea).
        error:          Mensaje de error si falló.
        execution_time: Duración en milisegundos.
        agent_id:       ID del agente que ejecutó la tarea.
        agent_name:     Nombre del agente que ejecutó la tarea.
        metrics:        Métricas adicionales (tokens, tools, etc.).
        task_id:        ID de la tarea asociada.
        step_id:        ID del step que generó esta tarea.
    """

    success: bool = False
    output: Any = None
    error: str | None = None
    execution_time: float = 0.0
    agent_id: str = ""
    agent_name: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    task_id: str = ""
    step_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "success": self.success,
            "output": self.output if _jsonable(self.output) else str(self.output),
            "error": self.error,
            "execution_time": round(self.execution_time, 2),
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "metrics": dict(self.metrics),
            "task_id": self.task_id,
            "step_id": self.step_id,
        }
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskResult:
        return cls(
            success=data.get("success", False),
            output=data.get("output"),
            error=data.get("error"),
            execution_time=float(data.get("execution_time", 0.0)),
            agent_id=data.get("agent_id", ""),
            agent_name=data.get("agent_name", ""),
            metrics=data.get("metrics", {}),
            task_id=data.get("task_id", ""),
            step_id=data.get("step_id", ""),
        )

    @classmethod
    def ok(cls, *, output: Any = None, execution_time: float = 0.0,
           agent_id: str = "", agent_name: str = "", metrics: dict[str, Any] | None = None,
           task_id: str = "", step_id: str = "") -> TaskResult:
        """Convenience: crea un resultado exitoso."""
        return cls(
            success=True, output=output, execution_time=execution_time,
            agent_id=agent_id, agent_name=agent_name,
            metrics=metrics or {}, task_id=task_id, step_id=step_id,
        )

    @classmethod
    def fail(cls, *, error: str, execution_time: float = 0.0,
             agent_id: str = "", agent_name: str = "",
             task_id: str = "", step_id: str = "") -> TaskResult:
        """Convenience: crea un resultado fallido."""
        return cls(
            success=False, error=error, execution_time=execution_time,
            agent_id=agent_id, agent_name=agent_name,
            task_id=task_id, step_id=step_id,
        )


def _jsonable(v: Any) -> bool:
    import json
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


# ----------------------------------------------------------------------
# WorkflowStepDef
# ----------------------------------------------------------------------


@dataclass
class WorkflowStepDef:
    """Definición de un step para el TaskPipelineEngine.

    A diferencia del Step de Sprint 2.2 (que usa agent: str),
    WorkflowStepDef usa capability_required para resolución dinámica.

    Attributes:
        name:                Nombre corto del step.
        description:         Descripción humana.
        capability_required: Capability que el agente debe proveer.
        agent_id:            ID opcional del agente (si se quiere forzar).
        dependencies:        Lista de step_ids que deben completarse antes.
        timeout:             Timeout en segundos (0 = sin timeout).
        retry_policy:        Política de reintento para este step.
        metadata:            Metadata libre.
    """

    name: str
    capability_required: str
    description: str = ""
    agent_id: str | None = None
    dependencies: list[str] = field(default_factory=list)
    timeout: float = 0.0
    retry_policy: StepRetryPolicy | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "capability_required": self.capability_required,
            "agent_id": self.agent_id,
            "dependencies": list(self.dependencies),
            "timeout": self.timeout,
            "retry_policy": self.retry_policy.to_dict() if self.retry_policy else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStepDef:
        rp_data = data.get("retry_policy")
        retry_policy = StepRetryPolicy.from_dict(rp_data) if rp_data else None
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            capability_required=data.get("capability_required", ""),
            agent_id=data.get("agent_id"),
            dependencies=list(data.get("dependencies", [])),
            timeout=float(data.get("timeout", 0.0)),
            retry_policy=retry_policy,
            metadata=data.get("metadata", {}),
        )


# ----------------------------------------------------------------------
# StepRetryPolicy
# ----------------------------------------------------------------------


@dataclass
class StepRetryPolicy:
    """Política de reintento a nivel de step (Sprint 2.4).

    Extiende la RetryPolicy de Sprint 2.1 con retry_on_errors.

    Attributes:
        max_attempts:     Máximo de intentos (incluye el primero).
        retry_delay:      Delay base en segundos entre reintentos.
        retry_on_errors:  Lista de strings de error que triggerean retry.
                          Si está vacía, se reintenta en cualquier error.
    """

    max_attempts: int = 3
    retry_delay: float = 1.0
    retry_on_errors: list[str] = field(default_factory=list)

    def should_retry(self, error: str | None) -> bool:
        """Determina si se debe reintentar dado un error.

        Si retry_on_errors está vacío, se reintenta en cualquier error.
        Si retry_on_errors tiene elementos, sólo se reintenta si el error
        contiene alguno de los strings.
        """
        if not error:
            return False
        if not self.retry_on_errors:
            return True
        return any(e.lower() in error.lower() for e in self.retry_on_errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "retry_delay": self.retry_delay,
            "retry_on_errors": list(self.retry_on_errors),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepRetryPolicy:
        return cls(
            max_attempts=int(data.get("max_attempts", 3)),
            retry_delay=float(data.get("retry_delay", 1.0)),
            retry_on_errors=list(data.get("retry_on_errors", [])),
        )


# ----------------------------------------------------------------------
# PipelineWorkflow — definición completa de un workflow para el pipeline
# ----------------------------------------------------------------------


@dataclass
class PipelineWorkflow:
    """Definición completa de un workflow para el TaskPipelineEngine.

    Attributes:
        name:        Nombre del workflow.
        description: Descripción.
        steps:       Lista de WorkflowStepDef (se les asigna IDs automáticos).
        metadata:    Metadata libre.
        timeout:     Timeout global en segundos (0 = sin timeout).
    """

    name: str
    steps: list[WorkflowStepDef] = field(default_factory=list)
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timeout: float = 0.0
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "created"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
            "metadata": dict(self.metadata),
            "timeout": self.timeout,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


__all__ = [
    "PipelineWorkflow",
    "StepRetryPolicy",
    "TaskRequest",
    "TaskResult",
    "WorkflowStepDef",
]