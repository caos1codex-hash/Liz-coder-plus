"""Unit tests for the unified execution pipeline.

Sprint 1.7 — Phase 7.
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

from src.event_bus import EventBus  # noqa: E402
from src.orchestrator import Orchestrator  # noqa: E402
from src.pipeline import (  # noqa: E402
    DEFAULT_STAGES,
    ExecutionPipeline,
    PipelineError,
    PipelineRequest,
    stage_validate_request,
)


# ----------------------------------------------------------------------
# Stage: validate_request
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage_validate_request_rejects_empty_message() -> None:
    orch = Orchestrator()
    req = PipelineRequest(message="", session_id="s1")
    with pytest.raises(PipelineError, match="non-empty"):
        await stage_validate_request(orch, req)


@pytest.mark.asyncio
async def test_stage_validate_request_rejects_empty_session() -> None:
    orch = Orchestrator()
    req = PipelineRequest(message="hola", session_id="")
    with pytest.raises(PipelineError, match="session_id"):
        await stage_validate_request(orch, req)


@pytest.mark.asyncio
async def test_stage_validate_request_defaults_unknown_mode() -> None:
    orch = Orchestrator()
    req = PipelineRequest(message="hola", session_id="s1", mode="weird")
    out = await stage_validate_request(orch, req)
    assert out.mode == "confirmation"


# ----------------------------------------------------------------------
# Pipeline: end-to-end success
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_runs_all_default_stages() -> None:
    bus = EventBus()
    orch = Orchestrator(event_bus=bus)

    events: list[tuple[str, Any]] = []
    for et in ("user.message.received", "agent.invoked", "agent.completed"):
        bus.subscribe(et, lambda p, _et=et: events.append((_et, p)))  # type: ignore[misc]

    result = await orch.handle_message("hola", "s1")

    assert result["type"] == "message"
    assert result["status"] == "completed"
    assert result["agent_name"] == "echo"
    assert "stage_timings" in result
    types = [e[0] for e in events]
    assert "user.message.received" in types
    assert "agent.invoked" in types
    assert "agent.completed" in types


@pytest.mark.asyncio
async def test_pipeline_records_stage_timings() -> None:
    orch = Orchestrator()
    result = await orch.handle_message("hola", "s1")
    timings = result["stage_timings"]
    assert "stage_validate_request" in timings
    assert "stage_execute_agent" in timings
    assert "stage_update_memory" in timings
    # All timings should be non-negative ints.
    for v in timings.values():
        assert isinstance(v, int)
        assert v >= 0


# ----------------------------------------------------------------------
# Pipeline: error paths
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_recovers_from_agent_error() -> None:
    from src.agent_router import AgentRouter

    class BoomAgent:
        name = "boom"
        async def handle(self, msg, ctx):
            raise ValueError("boom")

    router = AgentRouter(default_agent=BoomAgent())
    orch = Orchestrator(router=router)

    result = await orch.handle_message("hola", "s1")
    assert result["type"] == "error"
    assert result["status"] == "failed"
    assert result["stage"] == "stage_execute_agent"


@pytest.mark.asyncio
async def test_pipeline_timeout_produces_error_envelope() -> None:
    from src.agent_router import AgentRouter

    class SlowAgent:
        name = "slow"
        async def handle(self, msg, ctx):
            import asyncio
            await asyncio.sleep(10)

    router = AgentRouter(default_agent=SlowAgent())
    orch = Orchestrator(router=router, agent_timeout=0.05)

    result = await orch.handle_message("hola", "s1")
    assert result["type"] == "error"
    assert result["error"] == "agent_timeout"
    assert "demasiado" in result["content"]


@pytest.mark.asyncio
async def test_pipeline_invalid_input_returns_error() -> None:
    orch = Orchestrator()
    result = await orch.handle_message("", "s1")
    assert result["type"] == "error"
    assert result["stage"] == "stage_validate_request"


# ----------------------------------------------------------------------
# Pipeline: structure
# ----------------------------------------------------------------------


def test_default_stages_are_ordered() -> None:
    names = [getattr(s, "__name__", s.__class__.__name__) for s in DEFAULT_STAGES]
    # Validate first, update memory last.
    assert names[0] == "stage_validate_request"
    assert names[-1] == "stage_update_memory"
    # Planning happens before agent selection.
    assert names.index("stage_plan_execution") < names.index("stage_select_agent")


def test_pipeline_exposes_stages_list() -> None:
    orch = Orchestrator()
    pipeline = orch.pipeline
    stages = pipeline.stages
    assert len(stages) == len(DEFAULT_STAGES)
    # Ensure we get a copy, not the internal list.
    stages.append(lambda orch, req: req)  # type: ignore
    assert len(pipeline.stages) == len(DEFAULT_STAGES)


# ----------------------------------------------------------------------
# Pipeline: planner integration
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_invokes_planner_when_attached() -> None:
    from src.planner import Planner

    bus = EventBus()
    orch = Orchestrator(event_bus=bus)
    orch.attach_planner(Planner(event_bus=bus))

    plan_events: list[Any] = []
    bus.subscribe("planner.plan.created", lambda p: plan_events.append(p))

    await orch.handle_message("lee el archivo /etc/hosts", "s1")

    assert len(plan_events) == 1
    # File-operation plan has 3 subtasks.
    assert plan_events[0]["subtask_count"] == 3
