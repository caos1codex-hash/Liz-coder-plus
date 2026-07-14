"""Tests para el DAG (Sprint 2.2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.workflow.dag import DAG  # noqa: E402


# --- Construcción ---


def test_empty_dag() -> None:
    dag = DAG()
    v = dag.validate()
    assert v.ok is True
    assert v.cycles == []


def test_add_step() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b", depends_on=["a"])
    assert dag.has_step("a")
    assert dag.has_step("b")
    assert dag.dependencies_of("b") == {"a"}


def test_remove_step_cleans_references() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b", depends_on=["a"])
    dag.remove_step("a")
    assert not dag.has_step("a")
    # b ya no depende de a
    assert dag.dependencies_of("b") == set()


def test_add_dependency() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b")
    dag.add_dependency("b", "a")
    assert "a" in dag.dependencies_of("b")


# --- Validación: ciclos ---


def test_validate_detects_self_cycle() -> None:
    dag = DAG()
    dag.add_step("a", depends_on=["a"])
    v = dag.validate()
    assert v.ok is False
    assert len(v.cycles) >= 1


def test_validate_detects_simple_cycle() -> None:
    dag = DAG()
    dag.add_step("a", depends_on=["b"])
    dag.add_step("b", depends_on=["a"])
    v = dag.validate()
    assert v.ok is False
    assert len(v.cycles) >= 1


def test_validate_detects_long_cycle() -> None:
    dag = DAG()
    dag.add_step("a", depends_on=["c"])
    dag.add_step("b", depends_on=["a"])
    dag.add_step("c", depends_on=["b"])
    v = dag.validate()
    assert v.ok is False
    assert len(v.cycles) >= 1


def test_validate_no_cycle_in_diamond() -> None:
    """DAG en forma de diamante: a → (b, c) → d."""
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b", depends_on=["a"])
    dag.add_step("c", depends_on=["a"])
    dag.add_step("d", depends_on=["b", "c"])
    v = dag.validate()
    assert v.ok is True
    assert v.cycles == []


# --- Validación: dependencias rotas ---


def test_validate_detects_broken_dependency() -> None:
    dag = DAG()
    dag.add_step("a", depends_on=["nonexistent"])
    v = dag.validate()
    assert v.ok is False
    assert ("a", "nonexistent") in v.broken_deps


# --- Validación: huérfanos ---


def test_validate_orphan_steps_warning() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b")
    # No hay dependencias entre a y b; ambos son "huérfanos" en un DAG >1.
    v = dag.validate()
    # En un workflow real esto puede ser válido; sólo se reporta como warning.
    assert "a" in v.orphan_steps or "b" in v.orphan_steps


def test_single_step_no_orphan_warning() -> None:
    dag = DAG()
    dag.add_step("only")
    v = dag.validate()
    assert v.ok is True
    assert v.orphan_steps == []


# --- Orden topológico ---


def test_topological_order_linear() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b", depends_on=["a"])
    dag.add_step("c", depends_on=["b"])
    order = dag.topological_order()
    assert order.index("a") < order.index("b") < order.index("c")


def test_topological_order_diamond() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b", depends_on=["a"])
    dag.add_step("c", depends_on=["a"])
    dag.add_step("d", depends_on=["b", "c"])
    order = dag.topological_order()
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_topological_order_raises_on_cycle() -> None:
    dag = DAG()
    dag.add_step("a", depends_on=["b"])
    dag.add_step("b", depends_on=["a"])
    with pytest.raises(ValueError, match="cycle"):
        dag.topological_order()


def test_topological_order_with_priorities() -> None:
    dag = DAG()
    dag.add_step("low", priority=10)
    dag.add_step("high", priority=1)
    dag.add_step("normal", priority=5)
    order = dag.topological_order()
    # Los 3 son root (sin dependencias), deben salir por prioridad.
    assert order[0] == "high"
    assert order[1] == "normal"
    assert order[2] == "low"


# --- ready_steps ---


def test_ready_steps_empty_completed() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b", depends_on=["a"])
    # Sin nada completado: sólo "a" está lista.
    ready = dag.ready_steps(set())
    assert ready == ["a"]


def test_ready_steps_after_a_completed() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b", depends_on=["a"])
    dag.add_step("c", depends_on=["b"])
    ready = dag.ready_steps({"a"})
    assert ready == ["b"]


def test_ready_steps_with_skip() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b")
    ready = dag.ready_steps(set(), skip={"a"})
    assert ready == ["b"]


# --- ancestors / descendants ---


def test_ancestors() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b", depends_on=["a"])
    dag.add_step("c", depends_on=["b"])
    assert dag.ancestors("c") == {"a", "b"}
    assert dag.ancestors("a") == set()


def test_descendants() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b", depends_on=["a"])
    dag.add_step("c", depends_on=["b"])
    assert dag.descendants("a") == {"b", "c"}
    assert dag.descendants("c") == set()


def test_has_path() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b", depends_on=["a"])
    dag.add_step("c", depends_on=["b"])
    assert dag.has_path("a", "c") is True
    assert dag.has_path("c", "a") is False
    assert dag.has_path("a", "a") is True


# --- subgraph ---


def test_subgraph() -> None:
    dag = DAG()
    dag.add_step("a")
    dag.add_step("b", depends_on=["a"])
    dag.add_step("c", depends_on=["b"])
    dag.add_step("d")  # fuera del subgraph
    sub = dag.subgraph({"a", "b"})
    assert sub.has_step("a")
    assert sub.has_step("b")
    assert not sub.has_step("c")
    assert not sub.has_step("d")
    # b sigue dependiendo de a (que está en el subgraph).
    assert "a" in sub.dependencies_of("b")


# --- Serialización ---


def test_serialization_roundtrip() -> None:
    dag = DAG()
    dag.add_step("a", priority=1)
    dag.add_step("b", depends_on=["a"], priority=2)
    d = dag.to_dict()
    dag2 = DAG.from_dict(d)
    assert dag2.has_step("a")
    assert dag2.has_step("b")
    assert dag2.dependencies_of("b") == {"a"}
    assert dag2.priorities["a"] == 1
