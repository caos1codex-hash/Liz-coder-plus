"""Unit tests for the MemoryManager.

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

from src.memory.manager import MemoryManager  # noqa: E402


@pytest.fixture
async def mm(tmp_path: Path):
    """Create an initialized MemoryManager with a temporary database."""
    manager = MemoryManager(max_history=10)
    await manager.initialize(str(tmp_path / "test.db"))
    yield manager
    await manager.close()


@pytest.mark.asyncio
async def test_add_and_retrieve(mm: MemoryManager) -> None:
    await mm.add_message("s1", "user", "hello")
    await mm.add_message("s1", "assistant", "hi there")

    ctx = await mm.get_context("s1")
    assert len(ctx) == 2
    assert ctx[0]["role"] == "user"
    assert ctx[0]["content"] == "hello"
    assert ctx[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_get_context_with_limit(mm: MemoryManager) -> None:
    for i in range(5):
        await mm.add_message("s1", "user", f"msg-{i}")

    ctx = await mm.get_context("s1", limit=2)
    assert len(ctx) == 2
    assert ctx[0]["content"] == "msg-3"
    assert ctx[1]["content"] == "msg-4"


@pytest.mark.asyncio
async def test_get_context_empty_session(mm: MemoryManager) -> None:
    ctx = await mm.get_context("nonexistent")
    assert ctx == []


@pytest.mark.asyncio
async def test_clear_session(mm: MemoryManager) -> None:
    await mm.add_message("s1", "user", "hello")
    await mm.add_message("s1", "assistant", "hi")

    cleared = await mm.clear_session("s1")
    assert cleared is True

    ctx = await mm.get_context("s1")
    assert len(ctx) == 0


@pytest.mark.asyncio
async def test_clear_nonexistent_session(mm: MemoryManager) -> None:
    cleared = await mm.clear_session("nonexistent")
    assert cleared is False


@pytest.mark.asyncio
async def test_add_message_persists_metadata(mm: MemoryManager) -> None:
    await mm.add_message("s1", "user", "hello", {"mode": "test", "agent": "echo"})
    ctx = await mm.get_context("s1")
    assert ctx[0]["metadata"] == {"mode": "test", "agent": "echo"}


@pytest.mark.asyncio
async def test_max_history_trims_cache(mm: MemoryManager) -> None:
    """Verify that the cache does not grow beyond max_history."""
    assert mm.max_history == 10
    for i in range(15):
        await mm.add_message("s1", "user", f"msg-{i}")

    # Cache should be trimmed to 10.
    ctx = await mm.get_context("s1")
    assert len(ctx) == 10
    # Oldest should be msg-5 (first 5 trimmed).
    assert ctx[0]["content"] == "msg-5"


@pytest.mark.asyncio
async def test_initialize_is_idempotent(mm: MemoryManager) -> None:
    # Already initialized by fixture; calling again is safe.
    await mm.initialize(str(mm._db.database_path) if mm._db else "")
    assert mm.is_initialized


@pytest.mark.asyncio
async def test_close_is_idempotent(mm: MemoryManager) -> None:
    await mm.close()
    assert not mm.is_initialized
    await mm.close()  # safe


@pytest.mark.asyncio
async def test_raises_if_not_initialized() -> None:
    mm = MemoryManager()
    with pytest.raises(RuntimeError, match="not initialized"):
        await mm.add_message("s1", "user", "hello")