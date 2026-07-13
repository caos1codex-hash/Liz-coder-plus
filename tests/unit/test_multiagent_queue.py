"""Tests para PriorityQueue (Sprint 2.1)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.enums import Priority, TaskStatus  # noqa: E402
from multiagent.queue import PriorityQueue, Task  # noqa: E402


# --- Task ---


def test_task_default_values() -> None:
    t = Task(name="x")
    assert t.status == TaskStatus.QUEUED
    assert t.priority == Priority.NORMAL
    assert t.attempts == 0
    assert t.max_attempts == 3
    assert t.id  # auto-generated
    assert t.created_at
    assert t.is_terminal is False
    assert t.is_active is True


def test_task_can_retry() -> None:
    t = Task(name="x", max_attempts=3)
    t.attempts = 1
    t.transition_to(TaskStatus.FAILED)
    assert t.can_retry() is True
    t.attempts = 3
    assert t.can_retry() is False


def test_task_transition_validation() -> None:
    t = Task(name="x")
    t.transition_to(TaskStatus.RUNNING)
    t.transition_to(TaskStatus.COMPLETED)
    with pytest.raises(ValueError):
        t.transition_to(TaskStatus.RUNNING)


def test_task_serialization() -> None:
    t = Task(name="x", description="d", priority=Priority.HIGH)
    t.transition_to(TaskStatus.RUNNING)
    d = t.to_dict()
    assert d["name"] == "x"
    assert d["priority"] == "high"
    assert d["status"] == "running"


# --- Queue basic ---


@pytest.mark.asyncio
async def test_enqueue_dequeue_basic() -> None:
    q = PriorityQueue()
    t = Task(name="t1")
    await q.enqueue(t)
    assert q.get(t.id) is not None
    out = q.dequeue_nowait()
    assert out is not None
    assert out.id == t.id
    assert out.status == TaskStatus.RUNNING  # dequeue transiciona a RUNNING


@pytest.mark.asyncio
async def test_dequeue_empty_returns_none() -> None:
    q = PriorityQueue()
    assert q.dequeue_nowait() is None


@pytest.mark.asyncio
async def test_enqueue_requires_queued() -> None:
    q = PriorityQueue()
    t = Task(name="x")
    t.transition_to(TaskStatus.RUNNING)
    with pytest.raises(ValueError, match="must be QUEUED"):
        await q.enqueue(t)


@pytest.mark.asyncio
async def test_priority_ordering() -> None:
    q = PriorityQueue()
    t_low = Task(name="low", priority=Priority.LOW)
    t_high = Task(name="high", priority=Priority.HIGH)
    t_crit = Task(name="crit", priority=Priority.CRITICAL)
    # Encolar en orden no-prioritario.
    await q.enqueue(t_low)
    await q.enqueue(t_high)
    await q.enqueue(t_crit)
    # Deben salir en orden de prioridad: crit, high, low.
    first = q.dequeue_nowait()
    second = q.dequeue_nowait()
    third = q.dequeue_nowait()
    assert first.name == "crit"
    assert second.name == "high"
    assert third.name == "low"


@pytest.mark.asyncio
async def test_fifo_within_same_priority() -> None:
    q = PriorityQueue()
    t1 = Task(name="t1")
    # Pequeño sleep para garantizar created_at distinto.
    await asyncio.sleep(0.001)
    t2 = Task(name="t2")
    await q.enqueue(t1)
    await q.enqueue(t2)
    first = q.dequeue_nowait()
    second = q.dequeue_nowait()
    assert first.name == "t1"
    assert second.name == "t2"


@pytest.mark.asyncio
async def test_cancel_task() -> None:
    q = PriorityQueue()
    t = Task(name="x")
    await q.enqueue(t)
    await q.cancel(t.id, reason="test")
    assert t.status == TaskStatus.CANCELLED
    assert t.is_terminal is True


@pytest.mark.asyncio
async def test_pause_resume() -> None:
    q = PriorityQueue()
    t = Task(name="x")
    await q.enqueue(t)
    # Marcar RUNNING primero.
    t.transition_to(TaskStatus.RUNNING)
    await q.pause(t.id)
    assert t.status == TaskStatus.PAUSED
    await q.resume(t.id)
    assert t.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_complete_and_fail() -> None:
    q = PriorityQueue()
    t = Task(name="x")
    await q.enqueue(t)
    t.transition_to(TaskStatus.RUNNING)
    await q.complete(t.id, result={"done": True})
    assert t.status == TaskStatus.COMPLETED
    assert t.result == {"done": True}

    t2 = Task(name="y")
    await q.enqueue(t2)
    t2.transition_to(TaskStatus.RUNNING)
    await q.fail(t2.id, "boom")
    assert t2.status == TaskStatus.FAILED
    assert t2.error == "boom"
    assert t2.attempts == 1


@pytest.mark.asyncio
async def test_retry() -> None:
    q = PriorityQueue()
    t = Task(name="x", max_attempts=3)
    await q.enqueue(t)
    t.transition_to(TaskStatus.RUNNING)
    await q.fail(t.id, "boom")
    assert t.can_retry()
    await q.retry(t.id)
    assert t.status == TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_retry_exhausted_raises() -> None:
    q = PriorityQueue()
    t = Task(name="x", max_attempts=2)
    await q.enqueue(t)
    t.attempts = 2
    t.transition_to(TaskStatus.FAILED)
    with pytest.raises(ValueError, match="cannot be retried"):
        await q.retry(t.id)


@pytest.mark.asyncio
async def test_list_by_status() -> None:
    q = PriorityQueue()
    t1 = Task(name="t1")
    t2 = Task(name="t2")
    await q.enqueue(t1)
    await q.enqueue(t2)
    t2.transition_to(TaskStatus.RUNNING)
    queued = q.list_by_status(TaskStatus.QUEUED)
    running = q.list_by_status(TaskStatus.RUNNING)
    assert len(queued) == 1
    assert len(running) == 1


@pytest.mark.asyncio
async def test_summary() -> None:
    q = PriorityQueue()
    await q.enqueue(Task(name="t1"))
    await q.enqueue(Task(name="t2", priority=Priority.HIGH))
    s = q.summary()
    assert s["total"] == 2
    assert s["by_status"]["queued"] == 2
    assert s["queue_size"] == 2


@pytest.mark.asyncio
async def test_clear() -> None:
    q = PriorityQueue()
    await q.enqueue(Task(name="t1"))
    await q.enqueue(Task(name="t2"))
    await q.clear()
    assert q.summary()["total"] == 0


@pytest.mark.asyncio
async def test_dequeue_async_timeout() -> None:
    q = PriorityQueue()
    # Cola vacía, timeout corto -> None.
    out = await q.dequeue(timeout=0.05)
    assert out is None
