"""Sistema de mensajería entre agentes (Sprint 2.1).

Cada mensaje es una estructura inmutable con:

- id (uuid4)
- timestamp (UTC ISO-8601)
- sender (nombre del agente emisor; "system" si es del orquestador)
- receiver (nombre del agente destinatario; "*" para broadcast)
- type (MessageType)
- priority (Priority)
- payload (dict plano)
- correlation_id (opcional, para emparejar REQUEST/RESPONSE)
- ttl (segundos; 0 = sin expiración)

El `MessageBus` mantiene dos colas internas:

- una por agente destinatario (para `dispatch` directo)
- una de broadcast (para `broadcast`)

El bus es asyncio-safe y soporta subscriptores pub/sub para que el
orquestador y los agentes reaccionen a eventos sin acoplamiento.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .enums import MessageType, Priority, priority_weight

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# Mensaje
# ----------------------------------------------------------------------


@dataclass
class Message:
    """Mensaje entre agentes.

    Inmutable en práctica: los campos son mutables para permitir
    deserialización, pero los agentes deben tratarlo como solo-lectura.
    """

    sender: str
    receiver: str
    type: MessageType
    payload: dict[str, Any] = field(default_factory=dict)
    priority: Priority = Priority.NORMAL
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=_now_iso)
    correlation_id: str | None = None
    ttl: float = 0.0  # segundos; 0 = sin expiración

    # ------------------------------------------------------------------
    # Constructores de conveniencia
    # ------------------------------------------------------------------

    @classmethod
    def request(
        cls,
        sender: str,
        receiver: str,
        payload: dict[str, Any] | None = None,
        *,
        priority: Priority = Priority.NORMAL,
        correlation_id: str | None = None,
        ttl: float = 0.0,
    ) -> Message:
        return cls(
            sender=sender,
            receiver=receiver,
            type=MessageType.REQUEST,
            payload=payload or {},
            priority=priority,
            correlation_id=correlation_id,
            ttl=ttl,
        )

    @classmethod
    def response(
        cls,
        sender: str,
        receiver: str,
        payload: dict[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
        priority: Priority = Priority.NORMAL,
    ) -> Message:
        return cls(
            sender=sender,
            receiver=receiver,
            type=MessageType.RESPONSE,
            payload=payload or {},
            priority=priority,
            correlation_id=correlation_id,
        )

    @classmethod
    def event(
        cls,
        sender: str,
        receiver: str,
        name: str,
        data: dict[str, Any] | None = None,
        *,
        priority: Priority = Priority.NORMAL,
    ) -> Message:
        return cls(
            sender=sender,
            receiver=receiver,
            type=MessageType.EVENT,
            payload={"name": name, "data": data or {}},
            priority=priority,
        )

    @classmethod
    def error(
        cls,
        sender: str,
        receiver: str,
        error: str,
        *,
        code: str = "INTERNAL",
        correlation_id: str | None = None,
        priority: Priority = Priority.HIGH,
    ) -> Message:
        return cls(
            sender=sender,
            receiver=receiver,
            type=MessageType.ERROR,
            payload={"error": error, "code": code},
            priority=priority,
            correlation_id=correlation_id,
        )

    @classmethod
    def system(
        cls,
        sender: str,
        receiver: str,
        payload: dict[str, Any] | None = None,
        *,
        priority: Priority = Priority.NORMAL,
    ) -> Message:
        return cls(
            sender=sender,
            receiver=receiver,
            type=MessageType.SYSTEM,
            payload=payload or {},
            priority=priority,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def is_broadcast(self) -> bool:
        return self.receiver == "*"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "sender": self.sender,
            "receiver": self.receiver,
            "type": self.type.value,
            "priority": self.priority.value,
            "payload": dict(self.payload),
            "correlation_id": self.correlation_id,
            "ttl": self.ttl,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        return cls(
            sender=data["sender"],
            receiver=data["receiver"],
            type=MessageType(data["type"]),
            payload=dict(data.get("payload", {})),
            priority=Priority(data.get("priority", "normal")),
            id=data.get("id") or str(uuid.uuid4()),
            timestamp=data.get("timestamp") or _now_iso(),
            correlation_id=data.get("correlation_id"),
            ttl=float(data.get("ttl", 0.0)),
        )


# ----------------------------------------------------------------------
# Subscriber callback
# ----------------------------------------------------------------------


MessageHandler = Callable[[Message], Awaitable[None]]


# ----------------------------------------------------------------------
# Message Bus
# ----------------------------------------------------------------------


class MessageBus:
    """Bus de mensajes asyncio-safe entre agentes.

    Métodos principales:

    - `publish(message)`: enruta un mensaje al destinatario (o a todos
      si es broadcast).
    - `subscribe(receiver, handler)`: registra un handler async para
      un destinatario concreto. Devuelve un token de suscripción.
    - `unsubscribe(token)`: elimina la suscripción.
    - `dispatch_pending(receiver)`: procesa la cola pendiente de un
      destinatario.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, dict[str, MessageHandler]] = {}
        self._broadcast_subscribers: dict[str, MessageHandler] = {}
        self._pending: dict[str, list[Message]] = {}
        self._lock = asyncio.Lock()
        self._published_count: int = 0
        self._delivered_count: int = 0
        self._dropped_count: int = 0

    # ------------------------------------------------------------------
    # Suscripciones
    # ------------------------------------------------------------------

    async def subscribe(self, receiver: str, handler: MessageHandler) -> str:
        """Suscribe un handler a los mensajes dirigidos a `receiver`."""
        token = str(uuid.uuid4())
        async with self._lock:
            self._subscribers.setdefault(receiver, {})[token] = handler
        logger.debug("Subscribed handler to '%s' (token=%s)", receiver, token)
        return token

    async def subscribe_broadcast(self, handler: MessageHandler) -> str:
        """Suscribe un handler a mensajes broadcast."""
        token = str(uuid.uuid4())
        async with self._lock:
            self._broadcast_subscribers[token] = handler
        logger.debug("Subscribed broadcast handler (token=%s)", token)
        return token

    async def unsubscribe(self, token: str) -> bool:
        """Elimina una suscripción por token."""
        async with self._lock:
            for receiver, handlers in self._subscribers.items():
                if token in handlers:
                    del handlers[token]
                    if not handlers:
                        del self._subscribers[receiver]
                    return True
            if token in self._broadcast_subscribers:
                del self._broadcast_subscribers[token]
                return True
        return False

    # ------------------------------------------------------------------
    # Publicación
    # ------------------------------------------------------------------

    async def publish(self, message: Message) -> int:
        """Publica un mensaje. Devuelve el número de entregas realizadas.

        Si el mensaje es broadcast (`receiver == "*"`), se entrega a
        todos los subscriptores broadcast. Si no, se entrega a los
        subscriptores del destinatario; si no hay ninguno, se encola
        en `_pending` para entrega posterior.
        """
        self._published_count += 1
        delivered = 0
        if message.is_broadcast:
            async with self._lock:
                handlers = list(self._broadcast_subscribers.values())
            for h in handlers:
                try:
                    await h(message)
                    delivered += 1
                except Exception:  # noqa: BLE001
                    logger.exception("Broadcast handler failed for msg %s", message.id)
            self._delivered_count += delivered
            return delivered

        async with self._lock:
            handlers = list(self._subscribers.get(message.receiver, {}).values())
        if handlers:
            # Ordenar por prioridad del handler no aplica: el mensaje
            # ya tiene prioridad; se entrega a todos los handlers.
            for h in handlers:
                try:
                    await h(message)
                    delivered += 1
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Handler for '%s' failed on msg %s",
                        message.receiver, message.id,
                    )
            self._delivered_count += delivered
            return delivered

        # Sin subscriptores: encolar para entrega posterior.
        async with self._lock:
            self._pending.setdefault(message.receiver, []).append(message)
        logger.debug(
            "No subscribers for '%s'; queued (pending=%d)",
            message.receiver, len(self._pending.get(message.receiver, [])),
        )
        return 0

    async def dispatch_pending(self, receiver: str) -> int:
        """Entrega los mensajes pendientes al destinatario.

        Útil cuando un agente se registra después de que se hayan
        publicado mensajes para él.
        """
        async with self._lock:
            pending = self._pending.pop(receiver, [])
        if not pending:
            return 0
        # Ordenar por prioridad (más bajo = más prioritario).
        pending.sort(key=lambda m: priority_weight(m.priority))
        delivered = 0
        async with self._lock:
            handlers = list(self._subscribers.get(receiver, {}).values())
        for msg in pending:
            for h in handlers:
                try:
                    await h(msg)
                    delivered += 1
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Pending dispatch failed for '%s' on msg %s",
                        receiver, msg.id,
                    )
        self._delivered_count += delivered
        return delivered

    # ------------------------------------------------------------------
    # Estadísticas
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Devuelve métricas básicas del bus."""
        return {
            "published": self._published_count,
            "delivered": self._delivered_count,
            "dropped": self._dropped_count,
            "subscribers": sum(len(h) for h in self._subscribers.values()),
            "broadcast_subscribers": len(self._broadcast_subscribers),
            "pending_messages": sum(len(v) for v in self._pending.values()),
        }

    async def clear(self) -> None:
        """Limpia todas las suscripciones y mensajes pendientes."""
        async with self._lock:
            self._subscribers.clear()
            self._broadcast_subscribers.clear()
            self._pending.clear()


__all__ = ["Message", "MessageBus", "MessageHandler"]
