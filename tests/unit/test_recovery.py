"""Unit tests for the Recovery module.

Sprint 1.8 — Phase 7.

Cubre Checkpoint, CheckpointManager, RecoveryManager y
ResilienceHelper (retry_with_backoff, with_timeout).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.recovery import (  # noqa: E402
    Checkpoint,
    CheckpointManager,
    RecoveryManager,
    ResilienceHelper,
)


# ----------------------------------------------------------------------
# Checkpoint — creación y serialización
# ----------------------------------------------------------------------


def test_checkpoint_creation() -> None:
    """Crear un Checkpoint con valores por defecto."""
    cp = Checkpoint(name="auto")
    assert cp.name == "auto"
    assert cp.id  # No vacío (UUID).
    assert cp.created_at
    assert cp.data == {}
    assert cp.metadata == {}


def test_checkpoint_to_dict() -> None:
    """to_dict() serializa correctamente todos los campos."""
    cp = Checkpoint(
        name="pre-shutdown",
        data={"tasks": {"t1": "running"}},
        metadata={"reason": "graceful"},
    )
    d = cp.to_dict()
    assert d["name"] == "pre-shutdown"
    assert d["id"] == cp.id
    assert d["created_at"] == cp.created_at
    assert d["data"] == {"tasks": {"t1": "running"}}
    assert d["metadata"] == {"reason": "graceful"}


# ----------------------------------------------------------------------
# CheckpointManager — mock DB
# ----------------------------------------------------------------------


def _make_mock_db() -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """Crea un mock DB con conn, cursor básicos."""
    mock_cursor = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_cursor)
    mock_conn.commit = AsyncMock()
    mock_db = AsyncMock()
    mock_db.connection = AsyncMock(return_value=mock_conn)
    return mock_db, mock_conn, mock_cursor


@pytest.mark.asyncio
async def test_save_checkpoint_emits_event() -> None:
    """save_checkpoint() persiste y emite evento."""
    mock_db, mock_conn, _ = _make_mock_db()
    bus = AsyncMock()
    bus.publish = AsyncMock()

    cpm = CheckpointManager(mock_db)
    cpm.attach_event_bus(bus)

    cp_id = await cpm.save_checkpoint(
        "auto",
        data={"key": "val"},
        metadata={"env": "test"},
    )

    assert cp_id  # Devuelve un ID.
    mock_conn.execute.assert_awaited()
    mock_conn.commit.assert_awaited()
    bus.publish.assert_awaited_once()
    call_args = bus.publish.call_args[0]
    assert "checkpoint.saved" in call_args[0]


@pytest.mark.asyncio
async def test_restore_latest_with_data() -> None:
    """restore_latest() devuelve el checkpoint más reciente."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchone = AsyncMock(return_value={
        "id": "cp-1",
        "name": "auto",
        "created_at": "2026-01-01T00:00:00+00:00",
        "data": json.dumps({"tasks": {}}),
        "metadata": json.dumps({}),
    })
    bus = AsyncMock()

    cpm = CheckpointManager(mock_db)
    cpm.attach_event_bus(bus)

    result = await cpm.restore_latest()
    assert result is not None
    assert result["id"] == "cp-1"
    assert result["data"] == {"tasks": {}}


@pytest.mark.asyncio
async def test_restore_latest_empty() -> None:
    """restore_latest() devuelve None cuando no hay checkpoints."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchone = AsyncMock(return_value=None)

    cpm = CheckpointManager(mock_db)
    result = await cpm.restore_latest()
    assert result is None


@pytest.mark.asyncio
async def test_list_checkpoints() -> None:
    """list_checkpoints() devuelve la lista de checkpoints."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchall = AsyncMock(return_value=[
        {"id": "cp-1", "name": "auto", "created_at": "2026-01-01T00:00:00+00:00", "metadata": "{}"},
        {"id": "cp-2", "name": "manual", "created_at": "2026-01-02T00:00:00+00:00", "metadata": "{}"},
    ])

    cpm = CheckpointManager(mock_db)
    result = await cpm.list_checkpoints()
    assert len(result) == 2
    assert result[0]["id"] == "cp-1"


