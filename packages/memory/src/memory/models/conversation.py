"""Conversation message model.

Sprint 1 - Prompt 3: implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MessageRole(str, Enum):
    """Roles a message can have in a conversation."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass(slots=True)
class ConversationMessage:
    """A single message in a conversation.

    Attributes:
        id:          Auto-generated unique identifier.
        session_id:  The conversation session this message belongs to.
        role:        Who sent the message (user, assistant, system).
        content:     The message text.
        created_at:  UTC timestamp when the message was stored.
        metadata:    Optional bag for extra data (agent name, mode, etc.).
    """

    session_id: str
    role: str
    content: str
    id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }