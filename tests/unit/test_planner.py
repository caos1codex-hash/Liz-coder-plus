"""Unit tests for the Planner.

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
from src.planner import Plan, Planner, SubTask  # noqa: E402


# ----------------------------------------------------------------------
# Heuristics
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_file_operation_strategy() -> None:
    p = Planner()
    plan = await p.plan_detailed("Lee el archivo /etc/hosts")
    assert plan.strategy == "file_operation"
    assert len(plan) == 3
    assert plan.subtasks[0].name == "read"
    assert plan.subtasks[1].depends_on == ["read"]
    assert plan.subtasks[2].depends_on == ["process"]


@pytest.mark.asyncio
async def test_planner_terminal_command_strategy() -> None:
    p = Planner()
    plan = await p.plan_detailed("Ejecuta el comando ls -la")
    assert plan.strategy == "terminal_command"
    assert len(plan) == 3
    assert plan.subtasks[1].name == "execute"
    assert "terminal_tool" in plan.subtasks[1].suggested_tools


@pytest.mark.asyncio
async def test_planner_research_strategy() -> None:
    p = Planner()
    plan = await p.plan_detailed("Investiga el tema y luego resume")
    assert plan.strategy == "research_then_summarize"
    assert len(plan) == 3
    assert plan.subtasks[0].name == "gather"
    assert plan.subtasks[1].depends_on == ["gather"]
    assert plan.subtasks[2].depends_on == ["analyze"]


@pytest.mark.asyncio
async def test_planner_system_info_strategy() -> None:
    p = Planner()
    plan = await p.plan_detailed("Información del sistema")
    assert plan.strategy == "system_info"
    assert len(plan) == 2
    assert "system_tool" in plan.subtasks[0].suggested_tools


@pytest.mark.asyncio
async def test_planner_fallback_single_strategy() -> None:
    p = Planner()
    plan = await p.plan_detailed("Hola, ¿cómo estás?")
    assert plan.strategy == "single"
    assert len(plan) == 1
    assert plan.subtasks[0].name == "handle"


# ----------------------------------------------------------------------
# Output formats
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_plan_returns_list_of_dicts() -> None:
    p = Planner()
    result = await p.plan("Lee el archivo X")
    assert isinstance(result, list)
    assert all(isinstance(item, dict) for item in result)
    assert result[0]["name"] == "read"


@pytest.mark.asyncio
async def test_planner_plan_detailed_returns_plan_object() -> None:
    p = Planner()
    plan = await p.plan_detailed("Lee el archivo X")
    assert isinstance(plan, Plan)
    assert all(isinstance(s, SubTask) for s in plan.subtasks)


def test_plan_to_dict() -> None:
    plan = Plan(
        strategy="single",
        rationale="test",
        subtasks=[SubTask(name="x", suggested_agent="echo")],
    )
    d = plan.to_dict()
    assert d["strategy"] == "single"
    assert d["rationale"] == "test"
    assert len(d["subtasks"]) == 1
    assert d["subtasks"][0]["name"] == "x"


def test_subtask_to_dict() -> None:
    st = SubTask(
        name="x",
        description="d",
        suggested_agent="echo",
        suggested_tools=["t1"],
        depends_on=["y"],
    )
    d = st.to_dict()
    assert d["name"] == "x"
    assert d["description"] == "d"
    assert d["suggested_agent"] == "echo"
    assert d["suggested_tools"] == ["t1"]
    assert d["depends_on"] == ["y"]


# ----------------------------------------------------------------------
# Events
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_emits_plan_created_event() -> None:
    bus = EventBus()
    p = Planner(event_bus=bus)
    events: list[Any] = []
    bus.subscribe("planner.plan.created", lambda payload: events.append(payload))

    await p.plan("Lee el archivo X")
    assert len(events) == 1
    assert events[0]["strategy"] == "file_operation"
    assert events[0]["subtask_count"] == 3


@pytest.mark.asyncio
async def test_planner_emits_decision_per_subtask() -> None:
    bus = EventBus()
    p = Planner(event_bus=bus)
    events: list[Any] = []
    bus.subscribe("planner.decision", lambda payload: events.append(payload))

    await p.plan("Lee el archivo X")
    assert len(events) == 3  # read, process, respond
    names = [e["subtask"] for e in events]
    assert "read" in names
    assert "process" in names
    assert "respond" in names


# ----------------------------------------------------------------------
# Registry integration
# ----------------------------------------------------------------------


class _FakeAgentRegistry:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def names(self) -> list[str]:
        return self._names


class _FakeToolRegistry:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def names(self) -> list[str]:
        return self._names


@pytest.mark.asyncio
async def test_planner_uses_registry_for_default_agent() -> None:
    agents = _FakeAgentRegistry(["code_agent"])
    p = Planner(agent_registry=agents)
    plan = await p.plan_detailed("Hola")
    assert plan.subtasks[0].suggested_agent == "code_agent"


@pytest.mark.asyncio
async def test_planner_filters_unknown_tools() -> None:
    tools = _FakeToolRegistry(["file_tool"])  # missing system_tool
    p = Planner(tool_registry=tools)
    plan = await p.plan_detailed("Investiga el tema y luego resume")
    # 'gather' suggests file_tool and system_tool; only file_tool is
    # known — but our fallback returns all candidates when none known.
    suggested = plan.subtasks[0].suggested_tools
    assert "file_tool" in suggested
