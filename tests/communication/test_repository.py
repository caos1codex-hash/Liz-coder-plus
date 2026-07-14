"""Unit tests for communication.repository (Sprint 2.9)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMM_SRC = ROOT / "packages" / "communication" / "src"
sys.path.insert(0, str(COMM_SRC))

for _k in [k for k in list(sys.modules) if k == "communication" or k.startswith("communication.")]:
    sys.modules.pop(_k, None)

from communication.protocol import AgentMessage, MessageType  # noqa: E402
from communication.repository import (  # noqa: E402
    InMemoryMessageRepository,
    MessageRepository,
)

# ----------------------------------------------------------------------
# InMemoryMessageRepository
# ----------------------------------------------------------------------


def test_save_and_get():
    repo = InMemoryMessageRepository()
    msg = AgentMessage.task_request("a", "b", payload={"x": 1})
    msg_id = repo.save(msg)
    assert msg_id == msg.message_id
    assert repo.get(msg.message_id) is msg


def test_get_missing_returns_none():
    repo = InMemoryMessageRepository()
    assert repo.get("nonexistent") is None


def test_list_by_correlation():
    repo = InMemoryMessageRepository()
    m1 = AgentMessage.task_request("a", "b", correlation_id="corr-1")
    m2 = AgentMessage.task_response("b", "a", correlation_id="corr-1")
    m3 = AgentMessage.task_request("a", "c", correlation_id="corr-2")
    repo.save(m1)
    repo.save(m2)
    repo.save(m3)
    results = repo.list_by_correlation("corr-1")
    assert len(results) == 2
    assert all(m.correlation_id == "corr-1" for m in results)


def test_list_by_sender():
    repo = InMemoryMessageRepository()
    repo.save(AgentMessage.task_request("alice", "b"))
    repo.save(AgentMessage.task_request("bob", "a"))
    repo.save(AgentMessage.task_request("alice", "c"))
    results = repo.list_by_sender("alice", limit=10)
    assert len(results) == 2
    assert all(m.sender_agent == "alice" for m in results)


def test_list_by_receiver():
    repo = InMemoryMessageRepository()
    repo.save(AgentMessage.task_request("a", "bob"))
    repo.save(AgentMessage.task_request("b", "alice"))
    repo.save(AgentMessage.task_request("c", "bob"))
    results = repo.list_by_receiver("bob", limit=10)
    assert len(results) == 2


def test_list_by_receiver_includes_broadcast():
    repo = InMemoryMessageRepository()
    repo.save(AgentMessage.task_request("a", "bob"))
    # status_update defaults to broadcast (receiver="*")
    repo.save(AgentMessage.status_update("c"))
    results = repo.list_by_receiver("bob", limit=10)
    assert len(results) == 2  # direct + broadcast


def test_list_by_task():
    repo = InMemoryMessageRepository()
    repo.save(AgentMessage.task_request("a", "b", task_id="t1"))
    repo.save(AgentMessage.task_response("b", "a", task_id="t1"))
    repo.save(AgentMessage.task_request("a", "b", task_id="t2"))
    results = repo.list_by_task("t1")
    assert len(results) == 2


def test_list_by_type():
    repo = InMemoryMessageRepository()
    repo.save(AgentMessage.task_request("a", "b"))
    repo.save(AgentMessage.task_response("b", "a"))
    repo.save(AgentMessage.task_request("c", "d"))
    results = repo.list_by_type(MessageType.TASK_REQUEST)
    assert len(results) == 2


def test_list_recent():
    repo = InMemoryMessageRepository()
    for i in range(5):
        repo.save(AgentMessage.task_request("a", "b", payload={"i": i}))
    results = repo.list_recent(limit=3)
    assert len(results) == 3
    # Newest first.
    assert results[0].payload["i"] == 4


def test_count():
    repo = InMemoryMessageRepository()
    assert repo.count() == 0
    repo.save(AgentMessage.task_request("a", "b"))
    assert repo.count() == 1
    repo.save(AgentMessage.task_response("b", "a"))
    assert repo.count() == 2


def test_clear():
    repo = InMemoryMessageRepository()
    repo.save(AgentMessage.task_request("a", "b"))
    repo.save(AgentMessage.task_response("b", "a"))
    n = repo.clear()
    assert n == 2
    assert repo.count() == 0


def test_max_size_trim():
    repo = InMemoryMessageRepository(max_size=3)
    for i in range(5):
        repo.save(AgentMessage.task_request("a", "b", payload={"i": i}))
    assert repo.count() == 3
    # Oldest should be dropped.
    results = repo.list_recent(limit=10)
    assert results[-1].payload["i"] == 2  # oldest kept is i=2


# ----------------------------------------------------------------------
# MessageRepository (wraps ExecutionRepository — needs multiagent+memory)
# ----------------------------------------------------------------------


class StubExecutionRepository:
    """Stub that mimics ExecutionRepository's interface."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._next_id = 1

    def record(
        self,
        *,
        event_type: str,
        task_id: str | None = None,
        workflow_id: str | None = None,
        agent_name: str = "",
        tool_name: str = "",
        duration_ms: float | None = None,
        status: str = "",
        error: str | None = None,
        result=None,
        metadata: dict | None = None,
    ) -> int:
        row_id = self._next_id
        self._next_id += 1
        self.rows.append(
            {
                "id": row_id,
                "event_type": event_type,
                "task_id": task_id,
                "workflow_id": workflow_id,
                "agent_name": agent_name,
                "tool_name": tool_name,
                "duration_ms": duration_ms,
                "status": status,
                "error": error,
                "result": result,
                "metadata": metadata or {},
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        )
        return row_id

    def get_recent(self, limit: int = 50) -> list[dict]:
        return list(reversed(self.rows))[:limit]

    def get_by_event_type(self, event_type: str, limit: int = 100) -> list[dict]:
        return [r for r in self.rows if r["event_type"] == event_type][:limit]

    def get_by_task(self, task_id: str, limit: int = 100) -> list[dict]:
        return [r for r in self.rows if r["task_id"] == task_id][:limit]

    def count_by_event_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r["event_type"]] = counts.get(r["event_type"], 0) + 1
        return counts


