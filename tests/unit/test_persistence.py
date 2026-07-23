"""Unit tests for the persistence repositories.

Sprint 1.8 — Phase 7.

Cubre TaskRepository, WorkflowRepository y ExecutionRepository
usando mocks completos de DatabaseManager. No se usa una base de
datos real.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
MEMORY_SRC = ROOT / "packages" / "memory"
sys.path.insert(0, str(MEMORY_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.memory.repositories.task_repository import TaskRepository  # noqa: E402
from src.memory.repositories.workflow_repository import WorkflowRepository  # noqa: E402
from src.memory.repositories.execution_repository import ExecutionRepository  # noqa: E402


def _make_mock_db() -> tuple[AsyncMock, AsyncMock, MagicMock]:
    """Crea un mock DB con conn, cursor básicos."""
    mock_cursor = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_cursor)
    mock_conn.commit = AsyncMock()
    mock_db = AsyncMock()
    mock_db.connection = AsyncMock(return_value=mock_conn)
    return mock_db, mock_conn, mock_cursor


# ======================================================================
# TaskRepository
# ======================================================================


@pytest.mark.asyncio
async def test_task_repo_save_task() -> None:
    """save_task() ejecuta INSERT OR REPLACE."""
    mock_db, mock_conn, _ = _make_mock_db()
    repo = TaskRepository(mock_db)

    task_dict = {
        "id": "t-1",
        "name": "test-task",
        "description": "desc",
        "priority": "high",
        "state": "running",
        "progress": 50,
        "agent_name": "echo",
        "tools_used": ["tool_a"],
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": "2026-01-01T00:01:00+00:00",
        "completed_at": None,
        "result": {"ok": True},
        "errors": [],
        "metadata": {},
        "parent_id": None,
        "workflow_id": None,
    }

    await repo.save_task(task_dict)

    assert mock_conn.execute.called
    sql = mock_conn.execute.call_args[0][0]
    assert "INSERT OR REPLACE INTO tasks" in sql
    mock_conn.commit.assert_awaited()


@pytest.mark.asyncio
async def test_task_repo_get_task() -> None:
    """get_task() devuelve un dict con JSON parseado."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchone = AsyncMock(return_value={
        "id": "t-1",
        "name": "task-a",
        "description": "",
        "priority": "normal",
        "state": "completed",
        "progress": 100,
        "agent_name": "",
        "tools_used": '["tool_x"]',
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": "2026-01-01T00:01:00+00:00",
        "completed_at": "2026-01-01T00:02:00+00:00",
        "result": '{"done": true}',
        "errors": '[]',
        "metadata": '{}',
        "parent_id": None,
        "workflow_id": None,
    })

    repo = TaskRepository(mock_db)
    result = await repo.get_task("t-1")

    assert result is not None
    assert result["id"] == "t-1"
    assert result["tools_used"] == ["tool_x"]
    assert result["result"] == {"done": True}
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_task_repo_get_task_not_found() -> None:
    """get_task() devuelve None si no existe."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchone = AsyncMock(return_value=None)

    repo = TaskRepository(mock_db)
    result = await repo.get_task("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_task_repo_list_tasks_with_filters() -> None:
    """list_tasks() aplica filtros de estado y agente."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchall = AsyncMock(return_value=[
        {"id": "t-1", "name": "a", "state": "running", "priority": "normal",
         "description": "", "agent_name": "echo", "tools_used": "[]",
         "created_at": "2026-01-01T00:00:00+00:00", "started_at": None,
         "completed_at": None, "result": None, "errors": "[]",
         "metadata": "{}", "parent_id": None, "workflow_id": None},
    ])

    repo = TaskRepository(mock_db)
    results = await repo.list_tasks(state="running", agent_name="echo")

    assert len(results) == 1
    assert results[0]["name"] == "a"
    sql = mock_conn.execute.call_args[0][0]
    assert "WHERE" in sql


@pytest.mark.asyncio
async def test_task_repo_get_active_tasks_ordered_by_priority() -> None:
    """get_active_tasks() ordena por prioridad."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchall = AsyncMock(return_value=[
        {"id": "t-crit", "name": "critical", "state": "running", "priority": "critical",
         "description": "", "agent_name": "", "tools_used": "[]",
         "created_at": "2026-01-01T00:00:00+00:00", "started_at": None,
         "completed_at": None, "result": None, "errors": "[]",
         "metadata": "{}", "parent_id": None, "workflow_id": None},
        {"id": "t-norm", "name": "normal", "state": "running", "priority": "normal",
         "description": "", "agent_name": "", "tools_used": "[]",
         "created_at": "2026-01-01T00:00:00+00:00", "started_at": None,
         "completed_at": None, "result": None, "errors": "[]",
         "metadata": "{}", "parent_id": None, "workflow_id": None},
    ])

    repo = TaskRepository(mock_db)
    results = await repo.get_active_tasks()

    sql = mock_conn.execute.call_args[0][0]
    assert "CASE priority" in sql
    assert "critical" in sql
    assert len(results) == 2


@pytest.mark.asyncio
async def test_task_repo_delete_task() -> None:
    """delete_task() devuelve True si se eliminó."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.rowcount = 1

    repo = TaskRepository(mock_db)
    deleted = await repo.delete_task("t-1")

    assert deleted is True
    mock_conn.commit.assert_awaited()


