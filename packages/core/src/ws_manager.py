"""WebSocket manager.

Central registry of active WebSocket connections. Provides:

- Connection lifecycle tracking (register / unregister).
- Per-session and global broadcast helpers.
- Heartbeat support (ping/pong) for stale-connection detection.
- Event hooks for connection/disconnection, used by the SessionManager
  and the audit log.

The manager is intentionally agnostic of any specific WebSocket
implementation; it works against the `WebSocketConnection` Protocol
defined in `ws_connection.py`.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable
from uuid import uuid4

from src.ws_connection import WebSocketConnection

logger = logging.getLogger(__name__)

# Event callback signature: async fn(connection_id, payload)
ConnectionCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class WebSocketManager:
    """Registry and dispatcher for active WebSocket connections.

    The manager keeps connections grouped by session_id so we can
    broadcast to all clients participating in the same conversation.
    A single session may have more than one connection (e.g. user has
    the desktop app open on two machines).
    """

    def __init__(self) -> None:
        # connection_id -> WebSocketConnection
        self._connections: dict[str, WebSocketConnection] = {}
        # session_id -> set of connection_ids
        self._by_session: dict[str, set[str]] = defaultdict(set)

        # Event callbacks
        self._on_connect: list[ConnectionCallback] = []
        self._on_disconnect: list[ConnectionCallback] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def connection_count(self) -> int:
        """Total number of active connections."""
        return len(self._connections)

    def session_connection_count(self, session_id: str) -> int:
        """Number of active connections for a given session."""
        return len(self._by_session.get(session_id, set()))

    def list_sessions(self) -> list[str]:
        """List session_ids that currently have at least one connection."""
        return [sid for sid, conns in self._by_session.items() if conns]

    # ------------------------------------------------------------------
    # Event subscription
    # ------------------------------------------------------------------

    def on_connect(self, callback: ConnectionCallback) -> None:
        """Register a callback invoked when a connection is registered."""
        self._on_connect.append(callback)

    def on_disconnect(self, callback: ConnectionCallback) -> None:
        """Register a callback invoked when a connection is removed."""
        self._on_disconnect.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def register(
        self,
        connection: WebSocketConnection,
        *,
        session_id: str | None = None,
    ) -> str:
        """Register a new connection.

        Args:
            connection: The WebSocket connection to register.
            session_id: Optional session to bind. If None, the
                connection is registered without a session until
                `bind_session` is called.

        Returns:
            The connection id assigned to this connection.
        """
        conn_id = getattr(connection, "id", None) or str(uuid4())
        # Force id assignment if the attribute is writable.
        try:
            connection.id = conn_id  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            # Protocol objects may not allow write; that's fine for
            # implementations that pre-assign id.
            pass

        self._connections[conn_id] = connection
        if session_id:
            await self.bind_session(conn_id, session_id)

        logger.info(
            "WebSocket registered: id=%s session=%s (total=%d)",
            conn_id,
            session_id,
            self.connection_count,
        )

        await self._fire(self._on_connect, conn_id, {"session_id": session_id})
        return conn_id

    async def bind_session(self, connection_id: str, session_id: str) -> None:
        """Bind (or rebind) a connection to a session id."""
        conn = self._connections.get(connection_id)
        if conn is None:
            logger.warning("bind_session: unknown connection_id=%s", connection_id)
            return
        try:
            conn.session_id = session_id  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            pass
        self._by_session[session_id].add(connection_id)
        logger.debug("Connection %s bound to session %s", connection_id, session_id)

    async def unregister(self, connection_id: str, *, reason: str = "") -> None:
        """Remove a connection from the registry.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        conn = self._connections.pop(connection_id, None)
        if conn is None:
            return

        # Detach from any session buckets.
        session_id = getattr(conn, "session_id", None)
        if session_id:
            self._by_session.get(session_id, set()).discard(connection_id)
            if not self._by_session.get(session_id):
                self._by_session.pop(session_id, None)

        # Close the underlying socket if still active.
        if getattr(conn, "is_active", False):
            try:
                await conn.close(reason=reason or "unregister")
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to close connection %s during unregister",
                    connection_id,
                )

        logger.info(
            "WebSocket unregistered: id=%s session=%s reason=%s (total=%d)",
            connection_id,
            session_id,
            reason,
            self.connection_count,
        )

        await self._fire(
            self._on_disconnect,
            connection_id,
            {"session_id": session_id, "reason": reason},
        )

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_to(self, connection_id: str, payload: dict[str, Any]) -> bool:
        """Send a payload to a single connection.

        Returns:
            True if the message was sent, False if the connection is
            not registered or has gone inactive.
        """
        conn = self._connections.get(connection_id)
        if conn is None or not getattr(conn, "is_active", False):
            return False
        try:
            await conn.send_json(payload)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("send_to failed for connection %s", connection_id)
            await self.unregister(connection_id, reason="send failure")
            return False

    async def send_to_session(self, session_id: str, payload: dict[str, Any]) -> int:
        """Send a payload to every connection bound to a session.

        Returns:
            Number of connections that successfully received the message.
        """
        conn_ids = list(self._by_session.get(session_id, set()))
        delivered = 0
        for conn_id in conn_ids:
            if await self.send_to(conn_id, payload):
                delivered += 1
        return delivered

    async def broadcast(self, payload: dict[str, Any]) -> int:
        """Send a payload to every active connection.

        Returns:
            Number of connections that received the message.
        """
        conn_ids = list(self._connections.keys())
        delivered = 0
        for conn_id in conn_ids:
            if await self.send_to(conn_id, payload):
                delivered += 1
        return delivered

    async def broadcast_except(
        self, excluded_connection_id: str, payload: dict[str, Any]
    ) -> int:
        """Broadcast to every connection except one.

        Useful when a server-side event triggered by connection A
        should not echo back to A.
        """
        conn_ids = [c for c in self._connections if c != excluded_connection_id]
        delivered = 0
        for conn_id in conn_ids:
            if await self.send_to(conn_id, payload):
                delivered += 1
        return delivered

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def ping_all(self) -> int:
        """Send a ping payload to every connection.

        Returns:
            Number of connections pinged. Connections that fail to
            receive the ping are unregistered.
        """
        return await self.broadcast({"type": "ping"})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get(self, connection_id: str) -> WebSocketConnection | None:
        """Look up a connection by id."""
        return self._connections.get(connection_id)

    async def _fire(
        self,
        callbacks: list[ConnectionCallback],
        connection_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Invoke a list of callbacks, isolating failures."""
        for cb in callbacks:
            try:
                await cb(connection_id, payload)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Callback %s failed for connection %s",
                    getattr(cb, "__name__", cb),
                    connection_id,
                )