def test_sqlite_repo_save_and_get():
    stub = StubExecutionRepository()
    repo = MessageRepository(stub)
    msg = AgentMessage.task_request("a", "b", payload={"x": 1})
    msg_id = repo.save(msg)
    assert msg_id == msg.message_id
    retrieved = repo.get(msg.message_id)
    assert retrieved is not None
    assert retrieved.sender_agent == "a"
    assert retrieved.payload["x"] == 1


def test_sqlite_repo_count():
    stub = StubExecutionRepository()
    repo = MessageRepository(stub)
    assert repo.count() == 0
    repo.save(AgentMessage.task_request("a", "b"))
    repo.save(AgentMessage.task_response("b", "a"))
    assert repo.count() == 2


def test_sqlite_repo_list_by_correlation():
    stub = StubExecutionRepository()
    repo = MessageRepository(stub)
    repo.save(AgentMessage.task_request("a", "b", correlation_id="c1"))
    repo.save(AgentMessage.task_response("b", "a", correlation_id="c1"))
    repo.save(AgentMessage.task_request("a", "b", correlation_id="c2"))
    results = repo.list_by_correlation("c1")
    assert len(results) == 2


def test_sqlite_repo_list_by_sender():
    stub = StubExecutionRepository()
    repo = MessageRepository(stub)
    repo.save(AgentMessage.task_request("alice", "b"))
    repo.save(AgentMessage.task_request("bob", "a"))
    repo.save(AgentMessage.task_request("alice", "c"))
    results = repo.list_by_sender("alice", limit=10)
    assert len(results) == 2


def test_sqlite_repo_list_by_receiver():
    stub = StubExecutionRepository()
    repo = MessageRepository(stub)
    repo.save(AgentMessage.task_request("a", "bob"))
    repo.save(AgentMessage.task_request("b", "alice"))
    results = repo.list_by_receiver("bob", limit=10)
    assert len(results) == 1


def test_sqlite_repo_list_by_task():
    stub = StubExecutionRepository()
    repo = MessageRepository(stub)
    repo.save(AgentMessage.task_request("a", "b", task_id="t1"))
    repo.save(AgentMessage.task_response("b", "a", task_id="t1"))
    repo.save(AgentMessage.task_request("a", "b", task_id="t2"))
    results = repo.list_by_task("t1")
    assert len(results) == 2


def test_sqlite_repo_list_by_type():
    stub = StubExecutionRepository()
    repo = MessageRepository(stub)
    repo.save(AgentMessage.task_request("a", "b"))
    repo.save(AgentMessage.task_response("b", "a"))
    repo.save(AgentMessage.task_request("c", "d"))
    results = repo.list_by_type(MessageType.TASK_REQUEST, limit=10)
    assert len(results) == 2


def test_sqlite_repo_list_recent():
    stub = StubExecutionRepository()
    repo = MessageRepository(stub)
    for i in range(5):
        repo.save(AgentMessage.task_request("a", "b", payload={"i": i}))
    results = repo.list_recent(limit=3)
    assert len(results) == 3


def test_sqlite_repo_clear_is_noop():
    """MessageRepository.clear() is a no-op on the SQLite backend (by design)."""
    stub = StubExecutionRepository()
    repo = MessageRepository(stub)
    repo.save(AgentMessage.task_request("a", "b"))
    n = repo.clear()
    assert n == 0  # documented no-op
