"""WebSocket connection abstraction.

Defines a backend-agnostic protocol that any WebSocket implementation
(FastAPI's `WebSocket`, Starlette's `WebSocket`, test doubles, etc.)
must satisfy. This lets the WebSocketManager be unit-tested without
spinning up a real ASGI server.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WebSocketConnection(Protocol):
    """Minimal contract for a bidirectional WebSocket connection.

    Properties:
        id:          Unique connection identifier.
        session_id:  Conversation session bound to this connection.
        is_active:   True while the connection is open.
    """

    id: str
    session_id: str | None

    @property
    def is_active(self) -> bool: ...

    async def send_json(self, payload: dict[str, Any]) -> None:
        """Send a JSON-encoded payload to the client."""

    async def receive_json(self) -> dict[str, Any]:
        """Block until a JSON message arrives from the client."""

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the connection gracefully."""
