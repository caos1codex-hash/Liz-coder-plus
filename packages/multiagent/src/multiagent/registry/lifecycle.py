"""Agent Lifecycle profesional (Sprint 2.3).

Estados del ciclo de vida:

    CREATED → INITIALIZING → READY ⇄ RUNNING → STOPPED
                                     ↓
                                    ERROR → READY (recovery) → STOPPED

Esta es una capa **nueva** que convive con el `AgentStatus` de Sprint 2.1
(CREATED/IDLE/BUSY/PAUSED/ERROR/STOPPED). El mapeo es:

    CREATED      → CREATED
    INITIALIZING → (nuevo, entre CREATED y READY)
    READY        → IDLE
    RUNNING      → BUSY
    ERROR        → ERROR
    STOPPED      → STOPPED

El `LifecycleManager` controla las transiciones y expone los métodos:

- `initialize()` — CREATED → INITIALIZING → READY
- `start()`      — READY → RUNNING (alias de "está ocupado")
- `stop()`       — cualquier estado → STOPPED
- `health_check()` — devuelve un `LifecycleHealth` con state + uptime + errores

Compatibilidad:

- `BaseAgent` de Sprint 2.1 sigue usando `AgentStatus` (IDLE/BUSY/...).
- El `LifecycleManager` se aplica **como wrapper** sobre un `BaseAgent`
  existente, sin modificarlo. Mantiene su propio estado paralelo
  (`LifecycleState`) y lo sincroniza con el `AgentStatus` del agente.
- Si un agente no pasa por `initialize()`, el lifecycle se auto-sincroniza
  desde el estado actual del agente (compatibilidad hacia atrás).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ..base import BaseAgent
from ..enums import AgentStatus

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# LifecycleState
# ----------------------------------------------------------------------


class LifecycleState(str, Enum):
    """Estados del ciclo de vida profesional de un agente.

    Transiciones::

        CREATED → INITIALIZING → READY ⇄ RUNNING
                                       ↓
                                      ERROR → READY (recovery)
        READY / RUNNING / ERROR → STOPPED (terminal)

    Diferencias con `AgentStatus` (Sprint 2.1):

    - `INITIALIZING` es nuevo: representa la fase de setup asíncrono.
    - `READY` equivale a `AgentStatus.IDLE`.
    - `RUNNING` equivale a `AgentStatus.BUSY`.
    - No hay equivalente a `PAUSED` (se maneja a nivel de task, no de agente).
    """

    CREATED = "created"
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    ERROR = "error"
    STOPPED = "stopped"


# Mapa de transiciones permitidas.
_ALLOWED_LIFECYCLE_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.CREATED: frozenset({
        LifecycleState.INITIALIZING, LifecycleState.STOPPED, LifecycleState.ERROR,
    }),
    LifecycleState.INITIALIZING: frozenset({
        LifecycleState.READY, LifecycleState.ERROR, LifecycleState.STOPPED,
    }),
    LifecycleState.READY: frozenset({
        LifecycleState.RUNNING, LifecycleState.ERROR, LifecycleState.STOPPED,
    }),
    LifecycleState.RUNNING: frozenset({
        LifecycleState.READY, LifecycleState.ERROR, LifecycleState.STOPPED,
    }),
    LifecycleState.ERROR: frozenset({
        LifecycleState.READY, LifecycleState.STOPPED,
    }),
    LifecycleState.STOPPED: frozenset(),  # terminal
}


def can_transition_lifecycle(current: LifecycleState, target: LifecycleState) -> bool:
    """Devuelve True si el lifecycle puede transicionar de `current` a `target`."""
    return target in _ALLOWED_LIFECYCLE_TRANSITIONS.get(current, frozenset())


# Mapeo LifecycleState ↔ AgentStatus (Sprint 2.1).
_LIFECYCLE_TO_AGENT: dict[LifecycleState, AgentStatus] = {
    LifecycleState.CREATED: AgentStatus.CREATED,
    LifecycleState.INITIALIZING: AgentStatus.CREATED,  # no hay equivalente directo
    LifecycleState.READY: AgentStatus.IDLE,
    LifecycleState.RUNNING: AgentStatus.BUSY,
    LifecycleState.ERROR: AgentStatus.ERROR,
    LifecycleState.STOPPED: AgentStatus.STOPPED,
}

_AGENT_TO_LIFECYCLE: dict[AgentStatus, LifecycleState] = {
    AgentStatus.CREATED: LifecycleState.CREATED,
    AgentStatus.IDLE: LifecycleState.READY,
    AgentStatus.BUSY: LifecycleState.RUNNING,
    AgentStatus.PAUSED: LifecycleState.READY,  # pausado ≈ ready (no acepta tasks)
    AgentStatus.ERROR: LifecycleState.ERROR,
    AgentStatus.STOPPED: LifecycleState.STOPPED,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return time.time()


# ----------------------------------------------------------------------
# LifecycleHealth
# ----------------------------------------------------------------------


@dataclass
class LifecycleHealth:
    """Resultado del health_check() de un lifecycle.

    Attributes:
        agent_id:       ID del agente.
        agent_name:     Nombre del agente.
        state:          LifecycleState actual.
        agent_status:   AgentStatus subyacente (Sprint 2.1).
        healthy:        True si state ∈ {CREATED, INITIALIZING, READY, RUNNING}.
        uptime_s:       Segundos desde created_at.
        last_state_change: ISO timestamp del último cambio de estado.
        error_count:    Número de errores registrados.
        last_error:     Último error, o None.
        initialized:    True si pasó por initialize() con éxito.
        started_count:  Número de veces que entró en RUNNING.
    """

    agent_id: str = ""
    agent_name: str = ""
    state: LifecycleState = LifecycleState.CREATED
    agent_status: AgentStatus = AgentStatus.CREATED
    healthy: bool = True
    uptime_s: float = 0.0
    last_state_change: str = ""
    error_count: int = 0
    last_error: str | None = None
    initialized: bool = False
    started_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "state": self.state.value,
            "agent_status": self.agent_status.value,
            "healthy": self.healthy,
            "uptime_s": round(self.uptime_s, 2),
            "last_state_change": self.last_state_change,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "initialized": self.initialized,
            "started_count": self.started_count,
        }


# ----------------------------------------------------------------------
# LifecycleManager
# ----------------------------------------------------------------------


class LifecycleManager:
    """Wrapper que añade lifecycle profesional sobre un `BaseAgent`.

    Uso::

        agent = CoderAgent()
        lifecycle = LifecycleManager(agent)
        await lifecycle.initialize()   # CREATED → INITIALIZING → READY
        await lifecycle.start()        # READY → RUNNING
        await lifecycle.stop()         # → STOPPED
        health = lifecycle.health_check()
    """

    MAX_ERRORS = 50

    def __init__(
        self,
        agent: BaseAgent,
        *,
        init_timeout: float = 30.0,
        stop_timeout: float = 10.0,
    ) -> None:
        self._agent = agent
        self._id: str = str(uuid.uuid4())
        self._state: LifecycleState = LifecycleState.CREATED
        self._created_at: float = _now_epoch()
        self._created_at_iso: str = _now_iso()
        self._last_state_change: str = _now_iso()
        self._errors: list[str] = []
        self._initialized: bool = False
        self._started_count: int = 0
        self._init_timeout = init_timeout
        self._stop_timeout = stop_timeout
        # Sincronizar estado inicial con el agente.
        self._sync_from_agent()

    # ------------------------------------------------------------------
    # Propiedades
    # ------------------------------------------------------------------

    @property
    def agent(self) -> BaseAgent:
        return self._agent

    @property
    def id(self) -> str:
        return self._id

    @property
    def state(self) -> LifecycleState:
        return self._state

    @property
    def is_terminal(self) -> bool:
        """True si el lifecycle está en estado terminal (STOPPED)."""
        return self._state == LifecycleState.STOPPED

    @property
    def is_healthy(self) -> bool:
        """True si el lifecycle está en un estado operacional."""
        return self._state in (
            LifecycleState.CREATED,
            LifecycleState.INITIALIZING,
            LifecycleState.READY,
            LifecycleState.RUNNING,
        )

    @property
    def is_ready(self) -> bool:
        """True si está listo para aceptar tareas."""
        return self._state == LifecycleState.READY

    @property
    def is_running(self) -> bool:
        """True si está ejecutando una tarea."""
        return self._state == LifecycleState.RUNNING

    @property
    def uptime_s(self) -> float:
        return _now_epoch() - self._created_at

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    @property
    def last_error(self) -> str | None:
        return self._errors[-1] if self._errors else None

    @property
    def error_count(self) -> int:
        return len(self._errors)

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def started_count(self) -> int:
        return self._started_count

    # ------------------------------------------------------------------
    # Lifecycle principal
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Inicializa el agente: CREATED → INITIALIZING → READY.

        Llama al método `start()` del BaseAgent subyacente (que en
        Sprint 2.1 transiciona CREATED → IDLE).

        Si el agente ya está inicializado, no hace nada (idempotente).
        """
        if self._initialized:
            return
        if self._state == LifecycleState.STOPPED:
            raise RuntimeError(
                f"Cannot initialize agent '{self._agent.name}': already STOPPED"
            )
        # CREATED → INITIALIZING
        self._transition(LifecycleState.INITIALIZING)
        try:
            # Llamar al start() del BaseAgent (CREATED → IDLE).
            if hasattr(self._agent, "start") and callable(self._agent.start):
                result = self._agent.start()
                if asyncio.iscoroutine(result):
                    await asyncio.wait_for(result, timeout=self._init_timeout)
            # INITIALIZING → READY
            self._transition(LifecycleState.READY)
            self._initialized = True
            logger.info(
                "Agent '%s' initialized (lifecycle=%s)", self._agent.name, self._state.value
            )
        except Exception as exc:  # noqa: BLE001
            self._add_error(f"initialize failed: {exc}")
            self._transition(LifecycleState.ERROR)
            raise

    async def start(self) -> None:
        """Marca el agente como RUNNING (está ocupado con una tarea).

        READY → RUNNING. Si ya está RUNNING, no hace nada.
        """
        if self._state == LifecycleState.RUNNING:
            return
        if self._state != LifecycleState.READY:
            # Auto-recovery desde ERROR si el agente subyacente está OK.
            if self._state == LifecycleState.ERROR:
                self._sync_from_agent()
            if self._state != LifecycleState.READY:
                raise RuntimeError(
                    f"Cannot start agent '{self._agent.name}': "
                    f"lifecycle is {self._state.value} (must be READY)"
                )
        self._transition(LifecycleState.RUNNING)
        self._started_count += 1

    async def stop(self) -> None:
        """Detiene el agente: cualquier estado → STOPPED.

        Idempotente: si ya está STOPPED, no hace nada.
        """
        if self._state == LifecycleState.STOPPED:
            return
        try:
            if hasattr(self._agent, "stop") and callable(self._agent.stop):
                result = self._agent.stop()
                if asyncio.iscoroutine(result):
                    await asyncio.wait_for(result, timeout=self._stop_timeout)
        except Exception as exc:  # noqa: BLE001
            self._add_error(f"stop failed: {exc}")
        finally:
            self._transition(LifecycleState.STOPPED)
            logger.info("Agent '%s' stopped", self._agent.name)

    def mark_error(self, error: str) -> None:
        """Marca el agente en estado ERROR."""
        self._add_error(error)
        if self._state != LifecycleState.STOPPED:
            self._transition(LifecycleState.ERROR)

    def recover(self) -> None:
        """Recupera el agente desde ERROR → READY.

        Sólo funciona si el agente subyacente está en estado IDLE o CREATED.
        """
        if self._state != LifecycleState.ERROR:
            return
        self._sync_from_agent()
        if self._agent.status in (AgentStatus.IDLE, AgentStatus.CREATED):
            self._transition(LifecycleState.READY)
            logger.info("Agent '%s' recovered from ERROR", self._agent.name)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self) -> LifecycleHealth:
        """Devuelve un snapshot de salud del lifecycle."""
        # Sincronizar estado desde el agente (por si cambió externamente).
        self._sync_from_agent()
        return LifecycleHealth(
            agent_id=self._id,
            agent_name=self._agent.name,
            state=self._state,
            agent_status=self._agent.status,
            healthy=self.is_healthy,
            uptime_s=self.uptime_s,
            last_state_change=self._last_state_change,
            error_count=self.error_count,
            last_error=self.last_error,
            initialized=self._initialized,
            started_count=self._started_count,
        )

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self._id,
            "agent_name": self._agent.name,
            "state": self._state.value,
            "agent_status": self._agent.status.value,
            "created_at": self._created_at_iso,
            "last_state_change": self._last_state_change,
            "uptime_s": round(self.uptime_s, 2),
            "initialized": self._initialized,
            "started_count": self._started_count,
            "error_count": self.error_count,
            "errors": list(self._errors[-10:]),  # últimos 10
            "last_error": self.last_error,
            "healthy": self.is_healthy,
        }

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _transition(self, target: LifecycleState) -> None:
        """Transiciona con validación."""
        if not can_transition_lifecycle(self._state, target):
            # Si la transición no es válida, log warning pero forzar
            # (el lifecycle no debe bloquear al agente).
            logger.warning(
                "Agent '%s' lifecycle invalid transition %s → %s (forcing)",
                self._agent.name, self._state.value, target.value,
            )
        old = self._state
        self._state = target
        self._last_state_change = _now_iso()
        logger.debug(
            "Agent '%s' lifecycle %s → %s", self._agent.name, old.value, target.value
        )

    def _sync_from_agent(self) -> None:
        """Sincroniza el estado del lifecycle desde el AgentStatus del agente."""
        agent_status = self._agent.status
        target = _AGENT_TO_LIFECYCLE.get(agent_status, LifecycleState.CREATED)
        if target != self._state:
            # No forzar transición inválida; usar el mapeo directo.
            self._state = target
            self._last_state_change = _now_iso()

    def _add_error(self, error: str) -> None:
        self._errors.append(error)
        if len(self._errors) > self.MAX_ERRORS:
            self._errors = self._errors[-self.MAX_ERRORS:]


__all__ = [
    "LifecycleHealth",
    "LifecycleManager",
    "LifecycleState",
    "can_transition_lifecycle",
]
