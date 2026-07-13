"""Scheduler inteligente para el workflow engine (Sprint 2.2).

Responsabilidades:

- Decide **qué step** ejecutar a continuación (respetando el DAG).
- Decide **qué agente** lo ejecuta (delegando al LoadBalancer).
- Decide **cuándo** ejecutarlo (inmediato, diferido, con prioridad).
- Aplica **balanceo**: no satura a un agente con demasiados steps.
- Respeta `max_parallel_steps` y `max_parallel_agents`.
- Emite eventos `AGENT_BUSY` / `AGENT_IDLE` al EventBus.

El Scheduler es **stateless** entre workflows (cada `schedule(workflow)`
es independiente), pero mantiene historial de asignaciones para el
LoadBalancer.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..base import BaseAgent
from ..enums import Priority, priority_weight
from .enums import StepStatus, WorkflowEvent, WorkflowStatus
from .event_bus import EventBus
from .load_balancer import LoadBalancer
from .models import Step, Workflow

logger = logging.getLogger(__name__)


@dataclass
class ScheduleDecision:
    """Decisión de scheduling para un step.

    Attributes:
        step_id:       ID del step a ejecutar.
        agent_name:    Agente seleccionado.
        reason:        Razón humana de la decisión.
        priority:      Prioridad efectiva.
        queued_at:     Epoch segundos.
    """

    step_id: str
    agent_name: str
    reason: str = ""
    priority: Priority = Priority.NORMAL
    queued_at: float = field(default_factory=time.time)


@dataclass
class SchedulerConfig:
    """Configuración del Scheduler.

    Attributes:
        max_parallel_steps:    Número máximo de steps en ejecución simultánea.
        max_parallel_agents:   Número máximo de agentes ocupados a la vez.
        agent_step_timeout:    Timeout por step en segundos (0 = sin).
        enable_preemption:     Si True, un step CRITICAL puede desplazar a uno NORMAL.
    """

    max_parallel_steps: int = 4
    max_parallel_agents: int = 4
    agent_step_timeout: float = 60.0
    enable_preemption: bool = False


class Scheduler:
    """Scheduler inteligente de workflows.

    Uso::

        scheduler = Scheduler(load_balancer=LoadBalancer(), event_bus=bus)
        decision = await scheduler.schedule(workflow, agents)
        if decision:
            await scheduler.dispatch(decision, workflow, agents)
    """

    def __init__(
        self,
        *,
        load_balancer: LoadBalancer | None = None,
        event_bus: EventBus | None = None,
        config: SchedulerConfig | None = None,
    ) -> None:
        self._lb = load_balancer or LoadBalancer()
        self._bus = event_bus
        self._config = config or SchedulerConfig()
        # Asignaciones activas: {step_id: agent_name}
        self._active: dict[str, str] = {}
        # Steps en cola esperando (no se han podido asignar)
        self._waiting: list[tuple[Step, Workflow]] = []

    @property
    def load_balancer(self) -> LoadBalancer:
        return self._lb

    @property
    def config(self) -> SchedulerConfig:
        return self._config

    @property
    def active_count(self) -> int:
        return len(self._active)

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    async def schedule(
        self,
        workflow: Workflow,
        agents: list[BaseAgent],
        *,
        exclude: dict[str, set[str]] | None = None,
    ) -> list[ScheduleDecision]:
        """Planifica los próximos steps a ejecutar.

        Devuelve una lista de `ScheduleDecision` (una por step listo que
        pudo ser asignado a un agente). Si ningún agente está disponible,
        devuelve lista vacía.

        Args:
            workflow: Workflow a planificar.
            agents:   Lista de agentes disponibles.
            exclude:  {step_id: {agent_names_a_excluir}} (p.ej. agentes
                      que ya fallaron ese step).
        """
        exclude = exclude or {}
        if workflow.status == WorkflowStatus.PENDING:
            workflow.transition_to(WorkflowStatus.RUNNING)
            await self._emit(
                WorkflowEvent.WORKFLOW_STARTED,
                source="scheduler",
                payload={"workflow_id": workflow.id, "name": workflow.name},
                correlation_id=workflow.id,
            )

        if workflow.status != WorkflowStatus.RUNNING:
            return []

        # 1. Encontrar steps listos.
        ready_step_ids = workflow.ready_steps()
        if not ready_step_ids:
            return []

        # 2. Filtrar por límite de paralelismo.
        available_slots = self._config.max_parallel_steps - self.active_count
        if available_slots <= 0:
            return []

        # 3. Ordenar steps por prioridad del workflow y luego por DAG order.
        ready_steps = [workflow.steps[sid] for sid in ready_step_ids]
        ready_steps.sort(key=lambda s: (priority_weight(workflow.priority), s.name))

        decisions: list[ScheduleDecision] = []
        agents_busy: set[str] = set(self._active.values())

        for step in ready_steps:
            if len(decisions) >= available_slots:
                break
            if len(agents_busy) >= self._config.max_parallel_agents:
                break

            # 4. Seleccionar agente.
            excluded_for_step = exclude.get(step.id, set())
            # Excluir agentes ya ocupados por otros steps en este ciclo.
            available_agents = [
                a for a in agents
                if a.name not in agents_busy and a.name not in excluded_for_step
            ]
            agent = self._lb.select(
                available_agents,
                action=step.action or step.name,
                exclude=excluded_for_step,
            )
            if agent is None:
                # Sin agente disponible; el step queda esperando.
                continue

            decision = ScheduleDecision(
                step_id=step.id,
                agent_name=agent.name,
                reason=f"matched action='{step.action}' to agent '{agent.name}'",
                priority=workflow.priority,
            )
            decisions.append(decision)
            agents_busy.add(agent.name)
            self._lb.assign(agent.name)

        return decisions

    # ------------------------------------------------------------------
    # Dispatch (delegado al engine, pero marcamos el agente como busy)
    # ------------------------------------------------------------------

    async def mark_step_started(
        self,
        step: Step,
        agent_name: str,
        workflow: Workflow,
    ) -> None:
        """Marca un step como RUNNING y emite eventos."""
        # PENDING → READY → RUNNING (si viene de PENDING).
        if step.status == StepStatus.PENDING:
            step.transition_to(StepStatus.READY)
        step.transition_to(StepStatus.RUNNING)
        self._active[step.id] = agent_name
        await self._emit(
            WorkflowEvent.STEP_STARTED,
            source="scheduler",
            payload={
                "workflow_id": workflow.id,
                "step_id": step.id,
                "step_name": step.name,
                "agent": agent_name,
                "action": step.action,
            },
            correlation_id=workflow.id,
        )
        await self._emit(
            WorkflowEvent.AGENT_BUSY,
            source="scheduler",
            payload={"agent": agent_name, "step_id": step.id},
            correlation_id=workflow.id,
        )

    async def mark_step_finished(
        self,
        step: Step,
        agent_name: str,
        workflow: Workflow,
        *,
        success: bool,
        duration_ms: float = 0.0,
        error: str | None = None,
    ) -> None:
        """Marca un step como COMPLETED/FAILED y emite eventos."""
        if success:
            step.transition_to(StepStatus.COMPLETED)
        else:
            step.error = error
            step.transition_to(StepStatus.FAILED)

        self._active.pop(step.id, None)
        self._lb.release(agent_name)
        self._lb.record_execution(
            agent_name, step.action or step.name,
            success=success, duration_ms=duration_ms,
        )

        await self._emit(
            WorkflowEvent.STEP_FINISHED if success else WorkflowEvent.STEP_FAILED,
            source="scheduler",
            payload={
                "workflow_id": workflow.id,
                "step_id": step.id,
                "step_name": step.name,
                "agent": agent_name,
                "success": success,
                "duration_ms": duration_ms,
                "error": error,
            },
            correlation_id=workflow.id,
        )
        await self._emit(
            WorkflowEvent.AGENT_IDLE,
            source="scheduler",
            payload={"agent": agent_name, "step_id": step.id},
            correlation_id=workflow.id,
        )

    # ------------------------------------------------------------------
    # Métricas
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        return {
            "active_steps": self.active_count,
            "waiting_steps": len(self._waiting),
            "max_parallel_steps": self._config.max_parallel_steps,
            "max_parallel_agents": self._config.max_parallel_agents,
            "load_balancer": self._lb.stats(),
        }

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
        if self._bus is None:
            return
        try:
            await self._bus.publish(
                event_type,
                source=source,
                payload=payload,
                correlation_id=correlation_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit event %s", event_type)


__all__ = ["ScheduleDecision", "Scheduler", "SchedulerConfig"]
