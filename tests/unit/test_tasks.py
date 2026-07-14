"""Unit tests for the Task system.

Sprint 1.7 — Phase 7.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.event_bus import EventBus  # noqa: E402
from src.task import Task, TaskManager, TaskPriority, TaskState  # noqa: E402


# ----------------------------------------------------------------------
# Task creation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_create_emits_event() -> None:
    bus = EventBus()
    tm = TaskManager(event_bus=bus)
    events: list[Any] = []
    bus.subscribe("task.created", lambda p: events.append(p))

    task = await tm.create("test", description="d", agent_name="echo")
    assert len(events) == 1
    assert events[0]["task_id"] == task.id
    assert events[0]["name"] == "test"
    assert task.state == TaskState.PENDING


@pytest.mark.asyncio
async def test_task_create_assigns_unique_ids() -> None:
    tm = TaskManager()
    t1 = await tm.create("a")
    t2 = await tm.create("b")
    assert t1.id != t2.id


# ----------------------------------------------------------------------
# Task lifecycle
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_full_lifecycle() -> None:
    tm = TaskManager()
    t = await tm.create("test", agent_name="echo")
    await tm.start(t.id)
    assert t.state == TaskState.RUNNING
    assert t.started_at is not None

    await tm.complete(t.id, result={"ok": True}, tools_used=["tool_a"])
    assert t.state == TaskState.COMPLETED
    assert t.completed_at is not None
    assert t.progress == 100
    assert t.tools_used == ["tool_a"]


@pytest.mark.asyncio
async def test_task_fail_records_error() -> None:
    tm = TaskManager()
    t = await tm.create("test")
    await tm.start(t.id)
    await tm.fail(t.id, "boom")
    assert t.state == TaskState.FAILED
    assert "boom" in t.errors[0]


@pytest.mark.asyncio
async def test_task_cancel_from_running() -> None:
    tm = TaskManager()
    t = await tm.create("test")
    await tm.start(t.id)
    await tm.cancel(t.id, reason="user requested")
    assert t.state == TaskState.CANCELLED


@pytest.mark.asyncio
async def test_task_cancel_terminal_raises() -> None:
    tm = TaskManager()
    t = await tm.create("test")
    await tm.start(t.id)
    await tm.complete(t.id, result="ok")
    with pytest.raises(ValueError, match="terminal"):
        await tm.cancel(t.id)


@pytest.mark.asyncio
async def test_task_pause_and_resume() -> None:
    tm = TaskManager()
    t = await tm.create("test")
    await tm.start(t.id)
    await tm.pause(t.id)
    assert t.state == TaskState.WAITING

    await tm.resume(t.id)
    assert t.state == TaskState.READY


@pytest.mark.asyncio
async def test_task_pause_from_non_running_raises() -> None:
    tm = TaskManager()
    t = await tm.create("test")
    with pytest.raises(ValueError, match="only be paused from RUNNING"):
        await tm.pause(t.id)


@pytest.mark.asyncio
async def test_task_retry_from_failed() -> None:
    tm = TaskManager()
    t = await tm.create("test")
    await tm.start(t.id)
    await tm.fail(t.id, "boom")
    await tm.retry(t.id)
    assert t.state == TaskState.READY


@pytest.mark.asyncio
async def test_task_retry_from_non_failed_raises() -> None:
    tm = TaskManager()
    t = await tm.create("test")
    with pytest.raises(ValueError, match="only be retried from FAILED"):
        await tm.retry(t.id)


# ----------------------------------------------------------------------
# State machine validation
# ----------------------------------------------------------------------


def test_task_state_transitions_are_validated() -> None:
    t = Task(name="x")
    assert t.state == TaskState.PENDING
    t.transition_to(TaskState.READY)
    t.transition_to(TaskState.RUNNING)
    t.transition_to(TaskState.COMPLETED)
    assert t.is_terminal()


def test_task_state_terminal_cannot_transition() -> None:
    t = Task(name="x")
    t.transition_to(TaskState.READY)
    t.transition_to(TaskState.RUNNING)
    t.transition_to(TaskState.COMPLETED)
    with pytest.raises(ValueError, match="cannot transition"):
        t.transition_to(TaskState.RUNNING)


def test_task_to_dict_serializes() -> None:
    t = Task(name="x", description="d", agent_name="a")
    d = t.to_dict()
    assert d["name"] == "x"
    assert d["state"] == "pending"
    assert d["agent_name"] == "a"
    assert "created_at" in d
    assert d["started_at"] is None


# ----------------------------------------------------------------------
# Queries
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_list_by_state() -> None:
    tm = TaskManager()
    t1 = await tm.create("a")
    t2 = await tm.create("b")
    await tm.start(t1.id)

    running = tm.list_by_state(TaskState.RUNNING)
    pending = tm.list_by_state(TaskState.PENDING)
    assert len(running) == 1
    assert running[0].name == "a"
    assert len(pending) == 1
    assert pending[0].name == "b"


@pytest.mark.asyncio
async def test_task_list_by_agent() -> None:
    tm = TaskManager()
    await tm.create("a", agent_name="echo")
    await tm.create("b", agent_name="code")
    await tm.create("c", agent_name="echo")
    echo_tasks = tm.list_by_agent("echo")
    assert len(echo_tasks) == 2


@pytest.mark.asyncio
async def test_task_next_ready_returns_highest_priority() -> None:
    tm = TaskManager()
    low = await tm.create("low", priority=TaskPriority.LOW)
    crit = await tm.create("crit", priority=TaskPriority.CRITICAL)
    norm = await tm.create("norm", priority=TaskPriority.NORMAL)
    # Move all to READY.
    low.transition_to(TaskState.READY)
    crit.transition_to(TaskState.READY)
    norm.transition_to(TaskState.READY)

    nxt = tm.next_ready()
    assert nxt is not None
    assert nxt.name == "crit"


@pytest.mark.asyncio
async def test_task_summary_reports_counts() -> None:
    tm = TaskManager()
    t1 = await tm.create("a")
    t2 = await tm.create("b")
    await tm.start(t1.id)
    await tm.complete(t1.id, result="ok")

    summary = tm.summary()
    assert summary["total"] == 2
    assert summary["active"] == 1
    assert summary["by_state"]["completed"] == 1
    assert summary["by_state"]["pending"] == 1


# ----------------------------------------------------------------------
# History
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_history_keeps_terminal_tasks() -> None:
    tm = TaskManager()
    t = await tm.create("a")
    await tm.start(t.id)
    await tm.complete(t.id, result="ok")
    history = tm.history()
    assert len(history) == 1
    assert history[0].id == t.id


@pytest.mark.asyncio
async def test_task_history_evicts_when_full() -> None:
    tm = TaskManager()
    tm.MAX_HISTORY = 5  # shrink for test
    for i in range(10):
        t = await tm.create(f"t{i}")
        await tm.start(t.id)
        await tm.complete(t.id, result=i)
    assert len(tm.history()) <= 5


# ----------------------------------------------------------------------
# Update progress
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_update_progress_clamps() -> None:
    tm = TaskManager()
    t = await tm.create("a")
    await tm.update_progress(t.id, 50)
    assert t.progress == 50
    await tm.update_progress(t.id, -10)
    assert t.progress == 0
    await tm.update_progress(t.id, 200)
    assert t.progress == 100
