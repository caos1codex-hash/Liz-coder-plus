"""WebSocket routes for Liz Coder Plus backend.

Exposes:
    /ws/chat   - bidirectional chat endpoint.

The endpoint accepts JSON messages of the shape defined by
`WebSocketChatRequest` (packages/shared/src/ws_models.py) and streams
back envelopes of the shape defined by `WebSocketChatResponse`.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.api.ws_adapter import FastAPIWebSocketAdapter

# Import core symbols via the monorepo shim. The shim adds
# packages/core/src to sys.path temporarily and re-exports the public
# API without polluting the backend's own `src` namespace.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_CORE_PACKAGE = _REPO_ROOT / "packages" / "core"
if str(_CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(_CORE_PACKAGE))

from liz_core import Orchestrator, WebSocketManager  # noqa: E402

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_router() -> APIRouter:
    """Return the APIRouter for /ws endpoints.

    This function exists so a fresh router can be created per test
    invocation with its own orchestrator/manager pair. The module-level
    `router` is constructed once at import time for the production app.
    """
    return APIRouter()


async def chat_endpoint(
    socket: WebSocket,
    orchestrator: Orchestrator,
    ws_manager: WebSocketManager,
) -> None:
    """Implementation of the /ws/chat endpoint.

    Args:
        socket:       Raw FastAPI WebSocket.
        orchestrator: Orchestrator that processes inbound messages.
        ws_manager:   WebSocketManager that tracks active connections.
    """
    adapter = FastAPIWebSocketAdapter(socket)
    await adapter.accept()

    connection_id = await ws_manager.register(adapter)
    logger.info("Chat WebSocket connected: id=%s", connection_id)

    try:
        while True:
            try:
                raw: dict[str, Any] = await adapter.receive_json()
            except WebSocketDisconnect:
                logger.info("Client disconnected cleanly: id=%s", connection_id)
                break

            await _process_message(connection_id, raw, orchestrator, ws_manager)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnect signal: id=%s", connection_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error on connection %s", connection_id)
        await ws_manager.send_to(
            connection_id,
            {
                "type": "error",
                "content": "",
                "status": "failed",
                "error": f"Internal error: {exc}",
            },
        )
    finally:
        await ws_manager.unregister(connection_id, reason="client disconnect")


async def _process_message(
    connection_id: str,
    raw: dict[str, Any],
    orchestrator: Orchestrator,
    ws_manager: WebSocketManager,
) -> None:
    """Validate and process a single inbound WebSocket message."""
    # Validate payload shape.
    message = (raw or {}).get("message")
    session_id = (raw or {}).get("session_id")
    mode = (raw or {}).get("mode", "confirmation")

    if not isinstance(message, str) or not message.strip():
        await ws_manager.send_to(
            connection_id,
            {
                "type": "error",
                "content": "",
                "status": "failed",
                "error": "Field 'message' is required and must be a non-empty string.",
            },
        )
        return

    if not isinstance(session_id, str) or not session_id.strip():
        await ws_manager.send_to(
            connection_id,
            {
                "type": "error",
                "content": "",
                "status": "failed",
                "error": "Field 'session_id' is required and must be a non-empty string.",
            },
        )
        return

    if mode not in ("confirmation", "automatic"):
        await ws_manager.send_to(
            connection_id,
            {
                "type": "error",
                "content": "",
                "status": "failed",
                "error": "Field 'mode' must be 'confirmation' or 'automatic'.",
            },
        )
        return

    # Bind the connection to this session if not yet bound.
    adapter = ws_manager.get(connection_id)
    if adapter is not None and getattr(adapter, "session_id", None) != session_id:
        await ws_manager.bind_session(connection_id, session_id)

    # Ensure the session exists in the session manager.
    orchestrator.session_manager.get_or_create(session_id)

    # Stream the response back to the originating client only.
    async for envelope in orchestrator.stream_message(
        message, session_id, mode=mode
    ):
        await ws_manager.send_to(connection_id, envelope)


# ---------------------------------------------------------------------
# Module-level wiring used by the FastAPI app.
# ---------------------------------------------------------------------

# These singletons are created once at import time. Tests can build
# their own router+orchestrator pair with `build_ws_router()`.
_global_ws_manager = WebSocketManager()
_global_orchestrator = Orchestrator(ws_manager=_global_ws_manager)


def get_ws_manager() -> WebSocketManager:
    """Return the process-wide WebSocket manager."""
    return _global_ws_manager


def get_orchestrator() -> Orchestrator:
    """Return the process-wide orchestrator."""
    return _global_orchestrator


@router.websocket("/ws/chat")
async def chat_websocket(socket: WebSocket) -> None:
    """WebSocket endpoint: /ws/chat.

    Accepts JSON requests of the form:
        {"message": "...", "session_id": "...", "mode": "confirmation"}

    Streams back envelopes with `type` in {"chunk", "message", "error"}.
    """
    await chat_endpoint(socket, _global_orchestrator, _global_ws_manager)


def build_ws_router(
    orchestrator: Orchestrator | None = None,
    ws_manager: WebSocketManager | None = None,
) -> APIRouter:
    """Construct a fresh WebSocket router for testing.

    Args:
        orchestrator: Optional orchestrator. If None, a new one is built.
        ws_manager:   Optional manager. If None, a new one is built.

    Returns:
        An APIRouter with a /ws/chat endpoint bound to the provided
        orchestrator/manager pair.
    """
    manager = ws_manager or WebSocketManager()
    orch = orchestrator or Orchestrator(ws_manager=manager)
    new_router = APIRouter()

    @new_router.websocket("/ws/chat")
    async def _chat(socket: WebSocket) -> None:
        await chat_endpoint(socket, orch, manager)

    return new_router
