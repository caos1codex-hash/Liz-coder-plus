"""Execution history repository — audit trail for task/workflow executions.

Sprint 1.8 — Phase 2.

Records every significant execution step (task start, tool execution,
errors, etc.) in the ``execution_history`` table. This provides a
durable audit trail that survives restarts and enables post-mortem
analysis.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.memory.database import DatabaseManager

logger = logging.getLogger(__name__)


class ExecutionRepository:
    """Data access layer for execution history.

    Each record represents a single execution event: a task starting,
    a tool being invoked, an error occurring, etc.

    Args:
        db: An initialized DatabaseManager instance.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
        logger.info("ExecutionRepository created")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def record(
        self,
        *,
        event_type: str,
        task_id: str | None = None,
        workflow_id: str | None = None,
        agent_name: str = "",
        tool_name: str = "",
        duration_ms: int | None = None,
        status: str = "",
        error: str | None = None,
        result: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Record an execution event.

        Args:
            event_type:  The type of event (e.g. 'task.started').
            task_id:     Optional task identifier.
            workflow_id: Optional workflow identifier.
            agent_name:  Agent that triggered the event.
            tool_name:   Tool that was executed (if applicable).
            duration_ms: Duration of the operation in ms.
            status:      Outcome status (success, failed, timeout, etc.).
            error:       Error message if any.
            result:      Result data (will be JSON-serialized).
            metadata:    Additional metadata dict.

        Returns:
            The auto-generated row id.
        """
        conn = await self._db.connection()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False, default=str)
        result_json = self._serialize_value(result)

        cursor = await conn.execute(
            "INSERT INTO execution_history "
            "(task_id, workflow_id, event_type, agent_name, tool_name, "
            "duration_ms, status, error, result, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                workflow_id,
                event_type,
                agent_name,
                tool_name,
                duration_ms,
                status,
                error,
                result_json,
                meta_json,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await conn.commit()
        row_id = cursor.lastrowid
        logger.debug(
            "Execution recorded: event=%s task=%s workflow=%s status=%s id=%d",
            event_type, task_id, workflow_id, status, row_id,
        )
        return row_id or 0

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_by_task(self, task_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieve execution history for a task.

        Args:
            task_id: The task to query.
            limit:   Maximum records.

        Returns:
            List of execution event dicts, newest first.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM execution_history WHERE task_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (task_id, limit),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def get_by_workflow(self, workflow_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """Retrieve execution history for a workflow.

        Args:
            workflow_id: The workflow to query.
            limit:       Maximum records.

        Returns:
            List of execution event dicts, newest first.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM execution_history WHERE workflow_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (workflow_id, limit),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def get_by_event_type(
        self, event_type: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Retrieve execution history for a specific event type.

        Args:
            event_type: The event type to filter by.
            limit:      Maximum records.

        Returns:
            List of execution event dicts, newest first.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM execution_history WHERE event_type = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (event_type, limit),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve the most recent execution events across all tasks/workflows.

        Args:
            limit: Maximum records.

        Returns:
            List of execution event dicts, newest first.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM execution_history ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def count_by_event_type(self) -> dict[str, int]:
        """Return a count of execution events grouped by event_type."""
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT event_type, COUNT(*) as cnt "
            "FROM execution_history GROUP BY event_type"
        )
        rows = await cursor.fetchall()
        return {row["event_type"]: row["cnt"] for row in rows}

    async def count_errors(self, since: str | None = None) -> int:
        """Count execution errors.

        Args:
            since: ISO timestamp to filter from (optional).

        Returns:
            Number of error records.
        """
        conn = await self._db.connection()
        if since is not None:
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM execution_history "
                "WHERE status = 'failed' AND created_at >= ?",
                (since,),
            )
        else:
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM execution_history WHERE status = 'failed'"
            )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    async def delete_by_task(self, task_id: str) -> int:
        """Delete execution history for a task.

        Returns:
            Number of records deleted.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "DELETE FROM execution_history WHERE task_id = ?",
            (task_id,),
        )
        await conn.commit()
        return cursor.rowcount

    async def delete_by_workflow(self, workflow_id: str) -> int:
        """Delete execution history for a workflow.

        Returns:
            Number of records deleted.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "DELETE FROM execution_history WHERE workflow_id = ?",
            (workflow_id,),
        )
        await conn.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Convert a database row to a dict with parsed JSON fields."""
        data = dict(row)
        try:
            data["metadata"] = json.loads(data.get("metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            data["metadata"] = {}
        result_raw = data.get("result")
        if result_raw is not None:
            try:
                data["result"] = json.loads(result_raw)
            except (json.JSONDecodeError, TypeError):
                pass
        return data

    @staticmethod
    def _serialize_value(value: Any) -> str | None:
        """Serialize a value to a storable string."""
        if value is None:
            return None
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)

    async def close(self) -> None:
        """No-op; the DatabaseManager owns the connection."""
        pass