"""Unit tests for planner.estimator (Sprint 2.8)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLANNER_SRC = ROOT / "packages" / "planner" / "src"
sys.path.insert(0, str(PLANNER_SRC))

for _k in [k for k in list(sys.modules) if k == "planner" or k.startswith("planner.")]:
    sys.modules.pop(_k, None)

from planner.estimator import (  # noqa: E402
    CostEstimator,
    EstimatorConfig,
)
from planner.models import Difficulty  # noqa: E402
from planner.task import Task  # noqa: E402

# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def test_default_config():
    est = CostEstimator()
    assert est.config.base_duration_s == 5.0
    assert est.config.base_tokens == 500


def test_custom_config():
    cfg = EstimatorConfig(base_duration_s=10.0, base_tokens=1000)
    est = CostEstimator(config=cfg)
    assert est.config.base_duration_s == 10.0


# ----------------------------------------------------------------------
# Basic estimation
# ----------------------------------------------------------------------


def test_estimate_basic():
    est = CostEstimator()
    t = Task(title="test", description="a simple task")
    e = est.estimate(t)
    assert e.task_id == t.id
    assert e.duration > 0
    assert e.tokens > 0
    assert e.cost > 0
    assert e.difficulty in Difficulty


def test_estimate_apply_to_task():
    est = CostEstimator()
    t = Task(title="test", description="do something")
    e = est.estimate(t)
    e.apply_to(t)
    assert t.estimated_duration == e.duration
    assert t.estimated_tokens == e.tokens
    assert t.estimated_cost == e.cost
    assert t.metadata["difficulty"] == e.difficulty.value


def test_estimate_many():
    est = CostEstimator()
    tasks = [
        Task(title="t1", description="first"),
        Task(title="t2", description="second"),
    ]
    results = est.estimate_many(tasks)
    assert len(results) == 2
    assert results[0].task_id == tasks[0].id
    assert results[1].task_id == tasks[1].id


def test_apply_writes_back():
    est = CostEstimator()
    tasks = [
        Task(title="t1", description="first"),
        Task(title="t2", description="second"),
    ]
    est.apply(tasks)
    assert tasks[0].estimated_duration > 0
    assert tasks[1].estimated_duration > 0


# ----------------------------------------------------------------------
# Difficulty inference
# ----------------------------------------------------------------------


def test_difficulty_trivial_keyword():
    est = CostEstimator()
    t = Task(title="print hello", description="just print something")
    e = est.estimate(t)
    assert e.difficulty == Difficulty.TRIVIAL


def test_difficulty_easy_keyword():
    est = CostEstimator()
    t = Task(title="create file", description="write a file")
    e = est.estimate(t)
    assert e.difficulty == Difficulty.EASY


def test_difficulty_medium_default():
    est = CostEstimator()
    t = Task(title="xyz random", description="abc unknown")
    e = est.estimate(t)
    assert e.difficulty == Difficulty.MEDIUM


def test_difficulty_hard_keyword():
    est = CostEstimator()
    t = Task(title="design system architecture", description="architect a new service")
    e = est.estimate(t)
    # "design" or "architect" -> HARD
    assert e.difficulty == Difficulty.HARD


def test_difficulty_expert_keyword():
    est = CostEstimator()
    t = Task(title="distributed consensus", description="build a consensus algorithm")
    e = est.estimate(t)
    assert e.difficulty == Difficulty.EXPERT


# ----------------------------------------------------------------------
# Token accounting
# ----------------------------------------------------------------------


def test_tokens_increase_with_description_length():
    est = CostEstimator()
    short = Task(title="t", description="short")
    long = Task(title="t", description=" ".join(["word"] * 100))
    e_short = est.estimate(short)
    e_long = est.estimate(long)
    assert e_long.tokens > e_short.tokens


def test_tokens_increase_with_capabilities():
    est = CostEstimator()
    no_caps = Task(title="t", description="test")
    many_caps = Task(title="t", description="test", required_capabilities=["a", "b", "c"])
    e_no = est.estimate(no_caps)
    e_many = est.estimate(many_caps)
    assert e_many.tokens > e_no.tokens


def test_tokens_increase_with_difficulty():
    est = CostEstimator()
    trivial = Task(title="print hello", description="print something")
    expert = Task(title="distributed consensus", description="ml algorithm")
    e_trivial = est.estimate(trivial)
    e_expert = est.estimate(expert)
    assert e_expert.tokens > e_trivial.tokens


# ----------------------------------------------------------------------
# Cost accounting
# ----------------------------------------------------------------------


def test_cost_positive():
    est = CostEstimator()
    t = Task(title="t", description="test")
    e = est.estimate(t)
    assert e.cost > 0


def test_cost_scales_with_capability():
    est = CostEstimator()
    no_cap = Task(title="t", description="test")
    expensive_cap = Task(
        title="t",
        description="test",
        required_capabilities=["research"],  # 1.4 multiplier
    )
    e_no = est.estimate(no_cap)
    e_exp = est.estimate(expensive_cap)
    assert e_exp.cost > e_no.cost


# ----------------------------------------------------------------------
# Confidence
# ----------------------------------------------------------------------


def test_confidence_in_range():
    est = CostEstimator()
    t = Task(title="t", description="test")
    e = est.estimate(t)
    assert 0.0 <= e.confidence <= 1.0


def test_confidence_increases_with_signals():
    est = CostEstimator()
    sparse = Task(title="t")
    rich = Task(
        title="Implement feature",
        description="This is a detailed description with enough words to be considered rich.",
        required_capabilities=["code.generate", "code.test"],
    )
    e_sparse = est.estimate(sparse)
    e_rich = est.estimate(rich)
    assert e_rich.confidence > e_sparse.confidence


# ----------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------


def test_metrics_initial():
    est = CostEstimator()
    m = est.metrics()
    assert m["estimates_total"] == 0
    assert m["avg_duration_s"] == 0.0


def test_metrics_after_estimates():
    est = CostEstimator()
    est.estimate(Task(title="t1", description="x"))
    est.estimate(Task(title="t2", description="y"))
    m = est.metrics()
    assert m["estimates_total"] == 2
    assert m["avg_duration_s"] > 0
    assert m["avg_tokens"] > 0


def test_metrics_by_difficulty():
    est = CostEstimator()
    est.estimate(Task(title="print hello", description="print"))
    est.estimate(Task(title="print world", description="print"))
    m = est.metrics()
    assert m["by_difficulty"]["trivial"] == 2


# ----------------------------------------------------------------------
# Estimate.to_dict
# ----------------------------------------------------------------------


def test_estimate_to_dict():
    est = CostEstimator()
    t = Task(title="t", description="test")
    e = est.estimate(t)
    d = e.to_dict()
    assert d["task_id"] == t.id
    assert "duration" in d
    assert "tokens" in d
    assert "cost" in d
    assert "difficulty" in d
    assert "confidence" in d
    assert "breakdown" in d


# ----------------------------------------------------------------------
# Custom config
# ----------------------------------------------------------------------


def test_custom_difficulty_keywords():
    cfg = EstimatorConfig(
        difficulty_keywords={
            Difficulty.EXPERT: ["quantum"],
            Difficulty.HARD: [],
            Difficulty.MEDIUM: [],
            Difficulty.EASY: [],
            Difficulty.TRIVIAL: [],
        }
    )
    est = CostEstimator(config=cfg)
    t = Task(title="quantum computing", description="build a quantum computer")
    e = est.estimate(t)
    assert e.difficulty == Difficulty.EXPERT


def test_custom_capability_cost():
    cfg = EstimatorConfig(
        capability_cost={"custom.cap": 5.0},
    )
    est = CostEstimator(config=cfg)
    t = Task(title="t", description="test", required_capabilities=["custom.cap"])
    e = est.estimate(t)
    # The custom multiplier should make the cost higher than baseline.
    assert e.breakdown["cap_cost_mult"] == 5.0


def test_capability_wildcard_match():
    cfg = EstimatorConfig(
        capability_cost={"code.*": 2.0},
    )
    est = CostEstimator(config=cfg)
    t = Task(title="t", description="test", required_capabilities=["code.generate"])
    e = est.estimate(t)
    assert e.breakdown["cap_cost_mult"] == 2.0


def test_capability_no_match_uses_default():
    est = CostEstimator()
    t = Task(title="t", description="test", required_capabilities=["unknown.cap"])
    e = est.estimate(t)
    # Default multiplier is 1.0.
    assert e.breakdown["cap_cost_mult"] == 1.0


# ----------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------


def test_estimate_empty_task():
    est = CostEstimator()
    t = Task(title="t")
    e = est.estimate(t)
    assert e.tokens >= est.config.base_tokens


def test_estimate_idempotent():
    est = CostEstimator()
    t = Task(title="print hello", description="print")
    e1 = est.estimate(t)
    e2 = est.estimate(t)
    assert e1.tokens == e2.tokens
    assert e1.duration == e2.duration
    assert e1.cost == e2.cost


def test_estimate_does_not_mutate_task():
    est = CostEstimator()
    t = Task(title="t", description="test")
    original_tokens = t.estimated_tokens
    est.estimate(t)
    assert t.estimated_tokens == original_tokens  # estimate() doesn't write back
