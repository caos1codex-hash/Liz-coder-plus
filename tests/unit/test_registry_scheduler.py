"""Tests para RegistryAwareScheduler (Sprint 2.2/2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent import (  # noqa: E402
    CoderAgent,
    PlannerAgent,
    ReviewerAgent,
    Step,
    Workflow,
)
from multiagent.base import BaseAgent  # noqa: E402
from multiagent.enums import Permission  # noqa: E402
from multiagent.registry.adapter import BackwardCompatibilityAdapter  # noqa: E402
from multiagent.registry.agent_registry import AgentRegistry  # noqa: E402
from multiagent.registry.capability import (  # noqa: E402
    CapabilityRegistry,
    register_standard_capabilities,
)
from multiagent.registry.resolver import CapabilityResolver  # noqa: E402
from multiagent.workflow.engine import WorkflowEngine  # noqa: E402
from multiagent.workflow.enums import WorkflowStatus  # noqa: E402
from multiagent.workflow.event_bus import EventBus  # noqa: E402
from multiagent.workflow.registry_scheduler import RegistryAwareScheduler  # noqa: E402


async def _setup():
    """Crea registry + resolver + adapter + scheduler con agentes estándar."""
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    bus = EventBus()
    registry = AgentRegistry(capability_registry=cap_reg, event_bus=bus)
    resolver = CapabilityResolver(registry=registry, event_bus=bus)
    adapter = BackwardCompatibilityAdapter(
        registry=registry, resolver=resolver,
    )
    scheduler = RegistryAwareScheduler(
        registry=registry, resolver=resolver, adapter=adapter, event_bus=bus,
    )
    return registry, resolver, adapter, scheduler, bus


# ------------------------------------------------------------------
# Schedule básico
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_schedule_empty_workflow() -> None:
    registry, _, _, scheduler, _ = await _setup()
    wf = Workflow(name="empty")
    decisions = await scheduler.schedule(wf, [])
    assert decisions == []


@pytest.mark.asyncio
async def test_scheduler_resolves_legacy_agent_name() -> None:
    registry, _, adapter, scheduler, _ = await _setup()
    coder = CoderAgent()
    await coder.start()
    await adapter.auto_register_legacy_agents([coder])

    wf = Workflow(name="test")
    s1 = Step(name="s1", agent="CoderAgent", action="refactor",
              input={"op": "refactor", "content": "x", "language": "python"})
    wf.add_step(s1)
    decisions = await scheduler.schedule(wf, [])
    assert len(decisions) == 1
    # Debe haber resuelto al coder (vía capability code.refactor).
    assert decisions[0].agent_name == "coder"


@pytest.mark.asyncio
async def test_scheduler_resolves_by_action_only() -> None:
    registry, _, adapter, scheduler, _ = await _setup()
    coder = CoderAgent()
    reviewer = ReviewerAgent()
    await coder.start()
    await reviewer.start()
    await adapter.auto_register_legacy_agents([coder, reviewer])

    wf = Workflow(name="test")
    # Sin agent explícito, con action="review".
    s1 = Step(name="s1", agent="", action="review",
              input={"content": "x", "path": "x.py"})
    wf.add_step(s1)
    decisions = await scheduler.schedule(wf, [])
    assert len(decisions) == 1
    assert decisions[0].agent_name == "reviewer"


@pytest.mark.asyncio
async def test_scheduler_respects_max_parallel_steps() -> None:
    from multiagent.workflow.scheduler import SchedulerConfig
    cap_reg = CapabilityRegistry()
    register_standard_capabilities(cap_reg)
    bus = EventBus()
    registry = AgentRegistry(capability_registry=cap_reg, event_bus=bus)
    resolver = CapabilityResolver(registry=registry, event_bus=bus)
    adapter = BackwardCompatibilityAdapter(registry=registry, resolver=resolver)
    scheduler = RegistryAwareScheduler(
        registry=registry, resolver=resolver, adapter=adapter, event_bus=bus,
        config=SchedulerConfig(max_parallel_steps=1),
    )
    # Dos agentes stub que proveen code.generate.
    from multiagent.base import BaseAgent
    from multiagent.enums import Permission

    class CodeStub(BaseAgent):
        default_permissions = frozenset({Permission.MEMORY_WRITE})
        default_objectives = ["code"]

        def __init__(self, name: str):
            super().__init__(name=name, description="code stub")

        async def _execute_impl(self, task: dict) -> dict:
            return {"handled": True}

    a1 = CodeStub("a1")
    a2 = CodeStub("a2")
    await a1.start()
    await a2.start()
    await registry.register(a1, provided_capabilities=["code.generate", "code.refactor"])
    await registry.register(a2, provided_capabilities=["code.generate", "code.refactor"])

    wf = Workflow(name="test")
    s1 = Step(name="s1", agent="", action="refactor", input={"content": "x"})
    s2 = Step(name="s2", agent="", action="refactor", input={"content": "y"})
    wf.add_step(s1)
    wf.add_step(s2)
    decisions = await scheduler.schedule(wf, [])
    assert len(decisions) == 1  # max_parallel_steps=1


@pytest.mark.asyncio
async def test_scheduler_excludes_failed_agents() -> None:
    registry, _, adapter, scheduler, _ = await _setup()
    coder = CoderAgent()
    await coder.start()
    await adapter.auto_register_legacy_agents([coder])

    wf = Workflow(name="test")
    s1 = Step(name="s1", agent="CoderAgent", action="refactor", input={})
    wf.add_step(s1)
    # Excluir al coder (simula que ya falló).
    decisions = await scheduler.schedule(wf, [], exclude={s1.id: {"coder"}})
    # Sin otros agentes, no hay decision.
    assert len(decisions) == 0


# ------------------------------------------------------------------
# Workflow completo con RegistryAwareScheduler
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_workflow_with_registry_scheduler() -> None:
    registry, _, adapter, scheduler, bus = await _setup()
    coder = CoderAgent()
    reviewer = ReviewerAgent()
    planner = PlannerAgent()
    await coder.start()
    await reviewer.start()
    await planner.start()
    await adapter.auto_register_legacy_agents([planner, coder, reviewer])

    engine = WorkflowEngine(
        agents=[planner, coder, reviewer],
        event_bus=bus,
        scheduler=scheduler,
    )

    wf = Workflow(name="demo")
    s1 = Step(name="refactor", agent="CoderAgent", action="refactor",
              input={"op": "refactor", "content": "def f():\r\n    return 1   \r\n", "language": "python"})
    wf.add_step(s1)
    s2 = Step(name="review", agent="ReviewerAgent", action="review",
              input={"content": "def f():\n    return 1\n", "path": "f.py"},
              depends_on=[s1.id])
    wf.add_step(s2)

    result = await engine.run(wf)
    assert result.status == WorkflowStatus.COMPLETED
    assert result.steps_completed == 2
    # El registry debe registrar 2 usos.
    assert registry.metrics()["total_usage"] == 2
    assert registry.metrics()["total_successes"] == 2


@pytest.mark.asyncio
async def test_registry_scheduler_records_usage_on_success() -> None:
    registry, _, adapter, scheduler, bus = await _setup()
    coder = CoderAgent()
    await coder.start()
    await adapter.auto_register_legacy_agents([coder])

    engine = WorkflowEngine(
        agents=[coder], event_bus=bus, scheduler=scheduler,
    )

    wf = Workflow(name="test")
    s1 = Step(name="s1", agent="CoderAgent", action="refactor",
              input={"op": "refactor", "content": "x", "language": "python"})
    wf.add_step(s1)
    await engine.run(wf)

    record = registry.get_by_name("coder")
    assert record.usage_count == 1
    assert record.success_count == 1
    assert record.failure_count == 0
    assert record.avg_latency_ms > 0


# ------------------------------------------------------------------
# Fallback del scheduler
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_fallback_when_resolver_misses() -> None:
    """Si el resolver no encuentra agente, el fallback usa LoadBalancer."""
    registry, _, adapter, scheduler, _ = await _setup()
    # Registrar un agente SIN capabilities mapeadas a su action.
    class CustomAgent(BaseAgent):
        default_permissions = frozenset({Permission.MEMORY_WRITE})
        default_objectives = ["custom"]
        default_tools = []

        def __init__(self):
            super().__init__(name="custom_agent", description="custom")

        async def _execute_impl(self, task: dict) -> dict:
            return {"handled": True}

    agent = CustomAgent()
    await agent.start()
    # Registrar SIN provided_capabilities (el resolver no lo encontrará).
    await registry.register(agent, provided_capabilities=[])

    wf = Workflow(name="test")
    s1 = Step(name="s1", agent="", action="unknown_action", input={})
    wf.add_step(s1)
    decisions = await scheduler.schedule(wf, [])
    # El fallback debe encontrar al agente custom_agent.
    assert len(decisions) == 1
    assert decisions[0].agent_name == "custom_agent"


# ------------------------------------------------------------------
# Metrics del registry tras workflow
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_metrics_after_workflow() -> None:
    registry, _, adapter, scheduler, bus = await _setup()
    coder = CoderAgent()
    reviewer = ReviewerAgent()
    await coder.start()
    await reviewer.start()
    await adapter.auto_register_legacy_agents([coder, reviewer])

    engine = WorkflowEngine(
        agents=[coder, reviewer], event_bus=bus, scheduler=scheduler,
    )

    wf = Workflow(name="test")
    s1 = Step(name="s1", agent="CoderAgent", action="refactor",
              input={"op": "refactor", "content": "x", "language": "python"})
    wf.add_step(s1)
    s2 = Step(name="s2", agent="ReviewerAgent", action="review",
              input={"content": "x", "path": "x.py"}, depends_on=[s1.id])
    wf.add_step(s2)
    await engine.run(wf)

    m = registry.metrics()
    assert m["registry_size"] == 2
    assert m["total_usage"] == 2
    assert m["total_successes"] == 2
    assert m["avg_success_rate"] == 1.0
