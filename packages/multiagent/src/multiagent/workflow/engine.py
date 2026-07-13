"""WorkflowEngine — motor de ejecución de workflows (Sprint 2.2).

Responsabilidades:

- Ejecuta un `Workflow` respetando el DAG de dependencias.
- Asigna steps a agentes vía `Scheduler` + `LoadBalancer`.
- Ejecuta steps en paralelo (hasta `max_parallel_steps`).
- Maneja **failover**: retry, reasignación, skip (opcional), cancel.
- Emite eventos al `EventBus` en cada transición.
- Integra el `ContextManager` para compartir contexto entre steps.
- Integra el `AgentLogger` de Sprint 2.1 para telemetría.
- Calcula métricas (duration, parallelism, queue_time, retry_count, success_rate).

El engine es asyncio-safe y puede ejecutar varios workflows concurrentes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from ..base import BaseAgent
from ..config import MultiAgentConfig
from ..logs import AgentLogger
from .context import ContextManager
from .enums import (
    FailoverAction,
    StepCriticality,
    StepStatus,
    WorkflowEvent,
    WorkflowStatus,
)
from .event_bus import EventBus
from .load_balancer import LoadBalancer
from .models import Step, Workflow
from .scheduler import Scheduler, SchedulerConfig

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Failover policy
# ----------------------------------------------------------------------


@dataclass
class FailoverPolicy:
    """Política de failover para el engine.

    Attributes:
        max_retries:           Reintentos por step antes de reasignar.
        reassign_on_fail:      Si True, intenta reasignar a otro agente.
        max_reassigns:         Máximo de reasignaciones por step.
        skip_optional_on_fail: Si True, los steps OPTIONAL fallidos se SKIP.
        cancel_on_required_fail: Si True, un step REQUIRED fallido cancela el workflow.
    """

    max_retries: int = 2
    reassign_on_fail: bool = True
    max_reassigns: int = 2
    skip_optional_on_fail: bool = True
    cancel_on_required_fail: bool = True


# ----------------------------------------------------------------------
# Resultado de ejecución
# ----------------------------------------------------------------------


@dataclass
class WorkflowExecutionResult:
    """Resultado de ejecutar un workflow.

    Attributes:
        workflow_id:    ID del workflow.
        status:         WorkflowStatus final.
        steps_total:    Total de steps.
        steps_completed: Steps completados.
        steps_failed:   Steps fallidos.
        steps_skipped:  Steps saltados.
        duration_ms:    Duración total.
        parallelism_max: Máximo nº de steps en paralelo alcanzado.
        retry_count:    Reintentos totales.
        reassign_count: Reasignaciones totales.
        result:         Resultado final del workflow.
        error:          Error fatal si hubo.
    """

    workflow_id: str = ""
    status: WorkflowStatus = WorkflowStatus.PENDING
    steps_total: int = 0
    steps_completed: int = 0
    steps_failed: int = 0
    steps_skipped: int = 0
    duration_ms: float = 0.0
    parallelism_max: int = 0
    retry_count: int = 0
    reassign_count: int = 0
    result: Any = None
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
            "parallelism_max": self.parallelism_max,
            "retry_count": self.retry_count,
            "reassign_count": self.reassign_count,
            "result": self.result,
            "error": self.error,
        }


# ----------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------


class WorkflowEngine:
    """Motor de ejecución de workflows.

    Args:
        agents:        Lista de agentes disponibles (se actualizará dinámicamente).
        config:        Configuración multi-agent.
        event_bus:     EventBus para telemetría.
        scheduler:     Scheduler (si None, se crea uno).
        load_balancer: LoadBalancer (si None, se crea uno).
        failover:      Política de failover.
        logger:        AgentLogger de Sprint 2.1.
    """

    def __init__(
        self,
        *,
        agents: list[BaseAgent] | None = None,
        config: MultiAgentConfig | None = None,
        event_bus: EventBus | None = None,
        scheduler: Scheduler | None = None,
        load_balancer: LoadBalancer | None = None,
        failover: FailoverPolicy | None = None,
        agent_logger: AgentLogger | None = None,
        scheduler_config: SchedulerConfig | None = None,
    ) -> None:
        self._config = config or MultiAgentConfig()
        self._bus = event_bus or EventBus()
        self._lb = load_balancer or LoadBalancer()
        self._scheduler = scheduler or Scheduler(
            load_balancer=self._lb,
            event_bus=self._bus,
            config=scheduler_config or SchedulerConfig(),
        )
        self._failover = failover or FailoverPolicy()
        self._logger = agent_logger or AgentLogger(
            history_size=self._config.log_history_size,
        )
        self._agents: dict[str, BaseAgent] = {}
        if agents:
            for a in agents:
                self._agents[a.name] = a

        # Contextos por workflow.
        self._contexts: dict[str, ContextManager] = {}
        # Workflows en ejecución.
        self._running: dict[str, Workflow] = {}

    # ------------------------------------------------------------------
    # Propiedades
    # ------------------------------------------------------------------

    @property
    def event_bus(self) -> EventBus:
        return self._bus

    @property
    def scheduler(self) -> Scheduler:
        return self._scheduler

    @property
    def load_balancer(self) -> LoadBalancer:
        return self._lb

    @property
    def logger(self) -> AgentLogger:
        return self._logger

    @property
    def failover(self) -> FailoverPolicy:
        return self._failover

    def register_agent(self, agent: BaseAgent) -> None:
        """Añade o reemplaza un agente disponible."""
        self._agents[agent.name] = agent

    def unregister_agent(self, name: str) -> bool:
        return self._agents.pop(name, None) is not None

    def list_agents(self) -> list[str]:
        return list(self._agents.keys())

    def get_context(self, workflow_id: str) -> ContextManager | None:
        return self._contexts.get(workflow_id)

    # ------------------------------------------------------------------
    # API principal
    # ------------------------------------------------------------------

    async def submit(self, workflow: Workflow) -> Workflow:
        """Registra un workflow para ejecución. Emite WORKFLOW_CREATED."""
        # Validar DAG.
        validation = workflow.validate()
        if not validation.ok:
            raise ValueError(
                f"Workflow '{workflow.name}' has invalid DAG: {validation.to_dict()}"
            )

        self._running[workflow.id] = workflow
        self._contexts[workflow.id] = ContextManager(
            workflow.id, event_bus=self._bus
        )
        await self._emit(
            WorkflowEvent.WORKFLOW_CREATED,
            source="engine",
            payload={
                "workflow_id": workflow.id,
                "name": workflow.name,
                "step_count": workflow.step_count,
                "priority": workflow.priority.value,
            },
            correlation_id=workflow.id,
        )
        return workflow

    async def run(
        self,
        workflow: Workflow,
        *,
        max_iterations: int = 1000,
    ) -> WorkflowExecutionResult:
        """Ejecuta un workflow completo hasta terminar (o fallar).

        Bloquea hasta que el workflow reaches terminal state.
        """
        if workflow.id not in self._running:
            await self.submit(workflow)

        start = time.monotonic()
        result = WorkflowExecutionResult(
            workflow_id=workflow.id,
            steps_total=workflow.step_count,
        )

        # Estado de asignaciones fallidas para reasignación.
        failed_assignments: dict[str, set[str]] = {}

        # Control de paralelismo.
        active_steps: dict[str, asyncio.Task] = {}  # step_id -> task
        max_parallelism = 0

        # Mapeo step_id -> agent_name (para liberar al agente al terminar).
        step_agent_map: dict[str, str] = {}

        for _ in range(max_iterations):
            # 1. Comprobar si el workflow sigue vivo.
            if workflow.is_terminal:
                break

            # 2. Planificar nuevos steps.
            decisions = await self._scheduler.schedule(
                workflow,
                list(self._agents.values()),
                exclude=failed_assignments,
            )

            # 3. Lanzar steps en paralelo.
            for decision in decisions:
                step = workflow.steps.get(decision.step_id)
                if step is None:
                    continue
                agent_name = decision.agent_name
                agent = self._agents.get(agent_name)
                if agent is None:
                    continue
                await self._scheduler.mark_step_started(
                    step, agent_name, workflow
                )
                step_agent_map[step.id] = agent_name
                task = asyncio.create_task(
                    self._execute_step(workflow, step, agent)
                )
                active_steps[step.id] = task

            max_parallelism = max(max_parallelism, len(active_steps))

            # 4. Si no hay steps activos ni decisiones, comprobar estado.
            if not active_steps and not decisions:
                # ¿Quedan steps pendientes? Si sí y no hay agentes → fallo.
                pending = [
                    s for s in workflow.steps.values()
                    if s.status in (StepStatus.PENDING, StepStatus.READY)
                ]
                if pending:
                    # Esperar a que un agente quede libre.
                    await asyncio.sleep(0.01)
                    continue
                else:
                    # No hay nada más que hacer.
                    break

            # 5. Esperar a que termine al menos un step.
            if active_steps:
                done, pending_tasks = await asyncio.wait(
                    active_steps.values(),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Procesar completados.
                completed_step_ids: list[str] = []
                for step_id, task in list(active_steps.items()):
                    if task in done:
                        completed_step_ids.append(step_id)
                        try:
                            step_result = await task
                        except Exception as exc:  # noqa: BLE001
                            step_result = {
                                "success": False,
                                "error": str(exc),
                                "duration_ms": 0.0,
                            }
                        agent_name = step_agent_map.pop(step_id, "")
                        await self._handle_step_result(
                            workflow=workflow,
                            step=workflow.steps[step_id],
                            agent_name=agent_name,
                            step_result=step_result,
                            failed_assignments=failed_assignments,
                            result=result,
                        )
                        del active_steps[step_id]
                continue

        # 6. Workflow terminó (o se agotaron iteraciones).
        result.duration_ms = (time.monotonic() - start) * 1000.0
        result.parallelism_max = max_parallelism
        result.steps_completed = workflow.completed_count
        result.steps_failed = workflow.failed_count
        result.steps_skipped = sum(
            1 for s in workflow.steps.values() if s.status == StepStatus.SKIPPED
        )
        result.status = workflow.status
        result.result = workflow.result
        if workflow.status == WorkflowStatus.FAILED:
            # Buscar el error del primer step REQUIRED fallido.
            for s in workflow.steps.values():
                if s.status == StepStatus.FAILED and s.criticality == StepCriticality.REQUIRED:
                    result.error = s.error
                    break

        # Emitir evento final.
        final_event = (
            WorkflowEvent.WORKFLOW_COMPLETED
            if workflow.status == WorkflowStatus.COMPLETED
            else WorkflowEvent.WORKFLOW_FAILED
            if workflow.status == WorkflowStatus.FAILED
            else WorkflowEvent.WORKFLOW_CANCELLED
        )
        await self._emit(
            final_event,
            source="engine",
            payload=result.to_dict(),
            correlation_id=workflow.id,
        )

        return result

    async def cancel(self, workflow_id: str, reason: str = "") -> bool:
        """Cancela un workflow en ejecución."""
        workflow = self._running.get(workflow_id)
        if workflow is None:
            return False
        if workflow.is_terminal:
            return False
        workflow.transition_to(WorkflowStatus.CANCELLED)
        # Marcar steps no terminales como CANCELLED.
        for step in workflow.steps.values():
            if not step.is_terminal:
                try:
                    step.transition_to(StepStatus.CANCELLED)
                except ValueError:
                    pass
        await self._emit(
            WorkflowEvent.WORKFLOW_CANCELLED,
            source="engine",
            payload={"workflow_id": workflow_id, "reason": reason},
            correlation_id=workflow_id,
        )
        return True

    async def pause(self, workflow_id: str) -> bool:
        """Pausa un workflow (no interrumpe steps en curso)."""
        workflow = self._running.get(workflow_id)
        if workflow is None or workflow.status != WorkflowStatus.RUNNING:
            return False
        workflow.transition_to(WorkflowStatus.PAUSED)
        await self._emit(
            WorkflowEvent.WORKFLOW_PAUSED,
            source="engine",
            payload={"workflow_id": workflow_id},
            correlation_id=workflow_id,
        )
        return True

    async def resume(self, workflow_id: str) -> bool:
        """Reanuda un workflow pausado."""
        workflow = self._running.get(workflow_id)
        if workflow is None or workflow.status != WorkflowStatus.PAUSED:
            return False
        workflow.transition_to(WorkflowStatus.RUNNING)
        await self._emit(
            WorkflowEvent.WORKFLOW_RESUMED,
            source="engine",
            payload={"workflow_id": workflow_id},
            correlation_id=workflow_id,
        )
        return True

    # ------------------------------------------------------------------
    # Ejecución de un step
    # ------------------------------------------------------------------

    async def _execute_step(
        self,
        workflow: Workflow,
        step: Step,
        agent: BaseAgent,
    ) -> dict[str, Any]:
        """Ejecuta un step en un agente y devuelve el resultado."""
        task_dict = {
            "id": step.id,
            "name": step.name,
            "description": step.action,
            "payload": {**step.input, "action": step.action},
            "priority": workflow.priority.value,
            "workflow_id": workflow.id,
            "context": workflow.context,
        }
        # Log start.
        await self._logger.log_start(
            agent.name, step.id, f"workflow step: {step.name}"
        )
        start = time.monotonic()
        result = await agent.execute(task_dict)
        duration_ms = (time.monotonic() - start) * 1000.0
        success = result.get("status") == "ok"
        # Log end.
        if success:
            inner = result.get("result", {})
            await self._logger.log_end(
                agent.name, step.id,
                duration_ms=duration_ms,
                tokens_prompt=int(inner.get("tokens_prompt", 0)) if isinstance(inner, dict) else 0,
                tokens_completion=int(inner.get("tokens_completion", 0)) if isinstance(inner, dict) else 0,
                tools_used=inner.get("tools_used", []) if isinstance(inner, dict) else [],
            )
            # Registrar herramientas usadas.
            if isinstance(inner, dict):
                for tool in inner.get("tools_used", []):
                    await self._emit(
                        WorkflowEvent.TOOL_EXECUTED,
                        source=agent.name,
                        payload={
                            "workflow_id": workflow.id,
                            "step_id": step.id,
                            "tool": tool,
                        },
                        correlation_id=workflow.id,
                    )
            # Actualizar step.
            step.output = inner
            step.record_execution(
                duration_ms=duration_ms,
                tokens_prompt=int(inner.get("tokens_prompt", 0)) if isinstance(inner, dict) else 0,
                tokens_completion=int(inner.get("tokens_completion", 0)) if isinstance(inner, dict) else 0,
                tools_used=inner.get("tools_used", []) if isinstance(inner, dict) else None,
                files_modified=inner.get("files_modified", []) if isinstance(inner, dict) else None,
            )
            # Actualizar contexto.
            ctx = self._contexts.get(workflow.id)
            if ctx is not None and isinstance(inner, dict):
                if "files_modified" in inner:
                    for path in inner["files_modified"]:
                        await ctx.register_file(path, None, modified_by=agent.name)
        else:
            await self._logger.log_error(
                agent.name, step.id,
                result.get("error", "unknown"),
            )
        return {
            "success": success,
            "error": result.get("error"),
            "duration_ms": duration_ms,
            "result": result.get("result"),
        }

    # ------------------------------------------------------------------
    # Manejo del resultado de un step (failover)
    # ------------------------------------------------------------------

    async def _handle_step_result(
        self,
        *,
        workflow: Workflow,
        step: Step,
        agent_name: str,
        step_result: dict[str, Any],
        failed_assignments: dict[str, set[str]],
        result: WorkflowExecutionResult,
    ) -> None:
        """Procesa el resultado de un step y decide el failover."""
        success = step_result.get("success", False)
        duration_ms = step_result.get("duration_ms", 0.0)
        error = step_result.get("error")

        await self._scheduler.mark_step_finished(
            step, agent_name, workflow,
            success=success,
            duration_ms=duration_ms,
            error=error if not success else None,
        )

        if success:
            # ¿Workflow completo?
            if workflow.progress >= 1.0:
                workflow.transition_to(WorkflowStatus.COMPLETED)
                workflow.result = step_result.get("result")
            return

        # Failover.
        action = self._decide_failover(step, failed_assignments)
        if action == FailoverAction.RETRY:
            step.retry += 1
            step.error = None
            step.transition_to(StepStatus.READY)
            result.retry_count += 1
            await self._emit(
                WorkflowEvent.STEP_RETRY,
                source="engine",
                payload={
                    "workflow_id": workflow.id,
                    "step_id": step.id,
                    "attempt": step.retry,
                },
                correlation_id=workflow.id,
            )
        elif action == FailoverAction.REASSIGN:
            # Marcar el agente como fallido para este step.
            failed_assignments.setdefault(step.id, set()).add(agent_name)
            step.agent_reassigned.append(agent_name)
            step.error = None
            step.transition_to(StepStatus.READY)
            result.reassign_count += 1
            await self._emit(
                WorkflowEvent.AGENT_REASSIGNED,
                source="engine",
                payload={
                    "workflow_id": workflow.id,
                    "step_id": step.id,
                    "from_agent": agent_name,
                    "excluded": list(failed_assignments[step.id]),
                },
                correlation_id=workflow.id,
            )
        elif action == FailoverAction.SKIP:
            step.transition_to(StepStatus.SKIPPED)
            await self._emit(
                WorkflowEvent.STEP_SKIPPED,
                source="engine",
                payload={
                    "workflow_id": workflow.id,
                    "step_id": step.id,
                    "reason": "optional step failed",
                },
                correlation_id=workflow.id,
            )
            # ¿Workflow completo tras skip?
            if self._all_steps_terminal(workflow):
                # Si todos los REQUIRED están completos, el workflow está OK.
                required_failed = any(
                    s.status == StepStatus.FAILED and s.criticality == StepCriticality.REQUIRED
                    for s in workflow.steps.values()
                )
                if not required_failed:
                    workflow.transition_to(WorkflowStatus.COMPLETED)
                    workflow.result = {"skipped_steps": [
                        s.id for s in workflow.steps.values() if s.status == StepStatus.SKIPPED
                    ]}
        else:  # NONE or CANCEL
            # Marcar como fallo definitivo.
            # Si el step es REQUIRED y cancel_on_required_fail, cancelar workflow.
            if (
                step.criticality == StepCriticality.REQUIRED
                and self._failover.cancel_on_required_fail
            ):
                workflow.transition_to(WorkflowStatus.FAILED)
            else:
                # Step OPTIONAL fallido definitivo: dejarlo FAILED pero
                # permitir que el workflow continúe.
                if self._all_steps_terminal(workflow):
                    required_failed = any(
                        s.status == StepStatus.FAILED and s.criticality == StepCriticality.REQUIRED
                        for s in workflow.steps.values()
                    )
                    if not required_failed and workflow.completed_count > 0:
                        workflow.transition_to(WorkflowStatus.COMPLETED)
                    elif required_failed:
                        workflow.transition_to(WorkflowStatus.FAILED)

    def _decide_failover(
        self,
        step: Step,
        failed_assignments: dict[str, set[str]],
    ) -> FailoverAction:
        """Decide la acción de failover para un step fallido."""
        # 1. Retry si quedan intentos.
        if step.retry < self._failover.max_retries:
            return FailoverAction.RETRY
        # 2. Reasignar si está habilitado y no hemos excedido el máximo.
        if (
            self._failover.reassign_on_fail
            and len(step.agent_reassigned) < self._failover.max_reassigns
        ):
            return FailoverAction.REASSIGN
        # 3. Skip si es opcional.
        if (
            step.criticality == StepCriticality.OPTIONAL
            and self._failover.skip_optional_on_fail
        ):
            return FailoverAction.SKIP
        # 4. Cancelar workflow si es required y cancel_on_required_fail.
        if (
            step.criticality == StepCriticality.REQUIRED
            and self._failover.cancel_on_required_fail
        ):
            return FailoverAction.CANCEL
        return FailoverAction.NONE

    def _all_steps_terminal(self, workflow: Workflow) -> bool:
        """Devuelve True si todos los steps están en estado terminal."""
        return all(s.is_terminal for s in workflow.steps.values())

    # ------------------------------------------------------------------
    # Métricas / observabilidad
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """Métricas agregadas del engine."""
        return {
            "scheduler": self._scheduler.stats(),
            "load_balancer": self._lb.stats(),
            "event_bus": self._bus.metrics(),
            "logger": self._logger.stats(),
            "running_workflows": len(self._running),
            "registered_agents": len(self._agents),
        }

    def workflow_metrics(self, workflow_id: str) -> dict[str, Any] | None:
        """Métricas de un workflow específico."""
        wf = self._running.get(workflow_id)
        if wf is None:
            return None
        return wf.metrics()

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    async def _emit(
        self,
        event_type: WorkflowEvent,
        *,
        source: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        try:
            await self._bus.publish(
                event_type,
                source=source,
                payload=payload,
                correlation_id=correlation_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit event %s", event_type)


__all__ = [
    "FailoverPolicy",
    "WorkflowEngine",
    "WorkflowExecutionResult",
]
