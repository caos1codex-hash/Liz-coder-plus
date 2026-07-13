"""AgentOrchestrator — orquestador central (Sprint 2.1).

Responsabilidades:

- `registerAgent(agent)` / `removeAgent(name)` / `getAgent(name)`
- `dispatch(task)` — asigna una tarea al agente adecuado.
- `broadcast(message)` — envía un mensaje a todos los agentes.
- `pauseAll()` / `resumeAll()` — control global.
- `health()` — health check de todos los agentes.
- `metrics()` — métricas agregadas.

Internamente:

- Mantiene un `MessageBus` compartido.
- Mantiene una `PriorityQueue` de tareas pendientes.
- Mantiene un `AgentLogger` compartido.
- Heartbeat opcional (background task) que consulta `health()` de
  cada agente periódicamente.
- Respeta `MultiAgentConfig.max_agents`.

El orquestador es asyncio-safe.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from .base import BaseAgent, HealthReport
from .config import MultiAgentConfig
from .enums import (
    AgentStatus,
    HealthState,
    MessageType,
    Permission,
    Priority,
    TaskStatus,
)
from .logs import AgentLogger
from .messages import Message, MessageBus
from .queue import PriorityQueue, Task

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Orquestador central del sistema multi-agente.

    Uso típico::

        config = MultiAgentConfig()
        orchestrator = AgentOrchestrator(config=config)
        await orchestrator.register_agent(PlannerAgent())
        await orchestrator.register_agent(CoderAgent())

        task = Task(name="refactor", payload={"file": "x.py"})
        await orchestrator.enqueue(task)
        result = await orchestrator.run_once()
    """

    def __init__(
        self,
        *,
        config: MultiAgentConfig | None = None,
        message_bus: MessageBus | None = None,
        task_queue: PriorityQueue | None = None,
        agent_logger: AgentLogger | None = None,
    ) -> None:
        self._config: MultiAgentConfig = config or MultiAgentConfig()
        self._bus: MessageBus = message_bus or MessageBus()
        self._queue: PriorityQueue = task_queue or PriorityQueue(
            max_size=self._config.queue_max_size
        )
        self._logger: AgentLogger = agent_logger or AgentLogger(
            history_size=self._config.log_history_size,
        )
        self._agents: dict[str, BaseAgent] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._running: bool = False
        self._lock = asyncio.Lock()

        # Métricas globales
        self._metrics: dict[str, Any] = {
            "dispatched": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "broadcasts": 0,
            "start_time": None,
        }

    # ------------------------------------------------------------------
    # Propiedades
    # ------------------------------------------------------------------

    @property
    def config(self) -> MultiAgentConfig:
        return self._config

    @property
    def message_bus(self) -> MessageBus:
        return self._bus

    @property
    def queue(self) -> PriorityQueue:
        return self._queue

    @property
    def logger(self) -> AgentLogger:
        return self._logger

    @property
    def agent_count(self) -> int:
        return len(self._agents)

    @property
    def agents(self) -> dict[str, BaseAgent]:
        return dict(self._agents)

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Registro de agentes
    # ------------------------------------------------------------------

    async def register_agent(self, agent: BaseAgent) -> BaseAgent:
        """Registra un agente. Lanza ValueError si se excede el máximo."""
        if not isinstance(agent, BaseAgent):
            raise TypeError(
                f"Expected BaseAgent instance, got {type(agent).__name__}"
            )
        async with self._lock:
            if (
                len(self._agents) >= self._config.max_agents
                and agent.name not in self._agents
            ):
                raise ValueError(
                    f"Max agents reached ({self._config.max_agents}); "
                    f"cannot register '{agent.name}'"
                )
            if agent.name in self._agents:
                logger.warning("Replacing existing agent '%s'", agent.name)
            # Inyectar bus, logger, config si el agente no los tiene.
            if agent._bus is None:  # noqa: SLF001
                agent._bus = self._bus  # noqa: SLF001
                agent._subscription_token = await self._bus.subscribe(
                    agent.name, agent._handle_incoming  # noqa: SLF001
                )
            if agent._logger is None or agent._logger is not self._logger:  # noqa: SLF001
                # Reutilizar el logger del orquestador.
                agent._logger = self._logger  # noqa: SLF001
            self._agents[agent.name] = agent
        # Si el agente está en CREATED, arrancarlo.
        if agent.status == AgentStatus.CREATED:
            await agent.start()
        logger.info(
            "Registered agent '%s' (total=%d/%d)",
            agent.name, len(self._agents), self._config.max_agents,
        )
        return agent

    async def remove_agent(self, name: str) -> bool:
        """Detiene y elimina un agente por nombre."""
        async with self._lock:
            agent = self._agents.pop(name, None)
        if agent is None:
            return False
        await agent.stop()
        logger.info("Removed agent '%s'", name)
        return True

    def get_agent(self, name: str) -> BaseAgent | None:
        """Busca un agente por nombre."""
        return self._agents.get(name)

    def list_agents(self) -> list[str]:
        """Devuelve los nombres de los agentes registrados."""
        return list(self._agents.keys())

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        name: str,
        *,
        description: str = "",
        payload: dict[str, Any] | None = None,
        priority: Priority = Priority.NORMAL,
        agent_name: str = "",
        max_attempts: int | None = None,
    ) -> Task:
        """Crea y encola una tarea.

        Si `agent_name` no se especifica, el orquestador elegirá uno en
        `dispatch` según los objetivos de la tarea.
        """
        task = Task(
            name=name,
            description=description,
            priority=priority,
            agent_name=agent_name,
            payload=payload or {},
            max_attempts=max_attempts or self._config.retry.max_attempts,
        )
        await self._queue.enqueue(task)
        return task

    async def enqueue_task(self, task: Task) -> Task:
        """Encola una `Task` ya construida."""
        return await self._queue.enqueue(task)

    async def dispatch(self, task: Task | None = None) -> dict[str, Any]:
        """Saca la siguiente tarea y la despacha al agente adecuado.

        Si `task` es None, hace `dequeue` de la cola.
        Si `task.agent_name` está vacío, selecciona un agente capaz.
        """
        if task is None:
            task = self._queue.dequeue_nowait()
            if task is None:
                return {"status": "empty", "agent": None, "task_id": None}
        async with self._lock:
            self._metrics["dispatched"] += 1
        # Seleccionar agente
        agent = self._select_agent(task)
        if agent is None:
            await self._queue.fail(task.id, "no agent available")
            await self._logger.log_error(
                "orchestrator", task.id,
                f"no agent available for task '{task.name}'",
            )
            return {
                "status": "error",
                "error": "no agent available",
                "task_id": task.id,
            }
        task.agent_name = agent.name
        # Despachar
        task_dict = {
            "id": task.id,
            "name": task.name,
            "description": task.description,
            "payload": task.payload,
            "priority": task.priority.value,
        }
        result = await agent.execute(task_dict)
        if result.get("status") == "ok":
            await self._queue.complete(task.id, result.get("result"))
            async with self._lock:
                self._metrics["completed"] += 1
        else:
            err = result.get("error", "unknown error")
            await self._queue.fail(task.id, err)
            async with self._lock:
                self._metrics["failed"] += 1
            # ¿Retry?
            if task.can_retry():
                logger.info(
                    "Retrying task '%s' (attempt %d/%d)",
                    task.name, task.attempts, task.max_attempts,
                )
                await self._queue.retry(task.id)
        return result

    async def run_once(self, timeout: float | None = None) -> dict[str, Any]:
        """Dispatch de una sola tarea. Devuelve el resultado o `{"status": "empty"}`."""
        return await asyncio.wait_for(
            self.dispatch(), timeout=timeout or self._config.task_timeout
        )

    async def run_until_empty(
        self, *, max_iterations: int = 1000
    ) -> list[dict[str, Any]]:
        """Despacha tareas hasta vaciar la cola o alcanzar el máximo."""
        results: list[dict[str, Any]] = []
        for _ in range(max_iterations):
            result = await self.dispatch()
            if result.get("status") == "empty":
                break
            results.append(result)
        return results

    def _select_agent(self, task: Task) -> BaseAgent | None:
        """Heurística simple para elegir agente.

        - Si `task.agent_name` está seteado y existe, lo usa.
        - Si el payload tiene `kind`, busca un agente con ese `kind`
          en sus objectives.
        - Sino, devuelve el primer agente IDLE.
        """
        # 1. Asignación explícita
        if task.agent_name:
            agent = self._agents.get(task.agent_name)
            if agent and not agent.is_stopped:
                return agent
        # 2. Por kind en payload
        kind = task.payload.get("kind") if task.payload else None
        if kind:
            for agent in self._agents.values():
                if any(kind in obj for obj in agent.objectives):
                    if not agent.is_stopped:
                        return agent
        # 3. Por nombre de tarea
        task_name_lower = task.name.lower()
        for agent in self._agents.values():
            if any(word in agent.name.lower() for word in task_name_lower.split("-")):
                if not agent.is_stopped:
                    return agent
        # 4. Primer IDLE
        for agent in self._agents.values():
            if agent.status in (AgentStatus.IDLE, AgentStatus.CREATED):
                return agent
        return None

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    async def broadcast(
        self,
        payload: dict[str, Any] | None = None,
        *,
        type_: MessageType = MessageType.EVENT,
        priority: Priority = Priority.NORMAL,
    ) -> int:
        """Envía un mensaje broadcast a todos los agentes."""
        msg = Message(
            sender="orchestrator",
            receiver="*",
            type=type_,
            payload=payload or {},
            priority=priority,
        )
        delivered = await self._bus.publish(msg)
        async with self._lock:
            self._metrics["broadcasts"] += 1
        return delivered

    async def send_to(
        self,
        receiver: str,
        type_,  # MessageType
        payload: dict[str, Any] | None = None,
        *,
        priority: Priority = Priority.NORMAL,
    ) -> Message:
        """Envía un mensaje a un agente concreto."""
        from .enums import MessageType as _MT
        msg_type = type_ if isinstance(type_, _MT) else _MT(type_)
        msg = Message(
            sender="orchestrator",
            receiver=receiver,
            type=msg_type,
            payload=payload or {},
            priority=priority,
        )
        await self._bus.publish(msg)
        return msg

    # ------------------------------------------------------------------
    # Pause / resume global
    # ------------------------------------------------------------------

    async def pause_all(self) -> int:
        """Pausa todos los agentes. Devuelve cuántos se pausaron."""
        count = 0
        for agent in self._agents.values():
            try:
                if agent.status in (AgentStatus.IDLE, AgentStatus.BUSY):
                    await agent.pause()
                    count += 1
            except Exception:  # noqa: BLE001
                logger.exception("Failed to pause agent '%s'", agent.name)
        logger.info("Paused %d/%d agents", count, len(self._agents))
        return count

    async def resume_all(self) -> int:
        """Reanuda todos los agentes pausados."""
        count = 0
        for agent in self._agents.values():
            try:
                if agent.status == AgentStatus.PAUSED:
                    await agent.resume()
                    count += 1
            except Exception:  # noqa: BLE001
                logger.exception("Failed to resume agent '%s'", agent.name)
        logger.info("Resumed %d/%d agents", count, len(self._agents))
        return count

    async def stop_all(self) -> int:
        """Detiene todos los agentes."""
        count = 0
        for agent in self._agents.values():
            try:
                if agent.status != AgentStatus.STOPPED:
                    await agent.stop()
                    count += 1
            except Exception:  # noqa: BLE001
                logger.exception("Failed to stop agent '%s'", agent.name)
        await self.stop_heartbeat()
        return count

    # ------------------------------------------------------------------
    # Health & metrics
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Health check de todos los agentes."""
        reports: dict[str, HealthReport] = {
            name: agent.health() for name, agent in self._agents.items()
        }
        # Resumen
        summary: dict[str, int] = {s.value: 0 for s in HealthState}
        for r in reports.values():
            summary[r.state.value] += 1
        return {
            "agents": {name: r.to_dict() for name, r in reports.items()},
            "summary": summary,
            "total_agents": len(self._agents),
            "healthy": summary.get(HealthState.ALIVE.value, 0)
                       + summary.get(HealthState.IDLE.value, 0),
            "degraded": summary.get(HealthState.BUSY.value, 0)
                        + summary.get(HealthState.ERROR.value, 0),
            "dead": summary.get(HealthState.DEAD.value, 0),
        }

    def metrics(self) -> dict[str, Any]:
        """Métricas agregadas del orquestador."""
        return {
            "orchestrator": dict(self._metrics),
            "agents": {
                name: {
                    "tasks_total": a._tasks_total,  # noqa: SLF001
                    "tasks_ok": a._tasks_ok,  # noqa: SLF001
                    "tasks_failed": a._tasks_failed,  # noqa: SLF001
                    "uptime_s": round(a.uptime_s, 3),
                    "status": a.status.value,
                }
                for name, a in self._agents.items()
            },
            "queue": self._queue.summary(),
            "bus": self._bus.stats(),
            "logs": self._logger.stats(),
        }

    def agent_status(self) -> dict[str, str]:
        """Devuelve {agent_name: status_value}."""
        return {name: a.status.value for name, a in self._agents.items()}

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def start_heartbeat(self, interval: float | None = None) -> None:
        """Arranca un background task que hace health() periódicamente."""
        if self._heartbeat_task is not None:
            return
        if interval is None:
            interval = self._config.heartbeat_interval
        self._running = True

        async def _beat() -> None:
            while self._running:
                try:
                    report = self.health()
                    alive = report["healthy"]
                    total = report["total_agents"]
                    logger.debug(
                        "Heartbeat: %d/%d agents healthy", alive, total
                    )
                    # Emitir evento broadcast con el reporte.
                    await self._bus.publish(
                        Message.system(
                            sender="orchestrator",
                            receiver="*",
                            payload={"heartbeat": report["summary"]},
                        )
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Heartbeat failed")
                await asyncio.sleep(interval)

        self._heartbeat_task = asyncio.create_task(_beat())
        logger.info("Heartbeat started (interval=%.1fs)", interval)

    async def stop_heartbeat(self) -> None:
        """Detiene el heartbeat."""
        self._running = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
            logger.info("Heartbeat stopped")

    # ------------------------------------------------------------------
    # Lifecycle del propio orquestador
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Apaga el orquestador: heartbeat off, stop_all, clear bus."""
        await self.stop_heartbeat()
        await self.stop_all()
        await self._bus.clear()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serializa el estado del orquestador."""
        return {
            "config": self._config.to_dict(),
            "agents": [a.serialize() for a in self._agents.values()],
            "queue": self._queue.summary(),
            "bus": self._bus.stats(),
            "metrics": self.metrics(),
        }


__all__ = ["AgentOrchestrator"]
