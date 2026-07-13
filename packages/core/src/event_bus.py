"""Internal event bus for the core package.

Lightweight pub/sub implementation used to decouple modules within
Liz Coder Plus. The orchestrator, agents, memory, and tools communicate
through this bus.

Usage::

    bus = EventBus()
    bus.subscribe("agent.invoked", my_handler)
    await bus.publish("agent.invoked", {"agent": "echo", "session": "s1"})
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EventHandler = Callable[[Any], Awaitable[None]]


class EventBus:
    """In-process asynchronous event bus.

    Handlers are invoked sequentially in the order they were subscribed.
    If a handler raises an exception, it is logged and subsequent handlers
    still run (fault isolation).
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe a handler to an event type.

        Args:
            event_type: The event type string (e.g. "agent.invoked").
            handler: An async callable that receives the event payload.
        """
        self._handlers[event_type].append(handler)
        logger.debug("Subscribed handler to '%s'", event_type)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Remove a handler from an event type.

        Safe to call if the handler is not subscribed (no-op).
        """
        handlers = self._handlers.get(event_type, [])
        try:
            handlers.remove(handler)
        except ValueError:
            pass

    async def publish(self, event_type: str, payload: Any = None) -> int:
        """Publish an event to all subscribers.

        Args:
            event_type: The event type string.
            payload: Optional event payload (any serializable type).

        Returns:
            Number of handlers that were invoked.
        """
        handlers = self._handlers.get(event_type, [])
        count = 0
        for handler in handlers:
            try:
                await handler(payload)
                count += 1
            except Exception:  # noqa: BLE001
                logger.exception("Handler failed for event '%s'", event_type)
        return count

    def subscribers(self, event_type: str) -> int:
        """Return the number of subscribers for an event type."""
        return len(self._handlers.get(event_type, []))

    def event_types(self) -> list[str]:
        """Return all event types that have at least one subscriber."""
        return [k for k, v in self._handlers.items() if v]

    def clear(self) -> None:
        """Remove all subscribers. Useful for testing."""
        self._handlers.clear()