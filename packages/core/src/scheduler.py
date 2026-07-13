"""Scheduler engine for Liz Coder Plus.

Sprint 1.8 — Phase 3.

The Scheduler manages time-based execution of tasks. It supports:

  - One-shot (future) tasks: run once at a specific time.
  - Interval tasks: run repeatedly every N seconds.
  - Cron-like tasks: placeholder for future cron expression support.
  - Priority-based scheduling.
  - Retry with configurable max attempts.
  - Cancellation and pause/resume.
  - Persistence (via SchedulerRepository).

Architecture::

    Scheduler
      ├── SchedulerJob (data model)
      ├── SchedulerRepository (persistence)
      ├── _scheduler_loop (asyncio background task)
      └── → TaskManager / WorkflowManager (execution delegation)

The scheduler runs as an asyncio background task that wakes up
periodically, checks for due jobs, and dispatches them to the
TaskManager for execution.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.task import TaskManager, TaskPriority

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------


class ScheduleType(str, Enum):
    """Type of scheduling."""

    ONCE = "once"
    INTERVAL = "interval"
    CRON = "cron"


class JobState(str, Enum):
    """Lifecycle states of a SchedulerJob."""

    PENDING = "pending"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


_PRIORITY_WEIGHT: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "normal": 2,
    "low": 3,
}


@dataclass
class SchedulerJob:
    """A scheduled job.

    Attributes:
        id:               Unique identifier (UUID4).
        name:             Human-readable name.
        task_name:        Name of the task to create when the job fires.
        description:      Job description.
        priority:         Scheduling priority.
        state:            Current lifecycle state.
        schedule_type:    once, interval, or cron.
        run_at:           ISO timestamp for one-shot jobs.
        interval_seconds: Repeat interval for interval jobs.
        cron_expression:  Cron expression (reserved for future use).
        retry_count:      Number of retries attempted so far.
        max_retries:      Maximum retries before marking as failed.
        last_run_at:      Timestamp of last execution.
        next_run_at:      Computed timestamp for next execution.
        created_at:       Creation timestamp.
        completed_at:     Completion timestamp.
        error:            Last error message.
        metadata:         Free-form metadata.
        task_data:        Data to pass to TaskManager.create().
    """

    name: str
    task_name: str = ""
    description: str = ""
    priority: str = "normal"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: JobState = JobState.PENDING
    schedule_type: ScheduleType = ScheduleType.ONCE
    run_at: str | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    last_run_at: str | None = None
    next_run_at: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    task_data: dict[str, Any] = field(default_factory=dict)

    def is_terminal(self) -> bool:
        return self.state in (
            JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "task_name": self.task_name,
            "description": self.description,
            "priority": self.priority,
            "state": self.state.value,
            "schedule_type": self.schedule_type.value,
            "run_at": self.run_at,
            "interval_seconds": self.interval_seconds,
            "cron_expression": self.cron_expression,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "metadata": dict(self.metadata),
            "task_data": dict(self.task_data),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchedulerJob:
        """Reconstruct a SchedulerJob from a dict."""
        kwargs = dict(data)
        if "state" in kwargs and isinstance(kwargs["state"], str):
            kwargs["state"] = JobState(kwargs["state"])
        if "schedule_type" in kwargs and isinstance(kwargs["schedule_type"], str):
            kwargs["schedule_type"] = ScheduleType(kwargs["schedule_type"])
        return cls(**{k: v for k, v in kwargs.items() if k in cls.__dataclass_fields__})


# ----------------------------------------------------------------------
# Scheduler Repository (persistence)
# ----------------------------------------------------------------------


class SchedulerRepository:
    """Data access layer for scheduler jobs.

    Args:
        db: An initialized DatabaseManager.
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        logger.info("SchedulerRepository created")

    async def save_job(self, job: SchedulerJob) -> None:
        """Insert or update a job."""
        d = job.to_dict()
        conn = await self._db.connection()
        await conn.execute(
            "INSERT OR REPLACE INTO scheduler_jobs "
            "(id, name, task_name, description, priority, state, "
            "schedule_type, run_at, interval_seconds, cron_expression, "
            "retry_count, max_retries, last_run_at, next_run_at, "
            "created_at, completed_at, error, metadata, task_data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                d["id"], d["name"], d["task_name"], d["description"],
                d["priority"], d["state"], d["schedule_type"],
                d["run_at"], d["interval_seconds"], d["cron_expression"],
                d["retry_count"], d["max_retries"], d["last_run_at"],
                d["next_run_at"], d["created_at"], d["completed_at"],
                d["error"],
                __import__("json").dumps(d["metadata"], ensure_ascii=False, default=str),
                __import__("json").dumps(d["task_data"], ensure_ascii=False, default=str),
            ),
        )
        await conn.commit()

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM scheduler_jobs WHERE id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    async def get_due_jobs(self) -> list[dict[str, Any]]:
        """Return scheduled/pending jobs whose next_run_at has passed."""
        now = datetime.now(timezone.utc).isoformat()
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM scheduler_jobs "
            "WHERE state IN ('pending', 'scheduled', 'paused') "
            "AND next_run_at IS NOT NULL AND next_run_at <= ? "
            "ORDER BY "
            "  CASE priority "
            "    WHEN 'critical' THEN 0 "
            "    WHEN 'high' THEN 1 "
            "    WHEN 'normal' THEN 2 "
            "    WHEN 'low' THEN 3 "
            "  END, "
            "  next_run_at ASC",
            (now,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def get_active_jobs(self) -> list[dict[str, Any]]:
        """Return all non-terminal jobs."""
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT * FROM scheduler_jobs "
            "WHERE state NOT IN ('completed', 'failed', 'cancelled') "
            "ORDER BY next_run_at ASC"
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def delete_job(self, job_id: str) -> bool:
        conn = await self._db.connection()
        cursor = await conn.execute(
            "DELETE FROM scheduler_jobs WHERE id = ?", (job_id,)
        )
        await conn.commit()
        return cursor.rowcount > 0

    async def count_by_state(self) -> dict[str, int]:
        conn = await self._db.connection()
        cursor = await conn.execute(
            "SELECT state, COUNT(*) as cnt FROM scheduler_jobs GROUP BY state"
        )
        rows = await cursor.fetchall()
        return {row["state"]: row["cnt"] for row in rows}

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        import json
        data = dict(row)
        for fn in ("metadata", "task_data"):
            try:
                data[fn] = json.loads(data.get(fn, "{}"))
            except (json.JSONDecodeError, TypeError):
                data[fn] = {}
        return data


# ----------------------------------------------------------------------
# Scheduler Engine
# ----------------------------------------------------------------------


class Scheduler:
    """Time-based task scheduler.

    The scheduler runs a background asyncio task that periodically
    checks for due jobs and dispatches them to the TaskManager.

    Usage::

        scheduler = Scheduler(task_manager=tm)
        scheduler.attach_repository(repo)
        await scheduler.start()

        # Schedule a one-shot task
        job = await scheduler.schedule_once(
            "reminder",
            task_name="send_reminder",
            run_at="2026-07-14T15:00:00+00:00",
            task_data={"message": "Meeting in 5 min"},
        )

        # Schedule an interval task
        job = await scheduler.schedule_interval(
            "health_check",
            task_name="check_system",
            interval_seconds=300,
        )

        await scheduler.stop()

    Args:
        task_manager:    The TaskManager for dispatching work.
        event_bus:       Optional event bus for lifecycle events.
        tick_interval:   How often (seconds) the scheduler checks for due jobs.
    """

    DEFAULT_TICK_INTERVAL = 5.0

    def __init__(
        self,
        *,
        task_manager: Any | None = None,
        event_bus: Any | None = None,
        tick_interval: float = DEFAULT_TICK_INTERVAL,
    ) -> None:
        self._task_manager = task_manager
        self._bus = event_bus
        self._tick_interval = tick_interval
        self._repo: SchedulerRepository | None = None
        self._jobs: dict[str, SchedulerJob] = {}
        self._loop_task: asyncio.Task[Any] | None = None
        self._running = False
        self._paused = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Repository
    # ------------------------------------------------------------------

    def attach_repository(self, repo: Any) -> None:
        """Attach a SchedulerRepository for persistence."""
        self._repo = repo
        logger.info("SchedulerRepository attached to Scheduler")

    @property
    def repository(self) -> SchedulerRepository | None:
        return self._repo

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the scheduler loop.

        Loads persisted jobs and begins checking for due tasks.
        """
        if self._running:
            logger.warning("Scheduler already running")
            return

        # Load persisted jobs.
        await self._load_persisted_jobs()

        self._running = True
        self._paused = False
        self._loop_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Scheduler started (tick=%.1fs, jobs=%d)",
                     self._tick_interval, len(self._jobs))

    async def stop(self) -> None:
        """Stop the scheduler loop gracefully."""
        self._running = False
        if self._loop_task is not None and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
        self._loop_task = None
        logger.info("Scheduler stopped")

    async def pause(self) -> None:
        """Pause the scheduler (stop checking for due jobs)."""
        self._paused = True
        logger.info("Scheduler paused")

    async def resume(self) -> None:
        """Resume the scheduler."""
        self._paused = False
        logger.info("Scheduler resumed")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    # ------------------------------------------------------------------
    # Scheduling API
    # ------------------------------------------------------------------

    async def schedule_once(
        self,
        name: str,
        *,
        task_name: str = "",
        run_at: str,
        priority: str = "normal",
        description: str = "",
        task_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SchedulerJob:
        """Schedule a one-shot task at a specific time.

        Args:
            name:        Job name.
            task_name:   Name for the task to create.
            run_at:      ISO timestamp when the job should fire.
            priority:    Priority level.
            description: Job description.
            task_data:   Data passed to TaskManager.create().
            metadata:    Extra metadata.

        Returns:
            The created SchedulerJob.
        """
        job = SchedulerJob(
            name=name,
            task_name=task_name or name,
            description=description,
            priority=priority,
            schedule_type=ScheduleType.ONCE,
            run_at=run_at,
            next_run_at=run_at,
            task_data=task_data or {},
            metadata=metadata or {},
        )
        job.state = JobState.SCHEDULED
        await self._register_job(job)
        return job

    async def schedule_interval(
        self,
        name: str,
        *,
        task_name: str = "",
        interval_seconds: int,
        priority: str = "normal",
        description: str = "",
        task_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SchedulerJob:
        """Schedule a recurring task at a fixed interval.

        Args:
            name:             Job name.
            task_name:        Name for the task to create.
            interval_seconds: Seconds between executions.
            priority:         Priority level.
            description:      Job description.
            task_data:        Data passed to TaskManager.create().
            metadata:         Extra metadata.

        Returns:
            The created SchedulerJob.
        """
        next_run = datetime.now(timezone.utc).isoformat()
        job = SchedulerJob(
            name=name,
            task_name=task_name or name,
            description=description,
            priority=priority,
            schedule_type=ScheduleType.INTERVAL,
            interval_seconds=interval_seconds,
            next_run_at=next_run,
            task_data=task_data or {},
            metadata=metadata or {},
        )
        job.state = JobState.SCHEDULED
        await self._register_job(job)
        return job

    async def schedule_cron(
        self,
        name: str,
        *,
        task_name: str = "",
        cron_expression: str,
        priority: str = "normal",
        description: str = "",
        task_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SchedulerJob:
        """Schedule a cron-based task (placeholder).

        Cron expression parsing will be implemented in a future sprint.
        For now, the job is stored but will not fire automatically.
        """
        job = SchedulerJob(
            name=name,
            task_name=task_name or name,
            description=description,
            priority=priority,
            schedule_type=ScheduleType.CRON,
            cron_expression=cron_expression,
            task_data=task_data or {},
            metadata=metadata or {},
        )
        job.state = JobState.SCHEDULED
        await self._register_job(job)
        logger.info(
            "Cron job registered (execution pending future implementation): %s", name
        )
        return job

    # ------------------------------------------------------------------
    # Job management
    # ------------------------------------------------------------------

    async def cancel_job(self, job_id: str) -> SchedulerJob | None:
        """Cancel a scheduled job.

        Returns:
            The cancelled job, or None if not found.
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.is_terminal():
                logger.warning("Cannot cancel terminal job '%s'", job_id)
                return job
            job.state = JobState.CANCELLED
            await self._persist_job(job)
            return job

    async def get_job(self, job_id: str) -> SchedulerJob | None:
        """Retrieve a job by id."""
        return self._jobs.get(job_id)

    def list_jobs(
        self, *, state: JobState | None = None
    ) -> list[SchedulerJob]:
        """List jobs, optionally filtered by state."""
        jobs = list(self._jobs.values())
        if state is not None:
            jobs = [j for j in jobs if j.state == state]
        return jobs

    def summary(self) -> dict[str, Any]:
        """Return aggregate scheduler stats."""
        counts: dict[str, int] = {s.value: 0 for s in JobState}
        for j in self._jobs.values():
            counts[j.state.value] += 1
        return {
            "total": len(self._jobs),
            "running": self._running,
            "paused": self._paused,
            "tick_interval": self._tick_interval,
            "by_state": counts,
        }

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _scheduler_loop(self) -> None:
        """Background loop that checks for due jobs."""
        logger.debug("Scheduler loop started")
        try:
            while self._running:
                if not self._paused:
                    await self._check_and_dispatch()
                await asyncio.sleep(self._tick_interval)
        except asyncio.CancelledError:
            logger.debug("Scheduler loop cancelled")
        except Exception:
            logger.exception("Scheduler loop error")

    async def _check_and_dispatch(self) -> None:
        """Check for due jobs and dispatch them."""
        now = datetime.now(timezone.utc)

        due_jobs = [
            j for j in self._jobs.values()
            if not j.is_terminal()
            and j.state in (JobState.PENDING, JobState.SCHEDULED, JobState.PAUSED)
            and j.next_run_at is not None
        ]

        for job in due_jobs:
            try:
                next_time = datetime.fromisoformat(job.next_run_at)
                if next_time <= now:
                    await self._dispatch_job(job)
            except (ValueError, TypeError):
                logger.warning(
                    "Invalid next_run_at for job '%s': %s",
                    job.name, job.next_run_at,
                )

    async def _dispatch_job(self, job: SchedulerJob) -> None:
        """Dispatch a due job to the TaskManager."""
        async with self._lock:
            if job.is_terminal():
                return

            job.state = JobState.RUNNING
            job.last_run_at = datetime.now(timezone.utc).isoformat()
            await self._persist_job(job)

        try:
            if self._task_manager is not None:
                from src.task import TaskPriority

                priority_map = {
                    "critical": TaskPriority.CRITICAL,
                    "high": TaskPriority.HIGH,
                    "normal": TaskPriority.NORMAL,
                    "low": TaskPriority.LOW,
                }
                tp = priority_map.get(job.priority, TaskPriority.NORMAL)

                await self._task_manager.create(
                    job.task_name or job.name,
                    description=job.description,
                    priority=tp,
                    metadata={
                        **job.task_data,
                        "scheduler_job_id": job.id,
                    },
                )

            job.retry_count = 0
            job.error = None

            # Compute next run time or complete.
            if job.schedule_type == ScheduleType.ONCE:
                job.state = JobState.COMPLETED
                job.completed_at = datetime.now(timezone.utc).isoformat()
                job.next_run_at = None
            elif job.schedule_type == ScheduleType.INTERVAL and job.interval_seconds:
                next_run = datetime.now(timezone.utc)
                from datetime import timedelta
                next_run += timedelta(seconds=job.interval_seconds)
                job.next_run_at = next_run.isoformat()
                job.state = JobState.SCHEDULED
            else:
                # CRON or unknown: reschedule not yet implemented
                job.state = JobState.SCHEDULED

        except Exception as exc:
            logger.exception("Job '%s' dispatch failed", job.name)
            job.error = str(exc)
            job.retry_count += 1

            if job.retry_count >= job.max_retries:
                job.state = JobState.FAILED
                job.completed_at = datetime.now(timezone.utc).isoformat()
                logger.error(
                    "Job '%s' failed after %d retries",
                    job.name, job.retry_count,
                )
            else:
                # Retry with exponential backoff.
                from datetime import timedelta
                backoff = min(2 ** job.retry_count * 10, 300)  # max 5 min
                next_run = datetime.now(timezone.utc) + timedelta(seconds=backoff)
                job.next_run_at = next_run.isoformat()
                job.state = JobState.SCHEDULED
                logger.info(
                    "Job '%s' will retry (attempt %d/%d) at %s",
                    job.name, job.retry_count, job.max_retries,
                    job.next_run_at,
                )

        finally:
            async with self._lock:
                await self._persist_job(job)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    async def _register_job(self, job: SchedulerJob) -> None:
        """Register a new job in memory and persist it."""
        async with self._lock:
            self._jobs[job.id] = job
            await self._persist_job(job)
        logger.info(
            "Job scheduled: '%s' type=%s priority=%s next=%s",
            job.name, job.schedule_type.value, job.priority, job.next_run_at,
        )

    async def _persist_job(self, job: SchedulerJob) -> None:
        """Persist a job to the repository (if available)."""
        if self._repo is None:
            return
        try:
            await self._repo.save_job(job)
        except Exception:
            logger.exception("Failed to persist job '%s'", job.id)

    async def _load_persisted_jobs(self) -> int:
        """Load active jobs from the repository into memory."""
        if self._repo is None:
            return 0
        try:
            rows = await self._repo.get_active_jobs()
            for row in rows:
                job = SchedulerJob.from_dict(row)
                self._jobs[job.id] = job
            logger.info("Loaded %d persisted jobs", len(rows))
            return len(rows)
        except Exception:
            logger.exception("Failed to load persisted jobs")
            return 0


__all__ = [
    "JobState",
    "ScheduleType",
    "Scheduler",
    "SchedulerJob",
    "SchedulerRepository",
]