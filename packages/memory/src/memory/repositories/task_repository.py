"""Task repository — persistent storage for Task instances.

Sprint 1.8 — Phase 2.

Provides async CRUD operations against the SQLite ``tasks`` table.
The TaskManager delegates persistence calls to this repository when
a database is available, keeping the in-memory dict as a hot cache.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.memory.database import DatabaseManager

logger = logging.getLogger(__name__)


class TaskRepository:
    """Data access layer for Task persistence.

    All methods are async and operate through the DatabaseManager's
    SQLite connection. Tasks are stored in the ``tasks`` table with
    full lifecycle data: state, progress, timestamps, errors, result,
    and metadata.

    Args:
        db: An initialized DatabaseManager instance.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
        logger.info("TaskRepository created")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def save_task(self, task_dict: dict[str, Any]) -> None:
        """Insert or update a task in the database.

        Uses ``INSERT OR REPLACE`` so the same task id can be saved
        multiple times (state transitions, progress updates).

        Args:
            task_dict: A Task.to_dict() output.
        """
        conn = await self._db.connection()
        await conn.execute(
            "INSERT OR REPLACE INTO tasks "
            "(id, name, description, priority, state, progress, "
            "agent_name, tools_used, created_at, started_at, completed_at, "
            "result, errors, metadata, parent_id, workflow_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_dict["id"],
                task_dict["name"],
                task_dict.get("description", ""),
                task_dict.get("priority", "normal"),
                task_dict.get("state", "pending"),
                task_dict.get("progress", 0),
                task_dict.get("agent_name", ""),
                json.dumps(task_dict.get("tools_used", []), ensure_ascii=False),
                task_dict.get("created_at", datetime.now(timezone.utc).isoformat()),
                task_dict.get("started_at"),
                task_dict.get("completed_at"),
                self._serialize_result(task_dict.get("result")),
                json.dumps(task_dict.get("errors", []), ensure_ascii=False),
                json.dumps(task_dict.get("metadata", {}), ensure_ascii=False),
                task_dict.get("parent_id"),
                task_dict.get("workflow_id"),
            ),
        )
        await conn.commit()
        logger.debug("Task saved: id=%s state=%s", task_dict["id"], task_dict.get("state"))

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Retrieve a task by id.

        Returns:
            A task dict (matching Task.to_dict() shape), or None if
            the task does not exist.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    async def list_tasks(
        self,
        *,
        state: str | None = None,
        workflow_id: str | None = None,
        agent_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List tasks with optional filters.

        Args:
            state:      Filter by state (e.g. 'running', 'completed').
            workflow_id: Filter by workflow.
            agent_name: Filter by agent.
            limit:      Maximum results.
            offset:     Offset for pagination.

        Returns:
            List of task dicts.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if agent_name is not None:
            clauses.append("agent_name = ?")
            params.append(agent_name)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])

        conn = await self._db.connection()
        cursor = await conn.execute(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task by id.

        Returns:
            True if the task was deleted, False otherwise.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "DELETE FROM tasks WHERE id = ?",
            (task_id,),
        )
        await conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.debug("Task deleted: id=%s", task_id)
        return deleted

    async def delete_by_workflow(self, workflow_id: str) -> int:
        """Delete all tasks belonging to a workflow.

        Returns:
            Number of tasks deleted.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "DELETE FROM tasks WHERE workflow_id = ?",
            (workflow_id,),
        )
        await conn.commit()
        count = cursor.rowcount
        logger.debug("Deleted %d tasks for workflow=%s", count, workflow_id)
        return count

    async def count_by_state(self) -> dict[str, int]:
        """Return a count of tasks grouped by state.

        Returns:
            A dict mapping state names to counts.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT state, COUNT(*) as cnt FROM tasks GROUP BY state"
        )
        rows = await cursor.fetchall()
        return {row["state"]: row["cnt"] for row in rows}

    async def get_active_tasks(self) -> list[dict[str, Any]]:
        """Return all non-terminal tasks (pending, ready, running, waiting)."""
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM tasks "
            "WHERE state IN ('pending', 'ready', 'running', 'waiting') "
            "ORDER BY "
            "  CASE priority "
            "    WHEN 'critical' THEN 0 "
            "    WHEN 'high' THEN 1 "
            "    WHEN 'normal' THEN 2 "
            "    WHEN 'low' THEN 3 "
            "  END, "
            "  created_at ASC"
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Convert a database row to a Task-compatible dict."""
        data = dict(row)
        try:
            data["tools_used"] = json.loads(data.get("tools_used", "[]"))
        except (json.JSONDecodeError, TypeError):
            data["tools_used"] = []
        try:
            data["errors"] = json.loads(data.get("errors", "[]"))
        except (json.JSONDecodeError, TypeError):
            data["errors"] = []
        try:
            data["metadata"] = json.loads(data.get("metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            data["metadata"] = {}
        # Result may be a JSON string or a plain string.
        result_raw = data.get("result")
        if result_raw is not None:
            try:
                parsed = json.loads(result_raw)
                data["result"] = parsed
            except (json.JSONDecodeError, TypeError):
                data["result"] = result_raw
        else:
            data["result"] = None
        return data

    @staticmethod
    def _serialize_result(result: Any) -> str | None:
        """Serialize a task result to a storable string."""
        if result is None:
            return None
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(result)

    async def close(self) -> None:
        """No-op; the DatabaseManager owns the connection."""
        pass