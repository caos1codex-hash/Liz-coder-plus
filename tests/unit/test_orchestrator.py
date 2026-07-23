"""Unit tests for the Orchestrator.

Sprint 1 - Prompt 2.
"""

from __future__ import annotations

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

from src.orchestrator import Orchestrator  # noqa: E402


@pytest.mark.asyncio
async def test_handle_message_returns_completed_envelope() -> None:
    orch = Orchestrator()
    result = await orch.handle_message("Hola Liz", "s1", mode="confirmation")
    assert result["type"] == "message"
    assert result["status"] == "completed"
    assert result["session_id"] == "s1"
    assert "Hola, soy Liz" in result["content"]


@pytest.mark.asyncio
async def test_handle_message_records_history() -> None:
    orch = Orchestrator()
    await orch.handle_message("Hola", "s1")
    snapshot = orch.session_manager.snapshot("s1")
    assert snapshot is not None
    roles = [t["role"] for t in snapshot["history"]]
    assert roles == ["user", "assistant"]


@pytest.mark.asyncio
async def test_handle_message_persists_mode() -> None:
    orch = Orchestrator()
    await orch.handle_message("Hola", "s1", mode="automatic")
    snapshot = orch.session_manager.snapshot("s1")
    assert snapshot["last_mode"] == "automatic"


@pytest.mark.asyncio
async def test_stream_message_yields_chunks_then_final() -> None:
    orch = Orchestrator()
    envelopes = []
    async for env in orch.stream_message("Hola Liz", "s1", chunk_size=2):
        envelopes.append(env)

    # Last envelope must be the final message.
    assert envelopes[-1]["type"] == "message"
    assert envelopes[-1]["status"] == "completed"

    # There must be at least one chunk before the final.
    types = [e["type"] for e in envelopes]
    assert "chunk" in types


@pytest.mark.asyncio
async def test_stream_message_content_matches_handle() -> None:
    orch = Orchestrator()
    direct = await orch.handle_message("Hola Liz", "sA")
    streamed = []
    async for env in orch.stream_message("Hola Liz", "sB", chunk_size=5):
        streamed.append(env)
    final = streamed[-1]
    assert final["content"] == direct["content"]


@pytest.mark.asyncio
async def test_handle_message_with_failing_agent_returns_error_envelope() -> None:
    from src.agent_router import AgentRouter

    class BoomAgent:
        name = "boom"

        async def handle(self, message: str, context: dict) -> str:
            raise RuntimeError("kaboom")

    router = AgentRouter(default_agent=BoomAgent())
    orch = Orchestrator(router=router)

    result = await orch.handle_message("hola", "s1")
    assert result["type"] == "error"
    assert result["status"] == "failed"
    assert "kaboom" in result["error"]
