"""Unit tests for planner.graph (DAG) (Sprint 2.8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLANNER_SRC = ROOT / "packages" / "planner" / "src"
sys.path.insert(0, str(PLANNER_SRC))

for _k in [k for k in list(sys.modules) if k == "planner" or k.startswith("planner.")]:
    sys.modules.pop(_k, None)

from planner.exceptions import CycleDetectedError, DependencyError  # noqa: E402
from planner.graph import DAG, assert_dependency_satisfied, validate_no_cycles  # noqa: E402

# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def test_empty_dag():
    dag = DAG()
    assert len(dag) == 0
    assert dag.task_ids() == []


def test_add_task():
    dag = DAG()
    dag.add_task("a")
    assert "a" in dag
    assert dag.has_task("a")
    assert len(dag) == 1


def test_add_task_idempotent():
    dag = DAG()
    dag.add_task("a")
    dag.add_task("a")
    assert len(dag) == 1


def test_add_task_empty_raises():
    dag = DAG()
    with pytest.raises(ValueError):
        dag.add_task("")


def test_remove_task():
    dag = DAG()
    dag.add_task("a")
    dag.remove_task("a")
    assert "a" not in dag
    assert len(dag) == 0


def test_remove_task_idempotent():
    dag = DAG()
    dag.remove_task("nonexistent")  # should not raise


def test_remove_task_cleans_edges():
    dag = DAG()
    dag.add_task("a")
    dag.add_task("b")
    dag.add_dependency("b", "a")  # b depends on a
    dag.remove_task("a")
    assert dag.parents("b") == []


def test_init_with_tasks():
    dag = DAG(tasks=["a", "b", "c"])
    assert len(dag) == 3
    assert set(dag.task_ids()) == {"a", "b", "c"}


# ----------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------


def test_add_dependency():
    dag = DAG()
    dag.add_task("a")
    dag.add_task("b")
    dag.add_dependency("b", "a")  # b depends on a
    assert dag.parents("b") == ["a"]
    assert dag.children("a") == ["b"]


def test_add_dependency_auto_creates_nodes():
    dag = DAG()
    dag.add_dependency("b", "a")
    assert dag.has_task("a")
    assert dag.has_task("b")


def test_add_dependency_idempotent():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("b", "a")
    assert dag.parents("b") == ["a"]


def test_add_dependency_self_raises():
    dag = DAG()
    with pytest.raises(ValueError):
        dag.add_dependency("a", "a")


def test_add_dependency_empty_raises():
    dag = DAG()
    with pytest.raises(ValueError):
        dag.add_dependency("a", "")
    with pytest.raises(ValueError):
        dag.add_dependency("", "a")


def test_remove_dependency():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.remove_dependency("b", "a")
    assert dag.parents("b") == []
    assert dag.children("a") == []


def test_remove_dependency_idempotent():
    dag = DAG()
    dag.remove_dependency("x", "y")  # should not raise


# ----------------------------------------------------------------------
# Cycle detection
# ----------------------------------------------------------------------


def test_cycle_simple():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    with pytest.raises(CycleDetectedError):
        dag.add_dependency("a", "c")  # creates cycle a -> c -> b -> a


def test_cycle_self_via_two_edges():
    dag = DAG()
    dag.add_dependency("b", "a")
    with pytest.raises(CycleDetectedError):
        dag.add_dependency("a", "b")


def test_detect_cycles_empty_when_acyclic():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    assert dag.detect_cycles() == []


def test_detect_cycles_finds_cycle():
    # Build a cycle manually by manipulating internals (add_dependency
    # would have rejected it).
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    # Force-add the closing edge.
    dag._edges["c"].add("a")
    dag._reverse["a"].add("c")
    cycles = dag.detect_cycles()
    assert len(cycles) >= 1


def test_validate_no_cycles_helper():
    dag = DAG()
    dag.add_dependency("b", "a")
    validate_no_cycles(dag)  # should not raise


def test_validate_no_cycles_helper_raises():
    dag = DAG()
    # Force a cycle by manipulating internals (add_dependency would reject it).
    dag._nodes.update({"a", "b"})
    dag._edges["a"].add("b")
    dag._edges["b"].add("a")
    dag._reverse["b"].add("a")
    dag._reverse["a"].add("b")
    with pytest.raises(CycleDetectedError):
        validate_no_cycles(dag)


# ----------------------------------------------------------------------
# Topological sort
# ----------------------------------------------------------------------


def test_topological_sort_simple():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    order = dag.topological_sort()
    assert order.index("a") < order.index("b")
    assert order.index("b") < order.index("c")


def test_topological_sort_parallel():
    dag = DAG()
    dag.add_task("a")
    dag.add_task("b")
    dag.add_dependency("c", "a")
    dag.add_dependency("c", "b")
    order = dag.topological_sort()
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("c")


def test_topological_sort_empty():
    dag = DAG()
    assert dag.topological_sort() == []


def test_topological_sort_cycle_raises():
    dag = DAG()
    dag._nodes.update({"a", "b"})
    dag._edges["a"].add("b")
    dag._edges["b"].add("a")
    dag._reverse["b"].add("a")
    dag._reverse["a"].add("b")
    with pytest.raises(CycleDetectedError):
        dag.topological_sort()


# ----------------------------------------------------------------------
# Parallel layers
# ----------------------------------------------------------------------


def test_parallel_layers_sequential():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    layers = dag.parallel_layers()
    assert layers == [["a"], ["b"], ["c"]]


def test_parallel_layers_parallel():
    dag = DAG()
    dag.add_task("a")
    dag.add_task("b")
    dag.add_dependency("c", "a")
    dag.add_dependency("c", "b")
    layers = dag.parallel_layers()
    # Layer 0: a, b (both roots); Layer 1: c.
    assert layers[0] == ["a", "b"]
    assert layers[1] == ["c"]


def test_parallel_layers_diamond():
    dag = DAG()
    # a -> b, a -> c, b -> d, c -> d
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "a")
    dag.add_dependency("d", "b")
    dag.add_dependency("d", "c")
    layers = dag.parallel_layers()
    assert layers[0] == ["a"]
    assert set(layers[1]) == {"b", "c"}
    assert layers[2] == ["d"]


def test_parallel_layers_empty():
    dag = DAG()
    assert dag.parallel_layers() == []


# ----------------------------------------------------------------------
# Critical path
# ----------------------------------------------------------------------


def test_critical_path_simple():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    cp = dag.critical_path()
    assert cp == ["a", "b", "c"]


def test_critical_path_with_durations():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    # a=1s, b=10s, c=1s; critical path is a-b-c (12s).
    cp = dag.critical_path({"a": 1.0, "b": 10.0, "c": 1.0})
    assert cp == ["a", "b", "c"]


def test_critical_path_picks_longest_branch():
    dag = DAG()
    # Two parallel branches from a: a-b-c (1+1+1=3) and a-d (10).
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    dag.add_dependency("d", "a")
    cp = dag.critical_path({"a": 1.0, "b": 1.0, "c": 1.0, "d": 10.0})
    # Critical path is a -> d (total 11).
    assert cp == ["a", "d"]


def test_critical_path_empty():
    dag = DAG()
    assert dag.critical_path() == []


def test_critical_path_default_durations():
    dag = DAG()
    dag.add_task("a")
    dag.add_task("b")
    cp = dag.critical_path()
    # Without durations, every node has duration 1.0; critical path is
    # any single node (length 1).
    assert len(cp) == 1


# ----------------------------------------------------------------------
# Roots / leaves / ancestors / descendants
# ----------------------------------------------------------------------


def test_roots():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    assert dag.roots() == ["a"]


def test_leaves():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    assert dag.leaves() == ["c"]


def test_roots_multiple():
    dag = DAG()
    dag.add_task("a")
    dag.add_task("b")
    dag.add_dependency("c", "a")
    dag.add_dependency("c", "b")
    assert set(dag.roots()) == {"a", "b"}


def test_ancestors():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    dag.add_dependency("d", "c")
    assert set(dag.ancestors("d")) == {"a", "b", "c"}


def test_descendants():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    dag.add_dependency("d", "c")
    assert set(dag.descendants("a")) == {"b", "c", "d"}


def test_has_path():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    assert dag.has_path("a", "c")
    assert not dag.has_path("c", "a")
    assert dag.has_path("a", "a")  # trivial path


def test_parents_children_missing_raises():
    dag = DAG()
    with pytest.raises(KeyError):
        dag.parents("nonexistent")
    with pytest.raises(KeyError):
        dag.children("nonexistent")


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


def test_validate_clean():
    dag = DAG()
    dag.add_dependency("b", "a")
    assert dag.validate() == []


def test_validate_with_cycle():
    dag = DAG()
    dag._nodes.update({"a", "b"})
    dag._edges["a"].add("b")
    dag._edges["b"].add("a")
    dag._reverse["b"].add("a")
    dag._reverse["a"].add("b")
    errors = dag.validate()
    assert len(errors) >= 1
    assert any("Cycle" in e for e in errors)


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------


def test_to_from_dict_roundtrip():
    dag = DAG()
    dag.add_dependency("b", "a")
    dag.add_dependency("c", "b")
    d = dag.to_dict()
    dag2 = DAG.from_dict(d)
    assert dag2.parents("b") == ["a"]
    assert dag2.parents("c") == ["b"]
    assert set(dag2.task_ids()) == {"a", "b", "c"}


def test_to_dict_includes_isolated_nodes():
    dag = DAG()
    dag.add_task("lonely")
    d = dag.to_dict()
    assert "lonely" in d["nodes"]


def test_from_dict_cycle_raises():
    d = {
        "nodes": ["a", "b"],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "a"},
        ],
    }
    with pytest.raises(CycleDetectedError):
        DAG.from_dict(d)


# ----------------------------------------------------------------------
# Copy
# ----------------------------------------------------------------------


def test_copy_independent():
    dag = DAG()
    dag.add_dependency("b", "a")
    copy = dag.copy()
    copy.add_task("c")
    assert not dag.has_task("c")
    assert copy.has_task("c")


# ----------------------------------------------------------------------
# assert_dependency_satisfied
# ----------------------------------------------------------------------


def test_assert_dependency_satisfied_ok():
    dag = DAG()
    dag.add_dependency("b", "a")
    assert_dependency_satisfied(dag, "b", {"a"})


def test_assert_dependency_satisfied_missing():
    dag = DAG()
    dag.add_dependency("b", "a")
    with pytest.raises(DependencyError):
        assert_dependency_satisfied(dag, "b", set())


# ----------------------------------------------------------------------
# __repr__
# ----------------------------------------------------------------------


def test_repr():
    dag = DAG()
    dag.add_task("a")
    dag.add_task("b")
    dag.add_dependency("b", "a")
    r = repr(dag)
    assert "DAG" in r
    assert "nodes=2" in r
