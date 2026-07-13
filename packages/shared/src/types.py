"""Common type aliases used across the project."""

from __future__ import annotations

from typing import Any, Protocol

# Generic JSON-like payload type.
JSON = dict[str, Any]

# Identifier for sessions, messages, and entities.
EntityId = str


class EventCallback(Protocol):
    """Protocol for asynchronous event callbacks."""

    def __call__(self, payload: Any) -> Any: ...
