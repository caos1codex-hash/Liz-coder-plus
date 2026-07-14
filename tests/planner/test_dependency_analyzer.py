"""Unit tests for planner.dependency_analyzer (Sprint 2.8)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLANNER_SRC = ROOT / "packages" / "planner" / "src"
sys.path.insert(0, str(PLANNER_SRC))

for _k in [k for k in list(sys.modules) if k == "planner" or k.startswith("planner.")]:
    sys.modules.pop(_k, None)

from planner.dependency_analyzer import (  # noqa: E402
    DependencyAnalyzer,
    DependencyAnalyzerConfig,
)
from planner.task import Task  # noqa: E402

# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def test_default_config():
    a = DependencyAnalyzer()
    assert a.config.capability_order["plan"] == 0
    assert a.config.sequential_fallback is True


def test_custom_config():
    cfg = DependencyAnalyzerConfig(strict_phase_ordering=True)
    a = DependencyAnalyzer(config=cfg)
    assert a.config.strict_phase_ordering is True


# ----------------------------------------------------------------------
# Empty / single task
# ----------------------------------------------------------------------


def test_analyze_empty():
    a = DependencyAnalyzer()
    assert a.analyze([]) == {}


def test_analyze_single_task():
    a = DependencyAnalyzer()
    t = Task(id="t1", title="t1")
    deps = a.analyze([t])
    assert deps == {"t1": []}


# ----------------------------------------------------------------------
# Explicit hints
# ----------------------------------------------------------------------


def test_explicit_hint_by_id():
    a = DependencyAnalyzer()
    t1 = Task(id="t1", title="First")
    t2 = Task(id="t2", title="Second", metadata={"depends_on": ["t1"]})
    deps = a.analyze([t1, t2])
    assert deps["t2"] == ["t1"]


def test_explicit_hint_by_title():
    a = DependencyAnalyzer()
    t1 = Task(id="t1", title="First")
    t2 = Task(id="t2", title="Second", metadata={"depends_on": ["First"]})
    deps = a.analyze([t1, t2])
    assert deps["t2"] == ["t1"]


def test_explicit_hint_string_value():
    a = DependencyAnalyzer()
    t1 = Task(id="t1", title="First")
    t2 = Task(id="t2", title="Second", metadata={"depends_on": "t1"})
    deps = a.analyze([t1, t2])
    assert deps["t2"] == ["t1"]


def test_explicit_hint_unknown_ignored():
    a = DependencyAnalyzer()
    t1 = Task(id="t1", title="First", metadata={"depends_on": ["nonexistent"]})
    deps = a.analyze([t1])
    assert deps["t1"] == []


# ----------------------------------------------------------------------
# Capability-based ordering
# ----------------------------------------------------------------------


def test_capability_order_links_adjacent_phases():
    a = DependencyAnalyzer()
    # "plan" phase 0, "code.generate" phase 2 — should be linked via
    # phase 1 (research). But there's no phase 1 task, so direct link
    # is NOT created (only adjacent phases are linked).
    t_plan = Task(id="plan", title="plan", required_capabilities=["plan"])
    t_code = Task(id="code", title="code", required_capabilities=["code.generate"])
    deps = a.analyze([t_plan, t_code])
    # With sequential_fallback=True, t_code depends on t_plan.
    assert "plan" in deps["code"]


def test_capability_order_with_intermediate():
    a = DependencyAnalyzer()
    t_plan = Task(id="plan", title="plan", required_capabilities=["plan"])
    t_research = Task(id="research", title="research", required_capabilities=["research"])
    t_code = Task(id="code", title="code", required_capabilities=["code.generate"])
    deps = a.analyze([t_plan, t_research, t_code])
    # research depends on plan; code depends on research.
    assert "plan" in deps["research"]
    assert "research" in deps["code"]
    # code should NOT depend on plan directly (not adjacent phases).
    assert "plan" not in deps["code"]


def test_strict_phase_ordering_links_all_earlier():
    cfg = DependencyAnalyzerConfig(strict_phase_ordering=True, sequential_fallback=False)
    a = DependencyAnalyzer(config=cfg)
    t_plan = Task(id="plan", title="plan", required_capabilities=["plan"])
    t_research = Task(id="research", title="research", required_capabilities=["research"])
    t_code = Task(id="code", title="code", required_capabilities=["code.generate"])
    deps = a.analyze([t_plan, t_research, t_code])
    # In strict mode, code depends on BOTH plan and research.
    assert "plan" in deps["code"]
    assert "research" in deps["code"]


# ----------------------------------------------------------------------
# Phase metadata
# ----------------------------------------------------------------------


def test_explicit_phase_metadata():
    a = DependencyAnalyzer()
    t1 = Task(id="t1", title="t1", metadata={"phase": 0})
    t2 = Task(id="t2", title="t2", metadata={"phase": 1})
    t3 = Task(id="t3", title="t3", metadata={"phase": 2})
    deps = a.analyze([t1, t2, t3])
    # Adjacent phases linked.
    assert "t1" in deps["t2"]
    assert "t2" in deps["t3"]


def test_explicit_phase_string():
    a = DependencyAnalyzer()
    t1 = Task(id="t1", title="t1", metadata={"phase": "0"})
    t2 = Task(id="t2", title="t2", metadata={"phase": "1"})
    deps = a.analyze([t1, t2])
    assert "t1" in deps["t2"]


def test_invalid_phase_falls_back():
    a = DependencyAnalyzer()
    t1 = Task(id="t1", title="t1", metadata={"phase": "not a number"})
    deps = a.analyze([t1])
    # Should not crash; phase falls back to 9999.
    assert deps["t1"] == []


# ----------------------------------------------------------------------
# Sequential fallback
# ----------------------------------------------------------------------


def test_sequential_fallback_links_in_order():
    a = DependencyAnalyzer()
    t1 = Task(id="t1", title="t1")
    t2 = Task(id="t2", title="t2")
    t3 = Task(id="t3", title="t3")
    deps = a.analyze([t1, t2, t3])
    assert deps["t2"] == ["t1"]
    assert deps["t3"] == ["t2"]


def test_sequential_fallback_disabled():
    cfg = DependencyAnalyzerConfig(
        sequential_fallback=False,
        strict_phase_ordering=False,
    )
    a = DependencyAnalyzer(config=cfg)
    t1 = Task(id="t1", title="t1")
    t2 = Task(id="t2", title="t2")
    deps = a.analyze([t1, t2])
    # No linking happens.
    assert deps["t1"] == []
    assert deps["t2"] == []


# ----------------------------------------------------------------------
# apply()
# ----------------------------------------------------------------------


def test_apply_writes_dependencies():
    a = DependencyAnalyzer()
    t1 = Task(id="t1", title="t1")
    t2 = Task(id="t2", title="t2")
    a.apply([t1, t2])
    assert t2.dependencies == ["t1"]
    assert t1.dependencies == []


def test_apply_clears_existing_deps():
    a = DependencyAnalyzer()
    t1 = Task(id="t1", title="t1")
    t2 = Task(id="t2", title="t2", dependencies=["old_dep"])
    a.apply([t1, t2])
    # "old_dep" is unknown, so it's dropped; t2 depends on t1.
    assert "old_dep" not in t2.dependencies


# ----------------------------------------------------------------------
# Determinism
# ----------------------------------------------------------------------


def test_analyze_is_deterministic():
    a = DependencyAnalyzer()
    t1 = Task(id="t1", title="t1")
    t2 = Task(id="t2", title="t2")
    t3 = Task(id="t3", title="t3")
    tasks = [t1, t2, t3]
    deps1 = a.analyze(tasks)
    deps2 = a.analyze(tasks)
    assert deps1 == deps2


def test_analyze_returns_sorted_lists():
    a = DependencyAnalyzer()
    t_a = Task(id="aaa", title="aaa")
    t_b = Task(id="bbb", title="bbb")
    t_c = Task(id="ccc", title="ccc")
    # All three end up as deps of nothing (sequential: b->a, c->b).
    deps = a.analyze([t_a, t_b, t_c])
    # t_c depends on t_b (sorted).
    assert deps["ccc"] == ["bbb"]
