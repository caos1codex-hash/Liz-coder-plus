"""BaseAgent — clase abstracta para el sistema multi-agente (Sprint 2.1).

Diseño:

- Cada agente tiene `id`, `name`, `description`, `status`, `permissions`,
  `memory`, `tools`, `objectives`.
- Métodos lifecycle: `execute()`, `stop()`, `pause()`, `resume()`,
  `health()`, `serialize()`.
- `execute(task)` es el método abstracto que cada subclase implementa.
- `health()` devuelve un `HealthReport` con `state` (alive/busy/idle/error)
  y métricas.
- `serialize()` produce un dict plano serializable.
- El agente se suscribe al `MessageBus` para recibir mensajes dirigidos
  a él (sobre `name`).
- El agente tiene un `event` asyncio para soportar `pause`/`resume`
  sin perder tareas en curso.

Compatibilidad con Sprint 1:

- No reemplaza el `BaseAgent` de `packages/agents/src/base.py`. Esa
  clase sigue existiendo para los agentes del Orchestrator clásico.
- Esta nueva `BaseAgent` puede adaptarse al Protocol `Agent` de
  `packages/core/src/protocols.py` mediante un adapter (ver
  `multiagent.adapters.Sprint1Adapter`).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .config import MultiAgentConfig
from .enums import (
    AgentStatus,
    HealthState,
    Permission,
    can_transition,
)
from .logs import AgentLogger
from .memory import AgentMemory
from .messages import Message, MessageBus

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Health report
# ----------------------------------------------------------------------


@dataclass
class HealthReport:
    """Resultado del health check de un agente.

    Attributes:
        agent:       Nombre del agente.
        state:       HealthState (alive/busy/idle/error/dead).
        status:      AgentStatus actual.
        uptime_s:    Segundos desde la creación.
        tasks_total: Tareas totales procesadas.
        tasks_ok:    Tareas completadas.
        tasks_failed: Tareas fallidas.
        last_error:  Último error, si lo hay.
        memory_kb:   RSS aproximado.
        metadata:    Información adicional libre.
    """

    agent: str
    state: HealthState
    status: AgentStatus
    uptime_s: float = 0.0
    tasks_total: int = 0
    tasks_ok: int = 0
    tasks_failed: int = 0
    last_error: str | None = None
    memory_kb: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "state": self.state.value,
            "status": self.status.value,
            "uptime_s": round(self.uptime_s, 3),
            "tasks_total": self.tasks_total,
            "tasks_ok": self.tasks_ok,
            "tasks_failed": self.tasks_failed,
            "last_error": self.last_error,
            "memory_kb": self.memory_kb,
            "metadata": dict(self.metadata),
        }


# ----------------------------------------------------------------------
# Task (alias para multiagent.queue.Task)
# ----------------------------------------------------------------------


# Import diferido para evitar ciclos: base no depende directamente de queue.
# El tipo se declara como Any en runtime para no romper duck-typing.


# ----------------------------------------------------------------------
# BaseAgent
# ----------------------------------------------------------------------


class BaseAgent(ABC):
    """Clase base abstracta para todos los agentes del sistema multi-agente.

    Subclases deben:

    - Llamar `super().__init__(...)`.
    - Implementar `execute(task: dict) -> dict` (coroutine).
    - Opcionalmente implementar `on_message(message)` para reaccionar
      a mensajes entrantes.

    El agente se suscribe automáticamente al `MessageBus` sobre su
    `name` para recibir mensajes.
    """

    # Subclases pueden override de estos defaults de clase:
    default_permissions: frozenset[Permission] = frozenset()
    default_objectives: list[str] = []
    default_tools: list[str] = []

    def __init__(
        self,
        name: str,
        *,
        description: str = "",
        permissions: frozenset[Permission] | None = None,
        objectives: list[str] | None = None,
        tools: list[str] | None = None,
        memory: AgentMemory | None = None,
        config: MultiAgentConfig | None = None,
        message_bus: MessageBus | None = None,
        logger: AgentLogger | None = None,
        agent_id: str | None = None,
    ) -> None:
        # Identidad
        self._id: str = agent_id or str(uuid.uuid4())
        self._name: str = name
        self._description: str = description or self.__doc__ or ""

        # Estado
        self._status: AgentStatus = AgentStatus.CREATED
        self._created_at: float = time.monotonic()
        self._last_error: str | None = None

        # Capacidades declarativas
        self._permissions: frozenset[Permission] = (
            permissions if permissions is not None else self.default_permissions
        )
        self._objectives: list[str] = list(
            objectives if objectives is not None else self.default_objectives
        )
        self._tools: list[str] = list(
            tools if tools is not None else self.default_tools
        )

        # Recursos
        self._memory: AgentMemory = memory or AgentMemory(
            short_capacity=config.memory_short_capacity if config else 64,
            long_capacity=config.memory_long_capacity if config else 1024,
        )
        self._config: MultiAgentConfig = config or MultiAgentConfig()
        self._bus: MessageBus | None = message_bus
        self._logger: AgentLogger = logger or AgentLogger(
            history_size=self._config.log_history_size,
        )

        # Métricas
        self._tasks_total: int = 0
        self._tasks_ok: int = 0
        self._tasks_failed: int = 0

        # Control de pausa
        self._pause_event: asyncio.Event = asyncio.Event()
        self._pause_event.set()  # no pausado al inicio
        self._stop_requested: bool = False
        self._current_task_id: str | None = None
        self._subscription_token: str | None = None

        logger.info(
            "Agent '%s' (id=%s) instantiated (permissions=%d tools=%d)",
            self._name, self._id, len(self._permissions), len(self._tools),
        )

    # ------------------------------------------------------------------
    # Propiedades de identidad
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def status(self) -> AgentStatus:
        return self._status

    @property
    def permissions(self) -> frozenset[Permission]:
        return self._permissions

    @property
    def objectives(self) -> list[str]:
        return list(self._objectives)

    @property
    def tools(self) -> list[str]:
        return list(self._tools)

    @property
    def memory(self) -> AgentMemory:
        return self._memory

    @property
    def config(self) -> MultiAgentConfig:
        return self._config

    @property
    def message_bus(self) -> MessageBus | None:
        return self._bus

    @property
    def logger(self) -> AgentLogger:
        return self._logger

    @property
    def uptime_s(self) -> float:
        return time.monotonic() - self._created_at

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    @property
    def is_busy(self) -> bool:
        return self._status == AgentStatus.BUSY

    @property
    def is_stopped(self) -> bool:
        return self._status == AgentStatus.STOPPED

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # ------------------------------------------------------------------
    # Permisos
    # ------------------------------------------------------------------

    def has_permission(self, perm: Permission) -> bool:
        """Devuelve True si el agente tiene el permiso (o UNRESTRICTED)."""
        return (
            Permission.UNRESTRICTED in self._permissions
            or perm in self._permissions
        )

    def require_permission(self, perm: Permission) -> None:
        """Lanza PermissionError si el agente no tiene el permiso."""
        if not self.has_permission(perm):
            raise PermissionError(
                f"Agent '{self._name}' lacks permission '{perm.value}'"
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Arranca el agente: transiciona CREATED → IDLE y se suscribe al bus."""
        if self._status != AgentStatus.CREATED:
            raise RuntimeError(
                f"Agent '{self._name}' can only start from CREATED "
                f"(current: {self._status.value})"
            )
        self._set_status(AgentStatus.IDLE)
        if self._bus is not None:
            self._subscription_token = await self._bus.subscribe(
                self._name, self._handle_incoming
            )
        self._memory.add_short(
            {"event": "agent_started", "agent": self._name},
            tags=["lifecycle"],
        )
        logger.info("Agent '%s' started", self._name)

    async def stop(self) -> None:
        """Detiene el agente. Interrumpe la tarea en curso si la hay."""
        self._stop_requested = True
        if self._bus is not None and self._subscription_token is not None:
            await self._bus.unsubscribe(self._subscription_token)
            self._subscription_token = None
        # Liberar si está pausado para que execute despierte y vea stop.
        self._pause_event.set()
        if self._status != AgentStatus.STOPPED:
            self._set_status(AgentStatus.STOPPED)
        self._memory.add_short(
            {"event": "agent_stopped", "agent": self._name},
            tags=["lifecycle"],
        )
        logger.info("Agent '%s' stopped", self._name)

    async def pause(self) -> None:
        """Pausa al agente: no acepta nuevas tareas hasta resume()."""
        if not can_transition(self._status, AgentStatus.PAUSED):
            raise RuntimeError(
                f"Agent '{self._name}' cannot pause from {self._status.value}"
            )
        self._pause_event.clear()
        self._set_status(AgentStatus.PAUSED)
        self._memory.add_short(
            {"event": "agent_paused", "agent": self._name},
            tags=["lifecycle"],
        )
        logger.info("Agent '%s' paused", self._name)

    async def resume(self) -> None:
        """Reanuda al agente pausado."""
        if not can_transition(self._status, AgentStatus.IDLE):
            raise RuntimeError(
                f"Agent '{self._name}' cannot resume from {self._status.value}"
            )
        self._pause_event.set()
        self._set_status(AgentStatus.IDLE)
        self._memory.add_short(
            {"event": "agent_resumed", "agent": self._name},
            tags=["lifecycle"],
        )
        logger.info("Agent '%s' resumed", self._name)

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta una tarea.

        Args:
            task: Diccionario con al menos `name` y `payload`.

        Returns:
            Dict con `status` (ok/error), `result` y `agent`.
        """
        if self._stop_requested:
            return {
                "agent": self._name,
                "status": "error",
                "error": "agent stopped",
                "result": None,
            }
        if self._status == AgentStatus.PAUSED:
            # Esperar a resume (con timeout para no bloquear indefinidamente).
            try:
                await asyncio.wait_for(
                    self._pause_event.wait(),
                    timeout=self._config.default_timeout,
                )
            except asyncio.TimeoutError:
                return {
                    "agent": self._name,
                    "status": "error",
                    "error": "paused timeout",
                    "result": None,
                }
            if self._stop_requested:
                return {
                    "agent": self._name,
                    "status": "error",
                    "error": "agent stopped while paused",
                    "result": None,
                }

        # Transición IDLE → BUSY
        previous_status = self._status
        if previous_status == AgentStatus.PAUSED:
            # ya despausado arriba
            previous_status = AgentStatus.IDLE
        if previous_status == AgentStatus.IDLE:
            self._set_status(AgentStatus.BUSY)
        elif previous_status == AgentStatus.ERROR:
            # Auto-recuperación ligera.
            self._set_status(AgentStatus.BUSY)
        else:
            raise RuntimeError(
                f"Agent '{self._name}' cannot execute from {self._status.value}"
            )

        task_id = task.get("id") or str(uuid.uuid4())
        self._current_task_id = task_id
        self._tasks_total += 1
        task_name = task.get("name", "unnamed")
        await self._logger.log_start(self._name, task_id, f"executing: {task_name}")

        start = time.monotonic()
        try:
            # Ejecución con timeout global.
            result = await asyncio.wait_for(
                self._execute_impl(task),
                timeout=self._config.task_timeout,
            )
            duration_ms = (time.monotonic() - start) * 1000.0
            await self._logger.log_end(
                self._name, task_id,
                duration_ms=duration_ms,
                tokens_prompt=int(result.get("tokens_prompt", 0)) if isinstance(result, dict) else 0,
                tokens_completion=int(result.get("tokens_completion", 0)) if isinstance(result, dict) else 0,
                tools_used=result.get("tools_used", []) if isinstance(result, dict) else [],
            )
            self._tasks_ok += 1
            self._set_status(AgentStatus.IDLE)
            return {
                "agent": self._name,
                "status": "ok",
                "result": result,
                "task_id": task_id,
                "duration_ms": duration_ms,
            }
        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000.0
            err = f"task timeout after {self._config.task_timeout}s"
            self._tasks_failed += 1
            self._last_error = err
            await self._logger.log_error(self._name, task_id, err, message="timeout")
            self._set_status(AgentStatus.ERROR)
            return {
                "agent": self._name,
                "status": "error",
                "error": err,
                "task_id": task_id,
                "duration_ms": duration_ms,
            }
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.monotonic() - start) * 1000.0
            self._tasks_failed += 1
            self._last_error = str(exc)
            await self._logger.log_error(self._name, task_id, exc)
            self._set_status(AgentStatus.ERROR)
            return {
                "agent": self._name,
                "status": "error",
                "error": str(exc),
                "task_id": task_id,
                "duration_ms": duration_ms,
            }
        finally:
            self._current_task_id = None

    @abstractmethod
    async def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        """Implementación concreta de la ejecución. Debe devolver un dict.

        Las subclases tienen acceso a `self._memory`, `self._tools`,
        `self._permissions`, etc.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Mensajería entrante
    # ------------------------------------------------------------------

    async def _handle_incoming(self, message: Message) -> None:
        """Handler por defecto para mensajes del bus.

        Las subclases pueden override de `on_message`.
        """
        try:
            await self.on_message(message)
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.exception(
                "Agent '%s' failed handling message %s", self._name, message.id
            )

    async def on_message(self, message: Message) -> None:
        """Callback para mensajes entrantes. Override en subclases."""
        # Por defecto: almacenar en memoria corta para contexto.
        self._memory.add_short(
            {
                "from": message.sender,
                "type": message.type.value,
                "payload": message.payload,
            },
            tags=["message", message.type.value],
        )

    async def send_message(
        self,
        receiver: str,
        type_,  # MessageType
        payload: dict[str, Any] | None = None,
        *,
        priority=None,  # Priority
        correlation_id: str | None = None,
    ) -> Message | None:
        """Envía un mensaje a otro agente vía el bus compartido."""
        if self._bus is None:
            logger.warning("Agent '%s' has no message bus; cannot send", self._name)
            return None
        from .enums import MessageType, Priority  # local import to avoid cycle
        msg_type = type_ if isinstance(type_, MessageType) else MessageType(type_)
        msg_priority = priority if isinstance(priority, Priority) else (
            priority if priority is None else Priority(priority)
        )
        if msg_priority is None:
            msg_priority = Priority.NORMAL
        msg = Message(
            sender=self._name,
            receiver=receiver,
            type=msg_type,
            payload=payload or {},
            priority=msg_priority,
            correlation_id=correlation_id,
        )
        await self._bus.publish(msg)
        return msg

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> HealthReport:
        """Devuelve un reporte de salud del agente."""
        if self._status == AgentStatus.STOPPED:
            state = HealthState.DEAD
        elif self._status == AgentStatus.ERROR:
            state = HealthState.ERROR
        elif self._status == AgentStatus.BUSY:
            state = HealthState.BUSY
        elif self._status in (AgentStatus.IDLE, AgentStatus.CREATED, AgentStatus.PAUSED):
            state = HealthState.IDLE
        else:
            state = HealthState.ALIVE

        # `alive` significa "responde": si no está parado ni en error, alive.
        if state == HealthState.IDLE and self._status != AgentStatus.PAUSED:
            state = HealthState.ALIVE

        return HealthReport(
            agent=self._name,
            state=state,
            status=self._status,
            uptime_s=self.uptime_s,
            tasks_total=self._tasks_total,
            tasks_ok=self._tasks_ok,
            tasks_failed=self._tasks_failed,
            last_error=self._last_error,
            metadata={
                "current_task_id": self._current_task_id,
                "is_paused": self.is_paused,
                "tools": list(self._tools),
                "permissions": sorted(p.value for p in self._permissions),
            },
        )

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serializa el agente a un dict plano."""
        return {
            "id": self._id,
            "name": self._name,
            "description": self._description,
            "status": self._status.value,
            "permissions": sorted(p.value for p in self._permissions),
            "objectives": list(self._objectives),
            "tools": list(self._tools),
            "tasks_total": self._tasks_total,
            "tasks_ok": self._tasks_ok,
            "tasks_failed": self._tasks_failed,
            "last_error": self._last_error,
            "uptime_s": round(self.uptime_s, 3),
            "current_task_id": self._current_task_id,
            "memory": self._memory.to_dict(),
            "health": self.health().to_dict(),
        }

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _set_status(self, new_status: AgentStatus) -> None:
        """Transición de estado con validación (no raise; log warning)."""
        if not can_transition(self._status, new_status):
            logger.warning(
                "Agent '%s' invalid transition %s -> %s (forcing)",
                self._name, self._status.value, new_status.value,
            )
        self._status = new_status

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} name={self._name!r} "
            f"id={self._id[:8]} status={self._status.value}>"
        )


__all__ = ["BaseAgent", "HealthReport"]
