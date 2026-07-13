"""Unit tests for the AgentRouter with validation.

Sprint 1.5 - Phase 6.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.agent_router import AgentRouter, EchoAgent  # noqa: E402


def test_register_agent_rejects_invalid_protocol() -> None:
    router = AgentRouter()
    with pytest.raises(TypeError, match="Agent protocol"):
        router.register_agent("not an agent")  # type: ignore[arg-type]


def test_register_agent_rejects_object_without_handle() -> None:
    class NoHandle:
        name = "no_handle"

    router = AgentRouter()
    with pytest.raises(TypeError, match="Agent protocol"):
        router.register_agent(NoHandle())


def test_register_agent_accepts_sync_handle_protocol_check() -> None:
    """Note: runtime_checkable Protocol checks existence, not async-ness.
    A sync handle() passes isinstance but will fail at await time.
    This is by design — the Protocol is a minimal contract."""
    class SyncHandle:
        name = "sync"
        def handle(self, msg, ctx):
            return "sync"

    router = AgentRouter()
    # This actually passes because Protocol only checks the signature exists.
    # The error would happen at call time (await on non-coroutine).
    router.register_agent(SyncHandle())
    assert "sync" in router.agent_names


def test_register_agent_accepts_valid() -> None:
    class ValidAgent:
        name = "valid"
        async def handle(self, msg, ctx):
            return "ok"

    router = AgentRouter()
    router.register_agent(ValidAgent())
    assert "valid" in router.agent_names


def test_echo_agent_uses_context() -> None:
    """EchoAgent should include history count and mode in response."""
    agent = EchoAgent()

    import asyncio
    # No history
    result = asyncio.get_event_loop().run_until_complete(
        agent.handle("hi", {"session_id": "s1", "user_language": "es", "mode": "confirmation", "history": []})
    )
    assert "Hola, soy Liz" in result
    assert "intercambios" not in result

    # With history
    result = asyncio.get_event_loop().run_until_complete(
        agent.handle("hi", {"session_id": "s1", "user_language": "es", "mode": "automatic", "history": [{"role": "user", "content": "prev"}]})
    )
    assert "1 intercambios" in result
    assert "automatic" in result


def test_router_len() -> None:
    router = AgentRouter()
    assert len(router) == 1  # echo is default

    class A:
        name = "a"
        async def handle(self, m, c): return "a"

    router.register_agent(A())
    assert len(router) == 2