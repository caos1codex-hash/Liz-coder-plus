"""TaskPipelineEngine — motor de workflows basado en capacidades (Sprint 2.4).

A diferencia del WorkflowEngine de Sprint 2.2 (que recibe agentes directos),
este engine resuelve agentes dinámicamente vía AgentRegistry + CapabilityResolver.

Flujo de ejecución::

    WorkflowStep.capability_required
        ↓
    CapabilityResolver.resolve(capability)
        ↓
    AgentRegistry → AgentRecord → BaseAgent
        ↓
    LifecycleManager.start()
        ↓
    BaseAgent.execute(task)
        ↓
    TaskResult producido
        ↓
    Contexto actualizado para steps dependientes

API::

    create_workflow(name, steps)  → PipelineWorkflow
    register_workflow(workflow)   → PipelineWorkflow
    get_workflow(id)              → PipelineWorkflow | None
    list_workflows()              → list[PipelineWorkflow]
    execute_workflow(id)          → PipelineExecutionResult
    pause_workflow(id)            → bool
    resume_workflow(id)           → bool
    cancel_workflow(id)           → bool
    get_status(id)                → dict
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..config import RetryPolicy
from ..registry.agent_registry import AgentRegistry
from ..registry.lifecycle import LifecycleManager
from ..registry.resolver import CapabilityResolver, ResolutionResult
from .dag import DAG, ValidationResult
from .enums import StepStatus, WorkflowStatus
from .models import Step, Workflow
from .pipeline_models import (
    PipelineWorkflow,
    StepRetryPolicy,
    TaskRequest,
    TaskResult,
    WorkflowStepDef,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# PipelineExecutionResult
# ----------------------------------------------------------------------


@dataclass
class PipelineExecutionResult:
    """Resultado de ejecutar un workflow vía el TaskPipelineEngine.

    Attributes:
        workflow_id:      ID del workflow.
        status:           WorkflowStatus final.
        steps_total:      Total de steps.
        steps_completed:  Steps completados exitosamente.
        steps_failed:     Steps fallidos.
        steps_skipped:    Steps saltados.
        duration_ms:      Duración total de ejecución.
        agents_used:      Nombres de agentes utilizados.
        total_retries:    Total de reintentos realizados.
        results:          Dict {step_id: TaskResult}.
        error:            Error fatal si hubo.
    """

    workflow_id: str = ""
    status: WorkflowStatus = WorkflowStatus.PENDING
    steps_total: int = 0
    steps_completed: int = 0
    steps_failed: int = 0
    steps_skipped: int = 0
    duration_ms: float = 0.0
    agents_used: list[str] = field(default_factory=list)
    total_retries: int = 0
    results: dict[str, TaskResult] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "status": self.status.value,
            "steps_total": self.steps_total,
            "steps_completed": self.steps_completed,
            "steps_failed": self.steps_failed,
            "steps_skipped": self.steps_skipped,
            "duration_ms": round(self.duration_ms, 2),
            "agents_used": list(self.agents_used),
            "total_retries": self.total_retries,
            "results": {k: v.to_dict() for k, v in self.results.items()},
            "error": self.error,
        }


# ----------------------------------------------------------------------
# TaskPipelineEngine
# ----------------------------------------------------------------------


class TaskPipelineEngine:
    """Motor de workflows basado en capacidades.

    NO importa agentes concretos. Usa AgentRegistry + CapabilityResolver
    para encontrar el agente adecuado para cada step.

    Args:
        registry:     AgentRegistry para buscar agentes.
        resolver:     CapabilityResolver (si None, se crea desde registry).
        lifecycles:   Dict opcional de LifecycleManagers por agent_name.
        config:       MultiAgentConfig (opcional).
    """

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        resolver: CapabilityResolver | None = None,
        lifecycles: dict[str, LifecycleManager] | None = None,
        config: Any | None = None,
    ) -> None:
        self._registry = registry
        self._resolver = resolver or CapabilityResolver(registry=registry)
        self._lifecycles: dict[str, LifecycleManager] = lifecycles or {}
        self._config = config

        # Workflows registrados: {workflow_id: PipelineWorkflow}
        self._workflows: dict[str, PipelineWorkflow] = {}
        # Internal workflow (Sprint 2.2 models) for DAG management
        self._internal_workflows: dict[str, Workflow] = {}
        # Running state
        self._running: dict[str, bool] = {}
        # Results cache
        self._results: dict[str, dict[str, TaskResult]] = {}

        # Metrics
        self._metrics = {
            "workflows_created": 0,
            "workflows_completed": 0,
            "workflows_failed": 0,
            "workflows_cancelled": 0,
            "steps_executed": 0,
            "steps_retried": 0,
            "total_resolution_time_ms": 0.0,
            "total_execution_time_ms": 0.0,
        }

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    @property
    def resolver(self) -> CapabilityResolver:
        return self._resolver

    # ------------------------------------------------------------------
    # API: Workflow CRUD
    # ------------------------------------------------------------------

    def create_workflow(
        self,
        name: str,
        steps: list[WorkflowStepDef],
        *,
        description: str = "",
        metadata: dict[str, Any] | None = None,
        timeout: float = 0.0,
    ) -> PipelineWorkflow:
        """Crea un nuevo workflow con los steps dados."""
        wf = PipelineWorkflow(
            name=name,
            steps=list(steps),
            description=description or "",
            metadata=metadata or {},
            timeout=timeout,
        )
        self._metrics["workflows_created"] += 1
        logger.info(
            "Created workflow '%s' (id=%s, steps=%d)",
            name, wf.id[:8], len(steps),
        )
        return wf

    def register_workflow(self, workflow: PipelineWorkflow) -> PipelineWorkflow:
        """Registra un workflow para ejecución.

        Construye el DAG interno y valida dependencias.
        """
        # Build internal Workflow (Sprint 2.2 model) for DAG management.
        internal_wf = Workflow(name=workflow.name, description=workflow.description)

        step_id_map: dict[str, str] = {}  # step_def.name -> step_id
        for i, step_def in enumerate(workflow.steps):
            step_id = f"{workflow.id}-step-{i}"
            step_id_map[step_def.name] = step_id
            step = Step(
                id=step_id,
                name=step_def.name,
                agent=step_def.agent_id or "",
                action=step_def.capability_required,
                depends_on=[],
                timeout=step_def.timeout,
            )
            internal_wf.add_step(step)

        # Wire dependencies using step names.
        for i, step_def in enumerate(workflow.steps):
            step_id = f"{workflow.id}-step-{i}"
            dep_ids = []
            for dep_name in step_def.dependencies:
                if dep_name in step_id_map:
                    dep_ids.append(step_id_map[dep_name])
            if dep_ids:
                internal_wf.steps[step_id].depends_on = dep_ids
                # Update the DAG directly.
                for dep_id in dep_ids:
                    internal_wf.dag.add_dependency(step_id, dep_id)
                internal_wf._touch()

        # Validate DAG.
        validation = internal_wf.validate()
        if not validation.ok:
            raise ValueError(
                f"Workflow '{workflow.name}' has invalid DAG: {validation.to_dict()}"
            )

        self._workflows[workflow.id] = workflow
        self._internal_workflows[workflow.id] = internal_wf
        self._results[workflow.id] = {}

        logger.info(
            "Registered workflow '%s' (id=%s, steps=%d)",
            workflow.name, workflow.id[:8], len(workflow.steps),
        )
        return workflow

    def get_workflow(self, workflow_id: str) -> PipelineWorkflow | None:
        """Devuelve un workflow por ID."""
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> list[PipelineWorkflow]:
        """Lista todos los workflows registrados."""
        return list(self._workflows.values())

    # ------------------------------------------------------------------
    # API: Execution
    # ------------------------------------------------------------------

    async def execute_workflow(
        self,
        workflow_id: str,
        *,
        max_iterations: int = 1000,
    ) -> PipelineExecutionResult:
        """Ejecuta un workflow completo hasta terminar o fallar.

        Resuelve agentes por capability para cada step, ejecuta respetando
        el DAG de dependencias, y aplica retry policies.
        """
        workflow = self._workflows.get(workflow_id)
        if workflow is None:
            raise KeyError(f"Workflow '{workflow_id}' not found")

        internal_wf = self._internal_workflows[workflow_id]
        workflow.status = "running"
        self._running[workflow_id] = True

        start = time.monotonic()
        result = PipelineExecutionResult(
            workflow_id=workflow_id,
            steps_total=len(workflow.steps),
        )

        # Track completed step IDs (using internal step IDs).
        completed_steps: set[str] = set()
        failed_steps: set[str] = set()
        skipped_steps: set[str] = set()
        running_steps: set[str] = set()
        step_results: dict[str, TaskResult] = {}
        agents_used: set[str] = set()
        total_retries = 0

        # Track retry counts per step index.
        retry_counts: dict[int, int] = {}

        try:
            for _ in range(max_iterations):
                if not self._running.get(workflow_id, False):
                    # Cancelled during execution.
                    workflow.status = "cancelled"
                    break

                # Get ready steps from DAG.
                ready = internal_wf.dag.ready_steps(
                    completed_steps, skip=running_steps | failed_steps | skipped_steps
                )

                if not ready:
                    # Check if there are still pending steps.
                    all_terminal = all(
                        sid in completed_steps or sid in failed_steps or sid in skipped_steps
                        for sid in internal_wf.steps
                    )
                    if all_terminal:
                        break
                    # Check for deadlock: pending steps with unmet deps.
                    pending = [
                        sid for sid in internal_wf.steps
                        if sid not in completed_steps
                        and sid not in failed_steps
                        and sid not in skipped_steps
                        and sid not in running_steps
                    ]
                    if pending:
                        # Wait a bit and retry.
                        await asyncio.sleep(0.01)
                        continue
                    break

                # Execute ready steps (one at a time for simplicity;
                # parallel execution can be added later).
                for step_id in ready:
                    if not self._running.get(workflow_id, False):
                        break

                    step_idx = self._step_index_from_id(workflow_id, step_id)
                    if step_idx is None:
                        continue

                    step_def = workflow.steps[step_idx]
                    internal_step = internal_wf.steps[step_id]
                    running_steps.add(step_id)

                    # Resolve agent by capability.
                    task_result = await self._execute_step_with_retry(
                        workflow=workflow,
                        workflow_id=workflow_id,
                        step_def=step_def,
                        step_id=step_id,
                        step_idx=step_idx,
                        internal_step=internal_step,
                        retry_counts=retry_counts,
                        context=self._build_context(workflow_id, step_results, step_def),
                    )

                    step_results[step_id] = task_result
                    total_retries += retry_counts.get(step_idx, 0)
                    running_steps.discard(step_id)

                    if task_result.success:
                        completed_steps.add(step_id)
                        # Proper state transitions: PENDING → READY → RUNNING → COMPLETED
                        try:
                            internal_step.transition_to(StepStatus.READY)
                            internal_step.transition_to(StepStatus.RUNNING)
                            internal_step.transition_to(StepStatus.COMPLETED)
                        except ValueError:
                            # If already in a terminal state, skip.
                            pass
                        if task_result.agent_name:
                            agents_used.add(task_result.agent_name)
                    else:
                        # Check if we should skip dependents.
                        failed_steps.add(step_id)
                        try:
                            internal_step.transition_to(StepStatus.READY)
                            internal_step.transition_to(StepStatus.RUNNING)
                            internal_step.transition_to(StepStatus.FAILED)
                        except ValueError:
                            pass
                        internal_step.error = task_result.error
                        # Propagate skip to dependents.
                        self._propagate_skip(internal_wf, step_id, skipped_steps)

                # Check if workflow is complete.
                if self._all_steps_resolved(
                    internal_wf, completed_steps, failed_steps, skipped_steps
                ):
                    break

        except Exception as exc:  # noqa: BLE001
            logger.exception("Workflow '%s' execution error", workflow.name)
            result.error = str(exc)

        # Finalize.
        duration_ms = (time.monotonic() - start) * 1000.0
        self._running.pop(workflow_id, None)

        # Determine final status.
        has_required_failed = any(
            sid in failed_steps
            for sid, step in internal_wf.steps.items()
        )
        if has_required_failed and len(completed_steps) < len(internal_wf.steps):
            workflow.status = "failed"
            self._metrics["workflows_failed"] += 1
        elif len(completed_steps) >= len(internal_wf.steps):
            workflow.status = "completed"
            self._metrics["workflows_completed"] += 1
        elif workflow.status == "cancelled":
            self._metrics["workflows_cancelled"] += 1
        else:
            workflow.status = "completed"
            self._metrics["workflows_completed"] += 1

        workflow.updated_at = _now_iso()

        # Build result.
        result.workflow_id = workflow_id
        result.status = WorkflowStatus(workflow.status)
        result.steps_completed = len(completed_steps)
        result.steps_failed = len(failed_steps)
        result.steps_skipped = len(skipped_steps)
        result.duration_ms = duration_ms
        result.agents_used = sorted(agents_used)
        result.total_retries = total_retries
        result.results = step_results
        self._results[workflow_id] = step_results

        # Update metrics.
        self._metrics["steps_executed"] += len(step_results)
        # steps_retried is already counted in _execute_step_with_retry
        self._metrics["total_execution_time_ms"] += duration_ms

        logger.info(
            "Workflow '%s' finished: status=%s, completed=%d, failed=%d, duration=%.1fms",
            workflow.name, workflow.status, result.steps_completed,
            result.steps_failed, duration_ms,
        )
        return result

    async def pause_workflow(self, workflow_id: str) -> bool:
        """Pausa un workflow en ejecución."""
        wf = self._workflows.get(workflow_id)
        if wf is None or wf.status != "running":
            return False
        self._running[workflow_id] = False
        wf.status = "paused"
        wf.updated_at = _now_iso()
        logger.info("Workflow '%s' paused", wf.name)
        return True

    async def resume_workflow(self, workflow_id: str) -> bool:
        """Reanuda un workflow pausado (lo marca como running de nuevo)."""
        wf = self._workflows.get(workflow_id)
        if wf is None or wf.status != "paused":
            return False
        self._running[workflow_id] = True
        wf.status = "running"
        wf.updated_at = _now_iso()
        logger.info("Workflow '%s' resumed", wf.name)
        return True

    async def cancel_workflow(self, workflow_id: str) -> bool:
        """Cancela un workflow."""
        wf = self._workflows.get(workflow_id)
        if wf is None:
            return False
        self._running[workflow_id] = False
        wf.status = "cancelled"
        wf.updated_at = _now_iso()
        logger.info("Workflow '%s' cancelled", wf.name)
        return True

    def get_status(self, workflow_id: str) -> dict[str, Any]:
        """Devuelve el estado actual de un workflow."""
        wf = self._workflows.get(workflow_id)
        if wf is None:
            return {"error": f"Workflow '{workflow_id}' not found"}

        internal_wf = self._internal_workflows.get(workflow_id)
        step_statuses = {}
        if internal_wf:
            for sid, step in internal_wf.steps.items():
                step_statuses[step.name] = {
                    "id": sid,
                    "status": step.status.value,
                    "error": step.error,
                }

        return {
            "workflow_id": wf.id,
            "name": wf.name,
            "status": wf.status,
            "step_count": len(wf.steps),
            "step_statuses": step_statuses,
            "created_at": wf.created_at,
            "updated_at": wf.updated_at,
        }

    # ------------------------------------------------------------------
    # Metrics / Observabilidad
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """Métricas agregadas del engine."""
        return {
            **self._metrics,
            "registered_workflows": len(self._workflows),
            "running_workflows": sum(1 for v in self._running.values() if v),
            "registry_metrics": self._registry.metrics(),
            "resolver_metrics": self._resolver.metrics(),
        }

    def workflow_metrics(self, workflow_id: str) -> dict[str, Any] | None:
        """Métricas de un workflow específico."""
        wf = self._workflows.get(workflow_id)
        if wf is None:
            return None
        results = self._results.get(workflow_id, {})
        completed = sum(1 for r in results.values() if r.success)
        failed = sum(1 for r in results.values() if not r.success)
        total_time = sum(r.execution_time for r in results.values())
        agents = list({r.agent_name for r in results.values() if r.agent_name})
        return {
            "workflow_id": workflow_id,
            "name": wf.name,
            "status": wf.status,
            "steps_total": len(wf.steps),
            "steps_completed": completed,
            "steps_failed": failed,
            "total_execution_time_ms": round(total_time, 2),
            "agents_used": sorted(agents),
            "step_results": {
                sid: {
                    "success": r.success,
                    "agent": r.agent_name,
                    "execution_time_ms": round(r.execution_time, 2),
                    "error": r.error,
                }
                for sid, r in results.items()
            },
        }

    # ------------------------------------------------------------------
    # Internal: Step execution with retry
    # ------------------------------------------------------------------

    async def _execute_step_with_retry(
        self,
        *,
        workflow: PipelineWorkflow,
        workflow_id: str,
        step_def: WorkflowStepDef,
        step_id: str,
        step_idx: int,
        internal_step: Step,
        retry_counts: dict[int, int],
        context: dict[str, Any],
    ) -> TaskResult:
        """Ejecuta un step con política de reintentos."""
        retry_policy = step_def.retry_policy or StepRetryPolicy()
        max_attempts = retry_policy.max_attempts
        retry_delay = retry_policy.retry_delay

        last_result: TaskResult | None = None
        attempt = 0

        for attempt in range(1, max_attempts + 1):
            if not self._running.get(workflow_id, False):
                return TaskResult.fail(
                    error="workflow cancelled",
                    step_id=step_id,
                    task_id=step_id,
                )

            # Resolve agent by capability.
            resolution = await self._resolve_agent(
                step_def, workflow_id, step_id
            )

            if not resolution.resolved or resolution.agent_record is None:
                return TaskResult.fail(
                    error=resolution.reason or f"no agent for capability '{step_def.capability_required}'",
                    step_id=step_id,
                    task_id=step_id,
                )

            record = resolution.agent_record
            agent = record.agent
            self._metrics["total_resolution_time_ms"] += resolution.duration_ms

            # Update internal step with resolved agent name.
            internal_step.agent = record.name

            # Build TaskRequest.
            task_request = TaskRequest(
                description=step_def.description or step_def.name,
                capability=step_def.capability_required,
                context=context,
                metadata={
                    "workflow_id": workflow_id,
                    "step_id": step_id,
                    "step_name": step_def.name,
                    "attempt": attempt,
                    "priority": 0,
                },
            )

            # Execute via lifecycle if available, otherwise directly.
            task_result = await self._execute_via_agent(
                agent, record, task_request, step_def
            )
            task_result.step_id = step_id
            task_result.task_id = task_request.task_id

            if task_result.success:
                return task_result

            last_result = task_result

            # Check retry policy.
            if attempt < max_attempts and retry_policy.should_retry(task_result.error):
                retry_counts[step_idx] = retry_counts.get(step_idx, 0) + 1
                self._metrics["steps_retried"] += 1
                logger.warning(
                    "Step '%s' attempt %d/%d failed: %s. Retrying in %.1fs...",
                    step_def.name, attempt, max_attempts,
                    task_result.error, retry_delay,
                )
                await asyncio.sleep(retry_delay)
            else:
                break

        # All attempts exhausted.
        return last_result or TaskResult.fail(
            error="max retries exceeded",
            step_id=step_id,
            task_id=step_id,
        )

    async def _resolve_agent(
        self, step_def: WorkflowStepDef, workflow_id: str, step_id: str
    ) -> ResolutionResult:
        """Resuelve un agente para el step.

        Si step_def.agent_id está seteado, busca por nombre directo.
        Si no, usa CapabilityResolver.
        """
        if step_def.agent_id:
            # Direct agent lookup.
            record = self._registry.get(step_def.agent_id)
            if record is None:
                record = self._registry.get_by_name(step_def.agent_id)
            if record is not None:
                return ResolutionResult(
                    capability=step_def.capability_required,
                    agent_record=record,
                    resolved=True,
                    reason=f"direct agent lookup: {step_def.agent_id}",
                )
            return ResolutionResult(
                capability=step_def.capability_required,
                resolved=False,
                reason=f"agent '{step_def.agent_id}' not found in registry",
            )

        # Capability-based resolution.
        return await self._resolver.resolve(step_def.capability_required)

    async def _execute_via_agent(
        self,
        agent: Any,
        record: Any,
        task_request: TaskRequest,
        step_def: WorkflowStepDef,
    ) -> TaskResult:
        """Ejecuta una tarea vía un agente, usando lifecycle si disponible."""
        agent_name = record.name if hasattr(record, "name") else str(agent)
        agent_id = record.id if hasattr(record, "id") else ""

        # Use lifecycle if available.
        lifecycle = self._lifecycles.get(agent_name)

        try:
            if lifecycle is not None:
                await lifecycle.start()

            start = time.monotonic()
            agent_task = task_request.to_agent_task()

            # Execute with optional timeout.
            if step_def.timeout and step_def.timeout > 0:
                exec_result = await asyncio.wait_for(
                    agent.execute(agent_task),
                    timeout=step_def.timeout,
                )
            else:
                exec_result = await agent.execute(agent_task)

            duration_ms = (time.monotonic() - start) * 1000.0

            if exec_result.get("status") == "ok":
                inner = exec_result.get("result", {})
                metrics = {}
                if isinstance(inner, dict):
                    metrics = {
                        "tokens_prompt": inner.get("tokens_prompt", 0),
                        "tokens_completion": inner.get("tokens_completion", 0),
                        "tools_used": inner.get("tools_used", []),
                    }
                return TaskResult.ok(
                    output=inner,
                    execution_time=duration_ms,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    metrics=metrics,
                    task_id=task_request.task_id,
                )
            else:
                return TaskResult.fail(
                    error=exec_result.get("error", "unknown error"),
                    execution_time=duration_ms,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    task_id=task_request.task_id,
                )

        except asyncio.TimeoutError:
            return TaskResult.fail(
                error=f"timeout after {step_def.timeout}s",
                agent_id=agent_id,
                agent_name=agent_name,
                task_id=task_request.task_id,
            )
        except Exception as exc:  # noqa: BLE001
            return TaskResult.fail(
                error=str(exc),
                agent_id=agent_id,
                agent_name=agent_name,
                task_id=task_request.task_id,
            )
        finally:
            if lifecycle is not None and not lifecycle.is_terminal:
                lifecycle.recover()

    def _build_context(
        self,
        workflow_id: str,
        step_results: dict[str, TaskResult],
        current_step: WorkflowStepDef,
    ) -> dict[str, Any]:
        """Construye el contexto para un step basado en outputs de steps previos."""
        context: dict[str, Any] = {
            "workflow_id": workflow_id,
            "step_name": current_step.name,
        }
        internal_wf = self._internal_workflows.get(workflow_id)
        if internal_wf is None:
            return context

        # Add outputs from dependency steps.
        for dep_name in current_step.dependencies:
            for sid, step in internal_wf.steps.items():
                if step.name == dep_name and sid in step_results:
                    context[dep_name] = step_results[sid].output

        # Also add all completed step outputs keyed by step name.
        for sid, result in step_results.items():
            if result.success and sid in internal_wf.steps:
                step_name = internal_wf.steps[sid].name
                if step_name not in context:
                    context[step_name] = result.output

        return context

    def _step_index_from_id(self, workflow_id: str, step_id: str) -> int | None:
        """Devuelve el índice del step en la lista de steps del workflow."""
        # step_id format: "{workflow_id}-step-{i}"
        prefix = f"{workflow_id}-step-"
        if step_id.startswith(prefix):
            try:
                return int(step_id[len(prefix):])
            except ValueError:
                return None
        return None

    def _propagate_skip(
        self,
        internal_wf: Workflow,
        failed_step_id: str,
        skipped_steps: set[str],
    ) -> None:
        """Propaga SKIP a dependientes de un step fallido."""
        dependents = internal_wf.dag.dependents_of(failed_step_id)
        for dep_id in dependents:
            if dep_id in skipped_steps:
                continue
            dep_step = internal_wf.steps.get(dep_id)
            if dep_step is None:
                continue
            try:
                dep_step.transition_to(StepStatus.SKIPPED)
                skipped_steps.add(dep_id)
                self._propagate_skip(internal_wf, dep_id, skipped_steps)
            except ValueError:
                pass

    def _all_steps_resolved(
        self,
        internal_wf: Workflow,
        completed: set[str],
        failed: set[str],
        skipped: set[str],
    ) -> bool:
        """True si todos los steps están resueltos."""
        for sid in internal_wf.steps:
            if sid not in completed and sid not in failed and sid not in skipped:
                return False
        return True


__all__ = [
    "PipelineExecutionResult",
    "TaskPipelineEngine",
]