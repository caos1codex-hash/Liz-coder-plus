"""Unit tests for the SessionManager.

Sprint 1 - Prompt 2 (original), updated in Prompt 3 for async append_turn/drop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

# Drop any cached `src` namespace so the core's `src` is the one that
# gets imported, regardless of test execution order.
for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.session_manager import SessionManager  # noqa: E402


def test_create_session_returns_state() -> None:
    mgr = SessionManager()
    state = mgr.create_session("s1")
    assert state.session_id == "s1"
    assert state.history == []
    assert mgr.session_count == 1


def test_create_session_generates_id_when_none() -> None:
    mgr = SessionManager()
    state = mgr.create_session()
    assert state.session_id
    assert len(state.session_id) >= 8


def test_get_or_create_is_idempotent() -> None:
    mgr = SessionManager()
    s1 = mgr.get_or_create("s1")
    s2 = mgr.get_or_create("s1")
    assert s1 is s2
    assert mgr.session_count == 1


@pytest.mark.asyncio
async def test_append_turn_adds_history() -> None:
    mgr = SessionManager()
    await mgr.append_turn("s1", role="user", content="hola")
    await mgr.append_turn("s1", role="assistant", content="Hola, soy Liz")
    history = mgr.history("s1")
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "hola"
    assert history[1].role == "assistant"


@pytest.mark.asyncio
async def test_history_with_limit_returns_tail() -> None:
    mgr = SessionManager()
    for i in range(5):
        await mgr.append_turn("s1", role="user", content=f"msg-{i}")
    tail = mgr.history("s1", limit=2)
    assert len(tail) == 2
    assert tail[0].content == "msg-3"
    assert tail[1].content == "msg-4"


@pytest.mark.asyncio
async def test_history_trims_to_max() -> None:
    mgr = SessionManager()
    mgr.MAX_HISTORY = 5
    for i in range(10):
        await mgr.append_turn("s1", role="user", content=f"msg-{i}")
    history = mgr.history("s1")
    assert len(history) == 5
    assert history[0].content == "msg-5"
    assert history[-1].content == "msg-9"


def test_set_language_and_mode_persist() -> None:
    mgr = SessionManager()
    mgr.set_language("s1", "en")
    mgr.set_mode("s1", "automatic")
    snapshot = mgr.snapshot("s1")
    assert snapshot is not None
    assert snapshot["user_language"] == "en"
    assert snapshot["last_mode"] == "automatic"


@pytest.mark.asyncio
async def test_drop_removes_session() -> None:
    mgr = SessionManager()
    mgr.create_session("s1")
    assert await mgr.drop("s1") is True
    assert mgr.session_count == 0
    assert await mgr.drop("s1") is False  # already gone


def test_snapshot_returns_none_for_unknown() -> None:
    mgr = SessionManager()
    assert mgr.snapshot("nope") is None


def test_history_returns_empty_for_unknown() -> None:
    mgr = SessionManager()
    assert mgr.history("nope") == []


def test_list_sessions_returns_known_ids() -> None:
    mgr = SessionManager()
    mgr.create_session("a")
    mgr.create_session("b")
    assert set(mgr.list_sessions()) == {"a", "b"}


@pytest.mark.asyncio
async def test_has_memory_property() -> None:
    mgr = SessionManager()
    assert mgr.has_memory is False