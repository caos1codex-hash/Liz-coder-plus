"""Integration tests for the memory system.

Sprint 1 - Prompt 3.

Tests the full flow: Orchestrator -> SessionManager -> MemoryManager -> SQLite.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
MEM_SRC = ROOT / "packages" / "memory"
SHARED_SRC = ROOT / "packages" / "shared" / "src"

# Build a synthetic 'src' namespace that covers both core and memory.
for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

sys.path.insert(0, str(CORE_SRC))
sys.path.insert(0, str(MEM_SRC))

# Create a synthetic src package that merges both src directories.
src_pkg = types.ModuleType("src")
src_pkg.__path__ = [str(CORE_SRC / "src"), str(MEM_SRC / "src"), str(SHARED_SRC)]
src_pkg.__package__ = "src"
sys.modules["src"] = src_pkg

from src.memory.manager import MemoryManager  # noqa: E402
from src.orchestrator import Orchestrator  # noqa: E402
from src.session_manager import SessionManager  # noqa: E402


@pytest.fixture
async def memory(tmp_path: Path):
    """Create an initialized MemoryManager."""
    mm = MemoryManager(max_history=100)
    await mm.initialize(str(tmp_path / "test.db"))
    yield mm
    await mm.close()


@pytest.mark.asyncio
async def test_full_conversation_flow(memory: MemoryManager) -> None:
    """Test: user message -> memory -> agent -> response -> memory."""
    sessions = SessionManager()
    sessions.attach_memory(memory)
    orch = Orchestrator(session_manager=sessions)

    result = await orch.handle_message("Hola Liz", "s1", mode="confirmation")

    assert result["type"] == "message"
    assert result["status"] == "completed"

    # Verify both turns are in RAM.
    history = sessions.history("s1")
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[1].role == "assistant"

    # Verify both turns are in SQLite.
    ctx = await memory.get_context("s1")
    assert len(ctx) == 2


@pytest.mark.asyncio
async def test_persistence_across_restarts(tmp_path: Path) -> None:
    """Test: conversation survives a process restart (new SessionManager + MemoryManager)."""
    db_path = str(tmp_path / "persist.db")

    # First "process": create messages.
    mm1 = MemoryManager(max_history=100)
    await mm1.initialize(db_path)
    sessions1 = SessionManager()
    sessions1.attach_memory(mm1)
    orch1 = Orchestrator(session_manager=sessions1)

    await orch1.handle_message("Hola Liz", "s1")
    await orch1.handle_message("Como estas?", "s1")

    # Simulate shutdown.
    await mm1.close()

    # Second "process": restore from same database.
    mm2 = MemoryManager(max_history=100)
    await mm2.initialize(db_path)
    sessions2 = SessionManager()
    sessions2.attach_memory(mm2)

    # Restore session from persistent memory.
    state = await sessions2.restore_session("s1")
    assert len(state.history) == 4  # 2 user + 2 assistant turns

    # Verify from SQLite directly.
    ctx = await mm2.get_context("s1")
    assert len(ctx) == 4

    await mm2.close()


@pytest.mark.asyncio
async def test_orchestrator_attach_memory(memory: MemoryManager) -> None:
    """Test: Orchestrator.attach_memory wires everything together."""
    orch = Orchestrator()
    orch.attach_memory(memory)

    # SessionManager should have memory attached.
    assert orch.session_manager.has_memory

    result = await orch.handle_message("Hola", "s1")
    assert result["status"] == "completed"

    # Verify persistence.
    ctx = await memory.get_context("s1")
    assert len(ctx) == 2


@pytest.mark.asyncio
async def test_error_turn_persisted(memory: MemoryManager) -> None:
    """Test: even error turns are persisted to memory."""
    from src.agent_router import AgentRouter

    class BoomAgent:
        name = "boom"

        async def handle(self, message: str, context: dict) -> str:
            raise RuntimeError("kaboom")

    sessions = SessionManager()
    sessions.attach_memory(memory)
    router = AgentRouter(default_agent=BoomAgent())
    orch = Orchestrator(session_manager=sessions, router=router)

    result = await orch.handle_message("hola", "s1")
    assert result["type"] == "error"

    # Both user and error system turns should be persisted.
    ctx = await memory.get_context("s1")
    assert len(ctx) == 2
    assert ctx[0]["role"] == "user"
    assert ctx[1]["role"] == "system"