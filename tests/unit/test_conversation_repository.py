"""Unit tests for the ConversationRepository.

Sprint 1 - Prompt 3.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MEM_SRC = ROOT / "packages" / "memory"
sys.path.insert(0, str(MEM_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.memory.database import DatabaseManager  # noqa: E402
from src.memory.repositories.conversation_repository import (  # noqa: E402
    ConversationRepository,
)


@pytest.fixture
async def repo(tmp_path: Path):
    """Create an initialized repository with a temporary database."""
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    repository = ConversationRepository(db)
    yield repository
    await db.close()


@pytest.mark.asyncio
async def test_save_message_returns_row_id(repo: ConversationRepository) -> None:
    row_id = await repo.save_message("s1", "user", "hello")
    assert row_id == 1

    row_id2 = await repo.save_message("s1", "assistant", "hi there")
    assert row_id2 == 2


@pytest.mark.asyncio
async def test_save_message_with_metadata(repo: ConversationRepository) -> None:
    await repo.save_message("s1", "user", "hello", {"mode": "test"})
    history = await repo.get_session_history("s1")
    assert history[0]["metadata"] == {"mode": "test"}


@pytest.mark.asyncio
async def test_get_session_history_returns_all(repo: ConversationRepository) -> None:
    await repo.save_message("s1", "user", "msg1")
    await repo.save_message("s1", "assistant", "msg2")
    await repo.save_message("s1", "user", "msg3")

    history = await repo.get_session_history("s1")
    assert len(history) == 3
    assert history[0]["content"] == "msg1"
    assert history[2]["content"] == "msg3"


@pytest.mark.asyncio
async def test_get_session_history_with_limit(repo: ConversationRepository) -> None:
    for i in range(5):
        await repo.save_message("s1", "user", f"msg-{i}")

    limited = await repo.get_session_history("s1", limit=2)
    assert len(limited) == 2
    # Most recent 2, ordered ASC
    assert limited[0]["content"] == "msg-3"
    assert limited[1]["content"] == "msg-4"


@pytest.mark.asyncio
async def test_get_session_history_empty_for_unknown(repo: ConversationRepository) -> None:
    history = await repo.get_session_history("nonexistent")
    assert history == []


@pytest.mark.asyncio
async def test_count_messages(repo: ConversationRepository) -> None:
    assert await repo.count_messages("s1") == 0
    await repo.save_message("s1", "user", "hello")
    await repo.save_message("s1", "assistant", "hi")
    assert await repo.count_messages("s1") == 2


@pytest.mark.asyncio
async def test_delete_session(repo: ConversationRepository) -> None:
    await repo.save_message("s1", "user", "hello")
    await repo.save_message("s1", "assistant", "hi")
    assert await repo.count_messages("s1") == 2

    deleted = await repo.delete_session("s1")
    assert deleted is True
    assert await repo.count_messages("s1") == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_session(repo: ConversationRepository) -> None:
    deleted = await repo.delete_session("nonexistent")
    assert deleted is False


@pytest.mark.asyncio
async def test_sessions_are_isolated(repo: ConversationRepository) -> None:
    await repo.save_message("s1", "user", "from s1")
    await repo.save_message("s2", "user", "from s2")

    h1 = await repo.get_session_history("s1")
    h2 = await repo.get_session_history("s2")
    assert len(h1) == 1
    assert len(h2) == 1
    assert h1[0]["content"] == "from s1"
    assert h2[0]["content"] == "from s2"