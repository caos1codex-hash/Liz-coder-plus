"""Conversation repository.

Data access layer for conversation messages. Provides async CRUD
operations against the SQLite conversations table.

Sprint 1 - Prompt 3.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.memory.database import DatabaseManager

logger = logging.getLogger(__name__)


class ConversationRepository:
    """Data access layer for conversation messages.

    All methods are async and operate through the DatabaseManager's
    SQLite connection. Messages are stored in the ``conversations``
    table with session_id, role, content, timestamp, and JSON metadata.
    """

    _VALID_ROLES = ("user", "assistant", "system")

    def __init__(self, database_manager: DatabaseManager) -> None:
        self._db = database_manager
        logger.info("ConversationRepository created")

    def _validate_role(self, role: str) -> None:
        """Raise ValueError if role is not a valid conversation role."""
        if role not in self._VALID_ROLES:
            raise ValueError(
                f"Invalid role '{role}'. Must be one of: {self._VALID_ROLES}"
            )

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Save a message and return its row id.

        Args:
            session_id: The conversation session identifier.
            role:       Message role (user, assistant, system).
            content:    The message text.
            metadata:   Optional dict of extra data, serialized as JSON.

        Returns:
            The auto-generated row id of the inserted message.
        """
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        self._validate_role(role)

        conn = await self._db.connection()
        cursor = await conn.execute(
            "INSERT INTO conversations (session_id, role, content, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, now, meta_json),
        )
        await conn.commit()

        row_id = cursor.lastrowid
        if row_id is None:
            raise RuntimeError("Failed to insert message: no row id returned")
        logger.debug(
            "Saved message: session=%s role=%s id=%d", session_id, role, row_id
        )
        return row_id

    async def get_session_history(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve messages for a session, ordered by creation time.

        Args:
            session_id: The session to query.
            limit:      Maximum number of messages to return (most recent).
                        When None, all messages are returned.

        Returns:
            List of dicts, each with keys: id, session_id, role, content,
            created_at, metadata.
        """
        conn = await self._db.connection()

        if limit is not None:
            # Use a subquery to get the most recent N rows by id.
            cursor = await conn.execute(
                "SELECT * FROM ("
                "  SELECT id, session_id, role, content, created_at, metadata "
                "  FROM conversations WHERE session_id = ? "
                "  ORDER BY id DESC LIMIT ?"
                ") ORDER BY id ASC",
                (session_id, limit),
            )
        else:
            cursor = await conn.execute(
                "SELECT id, session_id, role, content, created_at, metadata "
                "FROM conversations WHERE session_id = ? "
                "ORDER BY id ASC",
                (session_id,),
            )

        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def delete_session(self, session_id: str) -> bool:
        """Delete all messages for a session.

        Args:
            session_id: The session whose messages should be removed.

        Returns:
            True if any rows were deleted, False otherwise.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "DELETE FROM conversations WHERE session_id = ?",
            (session_id,),
        )
        await conn.commit()

        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Deleted session messages: session=%s", session_id)
        return deleted

    async def count_messages(self, session_id: str) -> int:
        """Count total messages for a session.

        Args:
            session_id: The session to count messages for.

        Returns:
            The number of messages stored for the given session.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM conversations WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Convert an aiosqlite.Row to a plain dict with parsed metadata."""
        data = dict(row)
        # Parse JSON metadata back to dict.
        try:
            data["metadata"] = json.loads(data.get("metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            data["metadata"] = {}
        return data