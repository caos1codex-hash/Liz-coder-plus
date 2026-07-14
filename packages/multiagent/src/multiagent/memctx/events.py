"""Memory event system.

Provides a dedicated event bus for memory operations, with support for:
- Async event emission and subscription
- Event history
- Metrics tracking
- Integration with external event buses (workflow, plugin)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryEventType(str, Enum):
    """Types of memory-related events."""

    MEMORY_CREATED = "memory.created"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_DELETED = "memory.deleted"
    CONTEXT_CREATED = "context.created"
    CONTEXT_LOADED = "context.loaded"
    CONTEXT_TRIMMED = "context.trimmed"
    CONTEXT_EXPIRED = "context.expired"


MemoryEventHandler = Callable[["MemoryEvent"], Awaitable[None]]


@dataclass
class MemoryEvent:
    """An event emitted by the memory system.

    Attributes:
        type: Event type.
        source: Component that emitted the event.
        payload: Event data.
        id: Unique event id.
        timestamp: ISO-8601 UTC timestamp.
        correlation_id: Optional correlation for tracing.
    """

    type: MemoryEventType | str
    source: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: _utcnow())
    correlation_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        type_val = self.type if isinstance(self.type, str) else self.type.value
        return {
            "type": type_val,
            "source": self.source,
            "payload": self.payload,
            "id": self.id,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
        }


class MemoryEventBus:
    """Async event bus for memory system events.

    Supports:
    - Multiple subscribers per event type
    - Global subscribers (receive all events)
    - Event history (ring buffer)
    - Metrics (published/delivered/errors counts)
    """

    def __init__(self, max_history: int = 500) -> None:
        self._subscribers: Dict[str, List[tuple[MemoryEventHandler, str]]] = defaultdict(list)
        self._global_subscribers: List[tuple[MemoryEventHandler, str]] = []
        self._history: deque[MemoryEvent] = deque(maxlen=max_history)
        self._lock = asyncio.Lock()
        self._metrics = {
            "published": 0,
            "delivered": 0,
            "errors": 0,
            "by_type": defaultdict(int),
        }
        self._seq = 0

    # -- subscribe / unsubscribe -------------------------------------------

    async def subscribe(
        self,
        event_type: MemoryEventType | str,
        handler: MemoryEventHandler,
    ) -> str:
        """Subscribe to a specific event type. Returns a subscription token."""
        token = str(uuid.uuid4())[:8]
        type_key = event_type if isinstance(event_type, str) else event_type.value
        async with self._lock:
            self._subscribers[type_key].append((handler, token))
        logger.debug("subscribed %s to %s", token, type_key)
        return token

    async def subscribe_all(self, handler: MemoryEventHandler) -> str:
        """Subscribe to all events. Returns a subscription token."""
        token = str(uuid.uuid4())[:8]
        async with self._lock:
            self._global_subscribers.append((handler, token))
        logger.debug("subscribed %s to all events", token)
        return token

    async def unsubscribe(self, token: str) -> bool:
        """Unsubscribe by token. Returns True if found and removed."""
        async with self._lock:
            for type_key, subs in self._subscribers.items():
                for i, (_, t) in enumerate(subs):
                    if t == token:
                        subs.pop(i)
                        return True
            for i, (_, t) in enumerate(self._global_subscribers):
                if t == token:
                    self._global_subscribers.pop(i)
                    return True
        return False

    # -- emit --------------------------------------------------------------

    async def emit(
        self,
        event_type: MemoryEventType | str,
        source: str = "",
        payload: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> int:
        """Emit an event. Returns the number of handlers notified."""
        # Normalize to string value
        if isinstance(event_type, MemoryEventType):
            type_val = event_type.value
        else:
            type_val = str(event_type)
        self._seq += 1

        event = MemoryEvent(
            type=type_val,
            source=source,
            payload=payload or {},
            correlation_id=correlation_id,
        )

        async with self._lock:
            self._history.append(event)
            self._metrics["published"] += 1
            self._metrics["by_type"][type_val] += 1

            # Collect handlers
            handlers: List[MemoryEventHandler] = []
            for h, _ in self._subscribers.get(type_val, []):
                handlers.append(h)
            for h, _ in self._global_subscribers:
                handlers.append(h)

        # Deliver outside lock
        delivered = 0
        for handler in handlers:
            try:
                await handler(event)
                delivered += 1
            except Exception:
                self._metrics["errors"] += 1
                logger.exception(
                    "error in memory event handler for %s", type_val
                )

        async with self._lock:
            self._metrics["delivered"] += delivered

        return delivered

    # -- query -------------------------------------------------------------

    async def history(
        self,
        event_type: Optional[MemoryEventType | str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get event history, optionally filtered by type."""
        if event_type is not None:
            type_val = event_type if isinstance(event_type, str) else event_type.value
            filtered = [
                e for e in self._history
                if (e.type if isinstance(e.type, str) else e.type.value) == type_val
            ]
        else:
            filtered = list(self._history)

        return [e.to_dict() for e in filtered[-limit:]]

    def metrics(self) -> Dict[str, Any]:
        """Return event bus metrics."""
        return {
            "published": self._metrics["published"],
            "delivered": self._metrics["delivered"],
            "errors": self._metrics["errors"],
            "by_type": dict(self._metrics["by_type"]),
            "history_size": len(self._history),
            "subscribers_per_type": {
                k: len(v) for k, v in self._subscribers.items()
            },
            "global_subscribers": len(self._global_subscribers),
        }

    async def clear(self) -> None:
        """Clear history and metrics."""
        async with self._lock:
            self._history.clear()
            self._metrics = {
                "published": 0,
                "delivered": 0,
                "errors": 0,
                "by_type": defaultdict(int),
            }


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()