@pytest.mark.asyncio
async def test_prune_old_checkpoints() -> None:
    """prune_old_checkpoints() elimina los más antiguos."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()

    # Primera llamada: COUNT(*) para saber el total.
    mock_cursor.fetchone = AsyncMock(return_value={"cnt": 100})
    # Segunda llamada: DELETE.
    mock_cursor.rowcount = 50

    cpm = CheckpointManager(mock_db)
    deleted = await cpm.prune_old_checkpoints(keep=50)
    assert deleted == 50
    # Se debió llamar execute al menos 2 veces (COUNT + DELETE).
    assert mock_conn.execute.call_count >= 2


@pytest.mark.asyncio
async def test_prune_no_op_when_under_limit() -> None:
    """prune_old_checkpoints() no borra si no hay exceso."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchone = AsyncMock(return_value={"cnt": 10})

    cpm = CheckpointManager(mock_db)
    deleted = await cpm.prune_old_checkpoints(keep=50)
    assert deleted == 0


# ----------------------------------------------------------------------
# RecoveryManager — orquestación
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_all_success() -> None:
    """recover_all() recupera tareas y workflows correctamente."""
    tm = AsyncMock()
    tm.load_from_persistence = AsyncMock(return_value=5)
    tm.list_by_state = MagicMock(return_value=[])  # No stuck tasks.

    wm = AsyncMock()
    wm.load_from_persistence = AsyncMock(return_value=2)

    scheduler = AsyncMock()
    scheduler._load_persisted_jobs = AsyncMock(return_value=1)

    checkpoint = AsyncMock()
    checkpoint.restore_latest = AsyncMock(return_value=None)

    bus = AsyncMock()
    bus.publish = AsyncMock()

    rm = RecoveryManager(
        task_manager=tm,
        workflow_manager=wm,
        scheduler=scheduler,
        checkpoint_manager=checkpoint,
        event_bus=bus,
    )
    report = await rm.recover_all()

    assert report["tasks_recovered"] == 5
    assert report["workflows_recovered"] == 2
    assert report["scheduler_jobs_recovered"] == 1
    assert report["errors"] == []
    assert "duration_ms" in report


@pytest.mark.asyncio
async def test_recover_all_handles_errors_gracefully() -> None:
    """recover_all() captura errores y continúa con los demás pasos."""
    tm = MagicMock()
    tm.load_from_persistence = AsyncMock(side_effect=RuntimeError("DB down"))
    tm.list_by_state = MagicMock(return_value=[])

    wm = AsyncMock()
    wm.load_from_persistence = AsyncMock(return_value=3)

    bus = AsyncMock()

    rm = RecoveryManager(
        task_manager=tm,
        workflow_manager=wm,
        event_bus=bus,
    )
    report = await rm.recover_all()

    assert report["tasks_recovered"] == 0
    assert report["workflows_recovered"] == 3
    assert len(report["errors"]) >= 1
    assert "Task recovery failed" in report["errors"][0]


@pytest.mark.asyncio
async def test_recover_all_handles_stuck_tasks() -> None:
    """recover_all() reinicia tareas RUNNING a READY."""
    from src.task import Task, TaskState

    stuck_task = Task(name="stuck")
    stuck_task.state = TaskState.RUNNING

    tm = MagicMock()
    tm.load_from_persistence = AsyncMock(return_value=0)
    tm.list_by_state = MagicMock(return_value=[stuck_task])
    tm._persist_task = AsyncMock()

    wm = AsyncMock()
    wm.load_from_persistence = AsyncMock(return_value=0)

    bus = AsyncMock()

    rm = RecoveryManager(
        task_manager=tm,
        workflow_manager=wm,
        event_bus=bus,
    )
    report = await rm.recover_all()

    assert report["stuck_tasks_reset"] == 1
    assert stuck_task.state == TaskState.READY
    assert "recovered: interrupted by restart" in stuck_task.errors[0]


