"""Modelos de datos para workflows y steps (Sprint 2.2).

Un `Workflow` es una colección ordenada de `Step`s que se ejecutan
respetando un DAG de dependencias. Cada step se asigna a un agente
y produce un resultado.

Campos del Workflow (según especificación Sprint 2.2):

    id, name, description, status, priority,
    created_at, updated_at, owner,
    steps, dependencies, retry_policy, timeout,
    context, metadata, result

Campos del Step:

    id, agent, action, input, output, status, retry, timeout,
    depends_on, started_at, finished_at, error
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..config import RetryPolicy
from ..enums import Priority
from .dag import DAG
from .enums import (
    StepCriticality,
    StepStatus,
    WorkflowStatus,
    can_transition_step,
)

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


# ----------------------------------------------------------------------
# Step
# ----------------------------------------------------------------------


@dataclass
class Step:
    """Un paso dentro de un workflow.

    Attributes:
        id:            UUID4 auto-generado (o pasado).
        name:          Nombre corto.
        agent:         Nombre del agente que debe ejecutarlo.
        action:        Acción concreta (op del agente).
        input:         Datos de entrada (payload).
        output:        Resultado producido por el agente.
        status:        StepStatus actual.
        retry:         Número de reintentos ya realizados.
        max_retries:   Máximo de reintentos permitidos.
        timeout:       Timeout en segundos (0 = sin timeout).
        depends_on:    Lista de step_ids que deben completarse antes.
        started_at:    ISO timestamp o None.
        finished_at:   ISO timestamp o None.
        error:         Mensaje de error si falló.
        criticality:   REQUIRED u OPTIONAL (afecta failover).
        agent_reassigned: Lista de agentes previamente asignados (para
                          evitar reasignar al mismo agente en failover).
        tokens_prompt: Tokens consumidos (prompt).
        tokens_completion: Tokens consumidos (completion).
        duration_ms:   Duración de la última ejecución.
        tools_used:    Herramientas usadas en la última ejecución.
        warnings:      Advertencias no fatales.
        files_modified: Lista de archivos modificados.
    """

    name: str
    agent: str = ""
    action: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    output: Any = None
    status: StepStatus = StepStatus.PENDING
    retry: int = 0
    max_retries: int = 3
    timeout: float = 0.0
    depends_on: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    criticality: StepCriticality = StepCriticality.REQUIRED
    agent_reassigned: list[str] = field(default_factory=list)
    tokens_prompt: int = 0
    tokens_completion: int = 0
    duration_ms: float = 0.0
    tools_used: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.CANCELLED
        )

    @property
    def is_completed(self) -> bool:
        return self.status == StepStatus.COMPLETED

    @property
    def is_failed(self) -> bool:
        return self.status == StepStatus.FAILED

    @property
    def is_running(self) -> bool:
        return self.status == StepStatus.RUNNING

    @property
    def can_retry(self) -> bool:
        return self.status == StepStatus.FAILED and self.retry < self.max_retries

    def transition_to(self, new_status: StepStatus) -> None:
        """Transiciona con validación."""
        if not can_transition_step(self.status, new_status):
            raise ValueError(
                f"Step '{self.name}' ({self.id[:8]}) cannot transition "
                f"{self.status.value} → {new_status.value}"
            )
        old = self.status
        self.status = new_status
        if new_status == StepStatus.RUNNING and self.started_at is None:
            self.started_at = _now_iso()
        if new_status in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.CANCELLED):
            self.finished_at = _now_iso()
        logger.debug(
            "Step '%s' %s → %s", self.name, old.value, new_status.value
        )

    # ------------------------------------------------------------------
    # Métricas
    # ------------------------------------------------------------------

    def record_execution(
        self,
        *,
        duration_ms: float = 0.0,
        tokens_prompt: int = 0,
        tokens_completion: int = 0,
        tools_used: list[str] | None = None,
        files_modified: list[str] | None = None,
    ) -> None:
        """Registra métricas de la última ejecución."""
        self.duration_ms = duration_ms
        self.tokens_prompt = tokens_prompt
        self.tokens_completion = tokens_completion
        if tools_used:
            self.tools_used = list(tools_used)
        if files_modified:
            self.files_modified = list(files_modified)

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "agent": self.agent,
            "action": self.action,
            "input": dict(self.input) if isinstance(self.input, dict) else self.input,
            "output": self.output if _jsonable(self.output) else str(self.output),
            "status": self.status.value,
            "retry": self.retry,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
            "depends_on": list(self.depends_on),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "criticality": self.criticality.value,
            "agent_reassigned": list(self.agent_reassigned),
            "tokens_prompt": self.tokens_prompt,
            "tokens_completion": self.tokens_completion,
            "duration_ms": self.duration_ms,
            "tools_used": list(self.tools_used),
            "warnings": list(self.warnings),
            "files_modified": list(self.files_modified),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Step:
        return cls(
            id=data.get("id") or str(uuid.uuid4()),
            name=data["name"],
            agent=data.get("agent", ""),
            action=data.get("action", ""),
            input=data.get("input", {}),
            output=data.get("output"),
            status=StepStatus(data.get("status", "pending")),
            retry=int(data.get("retry", 0)),
            max_retries=int(data.get("max_retries", 3)),
            timeout=float(data.get("timeout", 0.0)),
            depends_on=list(data.get("depends_on", [])),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            error=data.get("error"),
            criticality=StepCriticality(data.get("criticality", "required")),
            agent_reassigned=list(data.get("agent_reassigned", [])),
            tokens_prompt=int(data.get("tokens_prompt", 0)),
            tokens_completion=int(data.get("tokens_completion", 0)),
            duration_ms=float(data.get("duration_ms", 0.0)),
            tools_used=list(data.get("tools_used", [])),
            warnings=list(data.get("warnings", [])),
            files_modified=list(data.get("files_modified", [])),
        )


def _jsonable(v: Any) -> bool:
    import json
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


# ----------------------------------------------------------------------
# Workflow
# ----------------------------------------------------------------------


@dataclass
class Workflow:
    """Un workflow: colección de steps con dependencias tipo DAG.

    Attributes:
        id:            UUID4 auto-generado.
        name:          Nombre corto.
        description:   Descripción humana.
        status:        WorkflowStatus actual.
        priority:      Prioridad de scheduling.
        owner:         Agente o usuario que creó el workflow.
        steps:         Dict {step_id: Step}.
        dag:           DAG de dependencias (redundante con step.depends_on;
                       se mantiene en sincronía).
        retry_policy:  Política de reintentos por defecto para los steps.
        timeout:       Timeout global del workflow en segundos (0 = sin).
        context:       Contexto compartido (referencia a un ContextManager).
        metadata:      Metadata libre.
        result:        Resultado final del workflow.
        created_at:    ISO timestamp.
        updated_at:    ISO timestamp.
        started_at:    ISO timestamp o None.
        finished_at:   ISO timestamp o None.
    """

    name: str
    description: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: WorkflowStatus = WorkflowStatus.PENDING
    priority: Priority = Priority.NORMAL
    owner: str = ""
    steps: dict[str, Step] = field(default_factory=dict)
    dag: DAG = field(default_factory=DAG)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout: float = 0.0
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    finished_at: str | None = None

    def __post_init__(self) -> None:
        # Sincronizar el DAG con los steps declarados.
        self._sync_dag()

    # ------------------------------------------------------------------
    # Construcción
    # ------------------------------------------------------------------

    def add_step(
        self,
        step: Step,
        *,
        priority: int = 0,
    ) -> Step:
        """Añade un step al workflow y actualiza el DAG."""
        if step.id in self.steps:
            raise ValueError(f"Step '{step.id}' already in workflow")
        self.steps[step.id] = step
        self.dag.add_step(step.id, step.depends_on, priority=priority)
        self._touch()
        return step

    def remove_step(self, step_id: str) -> bool:
        """Elimina un step del workflow."""
        if step_id not in self.steps:
            return False
        # Quitar de dependencias de otros steps.
        for s in self.steps.values():
            if step_id in s.depends_on:
                s.depends_on.remove(step_id)
        del self.steps[step_id]
        self.dag.remove_step(step_id)
        self._touch()
        return True

    def add_dependency(self, step_id: str, depends_on: str) -> None:
        """Añade una dependencia `depends_on` a `step_id`."""
        if step_id not in self.steps:
            raise KeyError(f"Step '{step_id}' not in workflow")
        if depends_on not in self.steps:
            raise KeyError(f"Step '{depends_on}' not in workflow")
        if depends_on not in self.steps[step_id].depends_on:
            self.steps[step_id].depends_on.append(depends_on)
        self.dag.add_dependency(step_id, depends_on)
        self._touch()

    def _sync_dag(self) -> None:
        """Reconstruye el DAG a partir de los steps."""
        self.dag = DAG()
        for step_id, step in self.steps.items():
            self.dag.add_step(step_id, step.depends_on)

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED
        )

    @property
    def is_running(self) -> bool:
        return self.status == WorkflowStatus.RUNNING

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def completed_count(self) -> int:
        return sum(1 for s in self.steps.values() if s.is_completed)

    @property
    def failed_count(self) -> int:
        return sum(1 for s in self.steps.values() if s.is_failed)

    @property
    def progress(self) -> float:
        """Progreso 0..1."""
        if not self.steps:
            return 0.0
        return self.completed_count / len(self.steps)

    def transition_to(self, new_status: WorkflowStatus) -> None:
        """Transiciona el workflow (con tracking de timestamps)."""
        old = self.status
        self.status = new_status
        if new_status == WorkflowStatus.RUNNING and self.started_at is None:
            self.started_at = _now_iso()
        if new_status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
            self.finished_at = _now_iso()
        self._touch()
        logger.debug(
            "Workflow '%s' %s → %s", self.name, old.value, new_status.value
        )

    def _touch(self) -> None:
        self.updated_at = _now_iso()

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def validate(self):
        """Valida el DAG del workflow."""
        return self.dag.validate()

    def ready_steps(self) -> list[str]:
        """Steps listos para ejecutarse (deps completadas, no en curso ni terminal).

        Un step está "listo" si:
        - Está en PENDING (nunca se ha planificado) o READY (vuelve de un retry).
        - Todas sus dependencias están COMPLETED.
        - No está ya RUNNING, COMPLETED, FAILED, SKIPPED o CANCELLED.
        """
        completed = {s_id for s_id, s in self.steps.items() if s.is_completed}
        in_progress = {
            s_id for s_id, s in self.steps.items()
            if s.status == StepStatus.RUNNING
        }
        terminal = {
            s_id for s_id, s in self.steps.items()
            if s.is_terminal
        }
        return self.dag.ready_steps(completed, skip=in_progress | terminal)

    def next_step(self) -> Step | None:
        """Devuelve el siguiente step listo, o None."""
        ready = self.ready_steps()
        if not ready:
            return None
        return self.steps[ready[0]]

    def steps_by_status(self, status: StepStatus) -> list[Step]:
        return [s for s in self.steps.values() if s.status == status]

    # ------------------------------------------------------------------
    # Métricas
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """Métricas agregadas del workflow."""
        total_duration = sum(s.duration_ms for s in self.steps.values())
        total_tokens_p = sum(s.tokens_prompt for s in self.steps.values())
        total_tokens_c = sum(s.tokens_completion for s in self.steps.values())
        retries = sum(s.retry for s in self.steps.values())
        all_tools: dict[str, int] = {}
        all_files: set[str] = set()
        for s in self.steps.values():
            for t in s.tools_used:
                all_tools[t] = all_tools.get(t, 0) + 1
            all_files.update(s.files_modified)
        return {
            "workflow_id": self.id,
            "name": self.name,
            "status": self.status.value,
            "progress": round(self.progress, 4),
            "step_count": self.step_count,
            "completed": self.completed_count,
            "failed": self.failed_count,
            "total_duration_ms": round(total_duration, 2),
            "total_tokens_prompt": total_tokens_p,
            "total_tokens_completion": total_tokens_c,
            "total_retries": retries,
            "tools_used": all_tools,
            "files_modified": sorted(all_files),
        }

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "owner": self.owner,
            "steps": {s_id: s.to_dict() for s_id, s in self.steps.items()},
            "dag": self.dag.to_dict(),
            "retry_policy": {
                "max_attempts": self.retry_policy.max_attempts,
                "backoff_base": self.retry_policy.backoff_base,
                "backoff_cap": self.retry_policy.backoff_cap,
            },
            "timeout": self.timeout,
            "context": dict(self.context) if isinstance(self.context, dict) else {},
            "metadata": dict(self.metadata),
            "result": self.result if _jsonable(self.result) else str(self.result),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metrics": self.metrics(),
        }


__all__ = ["Step", "Workflow"]
