"""FastAPI adapter for the WebSocketConnection protocol.

Wraps a `starlette.websockets.WebSocket` so it satisfies the
`WebSocketConnection` Protocol defined in `packages/core/ws_connection.py`.

This adapter lets the WebSocketManager work with FastAPI sockets
without depending on FastAPI itself.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class FastAPIWebSocketAdapter:
    """Adapter that exposes a FastAPI WebSocket as a WebSocketConnection.

    Attributes:
        id:          Unique connection id (auto-generated).
        session_id:  Session bound to this connection (set by the manager).
    """

    def __init__(self, socket: WebSocket, *, connection_id: str | None = None) -> None:
        self._socket = socket
        self.id = connection_id or str(uuid4())
        self.session_id: str | None = None
        self._active = False

    @property
    def is_active(self) -> bool:
        """True while the underlying socket is open."""
        return self._active

    async def accept(self) -> None:
        """Accept the WebSocket handshake and mark the connection active."""
        await self._socket.accept()
        self._active = True
        logger.debug("WebSocket accepted: id=%s", self.id)

    async def send_json(self, payload: dict[str, Any]) -> None:
        """Send a JSON payload to the client."""
        if not self._active:
            raise RuntimeError("Cannot send on inactive connection")
        await self._socket.send_json(payload)

    async def receive_json(self) -> dict[str, Any]:
        """Block until a JSON message arrives from the client."""
        return await self._socket.receive_json()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the connection gracefully."""
        if not self._active:
            return
        self._active = False
        try:
            await self._socket.close(code=code, reason=reason)
        except Exception:  # noqa: BLE001
            logger.debug("Underlying socket already closed: id=%s", self.id)
