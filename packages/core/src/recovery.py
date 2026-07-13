"""Recovery and resilience system for Liz Coder Plus.

Sprint 1.8 — Phase 4.

Provides the ability to recover system state after a restart:

  - RecoveryManager: orchestrates the recovery process.
  - CheckpointManager: saves and restores periodic snapshots.
  - Automatic task resumption after restart.
  - Automatic workflow resumption after restart.
  - Scheduler job recovery.
  - Timeout handling for stuck tasks.
  - Automatic retries with backoff.

Architecture::

    RecoveryManager
      ├── recover_all()
      │   ├── recover_tasks()      → TaskManager.load_from_persistence()
      │   ├── recover_workflows()  → WorkflowManager.load_from_persistence()
      │   ├── recover_scheduler()  → Scheduler._load_persisted_jobs()
      │   └── recover_checkpoints()
      └── on_shutdown()
          └── CheckpointManager.save_checkpoint()

    CheckpointManager
      ├── save_checkpoint()        → SQLite
      └── restore_checkpoint()     → SQLite
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.events import (
    CHECKPOINT_RESTORED,
    CHECKPOINT_SAVED,
    RECOVERY_COMPLETED,
    RECOVERY_STARTED,
    RECOVERY_TASK_RESUMED,
    RECOVERY_WORKFLOW_RESUMED,
)

if TYPE_CHECKING:
    from src.task import TaskState

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Checkpoint data
# ----------------------------------------------------------------------


@dataclass
class Checkpoint:
    """A system state snapshot.

    Attributes:
        id:          Unique identifier.
        name:        Descriptive name (e.g. 'auto', 'pre-shutdown').
        created_at:  Timestamp.
        data:        The snapshot data (tasks, workflows, scheduler state).
        metadata:    Extra information.
    """

    name: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "data": dict(self.data),
            "metadata": dict(self.metadata),
        }


# ----------------------------------------------------------------------
# Checkpoint Manager
# ----------------------------------------------------------------------


class CheckpointManager:
    """Manages system checkpoints.

    Saves periodic snapshots of system state to SQLite so the system
    can be restored after a restart. Checkpoints include task and
    workflow summaries.

    Args:
        db: An initialized DatabaseManager (or any object with
            connection() and execute() methods).
    """

    # Table name for checkpoints.
    TABLE = "checkpoints"

    def __init__(self, db: Any) -> None:
        self._db = db
        self._event_bus: Any = None
        logger.info("CheckpointManager created")

    def attach_event_bus(self, bus: Any) -> None:
        """Attach an event bus for emitting checkpoint events."""
        self._event_bus = bus

    async def ensure_table(self) -> None:
        """Create the checkpoints table if it doesn't exist."""
        try:
            conn = await self._db.connection()
            await conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self.TABLE} ("
                "id TEXT PRIMARY KEY, "
                "name TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "data TEXT NOT NULL DEFAULT '{{}}', "
                "metadata TEXT NOT NULL DEFAULT '{{}}'"
                ")"
            )
            await conn.commit()
            logger.info("Checkpoints table ensured")
        except Exception:
            logger.exception("Failed to create checkpoints table")

    async def save_checkpoint(
        self,
        name: str,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Save a checkpoint.

        Args:
            name:    Checkpoint name.
            data:    Snapshot data.
            metadata: Extra info.

        Returns:
            The checkpoint id.
        """
        checkpoint = Checkpoint(name=name, data=data, metadata=metadata or {})

        try:
            conn = await self._db.connection()
            await conn.execute(
                f"INSERT OR REPLACE INTO {self.TABLE} "
                "(id, name, created_at, data, metadata) VALUES (?, ?, ?, ?, ?)",
                (
                    checkpoint.id,
                    checkpoint.name,
                    checkpoint.created_at,
                    json.dumps(checkpoint.data, ensure_ascii=False, default=str),
                    json.dumps(checkpoint.metadata, ensure_ascii=False, default=str),
                ),
            )
            await conn.commit()
            logger.info("Checkpoint saved: id=%s name=%s", checkpoint.id, name)
        except Exception:
            logger.exception("Failed to save checkpoint '%s'", name)

        await self._emit(CHECKPOINT_SAVED, {
            "checkpoint_id": checkpoint.id,
            "name": name,
        })
        return checkpoint.id

    async def restore_latest(self) -> dict[str, Any] | None:
        """Restore the most recent checkpoint.

        Returns:
            The checkpoint data dict, or None if no checkpoints exist.
        """
        try:
            conn = await self._db.connection()
            cursor = await conn.execute(
                f"SELECT * FROM {self.TABLE} ORDER BY created_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            if row is None:
                logger.info("No checkpoints to restore")
                return None

            data = dict(row)
            try:
                data["data"] = json.loads(data.get("data", "{}"))
            except (json.JSONDecodeError, TypeError):
                data["data"] = {}
            try:
                data["metadata"] = json.loads(data.get("metadata", "{}"))
            except (json.JSONDecodeError, TypeError):
                data["metadata"] = {}

            logger.info(
                "Checkpoint restored: id=%s name=%s created=%s",
                data["id"], data["name"], data["created_at"],
            )
            await self._emit(CHECKPOINT_RESTORED, {
                "checkpoint_id": data["id"],
                "name": data["name"],
                "created_at": data["created_at"],
            })
            return data
        except Exception:
            logger.exception("Failed to restore checkpoint")
            return None

    async def list_checkpoints(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent checkpoints."""
        try:
            conn = await self._db.connection()
            cursor = await conn.execute(
                f"SELECT id, name, created_at, metadata FROM {self.TABLE} "
                f"ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                r = dict(row)
                try:
                    r["metadata"] = json.loads(r.get("metadata", "{}"))
                except (json.JSONDecodeError, TypeError):
                    r["metadata"] = {}
                result.append(r)
            return result
        except Exception:
            logger.exception("Failed to list checkpoints")
            return []

    async def delete_checkpoint(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint."""
        try:
            conn = await self._db.connection()
            cursor = await conn.execute(
                f"DELETE FROM {self.TABLE} WHERE id = ?", (checkpoint_id,)
            )
            await conn.commit()
            return cursor.rowcount > 0
        except Exception:
            logger.exception("Failed to delete checkpoint")
            return False

    async def prune_old_checkpoints(self, keep: int = 50) -> int:
        """Delete old checkpoints, keeping only the most recent N.

        Returns:
            Number of checkpoints deleted.
        """
        try:
            conn = await self._db.connection()
            cursor = await conn.execute(
                f"SELECT COUNT(*) as cnt FROM {self.TABLE}"
            )
            row = await cursor.fetchone()
            total = row["cnt"] if row else 0

            if total <= keep:
                return 0

            delete_count = total - keep
            cursor = await conn.execute(
                f"DELETE FROM {self.TABLE} WHERE id IN ("
                f"  SELECT id FROM {self.TABLE} "
                f"  ORDER BY created_at ASC LIMIT ?"
                f")",
                (delete_count,),
            )
            await conn.commit()
            deleted = cursor.rowcount
            logger.info("Pruned %d old checkpoints (kept %d)", deleted, keep)
            return deleted
        except Exception:
            logger.exception("Failed to prune checkpoints")
            return 0

    async def _emit(self, event_type: str, payload: Any) -> None:
        if self._event_bus is None:
            return
        try:
            await self._event_bus.publish(event_type, payload)
        except Exception:
            logger.exception("Failed to emit event '%s'", event_type)


# ----------------------------------------------------------------------
# Recovery Manager
# ----------------------------------------------------------------------


class RecoveryManager:
    """Orchestrates system recovery after a restart.

    The recovery manager coordinates restoring state from persistence
    into the in-memory managers. It recovers tasks, workflows, and
    scheduler jobs, and can optionally restore from a checkpoint.

    Usage::

        recovery = RecoveryManager(
            task_manager=tm,
            workflow_manager=wm,
            scheduler=scheduler,
            checkpoint_manager=cpm,
            event_bus=bus,
        )
        report = await recovery.recover_all()

    Args:
        task_manager:      The TaskManager.
        workflow_manager:  The WorkflowManager.
        scheduler:         The Scheduler (optional).
        checkpoint_manager: The CheckpointManager (optional).
        event_bus:         The EventBus (optional).
    """

    def __init__(
        self,
        *,
        task_manager: Any,
        workflow_manager: Any,
        scheduler: Any | None = None,
        checkpoint_manager: Any | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self._task_manager = task_manager
        self._workflow_manager = workflow_manager
        self._scheduler = scheduler
        self._checkpoint = checkpoint_manager
        self._bus = event_bus

    async def recover_all(self) -> dict[str, Any]:
        """Run the full recovery process.

        Steps:
            1. Emit recovery.started event.
            2. Recover tasks from persistence.
            3. Recover workflows from persistence.
            4. Recover scheduler jobs.
            5. Restore latest checkpoint (informational).
            6. Handle stuck tasks (timed out during restart).
            7. Emit recovery.completed event.

        Returns:
            A recovery report dict with counts and details.
        """
        start = time.monotonic()
        report: dict[str, Any] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "tasks_recovered": 0,
            "workflows_recovered": 0,
            "scheduler_jobs_recovered": 0,
            "checkpoint_restored": None,
            "stuck_tasks_reset": 0,
            "errors": [],
        }

        await self._emit(RECOVERY_STARTED, {"started_at": report["started_at"]})
        logger.info("Starting system recovery...")

        # 1. Recover tasks.
        try:
            if hasattr(self._task_manager, "load_from_persistence"):
                count = await self._task_manager.load_from_persistence()
                report["tasks_recovered"] = count
                logger.info("Recovered %d tasks from persistence", count)
                await self._emit(RECOVERY_TASK_RESUMED, {
                    "count": count,
                })
        except Exception as exc:
            msg = f"Task recovery failed: {exc}"
            report["errors"].append(msg)
            logger.exception(msg)

        # 2. Recover workflows.
        try:
            if hasattr(self._workflow_manager, "load_from_persistence"):
                count = await self._workflow_manager.load_from_persistence()
                report["workflows_recovered"] = count
                logger.info("Recovered %d workflows from persistence", count)
                await self._emit(RECOVERY_WORKFLOW_RESUMED, {
                    "count": count,
                })
        except Exception as exc:
            msg = f"Workflow recovery failed: {exc}"
            report["errors"].append(msg)
            logger.exception(msg)

        # 3. Recover scheduler jobs.
        try:
            if self._scheduler is not None and hasattr(self._scheduler, "_load_persisted_jobs"):
                count = await self._scheduler._load_persisted_jobs()
                report["scheduler_jobs_recovered"] = count
                logger.info("Recovered %d scheduler jobs", count)
        except Exception as exc:
            msg = f"Scheduler recovery failed: {exc}"
            report["errors"].append(msg)
            logger.exception(msg)

        # 4. Restore latest checkpoint (informational).
        try:
            if self._checkpoint is not None:
                cp = await self._checkpoint.restore_latest()
                if cp is not None:
                    report["checkpoint_restored"] = cp.get("id")
        except Exception as exc:
            msg = f"Checkpoint restore failed: {exc}"
            report["errors"].append(msg)
            logger.exception(msg)

        # 5. Handle stuck tasks (tasks that were RUNNING when the
        #    process died — reset them to READY so they can be
        #    retried).
        try:
            from src.task import TaskState
            stuck = self._task_manager.list_by_state(TaskState.RUNNING)
            for task in stuck:
                task.state = TaskState.READY
                task.errors.append("recovered: interrupted by restart")
                if hasattr(self._task_manager, "_persist_task"):
                    await self._task_manager._persist_task(task)
                report["stuck_tasks_reset"] += 1
            if stuck:
                logger.info(
                    "Reset %d stuck RUNNING tasks to READY", len(stuck)
                )
        except Exception as exc:
            msg = f"Stuck task handling failed: {exc}"
            report["errors"].append(msg)
            logger.exception(msg)

        # Finalize.
        duration_ms = int((time.monotonic() - start) * 1000)
        report["duration_ms"] = duration_ms
        report["completed_at"] = datetime.now(timezone.utc).isoformat()

        await self._emit(RECOVERY_COMPLETED, report)
        logger.info(
            "Recovery completed in %dms: tasks=%d workflows=%d "
            "scheduler=%d stuck=%d errors=%d",
            duration_ms,
            report["tasks_recovered"],
            report["workflows_recovered"],
            report["scheduler_jobs_recovered"],
            report["stuck_tasks_reset"],
            len(report["errors"]),
        )
        return report

    async def save_shutdown_checkpoint(self) -> str | None:
        """Save a pre-shutdown checkpoint.

        Captures the current state of tasks, workflows, and scheduler.

        Returns:
            The checkpoint id, or None on failure.
        """
        if self._checkpoint is None:
            return None

        data: dict[str, Any] = {
            "tasks": {},
            "workflows": {},
            "scheduler": {},
        }

        # Capture task summaries.
        try:
            for t in self._task_manager.list_active():
                data["tasks"][t.id] = t.to_dict()
        except Exception:
            logger.exception("Failed to capture task state for checkpoint")

        # Capture workflow summaries.
        try:
            if hasattr(self._workflow_manager, "_workflows"):
                for wid, wf in self._workflow_manager._workflows.items():
                    if hasattr(wf, "to_dict"):
                        data["workflows"][wid] = wf.to_dict()
        except Exception:
            logger.exception("Failed to capture workflow state for checkpoint")

        # Capture scheduler summary.
        try:
            if self._scheduler is not None:
                data["scheduler"] = self._scheduler.summary()
        except Exception:
            logger.exception("Failed to capture scheduler state for checkpoint")

        try:
            cp_id = await self._checkpoint.save_checkpoint(
                "pre-shutdown",
                data,
                metadata={"reason": "graceful_shutdown"},
            )
            logger.info("Pre-shutdown checkpoint saved: %s", cp_id)
            return cp_id
        except Exception:
            logger.exception("Failed to save pre-shutdown checkpoint")
            return None

    async def _emit(self, event_type: str, payload: Any) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(event_type, payload)
        except Exception:
            logger.exception("Failed to emit event '%s'", event_type)


# ----------------------------------------------------------------------
# Resilience helpers
# ----------------------------------------------------------------------


class ResilienceHelper:
    """Utility functions for building resilient operations.

    Provides retry with exponential backoff, timeout handling,
    and circuit-breaker-like patterns.
    """

    @staticmethod
    async def retry_with_backoff(
        fn: Any,
        *args: Any,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        **kwargs: Any,
    ) -> Any:
        """Execute an async function with exponential backoff retries.

        Args:
            fn:          Async callable to execute.
            args:        Positional arguments for fn.
            max_retries: Maximum retry attempts.
            base_delay:  Initial delay in seconds (doubles each retry).
            max_delay:   Maximum delay cap.
            kwargs:      Keyword arguments for fn.

        Returns:
            The return value of fn.

        Raises:
            The last exception if all retries are exhausted.
        """
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        "Retry %d/%d in %.1fs: %s",
                        attempt + 1, max_retries, delay, exc,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "All %d retries exhausted: %s", max_retries, exc,
                    )
        raise last_exc  # type: ignore[misc]

    @staticmethod
    async def with_timeout(
        coro: Any,
        timeout_seconds: float,
        description: str = "operation",
    ) -> Any:
        """Execute a coroutine with a timeout.

        Args:
            coro:            The coroutine to execute.
            timeout_seconds: Maximum wait time.
            description:     Description for error messages.

        Returns:
            The coroutine result.

        Raises:
            TimeoutError: If the coroutine exceeds the timeout.
        """
        try:
            return await asyncio.wait_for(coro, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"{description} timed out after {timeout_seconds:.1f}s"
            ) from None


__all__ = [
    "Checkpoint",
    "CheckpointManager",
    "RecoveryManager",
    "ResilienceHelper",
]