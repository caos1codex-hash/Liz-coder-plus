"""Unit tests for the AgentRouter.

Sprint 1 - Prompt 2.
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

from src.agent_router import AgentRouter, EchoAgent  # noqa: E402


class StubAgent:
    """Minimal agent for testing."""

    def __init__(self, name: str, reply: str = "ok") -> None:
        self.name = name
        self._reply = reply

    async def handle(self, message: str, context: dict) -> str:
        return self._reply


@pytest.mark.asyncio
async def test_default_agent_is_echo_when_none_provided() -> None:
    router = AgentRouter()
    assert isinstance(router.default_agent, EchoAgent)
    assert "echo" in router.agent_names


@pytest.mark.asyncio
async def test_route_falls_back_to_default_when_no_rule_matches() -> None:
    router = AgentRouter()
    agent = await router.route("hola", {"session_id": "s1"})
    assert isinstance(agent, EchoAgent)


@pytest.mark.asyncio
async def test_route_uses_first_matching_rule() -> None:
    router = AgentRouter()
    code_agent = StubAgent("code", "code reply")
    router.register_agent(code_agent)

    async def is_code(_msg: str, _ctx: dict) -> bool:
        return _msg.lower().startswith("code:")

    router.add_rule("code", is_code)

    selected = await router.route("code: print hello", {"session_id": "s1"})
    assert selected is code_agent

    fallback = await router.route("hola", {"session_id": "s1"})
    assert isinstance(fallback, EchoAgent)


@pytest.mark.asyncio
async def test_add_rule_rejects_unknown_agent() -> None:
    router = AgentRouter()
    with pytest.raises(KeyError):

        async def _pred(_m: str, _c: dict) -> bool:
            return True

        router.add_rule("does-not-exist", _pred)


@pytest.mark.asyncio
async def test_predicates_evaluated_in_order() -> None:
    router = AgentRouter()
    a = StubAgent("a", "a")
    b = StubAgent("b", "b")
    router.register_agent(a)
    router.register_agent(b)

    calls: list[str] = []

    async def pred_a(_m: str, _c: dict) -> bool:
        calls.append("a")
        return True

    async def pred_b(_m: str, _c: dict) -> bool:
        calls.append("b")
        return True

    router.add_rule("a", pred_a)
    router.add_rule("b", pred_b)

    selected = await router.route("hello", {})
    assert selected is a
    assert calls == ["a"]  # b never invoked


@pytest.mark.asyncio
async def test_predicate_exception_skips_rule() -> None:
    router = AgentRouter()
    bad = StubAgent("bad", "bad")
    good = StubAgent("good", "good")
    router.register_agent(bad)
    router.register_agent(good)

    async def pred_bad(_m: str, _c: dict) -> bool:
        raise RuntimeError("boom")

    async def pred_good(_m: str, _c: dict) -> bool:
        return True

    router.add_rule("bad", pred_bad)
    router.add_rule("good", pred_good)

    selected = await router.route("hello", {})
    assert selected is good


@pytest.mark.asyncio
async def test_echo_agent_reply_is_friendly_spanish() -> None:
    agent = EchoAgent()
    reply = await agent.handle("Hola Liz", {"session_id": "s1"})
    assert "Hola, soy Liz" in reply
    assert "Hola Liz" in reply
