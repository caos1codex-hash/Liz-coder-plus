"""Internal event bus.

Lightweight pub/sub implementation used to decouple modules within the
backend. The orchestrator, agents, memory, and tools all communicate
through this bus. Sprint 1 stub only.

.. TODO(TD-001): This module is dead code — the backend uses the
   core ``EventBus`` from ``packages/core/src/event_bus.py`` via
   the ``liz_core.py`` shim.  No other module imports from this
   file.  Defer deletion to Sprint 2.0 when ``liz_core.py`` is
   replaced by standard package imports (pyproject.toml editable
   installs), to avoid any risk of import breakage during the
   migration.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EventHandler = Callable[[Any], Awaitable[None]]


class EventBus:
    """In-process asynchronous event bus."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe a handler to an event type."""
        self._handlers[event_type].append(handler)
        logger.debug("Subscribed handler to '%s'", event_type)

    async def publish(self, event_type: str, payload: Any = None) -> None:
        """Publish an event to all subscribers.

        Args:
            event_type: The event type string.
            payload: Optional event payload.
        """
        for handler in self._handlers.get(event_type, []):
            try:
                await handler(payload)
            except Exception:  # noqa: BLE001
                logger.exception("Handler failed for event '%s'", event_type)
