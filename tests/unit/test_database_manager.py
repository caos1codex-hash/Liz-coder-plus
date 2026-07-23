"""Unit tests for the DatabaseManager.

Sprint 1 - Prompt 3.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MEM_SRC = ROOT / "packages" / "memory"
sys.path.insert(0, str(MEM_SRC))

# Drop any cached `src` namespace.
for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.memory.database import DatabaseManager  # noqa: E402


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Return a temporary database path."""
    return str(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_initialize_creates_database(db_path: str) -> None:
    db = DatabaseManager(db_path)
    await db.initialize()
    assert db.is_initialized
    assert os.path.exists(db_path)
    await db.close()


@pytest.mark.asyncio
async def test_initialize_creates_conversations_table(db_path: str) -> None:
    db = DatabaseManager(db_path)
    await db.initialize()
    conn = await db.connection()
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
    )
    row = await cursor.fetchone()
    assert row is not None
    await db.close()


@pytest.mark.asyncio
async def test_initialize_is_idempotent(db_path: str) -> None:
    db = DatabaseManager(db_path)
    await db.initialize()
    await db.initialize()  # second call should be a no-op
    assert db.is_initialized
    await db.close()


@pytest.mark.asyncio
async def test_close_is_idempotent(db_path: str) -> None:
    db = DatabaseManager(db_path)
    await db.initialize()
    await db.close()
    assert not db.is_initialized
    await db.close()  # second close should be safe


@pytest.mark.asyncio
async def test_connection_raises_before_initialize(db_path: str) -> None:
    db = DatabaseManager(db_path)
    with pytest.raises(RuntimeError, match="not initialized"):
        await db.connection()


@pytest.mark.asyncio
async def test_database_path_property() -> None:
    db = DatabaseManager("/some/path.db")
    assert db.database_path == "/some/path.db"