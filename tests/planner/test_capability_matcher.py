"""Unit tests for planner.capability_matcher (Sprint 2.8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLANNER_SRC = ROOT / "packages" / "planner" / "src"
sys.path.insert(0, str(PLANNER_SRC))

for _k in [k for k in list(sys.modules) if k == "planner" or k.startswith("planner.")]:
    sys.modules.pop(_k, None)

from planner.capability_matcher import (  # noqa: E402
    AgentInfo,
    AgentRegistryAdapter,
    CapabilityMatcher,
    MatcherWeights,
)
from planner.exceptions import AgentAssignmentError  # noqa: E402
from planner.task import Task  # noqa: E402

# ----------------------------------------------------------------------
# AgentInfo
# ----------------------------------------------------------------------


def test_agent_info_from_dict():
    info = AgentInfo.from_dict(
        {
            "id": "a1",
            "name": "Agent One",
            "capabilities": ["code.generate"],
            "priority": 5,
            "healthy": True,
            "available": True,
            "usage_count": 3,
            "success_rate": 0.9,
            "avg_latency_ms": 100.0,
            "metadata": {"cost": 0.5},
        }
    )
    assert info.id == "a1"
    assert info.name == "Agent One"
    assert info.capabilities == ["code.generate"]
    assert info.priority == 5
    assert info.healthy is True


def test_agent_info_to_dict():
    info = AgentInfo(id="a1", name="A", capabilities=["x"])
    d = info.to_dict()
    assert d["id"] == "a1"
    assert d["capabilities"] == ["x"]


def test_agent_info_defaults():
    info = AgentInfo(id="a1")
    assert info.name == "a1"
    assert info.capabilities == []
    assert info.priority == 0
    assert info.healthy is True
    assert info.success_rate == 1.0


# ----------------------------------------------------------------------
# AgentRegistryAdapter
# ----------------------------------------------------------------------


def test_adapter_register_list():
    adapter = AgentRegistryAdapter()
    adapter.register(AgentInfo(id="a1", capabilities=["x"]))
    adapter.register(AgentInfo(id="a2", capabilities=["y"]))
    assert len(adapter.list_agents()) == 2


def test_adapter_unregister():
    adapter = AgentRegistryAdapter()
    adapter.register(AgentInfo(id="a1"))
    assert adapter.unregister("a1") is True
    assert adapter.unregister("a1") is False


def test_adapter_get_agent():
    adapter = AgentRegistryAdapter()
    a = AgentInfo(id="a1", name="Agent1")
    adapter.register(a)
    assert adapter.get_agent("a1") is a
    assert adapter.get_agent("missing") is None


def test_adapter_find_by_capability_exact():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="a1", capabilities=["code.generate"]),
            AgentInfo(id="a2", capabilities=["code.review"]),
        ]
    )
    matches = adapter.find_by_capability("code.generate")
    assert len(matches) == 1
    assert matches[0].id == "a1"


def test_adapter_find_by_capability_wildcard():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="a1", capabilities=["code.*"]),
        ]
    )
    matches = adapter.find_by_capability("code.generate")
    assert len(matches) == 1


def test_adapter_find_by_capability_parent():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="a1", capabilities=["code"]),
        ]
    )
    matches = adapter.find_by_capability("code.generate")
    assert len(matches) == 1


def test_adapter_update_workload():
    adapter = AgentRegistryAdapter()
    a = AgentInfo(id="a1", usage_count=5)
    adapter.register(a)
    adapter.update_workload("a1", delta=+1)
    assert a.usage_count == 6
    adapter.update_workload("a1", delta=-3)
    assert a.usage_count == 3


def test_adapter_update_workload_clamps_to_zero():
    adapter = AgentRegistryAdapter()
    a = AgentInfo(id="a1", usage_count=2)
    adapter.register(a)
    adapter.update_workload("a1", delta=-10)
    assert a.usage_count == 0


# ----------------------------------------------------------------------
# MatcherWeights
# ----------------------------------------------------------------------


def test_weights_normalized():
    w = MatcherWeights(
        w_specialty=10.0,
        w_availability=10.0,
        w_workload=0.0,
        w_priority=0.0,
        w_affinity=0.0,
    )
    n = w.normalized()
    assert n.w_specialty == pytest.approx(0.5)
    assert n.w_availability == pytest.approx(0.5)


def test_weights_zero_total_returns_defaults():
    w = MatcherWeights(w_specialty=0, w_availability=0, w_workload=0, w_priority=0, w_affinity=0)
    n = w.normalized()
    # Defaults are returned (non-zero).
    assert n.w_specialty > 0


# ----------------------------------------------------------------------
# CapabilityMatcher — basic matching
# ----------------------------------------------------------------------


def test_match_no_candidates():
    matcher = CapabilityMatcher()
    t = Task(title="t", required_capabilities=["code.generate"])
    a = matcher.match(t)
    assert not a.assigned
    assert a.agent_id is None


def test_match_with_fallback():
    matcher = CapabilityMatcher(fallback_agent_id="fallback")
    t = Task(title="t", required_capabilities=["unknown.cap"])
    a = matcher.match(t)
    assert a.assigned
    assert a.agent_id == "fallback"


def test_match_single_candidate():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="coder", capabilities=["code.generate"], priority=5),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    t = Task(title="t", required_capabilities=["code.generate"])
    a = matcher.match(t)
    assert a.assigned
    assert a.agent_id == "coder"


def test_match_picks_best_specialty():
    adapter = AgentRegistryAdapter(
        agents=[
            # Equal priority so specialty alone decides.
            AgentInfo(id="exact", capabilities=["code.generate"], priority=5),
            AgentInfo(id="wildcard", capabilities=["code.*"], priority=5),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    t = Task(title="t", required_capabilities=["code.generate"])
    a = matcher.match(t)
    # exact match has higher specialty score.
    assert a.agent_id == "exact"


def test_match_picks_higher_priority_when_specialty_equal():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="low", capabilities=["code.generate"], priority=1),
            AgentInfo(id="high", capabilities=["code.generate"], priority=10),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    t = Task(title="t", required_capabilities=["code.generate"])
    a = matcher.match(t)
    assert a.agent_id == "high"


def test_match_picks_less_loaded():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="busy", capabilities=["code.generate"], priority=5, usage_count=10),
            AgentInfo(id="idle", capabilities=["code.generate"], priority=5, usage_count=0),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    t = Task(title="t", required_capabilities=["code.generate"])
    a = matcher.match(t)
    assert a.agent_id == "idle"


def test_match_skips_unhealthy():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="sick", capabilities=["code.generate"], healthy=False),
            AgentInfo(id="ok", capabilities=["code.generate"], healthy=True),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    t = Task(title="t", required_capabilities=["code.generate"])
    a = matcher.match(t)
    assert a.agent_id == "ok"


def test_match_skips_unavailable():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="busy", capabilities=["code.generate"], available=False),
            AgentInfo(id="free", capabilities=["code.generate"], available=True),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    t = Task(title="t", required_capabilities=["code.generate"])
    a = matcher.match(t)
    assert a.agent_id == "free"


def test_match_affinity():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="a1", capabilities=["code.generate"], priority=1),
            AgentInfo(id="preferred", capabilities=["code.generate"], priority=1),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    t = Task(
        title="t",
        required_capabilities=["code.generate"],
        metadata={"agent_affinity": ["preferred"]},
    )
    a = matcher.match(t)
    assert a.agent_id == "preferred"


def test_match_no_required_caps_returns_any_available():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="a1", capabilities=["x"], priority=5),
            AgentInfo(id="a2", capabilities=["y"], priority=10),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    t = Task(title="t")  # no required_capabilities
    a = matcher.match(t)
    assert a.assigned
    # Higher priority wins.
    assert a.agent_id == "a2"


# ----------------------------------------------------------------------
# CapabilityMatcher — assign
# ----------------------------------------------------------------------


def test_assign_writes_agent_to_task():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="coder", capabilities=["code.generate"]),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    t = Task(title="t", required_capabilities=["code.generate"])
    agent_id = matcher.assign(t)
    assert agent_id == "coder"
    assert t.assigned_agent == "coder"


def test_assign_increments_workload():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="coder", capabilities=["code.generate"], usage_count=0),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    t = Task(title="t", required_capabilities=["code.generate"])
    matcher.assign(t)
    assert adapter.get_agent("coder").usage_count == 1


def test_assign_raises_when_no_candidate():
    matcher = CapabilityMatcher()
    t = Task(title="t", required_capabilities=["code.generate"])
    with pytest.raises(AgentAssignmentError):
        matcher.assign(t)


def test_assign_many():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="coder", capabilities=["code.generate"]),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    tasks = [
        Task(id="t1", title="t1", required_capabilities=["code.generate"]),
        Task(id="t2", title="t2", required_capabilities=["code.generate"]),
        Task(id="t3", title="t3", required_capabilities=["unknown.cap"]),
    ]
    result = matcher.assign_many(tasks)
    assert "t1" in result
    assert "t2" in result
    assert "t3" not in result  # could not be assigned


# ----------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------


def test_metrics_initial():
    matcher = CapabilityMatcher()
    m = matcher.metrics()
    assert m["matches_total"] == 0
    assert m["assign_rate"] == 0.0


def test_metrics_after_matches():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="a1", capabilities=["code.generate"]),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    matcher.match(Task(title="t1", required_capabilities=["code.generate"]))
    matcher.match(Task(title="t2", required_capabilities=["unknown.cap"]))
    m = matcher.metrics()
    assert m["matches_total"] == 2
    assert m["assigned_count"] == 1
    assert m["miss_count"] == 1


# ----------------------------------------------------------------------
# match_many
# ----------------------------------------------------------------------


def test_match_many():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="coder", capabilities=["code.generate"]),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    tasks = [
        Task(id="t1", title="t1", required_capabilities=["code.generate"]),
        Task(id="t2", title="t2", required_capabilities=["code.generate"]),
    ]
    results = matcher.match_many(tasks)
    assert len(results) == 2
    assert all(r.assigned for r in results)


# ----------------------------------------------------------------------
# Candidates list
# ----------------------------------------------------------------------


def test_match_returns_candidates_list():
    adapter = AgentRegistryAdapter(
        agents=[
            AgentInfo(id="a1", capabilities=["code.generate"], priority=1),
            AgentInfo(id="a2", capabilities=["code.generate"], priority=5),
            AgentInfo(id="a3", capabilities=["code.generate"], priority=10),
        ]
    )
    matcher = CapabilityMatcher(adapter=adapter)
    t = Task(title="t", required_capabilities=["code.generate"])
    a = matcher.match(t)
    assert len(a.candidates) == 3
    # Sorted by score desc.
    assert a.candidates[0][0] == "a3"  # highest priority
    assert a.candidates[-1][0] == "a1"
