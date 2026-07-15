"""Sprint 3.4 — Plan repository: SQLite persistence for TaskPlan / PlanStep.

This repository persists :class:`TaskPlan` instances produced by the
planner package into the same SQLite database used by the
:class:`MemoryManager` (Sprint 1.8 schema).  It is **optional**: the
:class:`~planner.executor.PlannerExecutor` runs fine without it; the
repository is only needed when the caller wants to survive restarts
or replay past executions.

Schema (see ``migrations/sprint_3_4_plans.sql``)::

    plans          (id, goal, description, priority, status, metadata,
                    created_at, updated_at, workflow_id,
                    agent_context_summary)
    plan_steps     (id, plan_id, title, description, estimated_duration,
                    dependencies, required_agents, status, metadata,
                    created_at, updated_at)

The repository is intentionally **agnostic** about the planner
package's internal dataclasses: it works with plain dicts produced by
``TaskPlan.to_dict()``.  This keeps the memory package free of any
upward dependency on ``packages/planner``.

Usage::

    from src.memory.database import DatabaseManager
    from src.memory.repositories.plan_repository import PlanRepository

    db = DatabaseManager("data/liz_memory.db")
    await db.initialize()
    await db.run_migration("sprint_3_4_plans.sql")
    repo = PlanRepository(db)

    await repo.save_plan(plan.to_dict())
    fetched = await repo.get_plan(plan.id)
    await repo.list_plans(status="running")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.memory.database import DatabaseManager

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanRepository:
    """Data-access layer for the ``plans`` / ``plan_steps`` tables.

    Args:
        db: An initialised :class:`DatabaseManager`.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
        logger.info("PlanRepository created")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def save_plan(self, plan_dict: dict[str, Any]) -> None:
        """Upsert a plan and all its steps.

        Replaces the plan row and any existing steps for the same
        ``plan_id`` (steps are deleted and re-inserted to keep the
        semantics simple and idempotent).

        Args:
            plan_dict: Output of :meth:`TaskPlan.to_dict`.
        """
        plan_id = plan_dict.get("id") or ""
        if not plan_id:
            raise ValueError("plan_dict must contain a non-empty 'id'")

        conn = await self._db.connection()

        # ----- upsert plan row ---------------------------------------------
        plan_metadata = plan_dict.get("metadata") or {}
        if "agent_context_summary" in plan_metadata:
            agent_ctx_summary = plan_metadata["agent_context_summary"]
        else:
            agent_ctx_summary = {}
        workflow_id = plan_metadata.get("execution", {}).get("workflow_id") if isinstance(plan_metadata.get("execution"), dict) else None

        await conn.execute(
            """
            INSERT OR REPLACE INTO plans
                (id, goal, description, priority, status, metadata,
                 created_at, updated_at, workflow_id, agent_context_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                plan_dict.get("goal", ""),
                plan_dict.get("description", ""),
                plan_dict.get("priority", "normal"),
                plan_dict.get("status", "not_started"),
                json.dumps(plan_metadata, ensure_ascii=False, default=str),
                plan_dict.get("created_at") or _now_iso(),
                plan_dict.get("updated_at") or _now_iso(),
                workflow_id,
                json.dumps(agent_ctx_summary, ensure_ascii=False, default=str),
            ),
        )

        # ----- replace steps -----------------------------------------------
        # Delete existing steps for this plan, then re-insert.
        await conn.execute(
            "DELETE FROM plan_steps WHERE plan_id = ?",
            (plan_id,),
        )

        steps = plan_dict.get("steps") or []
        now = _now_iso()
        for step in steps:
            step_id = step.get("id") or ""
            if not step_id:
                # Generate a stable id if missing.
                import uuid
                step_id = uuid.uuid4().hex[:12]
            await conn.execute(
                """
                INSERT INTO plan_steps
                    (id, plan_id, title, description, estimated_duration,
                     dependencies, required_agents, status, metadata,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    plan_id,
                    step.get("title", ""),
                    step.get("description", ""),
                    float(step.get("estimated_duration", 0.0) or 0.0),
                    json.dumps(step.get("dependencies", []), ensure_ascii=False, default=str),
                    json.dumps(step.get("required_agents", []), ensure_ascii=False, default=str),
                    step.get("status", "pending"),
                    json.dumps(step.get("metadata", {}) or {}, ensure_ascii=False, default=str),
                    now,
                    now,
                ),
            )

        await conn.commit()
        logger.debug("Saved plan %s with %d steps", plan_id, len(steps))

    async def update_plan_status(
        self,
        plan_id: str,
        status: str,
        *,
        workflow_id: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Patch a plan's status (and optionally workflow_id / metadata).

        Args:
            plan_id: Plan identifier.
            status:  New status string (validated by the CHECK constraint).
            workflow_id: Optional workflow_id to record.
            extra_metadata: Optional dict merged into the plan's metadata
                (shallow merge; nested keys are replaced, not deep-merged).

        Returns:
            ``True`` if a row was updated, ``False`` if the plan was
            not found.
        """
        conn = await self._db.connection()

        # Fetch current metadata if we need to merge.
        if extra_metadata is not None:
            cursor = await conn.execute(
                "SELECT metadata FROM plans WHERE id = ?", (plan_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            try:
                current_meta = json.loads(row["metadata"] or "{}")
            except (json.JSONDecodeError, TypeError):
                current_meta = {}
            current_meta.update(extra_metadata)
            new_meta_json = json.dumps(current_meta, ensure_ascii=False, default=str)
            if workflow_id is not None:
                await conn.execute(
                    """
                    UPDATE plans
                       SET status = ?, metadata = ?, workflow_id = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (status, new_meta_json, workflow_id, _now_iso(), plan_id),
                )
            else:
                await conn.execute(
                    """
                    UPDATE plans
                       SET status = ?, metadata = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (status, new_meta_json, _now_iso(), plan_id),
                )
        else:
            if workflow_id is not None:
                await conn.execute(
                    """
                    UPDATE plans
                       SET status = ?, workflow_id = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (status, workflow_id, _now_iso(), plan_id),
                )
            else:
                await conn.execute(
                    """
                    UPDATE plans
                       SET status = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (status, _now_iso(), plan_id),
                )

        await conn.commit()
        return True

    async def update_step_status(
        self,
        step_id: str,
        status: str,
        *,
        extra_metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Patch a single step's status (and optionally merge metadata).

        Returns:
            ``True`` if a row was updated, ``False`` otherwise.
        """
        conn = await self._db.connection()
        if extra_metadata is not None:
            cursor = await conn.execute(
                "SELECT metadata FROM plan_steps WHERE id = ?", (step_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            try:
                current_meta = json.loads(row["metadata"] or "{}")
            except (json.JSONDecodeError, TypeError):
                current_meta = {}
            current_meta.update(extra_metadata)
            new_meta_json = json.dumps(current_meta, ensure_ascii=False, default=str)
            await conn.execute(
                """
                UPDATE plan_steps
                   SET status = ?, metadata = ?, updated_at = ?
                 WHERE id = ?
                """,
                (status, new_meta_json, _now_iso(), step_id),
            )
        else:
            await conn.execute(
                """
                UPDATE plan_steps
                   SET status = ?, updated_at = ?
                 WHERE id = ?
                """,
                (status, _now_iso(), step_id),
            )
        await conn.commit()
        return True

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        """Retrieve a plan and all its steps.

        Returns:
            A dict in the same shape as
            :meth:`TaskPlan.to_dict`, or ``None`` if the plan does not
            exist.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM plans WHERE id = ?", (plan_id,)
        )
        plan_row = await cursor.fetchone()
        if plan_row is None:
            return None
        return await self._row_to_plan_dict(plan_row)

    async def list_plans(
        self,
        *,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List plans, optionally filtered by status / priority.

        Plans are returned in descending ``created_at`` order (newest
        first).
        """
        conn = await self._db.connection()
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if priority is not None:
            clauses.append("priority = ?")
            params.append(priority)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        cursor = await conn.execute(
            f"SELECT * FROM plans{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params),
        )
        rows = await cursor.fetchall()
        # Fetch steps for each plan (one round-trip per plan; acceptable
        # for the expected small list sizes).
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(await self._row_to_plan_dict(row))
        return result

    async def get_active_plans(self) -> list[dict[str, Any]]:
        """Return plans whose status is ``planning``, ``ready`` or ``running``."""
        conn = await self._db.connection()
        placeholders = ",".join(["?"] * 3)
        cursor = await conn.execute(
            f"SELECT * FROM plans WHERE status IN ({placeholders}) ORDER BY created_at DESC",
            ("planning", "ready", "running"),
        )
        rows = await cursor.fetchall()
        return [await self._row_to_plan_dict(row) for row in rows]

    async def count_by_status(self) -> dict[str, int]:
        """Return ``{status: count}`` for all plans."""
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT status, COUNT(*) as cnt FROM plans GROUP BY status"
        )
        rows = await cursor.fetchall()
        return {row["status"]: row["cnt"] for row in rows}

    async def get_step(self, step_id: str) -> dict[str, Any] | None:
        """Return a single step dict, or ``None``."""
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM plan_steps WHERE id = ?", (step_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_step_dict(row)

    async def list_steps(self, plan_id: str) -> list[dict[str, Any]]:
        """Return all steps for a plan, ordered by insertion (i.e., plan order)."""
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY rowid",
            (plan_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_step_dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_plan(self, plan_id: str) -> bool:
        """Delete a plan and all its steps (CASCADE).

        Returns:
            ``True`` if a plan was deleted, ``False`` if not found.
        """
        conn = await self._db.connection()
        cursor = await conn.execute(
            "DELETE FROM plans WHERE id = ?", (plan_id,)
        )
        await conn.commit()
        # CASCADE handles plan_steps automatically.
        return cursor.rowcount > 0

    async def delete_by_status(self, status: str) -> int:
        """Delete all plans in a given status. Returns count deleted."""
        conn = await self._db.connection()
        cursor = await conn.execute(
            "DELETE FROM plans WHERE status = ?", (status,)
        )
        await conn.commit()
        return cursor.rowcount or 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _row_to_plan_dict(self, row: Any) -> dict[str, Any]:
        """Convert a plans row to a TaskPlan-shaped dict (with steps)."""
        data = dict(row)
        # Parse JSON columns.
        for k in ("metadata", "agent_context_summary"):
            if k in data and isinstance(data[k], str):
                try:
                    data[k] = json.loads(data[k] or ("{}" if k == "metadata" else "{}"))
                except (json.JSONDecodeError, TypeError):
                    data[k] = {}
        # Surface the workflow_id column back into metadata["execution"]["workflow_id"]
        # so callers (and tests) can recover it after a round-trip — the
        # executor writes it there too, and PlanExecutionResult.to_dict()
        # expects this layout.  This mirrors how apply_execution_result
        # structures the plan's metadata after a run.
        wf_id = data.pop("workflow_id", None)
        if wf_id:
            meta = data.setdefault("metadata", {})
            if not isinstance(meta, dict):
                meta = {}
                data["metadata"] = meta
            execution_meta = meta.setdefault("execution", {})
            if isinstance(execution_meta, dict):
                execution_meta.setdefault("workflow_id", wf_id)
            else:
                execution_meta = {"workflow_id": wf_id}
                meta["execution"] = execution_meta
        agent_ctx = data.pop("agent_context_summary", None)
        if agent_ctx and isinstance(agent_ctx, dict):
            data.setdefault("metadata", {}).setdefault(
                "agent_context_summary", agent_ctx
            )
        # Fetch steps.
        steps = await self.list_steps(data["id"])
        data["steps"] = steps
        return data

    @staticmethod
    def _row_to_step_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        # Parse JSON columns.
        for k in ("dependencies", "required_agents", "metadata"):
            if k in data and isinstance(data[k], str):
                try:
                    data[k] = json.loads(data[k] or "[]")
                except (json.JSONDecodeError, TypeError):
                    data[k] = [] if k != "metadata" else {}
        # Drop the plan_id (it's implicit in the parent plan dict).
        data.pop("plan_id", None)
        # Drop DB-only timestamps; TaskPlan uses its own.
        data.pop("created_at", None)
        data.pop("updated_at", None)
        return data

    async def close(self) -> None:
        """No-op; the DatabaseManager owns the connection."""
        pass


__all__ = ["PlanRepository"]
