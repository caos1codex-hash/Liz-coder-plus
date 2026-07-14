"""WorkflowOrchestrator — combina AgentOrchestrator + WorkflowEngine (Sprint 2.2).

Esta clase es la **API unificada** para usuarios que quieren ejecutar
workflows colaborativos. Internamente:

- Mantiene un `AgentOrchestrator` de Sprint 2.1 (registro de agentes,
  mensajería punto-a-punto, health check).
- Mantiene un `WorkflowEngine` de Sprint 2.2 (DAG, paralelismo, failover).
- Sincroniza los agentes registrados en el orchestrator con el engine.
- Expone métodos de coordinación: `delegate`, `request_help`,
  `transfer_context`, `wait_for`, `cancel_workflow`, `resume_workflow`.

Compatibilidad:

- `AgentOrchestrator` (Sprint 2.1) sigue siendo utilizable por sí solo.
- `WorkflowEngine` puede usarse por sí solo pasándole agentes manualmente.
- Esta clase es una capa de integración opcional.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..base import BaseAgent
from ..config import MultiAgentConfig
from ..enums import MessageType, Priority
from ..messages import Message
from ..orchestrator import AgentOrchestrator
from .engine import FailoverPolicy, WorkflowEngine, WorkflowExecutionResult
from .event_bus import EventBus
from .load_balancer import LoadBalancer
from .models import Workflow
from .scheduler import Scheduler, SchedulerConfig

logger = logging.getLogger(__name__)


class WorkflowOrchestrator:
    """API unificada: AgentOrchestrator + WorkflowEngine.

    Uso típico::

        wfo = WorkflowOrchestrator()
        await wfo.register_agent(PlannerAgent())
        await wfo.register_agent(CoderAgent())
        await wfo.register_agent(ReviewerAgent())

        wf = Workflow(name="refactor-and-review")
        wf.add_step(Step(name="write", agent="coder", action="write", input={...}))
        wf.add_step(Step(name="review", agent="reviewer", action="review",
                         depends_on=[<write_step_id>], input={...}))

        result = await wfo.run_workflow(wf)
    """

    def __init__(
        self,
        *,
        config: MultiAgentConfig | None = None,
        agent_orchestrator: AgentOrchestrator | None = None,
        workflow_engine: WorkflowEngine | None = None,
        event_bus: EventBus | None = None,
        scheduler_config: SchedulerConfig | None = None,
        failover: FailoverPolicy | None = None,
    ) -> None:
        self._config = config or MultiAgentConfig()
        self._agent_orch = agent_orchestrator or AgentOrchestrator(
            config=self._config
        )
        self._bus = event_bus or EventBus()
        # Crear engine reutilizando recursos del orchestrator cuando posible.
        self._engine = workflow_engine or WorkflowEngine(
            config=self._config,
            event_bus=self._bus,
            scheduler=Scheduler(
                load_balancer=LoadBalancer(),
                event_bus=self._bus,
                config=scheduler_config or SchedulerConfig(),
            ),
            failover=failover or FailoverPolicy(),
            agent_logger=self._agent_orch.logger,
        )
        self._workflows: dict[str, Workflow] = {}

    # ------------------------------------------------------------------
    # Propiedades
    # ------------------------------------------------------------------

    @property
    def agent_orchestrator(self) -> AgentOrchestrator:
        return self._agent_orch

    @property
    def engine(self) -> WorkflowEngine:
        return self._engine

    @property
    def event_bus(self) -> EventBus:
        return self._bus

    @property
    def config(self) -> MultiAgentConfig:
        return self._config

    # ------------------------------------------------------------------
    # Registro de agentes (delegado al AgentOrchestrator + sync al engine)
    # ------------------------------------------------------------------

    async def register_agent(self, agent: BaseAgent) -> BaseAgent:
        """Registra un agente en ambos: AgentOrchestrator y WorkflowEngine."""
        await self._agent_orch.register_agent(agent)
        self._engine.register_agent(agent)
        return agent

    async def remove_agent(self, name: str) -> bool:
        """Elimina un agente de ambos."""
        removed = await self._agent_orch.remove_agent(name)
        if removed:
            self._engine.unregister_agent(name)
        return removed

    def get_agent(self, name: str) -> BaseAgent | None:
        return self._agent_orch.get_agent(name)

    def list_agents(self) -> list[str]:
        return self._agent_orch.list_agents()

    # ------------------------------------------------------------------
    # Workflows
    # ------------------------------------------------------------------

    async def submit_workflow(self, workflow: Workflow) -> Workflow:
        """Registra un workflow sin ejecutarlo."""
        self._workflows[workflow.id] = workflow
        await self._engine.submit(workflow)
        return workflow

    async def run_workflow(
        self,
        workflow: Workflow,
        *,
        max_iterations: int = 1000,
    ) -> WorkflowExecutionResult:
        """Ejecuta un workflow completo."""
        if workflow.id not in self._workflows:
            self._workflows[workflow.id] = workflow
        return await self._engine.run(workflow, max_iterations=max_iterations)

    async def cancel_workflow(self, workflow_id: str, reason: str = "") -> bool:
        return await self._engine.cancel(workflow_id, reason=reason)

    async def pause_workflow(self, workflow_id: str) -> bool:
        return await self._engine.pause(workflow_id)

    async def resume_workflow(self, workflow_id: str) -> bool:
        return await self._engine.resume(workflow_id)

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> list[str]:
        return list(self._workflows.keys())

    # ------------------------------------------------------------------
    # Coordinación entre agentes
    # ------------------------------------------------------------------

    async def delegate(
        self,
        from_agent: str,
        to_agent: str,
        task: dict[str, Any],
        *,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        """Delega una tarea de un agente a otro vía el AgentOrchestrator.

        Crea una tarea en la cola del AgentOrchestrator dirigida al
        agente destino, con `delegated_by` en el payload.
        """
        payload = {
            **task,
            "delegated_by": from_agent,
            "workflow_id": workflow_id,
        }
        task_id = await self._agent_orch.enqueue(
            name=task.get("name", "delegated"),
            description=task.get("description", ""),
            payload=payload,
            priority=Priority(task.get("priority", "normal")),
            agent_name=to_agent,
        )
        # Notificar al agente destino vía mensaje.
        await self._agent_orch.send_to(
            to_agent,
            MessageType.REQUEST,
            {
                "type": "delegation",
                "from": from_agent,
                "task_id": task_id.id,
                "task": task,
            },
            correlation_id=workflow_id,
        )
        return {"task_id": task_id.id, "delegated_to": to_agent}

    async def request_help(
        self,
        from_agent: str,
        topic: str,
        *,
        workflow_id: str | None = None,
    ) -> Message | None:
        """Pide ayuda broadcast a todos los agentes."""
        msg = Message.request(
            sender=from_agent,
            receiver="*",
            payload={
                "type": "help_request",
                "topic": topic,
                "workflow_id": workflow_id,
            },
            correlation_id=workflow_id,
            priority=Priority.HIGH,
        )
        delivered = await self._agent_orch.message_bus.publish(msg)
        if delivered == 0:
            logger.warning("No agents answered help request from '%s'", from_agent)
        return msg

    async def transfer_context(
        self,
        workflow_id: str,
        from_agent: str,
        to_agent: str,
        *,
        include_files: bool = True,
        include_snippets: bool = True,
        include_variables: bool = True,
        include_messages: bool = False,
        include_memory: bool = True,
    ) -> dict[str, Any] | None:
        """Transfiere contexto compartido entre dos agentes."""
        ctx = self._engine.get_context(workflow_id)
        if ctx is None:
            return None
        return await ctx.transfer_context(
            from_agent, to_agent,
            include_files=include_files,
            include_snippets=include_snippets,
            include_variables=include_variables,
            include_messages=include_messages,
            include_memory=include_memory,
        )

    async def wait_for(
        self,
        workflow_id: str,
        step_id: str,
        *,
        timeout: float = 60.0,
    ) -> bool:
        """Espera a que un step específico termine.

        Devuelve True si el step terminó (completed/failed/skipped/cancelled),
        False si se agotó el timeout.
        """
        workflow = self._workflows.get(workflow_id)
        if workflow is None:
            return False
        step = workflow.steps.get(step_id)
        if step is None:
            return False
        if step.is_terminal:
            return True

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if step.is_terminal:
                return True
            await asyncio.sleep(0.05)
        return step.is_terminal

    # ------------------------------------------------------------------
    # Métricas y health
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """Métricas combinadas de AgentOrchestrator y WorkflowEngine."""
        return {
            "agents": self._agent_orch.metrics(),
            "workflows": self._engine.metrics(),
            "events": self._bus.metrics(),
            "tracked_workflows": len(self._workflows),
        }

    def health(self) -> dict[str, Any]:
        """Health combinado."""
        return {
            "agents": self._agent_orch.health(),
            "engine": {
                "running_workflows": len(self._engine._running),  # noqa: SLF001
                "registered_agents": len(self._engine.list_agents()),
            },
        }

    def workflow_metrics(self, workflow_id: str) -> dict[str, Any] | None:
        return self._engine.workflow_metrics(workflow_id)

    def workflow_events(
        self,
        workflow_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Devuelve los eventos asociados a un workflow."""
        events = self._bus.history(correlation_id=workflow_id, limit=limit)
        return [e.to_dict() for e in events]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Apaga orchestrator y engine."""
        await self._agent_orch.shutdown()
        # Limpiar contextos.
        for ctx in self._engine._contexts.values():  # noqa: SLF001
            await ctx.clear()
        await self._bus.clear()


__all__ = ["WorkflowOrchestrator"]
