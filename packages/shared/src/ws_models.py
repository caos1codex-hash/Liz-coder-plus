"""Shared WebSocket message models.

These models define the canonical wire format exchanged between the
Desktop client and the Backend over the /ws/chat WebSocket endpoint.

Sprint 1 - Prompt 2.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------
# Inbound: client -> server
# ---------------------------------------------------------------------


class WebSocketChatRequest(BaseModel):
    """Payload sent by the desktop client over WebSocket.

    Fields:
        message:    The user's raw message text.
        session_id: Stable identifier for the conversation session.
        mode:       Permission mode override for this message
                    ("confirmation" or "automatic"). Defaults to
                    "confirmation" for safety.
    """

    message: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    mode: Literal["confirmation", "automatic"] = "confirmation"


# ---------------------------------------------------------------------
# Outbound: server -> client (streaming envelope)
# ---------------------------------------------------------------------

MessageType = Literal[
    "message",       # final or chunked assistant message
    "chunk",         # partial token while streaming
    "status",        # lifecycle status update
    "error",         # error report
    "pong",          # heartbeat reply
]

MessageStatus = Literal[
    "pending",
    "streaming",
    "completed",
    "failed",
    "cancelled",
]


class WebSocketChatResponse(BaseModel):
    """Streaming-friendly envelope for server -> client messages.

    The Desktop client renders `content` as it arrives. When `type` is
    "chunk", the client appends to the current in-flight assistant
    bubble. When `type` is "message" with status "completed", the
    bubble is finalized.
    """

    type: MessageType
    content: str = ""
    status: MessageStatus = "completed"
    session_id: str | None = None
    message_id: str | None = None
    error: str | None = None
