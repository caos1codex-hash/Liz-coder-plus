"""EventBus del workflow engine (Sprint 2.2).

**Separación clara con MessageBus** (Sprint 2.1):

- `MessageBus` (multiagent/messages.py): mensajería **punto-a-punto**
  entre agentes. Un mensaje tiene `sender` y `receiver` concretos.
  Se usa para REQUEST/RESPONSE entre agentes.

- `EventBus` (este módulo): bus **pub/sub de eventos del sistema**.
  Un evento no tiene destinatario: cualquier suscriptor recibe los
  eventos a los que esté suscrito. Se usa para telemetría, logging,
  UI, observabilidad.

El EventBus es asyncio-safe, soporta múltiples suscriptores por tipo
de evento y filtros opcionales (callback que recibe el evento y
devuelve True/False).

Eventos mínimos según especificación (todos cubiertos por
`WorkflowEvent`):

    WorkflowCreated, WorkflowStarted, WorkflowCompleted, WorkflowFailed,
    StepStarted, StepFinished, AgentBusy, AgentIdle,
    MemoryUpdated, ToolExecuted
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .enums import WorkflowEvent

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Event
# ----------------------------------------------------------------------


@dataclass
class Event:
    """Un evento publicado en el EventBus.

    Attributes:
        id:          UUID4.
        type:        WorkflowEvent (o string).
        timestamp:   ISO-8601 UTC.
        source:      Quién emitió el evento (orchestrator, agent name, etc.).
        payload:     Datos del evento (dict plano).
        correlation_id: Para emparejar eventos del mismo workflow/step.
        sequence:    Número de secuencia monotónico dentro del bus.
    """

    type: WorkflowEvent | str
    source: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    correlation_id: str | None = None
    sequence: int = 0

    @property
    def type_str(self) -> str:
        """Devuelve el tipo como string.

        Maneja tanto `WorkflowEvent` como strings planos. Usa `hasattr`
        en lugar de `isinstance` para ser robusto frente a re-imports
        del módulo enums (que pueden crear clases distintas con el
        mismo nombre durante los tests).
        """
        if hasattr(self.type, "value"):
            return self.type.value  # type: ignore[union-attr]
        return str(self.type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type_str,
            "timestamp": self.timestamp,
            "source": self.source,
            "payload": dict(self.payload),
            "correlation_id": self.correlation_id,
            "sequence": self.sequence,
        }


# ----------------------------------------------------------------------
# Subscriber callback
# ----------------------------------------------------------------------


EventHandler = Callable[[Event], Awaitable[None]]
EventFilter = Callable[[Event], bool]


# ----------------------------------------------------------------------
# Subscription
# ----------------------------------------------------------------------


@dataclass
class _Subscription:
    """Suscripción interna."""
    handler: EventHandler
    event_type: str | None  # None = todos los eventos
    filter_fn: EventFilter | None
    token: str


# ----------------------------------------------------------------------
# EventBus
# ----------------------------------------------------------------------


class EventBus:
    """Bus pub/sub de eventos asyncio-safe.

    Características:

    - Múltiples suscriptores por tipo de evento.
    - Suscripción "global" (todos los eventos) con `event_type=None`.
    - Filtros opcionales.
    - Ring buffer configurable para historial.
    - Métricas: publicados, entregados, errores, por tipo.
    - Aislamiento de errores: un handler que falla no afecta a otros.
    """

    def __init__(
        self,
        *,
        history_size: int = 1000,
    ) -> None:
        self._subs: list[_Subscription] = []
        self._history: deque[Event] = deque(maxlen=history_size)
        self._lock = asyncio.Lock()
        self._sequence: int = 0
        self._metrics: dict[str, Any] = {
            "published": 0,
            "delivered": 0,
            "errors": 0,
            "by_type": {},
        }
        self._history_size = history_size

    # ------------------------------------------------------------------
    # Suscripción
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        handler: EventHandler,
        *,
        event_type: WorkflowEvent | str | None = None,
        filter_fn: EventFilter | None = None,
    ) -> str:
        """Suscribe un handler.

        Args:
            handler:    Callback async que recibe el Event.
            event_type: Filtra por tipo (None = todos).
            filter_fn:  Filtro adicional; si devuelve False, el handler no se llama.
        """
        token = str(uuid.uuid4())
        # Usar hasattr para ser robusto frente a re-imports del enum.
        if hasattr(event_type, "value"):
            type_str = event_type.value  # type: ignore[union-attr]
        else:
            type_str = event_type  # str o None
        sub = _Subscription(
            handler=handler,
            event_type=type_str,
            filter_fn=filter_fn,
            token=token,
        )
        async with self._lock:
            self._subs.append(sub)
        logger.debug("Subscribed to '%s' (token=%s)", type_str or "*", token)
        return token

    async def unsubscribe(self, token: str) -> bool:
        """Elimina una suscripción por token."""
        async with self._lock:
            for i, sub in enumerate(self._subs):
                if sub.token == token:
                    self._subs.pop(i)
                    return True
        return False

    # ------------------------------------------------------------------
    # Publicación
    # ------------------------------------------------------------------

    async def publish(
        self,
        event_type: WorkflowEvent | str,
        *,
        source: str = "",
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> Event:
        """Publica un evento. Devuelve el Event creado."""
        async with self._lock:
            self._sequence += 1
            event = Event(
                type=event_type,
                source=source,
                payload=payload or {},
                correlation_id=correlation_id,
                sequence=self._sequence,
            )
            self._history.append(event)
            self._metrics["published"] += 1
            type_str = event.type_str
            self._metrics["by_type"][type_str] = (
                self._metrics["by_type"].get(type_str, 0) + 1
            )
            # Snapshot de suscriptores para no mantener el lock durante los handlers.
            subs_snapshot = list(self._subs)

        # Entrega fuera del lock.
        delivered = 0
        for sub in subs_snapshot:
            # Filtro por tipo.
            if sub.event_type is not None and sub.event_type != type_str:
                continue
            # Filtro personalizado.
            if sub.filter_fn is not None:
                try:
                    if not sub.filter_fn(event):
                        continue
                except Exception:  # noqa: BLE001
                    logger.exception("Event filter failed; skipping subscriber")
                    continue
            try:
                await sub.handler(event)
                delivered += 1
            except Exception:  # noqa: BLE001
                async with self._lock:
                    self._metrics["errors"] += 1
                logger.exception(
                    "Event handler failed for event %s (seq=%d)",
                    type_str, event.sequence,
                )
        async with self._lock:
            self._metrics["delivered"] += delivered
        return event

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def history(
        self,
        *,
        event_type: WorkflowEvent | str | None = None,
        limit: int = 100,
        correlation_id: str | None = None,
    ) -> list[Event]:
        """Devuelve eventos del historial.

        Args:
            event_type:     Filtra por tipo (None = todos).
            limit:          Máximo número de eventos a devolver.
            correlation_id: Filtra por correlation_id.
        """
        if hasattr(event_type, "value"):
            type_str = event_type.value  # type: ignore[union-attr]
        else:
            type_str = event_type
        result: list[Event] = []
        for e in reversed(self._history):
            if type_str is not None and e.type_str != type_str:
                continue
            if correlation_id is not None and e.correlation_id != correlation_id:
                continue
            result.append(e)
            if len(result) >= limit:
                break
        # Invertir para orden cronológico.
        result.reverse()
        return result

    def metrics(self) -> dict[str, Any]:
        """Devuelve métricas del bus."""
        return {
            **self._metrics,
            "subscriber_count": len(self._subs),
            "history_size": len(self._history),
            "history_capacity": self._history_size,
        }

    async def clear(self) -> None:
        """Limpia suscripciones e historial."""
        async with self._lock:
            self._subs.clear()
            self._history.clear()
            self._metrics = {
                "published": 0,
                "delivered": 0,
                "errors": 0,
                "by_type": {},
            }
            self._sequence = 0


__all__ = ["Event", "EventBus", "EventHandler", "EventFilter"]
