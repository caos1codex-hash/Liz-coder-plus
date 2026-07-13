"""Error handling and edge case tests for the memory system.

Sprint 1.4 — FASE 5.
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
from src.memory.manager import MemoryManager  # noqa: E402
from src.memory.repositories.conversation_repository import (  # noqa: E402
    ConversationRepository,
)


@pytest.fixture
async def repo(tmp_path: Path):
    db = DatabaseManager(str(tmp_path / "test.db"))
    await db.initialize()
    yield ConversationRepository(db)
    await db.close()


@pytest.fixture
async def mm(tmp_path: Path):
    manager = MemoryManager(max_history=10)
    await manager.initialize(str(tmp_path / "test.db"))
    yield manager
    await manager.close()


# ------------------------------------------------------------------
# ConversationRepository — error and edge case tests
# ------------------------------------------------------------------


class TestRepositoryValidation:
    """Tests for input validation in the conversation repository."""

    @pytest.mark.asyncio
    async def test_save_message_rejects_invalid_role(self, repo: ConversationRepository) -> None:
        with pytest.raises(ValueError, match="Invalid role"):
            await repo.save_message("s1", "invalid_role", "hello")

    @pytest.mark.asyncio
    async def test_save_message_rejects_empty_role(self, repo: ConversationRepository) -> None:
        with pytest.raises(ValueError, match="Invalid role"):
            await repo.save_message("s1", "", "hello")

    @pytest.mark.asyncio
    async def test_save_message_accepts_all_valid_roles(self, repo: ConversationRepository) -> None:
        for role in ("user", "assistant", "system"):
            row_id = await repo.save_message("s1", role, f"msg from {role}")
            assert row_id > 0

    @pytest.mark.asyncio
    async def test_save_message_empty_content_stored(self, repo: ConversationRepository) -> None:
        """Empty content is stored (validation is the caller's responsibility)."""
        row_id = await repo.save_message("s1", "user", "")
        assert row_id > 0


# ------------------------------------------------------------------
# MemoryManager — error and edge case tests
# ------------------------------------------------------------------


class TestMemoryManagerValidation:
    """Tests for input validation in the memory manager."""

    @pytest.mark.asyncio
    async def test_add_message_rejects_invalid_role(self, mm: MemoryManager) -> None:
        with pytest.raises(ValueError, match="Invalid role"):
            await mm.add_message("s1", "hacker", "bad role")

    @pytest.mark.asyncio
    async def test_add_message_rejects_empty_content(self, mm: MemoryManager) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            await mm.add_message("s1", "user", "   ")

    @pytest.mark.asyncio
    async def test_add_message_rejects_whitespace_only(self, mm: MemoryManager) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            await mm.add_message("s1", "user", "\n\t")

    @pytest.mark.asyncio
    async def test_add_message_rejects_empty_session_id(self, mm: MemoryManager) -> None:
        """Empty session_id is technically valid (no explicit validation),
        but content validation still applies."""
        with pytest.raises(ValueError, match="must not be empty"):
            await mm.add_message("", "user", "")


# ------------------------------------------------------------------
# DatabaseManager — edge case tests
# ------------------------------------------------------------------


class TestDatabaseManagerEdgeCases:

    @pytest.mark.asyncio
    async def test_nonexistent_migration_is_skipped(self, tmp_path: Path) -> None:
        """A missing migration file is logged but doesn't crash."""
        db = DatabaseManager(str(tmp_path / "test.db"))
        await db.initialize()
        # Manually try to run a nonexistent migration.
        await db._run_migration("nonexistent.sql")
        # Should not raise; table still works.
        conn = await db.connection()
        cursor = await conn.execute("SELECT COUNT(*) FROM conversations")
        row = await cursor.fetchone()
        assert row["COUNT(*)"] == 0
        await db.close()

    @pytest.mark.asyncio
    async def test_execute_before_initialize_raises(self, tmp_path: Path) -> None:
        db = DatabaseManager(str(tmp_path / "test.db"))
        with pytest.raises(RuntimeError, match="not initialized"):
            await db.execute("SELECT 1")

    @pytest.mark.asyncio
    async def test_multiple_close_calls_are_safe(self, tmp_path: Path) -> None:
        db = DatabaseManager(str(tmp_path / "test.db"))
        await db.initialize()
        await db.close()
        await db.close()
        await db.close()


# ------------------------------------------------------------------
# Integration — session isolation and data integrity
# ------------------------------------------------------------------


class TestSessionIsolation:

    @pytest.mark.asyncio
    async def test_sessions_dont_interfere(self, mm: MemoryManager) -> None:
        await mm.add_message("s1", "user", "from s1")
        await mm.add_message("s2", "user", "from s2")
        await mm.add_message("s1", "assistant", "reply to s1")

        ctx1 = await mm.get_context("s1")
        ctx2 = await mm.get_context("s2")

        assert len(ctx1) == 2
        assert len(ctx2) == 1
        assert all(m["session_id"] == "s1" for m in ctx1)
        assert all(m["session_id"] == "s2" for m in ctx2)

    @pytest.mark.asyncio
    async def test_clear_one_session_preserves_others(self, mm: MemoryManager) -> None:
        await mm.add_message("s1", "user", "keep")
        await mm.add_message("s2", "user", "delete")
        await mm.clear_session("s2")

        assert len(await mm.get_context("s1")) == 1
        assert len(await mm.get_context("s2")) == 0

    @pytest.mark.asyncio
    async def test_metadata_preserves_special_characters(self, mm: MemoryManager) -> None:
        await mm.add_message("s1", "user", "test", {"key": 'value with "quotes"'})
        ctx = await mm.get_context("s1")
        assert ctx[0]["metadata"]["key"] == 'value with "quotes"'