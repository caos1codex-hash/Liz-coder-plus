"""RegistryAwareScheduler — Scheduler que usa CapabilityResolver (Sprint 2.2/2).

Este scheduler extiende el `Scheduler` de Sprint 2.2 para que, en lugar
de elegir agentes por nombre o vía el LoadBalancer directo, use el
`CapabilityResolver` para resolver cada step a un agente capaz.

Compatibilidad:

- Si el workflow usa `step.agent` concreto (legacy), el scheduler usa
  el `BackwardCompatibilityAdapter` para traducir a capability.
- Si el workflow no especifica `step.agent`, el scheduler deriva la
  capability de `step.action` vía el adapter.
- El `LoadBalancer` original sigue usándose como fallback si el
  resolver no encuentra agentes (p.ej. capabilities no declaradas).

El scheduler NO reemplaza al `Scheduler` original; es una alternativa
que se puede usar cuando se quiere el nuevo sistema basado en
capabilities.
"""

from __future__ import annotations

import logging

from ..base import BaseAgent
from ..enums import priority_weight
from ..registry.adapter import BackwardCompatibilityAdapter
from ..registry.agent_registry import AgentRegistry
from ..registry.resolver import CapabilityResolver, ResolutionResult
from .enums import WorkflowEvent, WorkflowStatus
from .event_bus import EventBus
from .load_balancer import LoadBalancer
from .models import Step, Workflow
from .scheduler import ScheduleDecision, Scheduler, SchedulerConfig

logger = logging.getLogger(__name__)


class RegistryAwareScheduler(Scheduler):
    """Scheduler que resuelve agentes vía CapabilityResolver.

    Hereda de `Scheduler` para reutilizar `mark_step_started/finished`
    y la emisión de eventos. Sobreescribe `schedule()` para usar el
    resolver en lugar del LoadBalancer directo.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        resolver: CapabilityResolver | None = None,
        adapter: BackwardCompatibilityAdapter | None = None,
        load_balancer: LoadBalancer | None = None,
        event_bus: EventBus | None = None,
        config: SchedulerConfig | None = None,
    ) -> None:
        super().__init__(
            load_balancer=load_balancer or LoadBalancer(),
            event_bus=event_bus,
            config=config or SchedulerConfig(),
        )
        self._registry = registry
        self._resolver = resolver or CapabilityResolver(registry=registry, event_bus=event_bus)
        self._adapter = adapter or BackwardCompatibilityAdapter(
            registry=registry, resolver=self._resolver,
        )

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    @property
    def resolver(self) -> CapabilityResolver:
        return self._resolver

    @property
    def adapter(self) -> BackwardCompatibilityAdapter:
        return self._adapter

    # ------------------------------------------------------------------
    # schedule (override)
    # ------------------------------------------------------------------

    async def schedule(
        self,
        workflow: Workflow,
        agents: list[BaseAgent] | None = None,
        *,
        exclude: dict[str, set[str]] | None = None,
    ) -> list[ScheduleDecision]:
        """Planifica los próximos steps usando el CapabilityResolver.

        Args:
            workflow: Workflow a planificar.
            agents:   Ignorado (los agentes se obtienen del registry).
                      Mantenido por compatibilidad de firma con Scheduler.
            exclude:  {step_id: {agent_names_a_excluir}}.
        """
        exclude = exclude or {}
        if workflow.status.value == "pending":
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

        # 3. Ordenar steps por prioridad.
        ready_steps = [workflow.steps[sid] for sid in ready_step_ids]
        ready_steps.sort(key=lambda s: (priority_weight(workflow.priority), s.name))

        decisions: list[ScheduleDecision] = []
        agents_busy: set[str] = set(self._active.values())

        for step in ready_steps:
            if len(decisions) >= available_slots:
                break
            if len(agents_busy) >= self._config.max_parallel_agents:
                break

            excluded_for_step = exclude.get(step.id, set()) | agents_busy

            # 4. Resolver agente vía adapter.
            result: ResolutionResult = await self._adapter.resolve_step_agent(
                step, exclude=excluded_for_step
            )

            if not result.resolved or result.agent_record is None:
                # Fallback: si el adapter no resuelve, intentar con
                # LoadBalancer sobre agentes disponibles del registry.
                fallback_agent = self._fallback_select(step, excluded_for_step)
                if fallback_agent is None:
                    continue
                agent_name = fallback_agent.name
                reason = "fallback load balancer (resolver miss)"
            else:
                agent_name = result.agent_record.name
                reason = (
                    f"resolved via capability '{result.capability}' "
                    f"(score={result.score:.3f})"
                )

            # 5. Marcar uso en el record.
            record = self._registry.get(agent_name)
            if record is not None:
                # Marcar como busy (el registry lo detecta vía agent.status).
                pass

            decision = ScheduleDecision(
                step_id=step.id,
                agent_name=agent_name,
                reason=reason,
                priority=workflow.priority,
            )
            decisions.append(decision)
            agents_busy.add(agent_name)
            self._lb.assign(agent_name)

        return decisions

    def _fallback_select(
        self,
        step: Step,
        exclude: set[str],
    ) -> BaseAgent | None:
        """Fallback: usa el LoadBalancer sobre los agentes del registry."""
        candidates = [
            record.agent for record in self._registry.get_all()
            if record.name not in exclude and record.is_available
        ]
        if not candidates:
            return None
        return self._lb.select(
            candidates,
            action=step.action or step.name,
            exclude=exclude,
        )

    # ------------------------------------------------------------------
    # mark_step_finished (override para registrar usage en el record)
    # ------------------------------------------------------------------

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
        """Marca step finished y registra usage en el AgentRecord."""
        await super().mark_step_finished(
            step, agent_name, workflow,
            success=success, duration_ms=duration_ms, error=error,
        )
        # Registrar usage en el registry.
        record = self._registry.get(agent_name)
        if record is not None:
            record.record_usage(success=success, duration_ms=duration_ms)


__all__ = ["RegistryAwareScheduler"]
