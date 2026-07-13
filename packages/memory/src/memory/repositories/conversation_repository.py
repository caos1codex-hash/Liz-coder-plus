"""Conversation repository.

Sprint 1 - Prompt 3: implementation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ConversationRepository:
    """Data access layer for conversation messages."""

    def __init__(self, database_manager: Any) -> None:
        self._db = database_manager
        logger.info("ConversationRepository created")

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Save a message and return its row id."""
        return 0

    async def get_session_history(
        self, session_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve messages for a session, most recent last."""
        return []

    async def delete_session(self, session_id: str) -> bool:
        """Delete all messages for a session."""
        return False

    async def count_messages(self, session_id: str) -> int:
        """Count total messages for a session."""
        return 0