@pytest.mark.asyncio
async def test_task_repo_delete_task_not_found() -> None:
    """delete_task() devuelve False si no existía."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.rowcount = 0

    repo = TaskRepository(mock_db)
    deleted = await repo.delete_task("nonexistent")

    assert deleted is False


# ======================================================================
# WorkflowRepository
# ======================================================================


@pytest.mark.asyncio
async def test_workflow_repo_save_and_get() -> None:
    """save_workflow() y get_workflow() con mock DB."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()

    # Primera llamada: save (no necesita fetchone).
    # Segunda llamada: get (usa fetchone).
    mock_cursor.fetchone = AsyncMock(return_value={
        "id": "wf-1",
        "name": "test-wf",
        "description": "desc",
        "state": "running",
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": "2026-01-01T00:01:00+00:00",
        "completed_at": None,
        "result": None,
        "errors": "[]",
        "metadata": "{}",
        "dag_definition": '{"nodes": []}',
    })

    repo = WorkflowRepository(mock_db)

    wf_dict = {
        "id": "wf-1",
        "name": "test-wf",
        "description": "desc",
        "state": "running",
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": "2026-01-01T00:01:00+00:00",
        "completed_at": None,
        "result": None,
        "errors": [],
        "metadata": {},
        "dag_definition": {"nodes": []},
    }
    await repo.save_workflow(wf_dict)
    save_sql = mock_conn.execute.call_args_list[0][0][0]
    assert "INSERT OR REPLACE INTO workflows" in save_sql

    result = await repo.get_workflow("wf-1")
    assert result is not None
    assert result["name"] == "test-wf"
    assert result["dag_definition"] == {"nodes": []}


@pytest.mark.asyncio
async def test_workflow_repo_list_with_state_filter() -> None:
    """list_workflows() filtra por estado correctamente."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchall = AsyncMock(return_value=[
        {"id": "wf-1", "name": "completed-wf", "state": "completed",
         "description": "", "created_at": "2026-01-01T00:00:00+00:00",
         "started_at": None, "completed_at": None, "result": None,
         "errors": "[]", "metadata": "{}", "dag_definition": "{}"},
    ])

    repo = WorkflowRepository(mock_db)
    results = await repo.list_workflows(state="completed")

    assert len(results) == 1
    sql = mock_conn.execute.call_args[0][0]
    assert "WHERE state = ?" in sql


@pytest.mark.asyncio
async def test_workflow_repo_list_all() -> None:
    """list_workflows() sin filtro devuelve todos."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchall = AsyncMock(return_value=[])

    repo = WorkflowRepository(mock_db)
    results = await repo.list_workflows()

    sql = mock_conn.execute.call_args[0][0]
    assert "WHERE" not in sql
    assert "ORDER BY created_at DESC" in sql


# ======================================================================
# ExecutionRepository
# ======================================================================


@pytest.mark.asyncio
async def test_execution_repo_record_and_get_by_task() -> None:
    """record() inserta y get_by_task() recupera eventos."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.lastrowid = 42
    mock_cursor.fetchall = AsyncMock(return_value=[
        {"id": 1, "task_id": "t-1", "workflow_id": None, "event_type": "task.started",
         "agent_name": "echo", "tool_name": "", "duration_ms": 100,
         "status": "success", "error": None, "result": None,
         "metadata": "{}", "created_at": "2026-01-01T00:00:00+00:00"},
    ])

    repo = ExecutionRepository(mock_db)

    row_id = await repo.record(
        event_type="task.started",
        task_id="t-1",
        agent_name="echo",
        duration_ms=100,
        status="success",
    )
    assert row_id == 42
    mock_conn.commit.assert_awaited()

    events = await repo.get_by_task("t-1")
    assert len(events) == 1
    assert events[0]["event_type"] == "task.started"
    assert events[0]["status"] == "success"


@pytest.mark.asyncio
async def test_execution_repo_get_recent() -> None:
    """get_recent() devuelve los eventos más recientes."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchall = AsyncMock(return_value=[
        {"id": 2, "task_id": "t-2", "workflow_id": None, "event_type": "tool.executed",
         "agent_name": "", "tool_name": "bash", "duration_ms": 50,
         "status": "success", "error": None, "result": None,
         "metadata": "{}", "created_at": "2026-01-01T00:01:00+00:00"},
        {"id": 1, "task_id": "t-1", "workflow_id": None, "event_type": "task.created",
         "agent_name": "", "tool_name": "", "duration_ms": None,
         "status": "", "error": None, "result": None,
         "metadata": "{}", "created_at": "2026-01-01T00:00:00+00:00"},
    ])

    repo = ExecutionRepository(mock_db)
    recent = await repo.get_recent(limit=10)

    assert len(recent) == 2
    sql = mock_conn.execute.call_args[0][0]
    assert "ORDER BY created_at DESC" in sql


@pytest.mark.asyncio
async def test_execution_repo_count_errors() -> None:
    """count_errors() devuelve el conteo de errores."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchone = AsyncMock(return_value={"cnt": 7})

    repo = ExecutionRepository(mock_db)
    count = await repo.count_errors()

    assert count == 7


@pytest.mark.asyncio
async def test_execution_repo_count_errors_since() -> None:
    """count_errors(since=...) filtra por fecha."""
    mock_db, mock_conn, mock_cursor = _make_mock_db()
    mock_cursor.fetchone = AsyncMock(return_value={"cnt": 3})

    repo = ExecutionRepository(mock_db)
    count = await repo.count_errors(since="2026-01-01T00:00:00+00:00")

    assert count == 3
    sql = mock_conn.execute.call_args[0][0]
    assert "created_at >= ?" in sql