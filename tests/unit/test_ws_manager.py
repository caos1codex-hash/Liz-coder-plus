"""Unit tests for the WebSocketManager.

Sprint 1 - Prompt 2.

Uses an in-memory FakeConnection that satisfies the
WebSocketConnection Protocol, so no real network is required.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
SHARED_SRC = ROOT / "packages" / "shared"
# Insert CORE first (it must win the `src` namespace lookup).
sys.path.insert(0, str(CORE_SRC))
sys.path.append(str(SHARED_SRC))

# Drop any cached `src` namespace so the core's `src` is the one that
# gets imported, regardless of test execution order.
for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.ws_manager import WebSocketManager  # noqa: E402


class FakeConnection:
    """In-memory implementation of the WebSocketConnection protocol."""

    def __init__(self, conn_id: str = "c1") -> None:
        self.id = conn_id
        self.session_id: str | None = None
        self.is_active = True
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload: dict) -> None:
        if not self.is_active:
            raise RuntimeError("inactive")
        self.sent.append(payload)

    async def receive_json(self) -> dict:
        await asyncio.sleep(0)
        return {}

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.is_active = False
        self.closed = True


@pytest.mark.asyncio
async def test_register_increases_count() -> None:
    mgr = WebSocketManager()
    conn = FakeConnection("c1")
    cid = await mgr.register(conn)
    assert cid == "c1"
    assert mgr.connection_count == 1


@pytest.mark.asyncio
async def test_bind_session_groups_connections() -> None:
    mgr = WebSocketManager()
    a = FakeConnection("a")
    b = FakeConnection("b")
    await mgr.register(a, session_id="s1")
    await mgr.register(b, session_id="s1")

    assert mgr.session_connection_count("s1") == 2
    assert "s1" in mgr.list_sessions()


@pytest.mark.asyncio
async def test_send_to_session_delivers_to_all_bound() -> None:
    mgr = WebSocketManager()
    a = FakeConnection("a")
    b = FakeConnection("b")
    c = FakeConnection("c")
    await mgr.register(a, session_id="s1")
    await mgr.register(b, session_id="s1")
    await mgr.register(c, session_id="s2")

    delivered = await mgr.send_to_session("s1", {"type": "hello"})
    assert delivered == 2
    assert len(a.sent) == 1
    assert len(b.sent) == 1
    assert len(c.sent) == 0


@pytest.mark.asyncio
async def test_unregister_removes_from_session_bucket() -> None:
    mgr = WebSocketManager()
    a = FakeConnection("a")
    await mgr.register(a, session_id="s1")
    assert mgr.session_connection_count("s1") == 1

    await mgr.unregister("a", reason="test")
    assert mgr.connection_count == 0
    assert mgr.session_connection_count("s1") == 0
    assert "s1" not in mgr.list_sessions()
    assert a.closed is True


@pytest.mark.asyncio
async def test_broadcast_sends_to_everyone() -> None:
    mgr = WebSocketManager()
    a = FakeConnection("a")
    b = FakeConnection("b")
    await mgr.register(a)
    await mgr.register(b)

    delivered = await mgr.broadcast({"type": "ping"})
    assert delivered == 2
    assert len(a.sent) == 1
    assert len(b.sent) == 1


@pytest.mark.asyncio
async def test_broadcast_except_skips_origin() -> None:
    mgr = WebSocketManager()
    a = FakeConnection("a")
    b = FakeConnection("b")
    await mgr.register(a)
    await mgr.register(b)

    delivered = await mgr.broadcast_except("a", {"type": "notice"})
    assert delivered == 1
    assert len(a.sent) == 0
    assert len(b.sent) == 1


@pytest.mark.asyncio
async def test_inactive_connection_returns_false_on_send() -> None:
    mgr = WebSocketManager()
    a = FakeConnection("a")
    await mgr.register(a)
    a.is_active = False

    ok = await mgr.send_to("a", {"type": "x"})
    assert ok is False


@pytest.mark.asyncio
async def test_on_connect_callback_fires() -> None:
    mgr = WebSocketManager()
    seen: list[tuple[str, dict]] = []

    async def cb(conn_id: str, payload: dict) -> None:
        seen.append((conn_id, payload))

    mgr.on_connect(cb)
    await mgr.register(FakeConnection("c1"), session_id="s1")

    assert seen == [("c1", {"session_id": "s1"})]


@pytest.mark.asyncio
async def test_on_disconnect_callback_fires() -> None:
    mgr = WebSocketManager()
    seen: list[tuple[str, dict]] = []

    async def cb(conn_id: str, payload: dict) -> None:
        seen.append((conn_id, payload))

    mgr.on_disconnect(cb)
    await mgr.register(FakeConnection("c1"), session_id="s1")
    await mgr.unregister("c1", reason="bye")

    assert seen and seen[0][0] == "c1"
    assert seen[0][1]["reason"] == "bye"


@pytest.mark.asyncio
async def test_unregister_unknown_id_is_noop() -> None:
    mgr = WebSocketManager()
    await mgr.unregister("does-not-exist")
    assert mgr.connection_count == 0


@pytest.mark.asyncio
async def test_send_failure_triggers_unregister() -> None:
    mgr = WebSocketManager()
    a = FakeConnection("a")
    await mgr.register(a)

    async def boom(_payload: dict) -> None:
        raise RuntimeError("network down")

    a.send_json = boom  # type: ignore[assignment]

    ok = await mgr.send_to("a", {"type": "x"})
    assert ok is False
    assert mgr.connection_count == 0
