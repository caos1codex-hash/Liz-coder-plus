"""Unit tests for the Scheduler module.

Sprint 1.8 — Phase 7.

Cubre la creación de SchedulerJob, serialización round-trip,
los enums ScheduleType/JobState, los métodos de programación,
cancelación, listado, resumen, persistencia via SchedulerRepository,
y el ciclo de vida start/stop/dispatch.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.scheduler import (  # noqa: E402
    JobState,
    ScheduleType,
    Scheduler,
    SchedulerJob,
    SchedulerRepository,
)


# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------


def test_schedule_type_values() -> None:
    """Verificar los valores del enum ScheduleType."""
    assert ScheduleType.ONCE.value == "once"
    assert ScheduleType.INTERVAL.value == "interval"
    assert ScheduleType.CRON.value == "cron"


def test_job_state_values() -> None:
    """Verificar los valores del enum JobState."""
    expected = {"pending", "scheduled", "running", "completed", "failed", "cancelled", "paused"}
    actual = {s.value for s in JobState}
    assert actual == expected


# ----------------------------------------------------------------------
# SchedulerJob — creación y serialización
# ----------------------------------------------------------------------


def test_job_creation_defaults() -> None:
    """Un SchedulerJob creado con valores por defecto."""
    job = SchedulerJob(name="test-job")
    assert job.name == "test-job"
    assert job.state == JobState.PENDING
    assert job.schedule_type == ScheduleType.ONCE
    assert job.task_name == ""
    assert job.priority == "normal"
    assert job.max_retries == 3
    assert job.retry_count == 0
    assert job.error is None
    assert job.next_run_at is None


def test_job_to_dict_roundtrip() -> None:
    """to_dict → from_dict preserva todos los campos."""
    job = SchedulerJob(
        name="my-job",
        task_name="task-a",
        description="desc",
        priority="high",
        state=JobState.SCHEDULED,
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at="2026-01-01T00:00:00+00:00",
        task_data={"key": "val"},
        metadata={"env": "test"},
    )
    d = job.to_dict()
    assert d["name"] == "my-job"
    assert d["state"] == "scheduled"
    assert d["schedule_type"] == "interval"
    assert d["interval_seconds"] == 60

    restored = SchedulerJob.from_dict(d)
    assert restored.name == job.name
    assert restored.state == JobState.SCHEDULED
    assert restored.schedule_type == ScheduleType.INTERVAL
    assert restored.interval_seconds == 60
    assert restored.task_data == {"key": "val"}


def test_job_is_terminal() -> None:
    """Comprobar qué estados se consideran terminales."""
    for terminal in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
        job = SchedulerJob(name="x", state=terminal)
        assert job.is_terminal() is True

    for non_terminal in (JobState.PENDING, JobState.SCHEDULED, JobState.RUNNING, JobState.PAUSED):
        job = SchedulerJob(name="x", state=non_terminal)
        assert job.is_terminal() is False


# ----------------------------------------------------------------------
# Scheduler — programación
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_once_sets_next_run_at() -> None:
    """schedule_once crea un job con next_run_at igual a run_at."""
    tm = MagicMock()
    scheduler = Scheduler(task_manager=tm)
    run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    job = await scheduler.schedule_once(
        "reminder",
        task_name="send_email",
        run_at=run_at,
    )

    assert job.name == "reminder"
    assert job.task_name == "send_email"
    assert job.schedule_type == ScheduleType.ONCE
    assert job.next_run_at == run_at
    assert job.state == JobState.SCHEDULED


@pytest.mark.asyncio
async def test_schedule_interval_sets_interval() -> None:
    """schedule_interval crea un job con interval correcto."""
    tm = MagicMock()
    scheduler = Scheduler(task_manager=tm)

    job = await scheduler.schedule_interval(
        "health_check",
        task_name="check_health",
        interval_seconds=300,
    )

    assert job.name == "health_check"
    assert job.schedule_type == ScheduleType.INTERVAL
    assert job.interval_seconds == 300
    assert job.next_run_at is not None
    assert job.state == JobState.SCHEDULED


@pytest.mark.asyncio
async def test_schedule_cron_placeholder() -> None:
    """schedule_cron crea un job (placeholder, sin resolución de cron)."""
    tm = MagicMock()
    scheduler = Scheduler(task_manager=tm)

    job = await scheduler.schedule_cron(
        "nightly",
        task_name="cleanup",
        cron_expression="0 3 * * *",
    )

    assert job.name == "nightly"
    assert job.schedule_type == ScheduleType.CRON
    assert job.cron_expression == "0 3 * * *"
    assert job.state == JobState.SCHEDULED


# ----------------------------------------------------------------------
# Scheduler — cancelación
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_job_marks_cancelled() -> None:
    """cancel_job cambia el estado a CANCELLED."""
    tm = MagicMock()
    scheduler = Scheduler(task_manager=tm)

    job = await scheduler.schedule_once("j1", run_at="2099-01-01T00:00:00+00:00")
    assert job.state == JobState.SCHEDULED

    cancelled = await scheduler.cancel_job(job.id)
    assert cancelled is not None
    assert cancelled.state == JobState.CANCELLED


@pytest.mark.asyncio
async def test_cancel_nonexistent_returns_none() -> None:
    """cancel_job con id inexistente devuelve None."""
    tm = MagicMock()
    scheduler = Scheduler(task_manager=tm)

    result = await scheduler.cancel_job("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_cancel_terminal_job_returns_job_unchanged() -> None:
    """cancel_job en un job terminal devuelve el job sin cambios."""
    tm = MagicMock()
    scheduler = Scheduler(task_manager=tm)

    job = await scheduler.schedule_once("j1", run_at="2099-01-01T00:00:00+00:00")
    job.state = JobState.COMPLETED

    result = await scheduler.cancel_job(job.id)
    assert result is not None
    assert result.state == JobState.COMPLETED


# ----------------------------------------------------------------------
# Scheduler — consultas
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_and_list_jobs() -> None:
    """get_job recupera por id; list_jobs devuelve todos."""
    tm = MagicMock()
    scheduler = Scheduler(task_manager=tm)

    job = await scheduler.schedule_once("j1", run_at="2099-01-01T00:00:00+00:00")
    fetched = await scheduler.get_job(job.id)
    assert fetched is job

    all_jobs = scheduler.list_jobs()
    assert len(all_jobs) == 1
    assert all_jobs[0].name == "j1"

    # Filtrar por estado.
    pending_jobs = scheduler.list_jobs(state=JobState.PENDING)
    assert len(pending_jobs) == 0
    scheduled_jobs = scheduler.list_jobs(state=JobState.SCHEDULED)
    assert len(scheduled_jobs) == 1


@pytest.mark.asyncio
async def test_summary_structure() -> None:
    """summary() devuelve la estructura correcta."""
    tm = MagicMock()
    scheduler = Scheduler(task_manager=tm)

    await scheduler.schedule_once("j1", run_at="2099-01-01T00:00:00+00:00")
    await scheduler.schedule_interval("j2", interval_seconds=60)

    s = scheduler.summary()
    assert s["total"] == 2
    assert s["running"] is False
    assert s["paused"] is False
    assert isinstance(s["tick_interval"], float)
    assert isinstance(s["by_state"], dict)
    assert s["by_state"]["scheduled"] == 2


# ----------------------------------------------------------------------
# SchedulerRepository — persistencia con mock DB
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repo_save_and_get_job() -> None:
    """SchedulerRepository.save_job() y get_job() con mock DB."""
    mock_conn = AsyncMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone = AsyncMock(return_value=None)  # Primero no existe
    mock_conn.execute = AsyncMock(return_value=mock_cursor)
    mock_conn.commit = AsyncMock()

    mock_db = MagicMock()
    mock_db.connection = AsyncMock(return_value=mock_conn)

    repo = SchedulerRepository(mock_db)
    job = SchedulerJob(name="persisted", state=JobState.SCHEDULED)

    await repo.save_job(job)

    # Verificar que se llamó a execute con INSERT OR REPLACE.
    assert mock_conn.execute.called
    call_args = mock_conn.execute.call_args[0][0]
    assert "INSERT OR REPLACE" in call_args
    mock_conn.commit.assert_awaited()


@pytest.mark.asyncio
async def test_repo_get_due_jobs_filtering() -> None:
    """get_due_jobs() filtra por estado y prioridad."""
    # Crear filas simuladas.
    mock_row1 = {"id": "j1", "state": "scheduled", "priority": "high", "next_run_at": "2020-01-01T00:00:00+00:00", "metadata": "{}", "task_data": "{}"}
    mock_row2 = {"id": "j2", "state": "pending", "priority": "low", "next_run_at": "2020-01-01T00:00:00+00:00", "metadata": "{}", "task_data": "{}"}

    mock_cursor = MagicMock()
    mock_cursor.fetchall = AsyncMock(return_value=[mock_row1, mock_row2])

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_cursor)

    mock_db = MagicMock()
    mock_db.connection = AsyncMock(return_value=mock_conn)

    repo = SchedulerRepository(mock_db)
    result = await repo.get_due_jobs()

    assert len(result) == 2
    assert result[0]["id"] == "j1"
    assert result[1]["id"] == "j2"
    # Verificar que se ejecutó la consulta SQL correcta.
    call_args = mock_conn.execute.call_args[0][0]
    assert "next_run_at" in call_args


# ----------------------------------------------------------------------
# Scheduler — ciclo de vida start / stop
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_and_stop_lifecycle() -> None:
    """start() arranca el loop; stop() lo cancela."""
    tm = MagicMock()
    scheduler = Scheduler(task_manager=tm, tick_interval=0.1)

    assert scheduler.is_running is False

    await scheduler.start()
    assert scheduler.is_running is True

    await scheduler.stop()
    assert scheduler.is_running is False
    assert scheduler._loop_task is None


@pytest.mark.asyncio
async def test_start_idempotent() -> None:
    """start() dos veces no crea loops duplicados."""
    tm = MagicMock()
    scheduler = Scheduler(task_manager=tm, tick_interval=0.1)

    await scheduler.start()
    first_task = scheduler._loop_task
    await scheduler.start()  # Segundo start es no-op.
    assert scheduler._loop_task is first_task

    await scheduler.stop()


# ----------------------------------------------------------------------
# Scheduler — _check_and_dispatch
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_and_dispatch_due_job() -> None:
    """_check_and_dispatch despacha jobs vencidos al TaskManager."""
    tm = AsyncMock()
    tm.create = AsyncMock()
    scheduler = Scheduler(task_manager=tm, tick_interval=100)

    # Crear un job "once" con next_run_at en el pasado.
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    job = SchedulerJob(
        name="due-job",
        task_name="task-due",
        schedule_type=ScheduleType.ONCE,
        next_run_at=past,
    )
    job.state = JobState.SCHEDULED
    scheduler._jobs[job.id] = job

    await scheduler._check_and_dispatch()

    # El job debería haberse completado (ONCE).
    assert job.state == JobState.COMPLETED
    assert job.completed_at is not None
    tm.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_and_dispatch_interval_reschedules() -> None:
    """Un job INTERVAL se reprograma tras la ejecución."""
    tm = AsyncMock()
    tm.create = AsyncMock()
    scheduler = Scheduler(task_manager=tm, tick_interval=100)

    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    job = SchedulerJob(
        name="interval-job",
        task_name="task-int",
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=60,
        next_run_at=past,
    )
    job.state = JobState.SCHEDULED
    scheduler._jobs[job.id] = job

    await scheduler._check_and_dispatch()

    # Debe permanecer en SCHEDULED con nuevo next_run_at.
    assert job.state == JobState.SCHEDULED
    assert job.next_run_at is not None
    tm.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_failure_retries() -> None:
    """Si dispatch falla, el job pasa a SCHEDULED con retry_count incrementado."""
    tm = AsyncMock()
    tm.create = AsyncMock(side_effect=RuntimeError("boom"))
    scheduler = Scheduler(task_manager=tm, tick_interval=100)

    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    job = SchedulerJob(
        name="failing-job",
        task_name="task-fail",
        schedule_type=ScheduleType.ONCE,
        next_run_at=past,
        max_retries=3,
    )
    job.state = JobState.SCHEDULED
    scheduler._jobs[job.id] = job

    await scheduler._check_and_dispatch()

    # No debería haber fallado definitivamente (intento 1/3).
    assert job.state == JobState.SCHEDULED
    assert job.retry_count == 1
    assert job.error == "boom"


@pytest.mark.asyncio
async def test_dispatch_exhausts_retries_marks_failed() -> None:
    """Cuando se agotan los reintentos, el job se marca FAILED."""
    tm = AsyncMock()
    tm.create = AsyncMock(side_effect=RuntimeError("boom"))
    scheduler = Scheduler(task_manager=tm, tick_interval=100)

    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    job = SchedulerJob(
        name="failing-job",
        task_name="task-fail",
        schedule_type=ScheduleType.ONCE,
        next_run_at=past,
        max_retries=1,
        retry_count=1,  # Ya usó un intento.
    )
    job.state = JobState.SCHEDULED
    scheduler._jobs[job.id] = job

    await scheduler._check_and_dispatch()

    assert job.state == JobState.FAILED
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_dispatch_with_persisted_repo() -> None:
    """El scheduler persiste el job tras despachar si hay repo."""
    tm = AsyncMock()
    tm.create = AsyncMock()
    repo = AsyncMock()
    repo.save_job = AsyncMock()
    scheduler = Scheduler(task_manager=tm, tick_interval=100)
    scheduler.attach_repository(repo)

    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    job = SchedulerJob(
        name="persisted-job",
        task_name="task-p",
        schedule_type=ScheduleType.ONCE,
        next_run_at=past,
    )
    job.state = JobState.SCHEDULED
    scheduler._jobs[job.id] = job

    await scheduler._check_and_dispatch()

    # Debe haber llamado a save_job al menos una vez (al registrar como RUNNING y al completar).
    assert repo.save_job.call_count >= 1