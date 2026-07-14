"""Tests para CapabilityResolver y BackwardCompatibilityAdapter (Sprint 2.2/2)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.base import BaseAgent  # noqa: E402
from multiagent.enums import Permission  # noqa: E402
from multiagent.registry.adapter import (  # noqa: E402
    ACTION_TO_CAPABILITY,
    AGENT_NAME_TO_CAPABILITIES,
    BackwardCompatibilityAdapter,
)
from multiagent.registry.agent_registry import AgentRegistry  # noqa: E402
from multiagent.registry.capability import (  # noqa: E402
    Capability,
    CapabilityRegistry,
    register_standard_capabilities,
)
from multiagent.registry.resolver import (  # noqa: E402
    CapabilityResolver,
    ResolverWeights,
)


# ------------------------------------------------------------------
# Stub agents
# ------------------------------------------------------------------


class StubAgent(BaseAgent):
    default_permissions = frozenset({Permission.MEMORY_WRITE})
    default_objectives = ["stub", "test"]

    def __init__(self, name: str = "stub", **kwargs):
        super().__init__(name=name, description="stub agent", **kwargs)

    async def _execute_impl(self, task: dict) -> dict:
        return {"handled": True}


async def _started(agent: BaseAgent) -> BaseAgent:
    await agent.start()
    return agent


async def _setup_registry():
    """Helper: crea registry + resolver + adapter con agentes estándar."""
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    registry = AgentRegistry(capability_registry=cap_reg)
    resolver = CapabilityResolver(registry=registry)
    adapter = BackwardCompatibilityAdapter(
        registry=registry, resolver=resolver,
    )
    return registry, resolver, adapter


# ------------------------------------------------------------------
# CapabilityResolver
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_resolves_capability() -> None:
    registry, resolver, _ = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    await registry.register(a1, provided_capabilities=["code.generate"])
    result = await resolver.resolve("code.generate")
    assert result.resolved is True
    assert result.agent_record is not None
    assert result.agent_record.name == "a1"
    assert result.score > 0.0


@pytest.mark.asyncio
async def test_resolver_miss_when_no_agent() -> None:
    registry, resolver, _ = await _setup_registry()
    result = await resolver.resolve("nonexistent.capability")
    assert result.resolved is False
    assert result.agent_record is None
    assert "no available agent" in result.reason


@pytest.mark.asyncio
async def test_resolver_excludes_agents() -> None:
    registry, resolver, _ = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    await registry.register(a1, provided_capabilities=["code.generate"])
    await registry.register(a2, provided_capabilities=["code.generate"])
    result = await resolver.resolve("code.generate", exclude={"a1"})
    assert result.resolved is True
    assert result.agent_record.name == "a2"


@pytest.mark.asyncio
async def test_resolver_prefers_higher_priority() -> None:
    registry, resolver, _ = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    await registry.register(a1, provided_capabilities=["code.generate"], priority=1)
    await registry.register(a2, provided_capabilities=["code.generate"], priority=10)
    result = await resolver.resolve("code.generate")
    # a2 tiene mayor prioridad → debe ganar (aunque usage sea igual).
    assert result.agent_record.name == "a2"


@pytest.mark.asyncio
async def test_resolver_balances_by_load() -> None:
    """Con igual prioridad, el agente con menos usage gana."""
    registry, resolver, _ = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    r1 = await registry.register(a1, provided_capabilities=["code.generate"])
    await registry.register(a2, provided_capabilities=["code.generate"])
    # Simular que a1 ya tuvo 5 usos.
    for _ in range(5):
        r1.record_usage(success=True, duration_ms=10.0)
    result = await resolver.resolve("code.generate")
    # a2 tiene menos usage → debe ganar.
    assert result.agent_record.name == "a2"


@pytest.mark.asyncio
async def test_resolver_considers_success_rate() -> None:
    """Con igual prioridad y load, mayor success_rate gana."""
    registry, resolver, _ = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    r1 = await registry.register(a1, provided_capabilities=["code.generate"])
    await registry.register(a2, provided_capabilities=["code.generate"])
    # a1 tiene fallos; a2 no.
    r1.record_usage(success=False, duration_ms=10.0)
    r1.record_usage(success=True, duration_ms=10.0)
    result = await resolver.resolve("code.generate")
    assert result.agent_record.name == "a2"


@pytest.mark.asyncio
async def test_resolver_with_capability_object() -> None:
    registry, resolver, _ = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    await registry.register(a1, provided_capabilities=["code.generate"])
    cap = Capability(name="code.generate")
    result = await resolver.resolve(cap)
    assert result.resolved is True
    assert result.capability == "code.generate"


@pytest.mark.asyncio
async def test_resolver_returns_candidates() -> None:
    registry, resolver, _ = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    a3 = await _started(StubAgent("a3"))
    await registry.register(a1, provided_capabilities=["code.generate"])
    await registry.register(a2, provided_capabilities=["code.generate"])
    await registry.register(a3, provided_capabilities=["code.review"])
    result = await resolver.resolve("code.generate")
    # Debe haber 2 candidatos (a1 y a2, no a3).
    assert len(result.candidates) == 2


@pytest.mark.asyncio
async def test_resolver_metrics() -> None:
    registry, resolver, _ = await _setup_registry()
    a1 = await _started(StubAgent("a1"))
    await registry.register(a1, provided_capabilities=["code.generate"])
    await resolver.resolve("code.generate")  # hit
    await resolver.resolve("nonexistent")    # miss
    m = resolver.metrics()
    assert m["resolutions_total"] == 2
    assert m["resolved_count"] == 1
    assert m["miss_count"] == 1
    assert m["success_rate"] == 0.5


def test_resolver_weights_normalized() -> None:
    w = ResolverWeights(w_specialty=2.0, w_history=2.0)
    n = w.normalized()
    total = n.w_specialty + n.w_priority + n.w_cost + n.w_history + n.w_load + n.w_latency
    assert abs(total - 1.0) < 0.001


@pytest.mark.asyncio
async def test_resolver_emits_events() -> None:
    from multiagent.workflow.event_bus import EventBus

    bus = EventBus()
    received: list[str] = []

    async def handler(e):
        received.append(e.type_str)

    await bus.subscribe(handler)
    registry = AgentRegistry(event_bus=bus)
    resolver = CapabilityResolver(registry=registry, event_bus=bus)
    a1 = await _started(StubAgent("a1"))
    await registry.register(a1, provided_capabilities=["code.generate"])
    await resolver.resolve("code.generate")
    await asyncio.sleep(0.05)
    assert "capability.resolved" in received
    assert "scheduler.selected_agent" in received


# ------------------------------------------------------------------
# BackwardCompatibilityAdapter
# ------------------------------------------------------------------


def test_agent_name_to_capabilities_planner() -> None:
    caps = BackwardCompatibilityAdapter.agent_name_to_capabilities("PlannerAgent")
    assert "planning" in caps
    assert "workflow.create" in caps


def test_agent_name_to_capabilities_coder() -> None:
    caps = BackwardCompatibilityAdapter.agent_name_to_capabilities("CoderAgent")
    assert "code.generate" in caps
    assert "code.refactor" in caps
    assert "filesystem.read" in caps


def test_agent_name_to_capabilities_case_insensitive() -> None:
    caps = BackwardCompatibilityAdapter.agent_name_to_capabilities("planner")
    assert "planning" in caps


def test_agent_name_to_capabilities_unknown() -> None:
    caps = BackwardCompatibilityAdapter.agent_name_to_capabilities("UnknownAgent")
    # Devuelve el nombre normalizado como fallback.
    assert len(caps) >= 1


def test_action_to_capability_refactor() -> None:
    assert BackwardCompatibilityAdapter.action_to_capability("refactor") == "code.refactor"


def test_action_to_capability_review() -> None:
    assert BackwardCompatibilityAdapter.action_to_capability("review") == "code.review"


def test_action_to_capability_unknown() -> None:
    assert BackwardCompatibilityAdapter.action_to_capability("unknown_action") == "unknown_action"


def test_action_to_capability_empty() -> None:
    assert BackwardCompatibilityAdapter.action_to_capability("") == "planning"


@pytest.mark.asyncio
async def test_adapter_resolve_step_legacy_agent() -> None:
    registry, resolver, adapter = await _setup_registry()
    coder = StubAgent("CoderAgent")
    await coder.start()
    await adapter.auto_register_legacy_agents([coder])

    class FakeStep:
        agent = "CoderAgent"
        action = "refactor"

    result = await adapter.resolve_step_agent(FakeStep())
    assert result.resolved is True
    assert result.agent_record.name == "CoderAgent"


@pytest.mark.asyncio
async def test_adapter_resolve_step_by_action_only() -> None:
    registry, resolver, adapter = await _setup_registry()
    coder = await _started(StubAgent("coder"))
    await registry.register(coder, provided_capabilities=["code.review"])

    class FakeStep:
        agent = ""  # sin agente explícito
        action = "review"

    result = await adapter.resolve_step_agent(FakeStep())
    assert result.resolved is True
    assert result.agent_record.name == "coder"


@pytest.mark.asyncio
async def test_adapter_auto_register_legacy_agents() -> None:
    registry, resolver, adapter = await _setup_registry()
    from multiagent import CoderAgent, ReviewerAgent, PlannerAgent

    coder = CoderAgent()
    reviewer = ReviewerAgent()
    planner = PlannerAgent()
    await coder.start()
    await reviewer.start()
    await planner.start()

    records = await adapter.auto_register_legacy_agents([planner, coder, reviewer])
    assert len(records) == 3
    assert registry.exists("planner")
    assert registry.exists("coder")
    assert registry.exists("reviewer")

    # Verificar capabilities mapeadas.
    coder_record = registry.get_by_name("coder")
    assert "code.generate" in coder_record.provided_capabilities
    reviewer_record = registry.get_by_name("reviewer")
    assert "code.review" in reviewer_record.provided_capabilities
    planner_record = registry.get_by_name("planner")
    assert "planning" in planner_record.provided_capabilities


@pytest.mark.asyncio
async def test_adapter_full_workflow_with_legacy_names() -> None:
    """Workflow con step.agent='CoderAgent' y step.action='refactor'."""
    registry, resolver, adapter = await _setup_registry()
    from multiagent import CoderAgent

    coder = CoderAgent()
    await coder.start()
    await adapter.auto_register_legacy_agents([coder])

    class FakeStep:
        agent = "CoderAgent"
        action = "refactor"

    result = await adapter.resolve_step_agent(FakeStep())
    assert result.resolved is True
    # El adapter debe haber traducido a code.refactor y encontrado al coder.
    assert result.agent_record.name == "coder"


@pytest.mark.asyncio
async def test_adapter_fallback_to_direct_lookup() -> None:
    """Si agent está registrado por nombre custom, usarlo directamente."""
    registry, resolver, adapter = await _setup_registry()
    custom = await _started(StubAgent("my_custom_agent"))
    await registry.register(custom, provided_capabilities=["custom.cap"])

    class FakeStep:
        agent = "my_custom_agent"
        action = ""

    result = await adapter.resolve_step_agent(FakeStep())
    assert result.resolved is True
    assert result.agent_record.name == "my_custom_agent"


# ------------------------------------------------------------------
# Mapeos estáticos
# ------------------------------------------------------------------


def test_agent_name_to_capabilities_completeness() -> None:
    """Verifica que todos los agentes legacy conocidos tienen mapeo."""
    expected = [
        "PlannerAgent", "CoderAgent", "ReviewerAgent",
        "ResearchAgent", "TerminalAgent", "GitAgent", "MemoryAgent",
    ]
    for name in expected:
        caps = AGENT_NAME_TO_CAPABILITIES.get(name, [])
        assert len(caps) > 0, f"{name} has no capability mapping"


def test_action_to_capability_completeness() -> None:
    """Verifica que actions clave tienen mapeo."""
    expected_actions = [
        "refactor", "review", "test", "fix", "write",
        "plan", "commit", "branch", "merge", "push", "pull",
        "store", "retrieve", "search", "execute",
    ]
    for action in expected_actions:
        assert action in ACTION_TO_CAPABILITY, f"{action} has no capability mapping"
