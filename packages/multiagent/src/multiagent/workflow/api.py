"""API REST para el workflow engine (Sprint 2.2).

Endpoints:

- `GET /workflows`             — lista workflows registrados.
- `POST /workflows`            — crea y ejecuta un workflow.
- `GET /workflows/{id}`        — detalle de un workflow.
- `POST /workflows/{id}/cancel`— cancela un workflow.
- `POST /workflows/{id}/pause` — pausa un workflow.
- `POST /workflows/{id}/resume`— reanuda un workflow.
- `GET /workflows/{id}/events` — eventos de un workflow.
- `GET /steps`                 — lista todos los steps (con filtros).
- `GET /events`                — stream de eventos recientes.
- `GET /metrics/workflows`     — métricas agregadas.

Diseño:

- Stateless en cuanto a agentes: la API recibe el `WorkflowOrchestrator`
  ya configurado al construir el router.
- La creación de workflows acepta JSON y construye el objeto `Workflow`.
- La ejecución es **background**: se lanza el workflow y se devuelve
  inmediatamente el ID.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, BackgroundTasks
    from pydantic import BaseModel, Field
    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment,misc]
    BackgroundTasks = None  # type: ignore[assignment,misc]
    BaseModel = object  # type: ignore[assignment,misc]
    Field = None  # type: ignore[assignment,misc]

from .enums import StepCriticality
from .models import Step, Workflow
from .observability import MetricsCollector
from .orchestrator import WorkflowOrchestrator

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Pydantic models (sólo si FastAPI está disponible)
# ----------------------------------------------------------------------


if FASTAPI_AVAILABLE:

    class StepCreate(BaseModel):
        name: str
        agent: str = ""
        action: str = ""
        input: dict[str, Any] = Field(default_factory=dict)
        depends_on: list[str] = Field(default_factory=list)
        timeout: float = 60.0
        max_retries: int = 3
        criticality: str = "required"

    class WorkflowCreate(BaseModel):
        name: str
        description: str = ""
        priority: str = "normal"
        owner: str = ""
        steps: list[StepCreate] = Field(default_factory=list)
        timeout: float = 0.0
        metadata: dict[str, Any] = Field(default_factory=dict)

    class WorkflowResponse(BaseModel):
        id: str
        name: str
        description: str
        status: str
        priority: str
        owner: str
        step_count: int
        completed_count: int
        failed_count: int
        progress: float
        created_at: str
        updated_at: str
        started_at: str | None
        finished_at: str | None

    class StepResponse(BaseModel):
        id: str
        name: str
        agent: str
        action: str
        status: str
        retry: int
        started_at: str | None
        finished_at: str | None
        duration_ms: float
        error: str | None
        workflow_id: str | None = None

    class EventResponse(BaseModel):
        id: str
        type: str
        timestamp: str
        source: str
        payload: dict[str, Any]
        correlation_id: str | None
        sequence: int


# ----------------------------------------------------------------------
# Router factory
# ----------------------------------------------------------------------


def create_workflow_router(
    orchestrator: WorkflowOrchestrator,
    metrics_collector: MetricsCollector | None = None,
):
    """Crea un APIRouter de FastAPI para el workflow engine.

    Args:
        orchestrator:        WorkflowOrchestrator ya configurado.
        metrics_collector:   MetricsCollector opcional (para /metrics/workflows).
    """
    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            "FastAPI is not installed. Install with: pip install fastapi"
        )

    router = APIRouter(prefix="/workflows", tags=["workflows"])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _to_workflow_response(wf: Workflow) -> "WorkflowResponse":
        return WorkflowResponse(
            id=wf.id,
            name=wf.name,
            description=wf.description,
            status=wf.status.value,
            priority=wf.priority.value,
            owner=wf.owner,
            step_count=wf.step_count,
            completed_count=wf.completed_count,
            failed_count=wf.failed_count,
            progress=round(wf.progress, 4),
            created_at=wf.created_at,
            updated_at=wf.updated_at,
            started_at=wf.started_at,
            finished_at=wf.finished_at,
        )

    def _to_step_response(step: Step, workflow_id: str | None = None) -> "StepResponse":
        return StepResponse(
            id=step.id,
            name=step.name,
            agent=step.agent,
            action=step.action,
            status=step.status.value,
            retry=step.retry,
            started_at=step.started_at,
            finished_at=step.finished_at,
            duration_ms=step.duration_ms,
            error=step.error,
            workflow_id=workflow_id,
        )

    # ------------------------------------------------------------------
    # GET /workflows
    # ------------------------------------------------------------------

    @router.get("", response_model=list[WorkflowResponse])
    async def list_workflows(
        status: str | None = None,
        limit: int = 100,
    ) -> list[WorkflowResponse]:
        """Lista workflows registrados."""
        result: list[WorkflowResponse] = []
        for wf_id in orchestrator.list_workflows():
            wf = orchestrator.get_workflow(wf_id)
            if wf is None:
                continue
            if status is not None and wf.status.value != status:
                continue
            result.append(_to_workflow_response(wf))
            if len(result) >= limit:
                break
        return result

    # ------------------------------------------------------------------
    # POST /workflows
    # ------------------------------------------------------------------

    @router.post("", response_model=WorkflowResponse, status_code=201)
    async def create_workflow(
        body: WorkflowCreate,
        background_tasks: BackgroundTasks,
    ) -> WorkflowResponse:
        """Crea y ejecuta un workflow en background."""
        from ..enums import Priority

        wf = Workflow(
            name=body.name,
            description=body.description,
            priority=Priority(body.priority),
            owner=body.owner,
            timeout=body.timeout,
            metadata=body.metadata,
        )
        for step_data in body.steps:
            step = Step(
                name=step_data.name,
                agent=step_data.agent,
                action=step_data.action,
                input=step_data.input,
                depends_on=step_data.depends_on,
                timeout=step_data.timeout,
                max_retries=step_data.max_retries,
                criticality=StepCriticality(step_data.criticality),
            )
            wf.add_step(step)

        # Validar DAG.
        validation = wf.validate()
        if not validation.ok:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid workflow DAG: {validation.to_dict()}",
            )

        # Registrar (sin bloquear).
        await orchestrator.submit_workflow(wf)

        # Lanzar ejecución en background.
        async def _run():
            try:
                await orchestrator.run_workflow(wf)
            except Exception:  # noqa: BLE001
                logger.exception("Background workflow execution failed")

        background_tasks.add_task(_run)
        return _to_workflow_response(wf)

    # ------------------------------------------------------------------
    # GET /workflows/{id}
    # ------------------------------------------------------------------

    @router.get("/{workflow_id}", response_model=WorkflowResponse)
    async def get_workflow(workflow_id: str) -> WorkflowResponse:
        wf = orchestrator.get_workflow(workflow_id)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return _to_workflow_response(wf)

    # ------------------------------------------------------------------
    # GET /workflows/{id}/steps
    # ------------------------------------------------------------------

    @router.get("/{workflow_id}/steps", response_model=list[StepResponse])
    async def get_workflow_steps(
        workflow_id: str,
        status: str | None = None,
    ) -> list[StepResponse]:
        wf = orchestrator.get_workflow(workflow_id)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        result: list[StepResponse] = []
        for step in wf.steps.values():
            if status is not None and step.status.value != status:
                continue
            result.append(_to_step_response(step, workflow_id=workflow_id))
        return result

    # ------------------------------------------------------------------
    # POST /workflows/{id}/cancel | pause | resume
    # ------------------------------------------------------------------

    @router.post("/{workflow_id}/cancel")
    async def cancel_workflow(workflow_id: str, reason: str = "") -> dict[str, Any]:
        ok = await orchestrator.cancel_workflow(workflow_id, reason=reason)
        if not ok:
            raise HTTPException(status_code=404, detail="Workflow not found or already terminal")
        return {"workflow_id": workflow_id, "status": "cancelled"}

    @router.post("/{workflow_id}/pause")
    async def pause_workflow(workflow_id: str) -> dict[str, Any]:
        ok = await orchestrator.pause_workflow(workflow_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Workflow not found or not running")
        return {"workflow_id": workflow_id, "status": "paused"}

    @router.post("/{workflow_id}/resume")
    async def resume_workflow(workflow_id: str) -> dict[str, Any]:
        ok = await orchestrator.resume_workflow(workflow_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Workflow not found or not paused")
        return {"workflow_id": workflow_id, "status": "running"}

    # ------------------------------------------------------------------
    # GET /workflows/{id}/events
    # ------------------------------------------------------------------

    @router.get("/{workflow_id}/events", response_model=list[EventResponse])
    async def get_workflow_events(
        workflow_id: str,
        limit: int = 100,
    ) -> list[EventResponse]:
        events = orchestrator.workflow_events(workflow_id, limit=limit)
        return [
            EventResponse(**e) for e in events
        ]

    # ------------------------------------------------------------------
    # GET /workflows/{id}/metrics
    # ------------------------------------------------------------------

    @router.get("/{workflow_id}/metrics")
    async def get_workflow_metrics(workflow_id: str) -> dict[str, Any]:
        m = orchestrator.workflow_metrics(workflow_id)
        if m is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return m

    return router


def create_misc_router(
    orchestrator: WorkflowOrchestrator,
    metrics_collector: MetricsCollector | None = None,
):
    """Crea el router para /steps, /events y /metrics/workflows."""
    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            "FastAPI is not installed. Install with: pip install fastapi"
        )

    router = APIRouter(tags=["observability"])

    # ------------------------------------------------------------------
    # GET /steps
    # ------------------------------------------------------------------

    @router.get("/steps", response_model=list)
    async def list_steps(
        status: str | None = None,
        agent: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lista steps de todos los workflows."""
        result: list[dict[str, Any]] = []
        for wf_id in orchestrator.list_workflows():
            wf = orchestrator.get_workflow(wf_id)
            if wf is None:
                continue
            for step in wf.steps.values():
                if status is not None and step.status.value != status:
                    continue
                if agent is not None and step.agent != agent:
                    continue
                result.append({
                    **step.to_dict(),
                    "workflow_id": wf_id,
                })
                if len(result) >= limit:
                    return result
        return result

    # ------------------------------------------------------------------
    # GET /events
    # ------------------------------------------------------------------

    @router.get("/events", response_model=list)
    async def list_events(
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lista eventos recientes del EventBus."""
        events = orchestrator.event_bus.history(limit=limit)
        result: list[dict[str, Any]] = []
        for e in events:
            if event_type is not None and e.type_str != event_type:
                continue
            result.append(e.to_dict())
        return result

    # ------------------------------------------------------------------
    # GET /metrics/workflows
    # ------------------------------------------------------------------

    @router.get("/metrics/workflows")
    async def get_workflow_metrics() -> dict[str, Any]:
        """Métricas agregadas de workflows."""
        if metrics_collector is not None:
            return metrics_collector.metrics()
        # Sin collector: devolver métricas básicas del engine.
        return orchestrator.engine.metrics()

    # ------------------------------------------------------------------
    # GET /metrics/agents
    # ------------------------------------------------------------------

    @router.get("/metrics/agents")
    async def get_agent_metrics() -> dict[str, Any]:
        """Métricas agregadas de agentes."""
        return orchestrator.agent_orchestrator.metrics()

    return router


def mount_workflow_api(
    app: Any,
    orchestrator: WorkflowOrchestrator,
    metrics_collector: MetricsCollector | None = None,
) -> None:
    """Monta todos los routers de workflow en una app FastAPI."""
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("FastAPI is not installed")
    app.include_router(create_workflow_router(orchestrator, metrics_collector))
    app.include_router(create_misc_router(orchestrator, metrics_collector))


__all__ = [
    "create_misc_router",
    "create_workflow_router",
    "mount_workflow_api",
]