@pytest.mark.asyncio
async def test_save_shutdown_checkpoint() -> None:
    """save_shutdown_checkpoint() captura el estado y lo guarda."""
    tm = MagicMock()
    tm.list_active = MagicMock(return_value=[])

    wf_mock = MagicMock()
    wf_dict = {"id": "wf-1", "name": "test", "state": "running"}
    wf_mock.to_dict = MagicMock(return_value=wf_dict)
    wm = MagicMock()
    wm._workflows = {"wf-1": wf_mock}

    scheduler = MagicMock()
    scheduler.summary = MagicMock(return_value={"total": 0})

    checkpoint = AsyncMock()
    checkpoint.save_checkpoint = AsyncMock(return_value="cp-shutdown-id")

    rm = RecoveryManager(
        task_manager=tm,
        workflow_manager=wm,
        scheduler=scheduler,
        checkpoint_manager=checkpoint,
    )

    cp_id = await rm.save_shutdown_checkpoint()
    assert cp_id == "cp-shutdown-id"
    checkpoint.save_checkpoint.assert_awaited_once()
    call_args = checkpoint.save_checkpoint.call_args
    # name es el primer argumento posicional; data es el segundo.
    assert call_args[0][0] == "pre-shutdown"
    data = call_args[0][1]
    assert "tasks" in data
    assert "workflows" in data
    assert "scheduler" in data


@pytest.mark.asyncio
async def test_save_shutdown_checkpoint_no_checkpoint_manager() -> None:
    """save_shutdown_checkpoint() devuelve None si no hay CheckpointManager."""
    tm = MagicMock()
    wm = MagicMock()
    rm = RecoveryManager(task_manager=tm, workflow_manager=wm)

    result = await rm.save_shutdown_checkpoint()
    assert result is None


# ----------------------------------------------------------------------
# ResilienceHelper — retry_with_backoff
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_succeeds_on_first_try() -> None:
    """retry_with_backoff devuelve inmediatamente si la función tiene éxito."""
    fn = AsyncMock(return_value=42)
    result = await ResilienceHelper.retry_with_backoff(
        fn, max_retries=3, base_delay=0.01
    )
    assert result == 42
    assert fn.call_count == 1


@pytest.mark.asyncio
async def test_retry_retries_and_succeeds() -> None:
    """retry_with_backoff reintentará y eventualmente tendrá éxito."""
    fn = AsyncMock(side_effect=[RuntimeError("fail"), RuntimeError("fail"), "ok"])
    result = await ResilienceHelper.retry_with_backoff(
        fn, max_retries=3, base_delay=0.01
    )
    assert result == "ok"
    assert fn.call_count == 3


@pytest.mark.asyncio
async def test_retry_exhausts_retries() -> None:
    """retry_with_backoff lanza la última excepción si se agotan los reintentos."""
    fn = AsyncMock(side_effect=RuntimeError("persistent failure"))
    with pytest.raises(RuntimeError, match="persistent failure"):
        await ResilienceHelper.retry_with_backoff(
            fn, max_retries=2, base_delay=0.01
        )
    assert fn.call_count == 3  # 1 intento + 2 reintentos.


# ----------------------------------------------------------------------
# ResilienceHelper — with_timeout
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_timeout_succeeds() -> None:
    """with_timeout devuelve el resultado si termina a tiempo."""
    async def quick():
        await asyncio.sleep(0.01)
        return "done"

    result = await ResilienceHelper.with_timeout(quick(), timeout_seconds=1.0)
    assert result == "done"


@pytest.mark.asyncio
async def test_with_timeout_raises_on_timeout() -> None:
    """with_timeout lanza TimeoutError si la corrutina tarda demasiado."""
    async def slow():
        await asyncio.sleep(10)

    with pytest.raises(TimeoutError, match="timed out"):
        await ResilienceHelper.with_timeout(
            slow(), timeout_seconds=0.05, description="slow_op"
        )