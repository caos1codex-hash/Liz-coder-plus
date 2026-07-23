"""Workflow repository — persistent storage for Workflow instances.

Sprint 1.8 — Phase 2.

Provides async CRUD operations against the SQLite ``workflows`` table.
The WorkflowManager delegates persistence calls to this repository.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.memory.database import DatabaseManager

logger = logging.getLogger(__name__)


class WorkflowRepository:
    """Data access layer for Workflow persistence.

    All methods are async and operate through the DatabaseManager's
    SQLite connection. Workflows are stored in the ``workflows`` table
    with state, timestamps, DAG definition, errors, result, and
    metadata.

    Args:
        db: An initialized DatabaseManager instance.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
        logger.info("WorkflowRepository created")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def save_workflow(self, workflow_dict: dict[str, Any]) -> None:
        """Insert or update a workflow in the database.

        Uses ``INSERT OR REPLACE`` so the same workflow id can be
        saved multiple times (state transitions).

        Args:
            workflow_dict: A dict with workflow fields matching the
                table schema. Must contain at least ``id`` and ``name``.
        """
        conn = await self._db.connection()
        await conn.execute(
            "INSERT OR REPLACE INTO workflows "
            "(id, name, description, state, created_at, started_at, "
            "completed_at, result, errors, metadata, dag_definition) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                workflow_dict["id"],
                workflow_dict["name"],
                workflow_dict.get("description", ""),
                workflow_dict.get("state", "pending"),
                workflow_dict.get("created_at", datetime.now(timezone.utc).isoformat()),
                workflow_dict.get("started_at"),
                workflow_dict.get("completed_at"),
                self._serialize_value(workflow_dict.get("result")),
                json.dumps(workflow_dict.get("errors", []), ensure_ascii=False),
                json.dumps(workflow_dict.get("metadata", {}), ensure_ascii=False),
                json.dumps(workflow_dict.get("dag_definition", {}), ensure_ascii=False),
            ),
        )
        await conn.commit()
        logger.debug(
            "Workflow saved: id=%s state=%s",
            workflow_dict["id"],
            workflow_dict.get("state"),
        )

    async def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        """Retrieve a workflow by id.

        Returns:
            A workflow dict, or None if not found.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM workflows WHERE id = ?",
            (workflow_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    async def list_workflows(
        self,
        *,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List workflows with optional state filter.

        Args:
            state:  Filter by state.
            limit:  Maximum results.
            offset: Pagination offset.

        Returns:
            List of workflow dicts.
        """
        if state is not None:
            conn = await self._db.connection()
            cursor = await conn.execute(
                "SELECT * FROM workflows WHERE state = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (state, limit, offset),
            )
        else:
            conn = await self._db.connection()
            cursor = await conn.execute(
                "SELECT * FROM workflows ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def delete_workflow(self, workflow_id: str) -> bool:
        """Delete a workflow and its associated tasks.

        Returns:
            True if the workflow was deleted.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "DELETE FROM workflows WHERE id = ?",
            (workflow_id,),
        )
        await conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.debug("Workflow deleted: id=%s", workflow_id)
        return deleted

    async def get_active_workflows(self) -> list[dict[str, Any]]:
        """Return all non-terminal workflows (pending, running)."""
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM workflows "
            "WHERE state IN ('pending', 'running') "
            "ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def count_by_state(self) -> dict[str, int]:
        """Return a count of workflows grouped by state."""
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT state, COUNT(*) as cnt FROM workflows GROUP BY state"
        )
        rows = await cursor.fetchall()
        return {row["state"]: row["cnt"] for row in rows}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Convert a database row to a dict with parsed JSON fields."""
        data = dict(row)
        for field_name in ("errors", "metadata", "dag_definition"):
            try:
                data[field_name] = json.loads(data.get(field_name, "{}"))
            except (json.JSONDecodeError, TypeError):
                data[field_name] = {} if field_name != "errors" else []
        # Result may be JSON or plain text.
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