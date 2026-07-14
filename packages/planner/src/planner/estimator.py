"""CostEstimator — estimates time / tokens / cost / difficulty (Sprint 2.8).

The :class:`CostEstimator` produces four signals for a given
:class:`~planner.task.Task`:

1. ``estimated_duration``  — wall-clock seconds.
2. ``estimated_tokens``    — total LLM tokens (prompt + completion).
3. ``estimated_cost``      — USD.
4. ``difficulty``          — :class:`~planner.models.Difficulty`.

The estimator is **heuristic**: it inspects task title / description /
required_capabilities and applies a baseline + per-keyword adjustment
model. It is fully configurable via :class:`EstimatorConfig` so that
operators can tune the model without touching code.

The estimator is intentionally stateless between calls (each
``estimate()`` is independent), but maintains rolling metrics for
observability.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .exceptions import EstimationError
from .models import Difficulty, difficulty_multiplier
from .task import Task

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


@dataclass
class EstimatorConfig:
    """Configuration for :class:`CostEstimator`.

    Attributes:
        base_duration_s:    Baseline duration per task (seconds).
        base_tokens:        Baseline token count per task.
        base_cost_usd:      Baseline cost per task (USD).
        cost_per_token:     Marginal cost per token (USD).
        duration_per_token_s: How many seconds each token adds (latency).
        difficulty_keywords: Mapping ``{difficulty: [keywords]}``.
        capability_cost:    Mapping ``{capability: cost_multiplier}``.
        capability_duration: Mapping ``{capability: duration_multiplier}``.
        max_description_tokens: Cap on tokens contributed by description length.
    """

    base_duration_s: float = 5.0
    base_tokens: int = 500
    base_cost_usd: float = 0.001
    cost_per_token: float = 0.00002  # $0.02 per 1k tokens, blended
    duration_per_token_s: float = 0.001  # 1ms per token
    difficulty_keywords: dict[Difficulty, list[str]] = field(
        default_factory=lambda: {
            Difficulty.TRIVIAL: ["print", "echo", "list", "show", "status"],
            Difficulty.EASY: ["read", "write", "create", "delete", "rename"],
            Difficulty.MEDIUM: ["refactor", "implement", "add", "fix", "update"],
            Difficulty.HARD: ["design", "architect", "migrate", "optimize", "scale"],
            Difficulty.EXPERT: ["distributed", "consensus", "compiler", "quantum", "ml"],
        }
    )
    capability_cost: dict[str, float] = field(
        default_factory=lambda: {
            "code.generate": 1.5,
            "code.refactor": 1.3,
            "code.review": 1.1,
            "research": 1.4,
            "terminal.execute": 0.8,
            "git.commit": 0.5,
            "memory.search": 0.3,
            "plan": 1.2,
            "decompose": 1.2,
        }
    )
    capability_duration: dict[str, float] = field(
        default_factory=lambda: {
            "code.generate": 1.5,
            "code.refactor": 1.4,
            "code.review": 1.0,
            "research": 1.8,
            "terminal.execute": 0.6,
            "git.commit": 0.4,
            "memory.search": 0.3,
            "plan": 1.3,
            "decompose": 1.3,
        }
    )
    max_description_tokens: int = 4000


# ----------------------------------------------------------------------
# Estimation result
# ----------------------------------------------------------------------


@dataclass
class Estimate:
    """Result of estimating a single task.

    Attributes:
        task_id:        The estimated task's id.
        duration:       Estimated wall-clock seconds.
        tokens:         Estimated total LLM tokens.
        cost:           Estimated USD cost.
        difficulty:     Estimated :class:`Difficulty`.
        confidence:     Heuristic confidence in ``[0.0, 1.0]``.
        breakdown:      Per-signal contributions (for debugging).
    """

    task_id: str
    duration: float = 0.0
    tokens: int = 0
    cost: float = 0.0
    difficulty: Difficulty = Difficulty.MEDIUM
    confidence: float = 0.5
    breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "duration": round(self.duration, 3),
            "tokens": int(self.tokens),
            "cost": round(self.cost, 6),
            "difficulty": self.difficulty.value,
            "confidence": round(self.confidence, 4),
            "breakdown": {k: round(v, 4) for k, v in self.breakdown.items()},
        }

    def apply_to(self, task: Task) -> Task:
        """Write the estimate back onto ``task`` in place."""
        task.estimated_duration = self.duration
        task.estimated_tokens = self.tokens
        task.estimated_cost = self.cost
        task.metadata["difficulty"] = self.difficulty.value
        task.metadata["estimate_confidence"] = round(self.confidence, 4)
        return task


@dataclass
class EstimatorMetrics:
    """Rolling metrics for :class:`CostEstimator`.

    Typed as a dataclass so mypy can verify field accesses (a plain
    ``dict[str, Any]`` loses type information).
    """

    estimates_total: int = 0
    total_duration_s: float = 0.0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    by_difficulty: dict[str, int] = field(default_factory=lambda: {d.value: 0 for d in Difficulty})

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimates_total": self.estimates_total,
            "total_duration_s": round(self.total_duration_s, 3),
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "by_difficulty": dict(self.by_difficulty),
            "avg_duration_s": (
                round(self.total_duration_s / self.estimates_total, 3)
                if self.estimates_total > 0
                else 0.0
            ),
            "avg_tokens": (
                int(self.total_tokens / self.estimates_total) if self.estimates_total > 0 else 0
            ),
        }


# ----------------------------------------------------------------------
# CostEstimator
# ----------------------------------------------------------------------


class CostEstimator:
    """Heuristic estimator for task time / tokens / cost / difficulty.

    Usage::

        est = CostEstimator()
        estimate = est.estimate(task)
        estimate.apply_to(task)  # write back onto the task
    """

    def __init__(self, *, config: EstimatorConfig | None = None) -> None:
        self._config = config or EstimatorConfig()
        # Pre-compile keyword regexes for each difficulty.
        self._compiled: dict[Difficulty, list[re.Pattern[str]]] = {
            d: [re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE) for k in kws]
            for d, kws in self._config.difficulty_keywords.items()
        }
        # Rolling metrics (typed as a small dataclass for mypy friendliness).
        self._metrics = EstimatorMetrics()

    @property
    def config(self) -> EstimatorConfig:
        return self._config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate(self, task: Task) -> Estimate:
        """Produce an :class:`Estimate` for ``task``.

        The estimator never raises for missing data; it falls back to
        the configured baselines. It only raises :class:`EstimationError`
        if internal arithmetic fails (e.g. NaN config).
        """
        cfg = self._config
        # 1. Difficulty.
        difficulty = self._infer_difficulty(task)
        diff_mult = difficulty_multiplier(difficulty)

        # 2. Tokens: base + description length contribution.
        desc_tokens = self._description_token_count(task)
        cap_tokens = self._capability_token_count(task)
        tokens = int((cfg.base_tokens + desc_tokens + cap_tokens) * diff_mult)

        # 3. Duration: base + token-driven latency, scaled by capability.
        cap_dur_mult = self._capability_multiplier(task, cfg.capability_duration, default=1.0)
        duration = (
            (cfg.base_duration_s + tokens * cfg.duration_per_token_s) * diff_mult * cap_dur_mult
        )

        # 4. Cost: base + per-token, scaled by capability.
        cap_cost_mult = self._capability_multiplier(task, cfg.capability_cost, default=1.0)
        cost = (cfg.base_cost_usd + tokens * cfg.cost_per_token) * cap_cost_mult

        # Sanity: detect NaN.
        if not (duration == duration) or not (cost == cost):  # NaN check
            raise EstimationError(
                f"NaN estimate for task {task.id}",
                details={"duration": duration, "cost": cost, "tokens": tokens},
            )

        # Confidence: higher when we have more signals.
        confidence = self._confidence(task, difficulty)

        est = Estimate(
            task_id=task.id,
            duration=max(0.0, duration),
            tokens=max(0, tokens),
            cost=max(0.0, cost),
            difficulty=difficulty,
            confidence=confidence,
            breakdown={
                "base_tokens": float(cfg.base_tokens),
                "desc_tokens": float(desc_tokens),
                "cap_tokens": float(cap_tokens),
                "difficulty_mult": diff_mult,
                "cap_duration_mult": cap_dur_mult,
                "cap_cost_mult": cap_cost_mult,
            },
        )
        self._record(est)
        return est

    def estimate_many(self, tasks: list[Task]) -> list[Estimate]:
        """Estimate a batch of tasks."""
        return [self.estimate(t) for t in tasks]

    def apply(self, tasks: list[Task]) -> None:
        """Estimate and write back onto each task in place."""
        for t in tasks:
            self.estimate(t).apply_to(t)

    # ------------------------------------------------------------------
    # Difficulty inference
    # ------------------------------------------------------------------

    def _infer_difficulty(self, task: Task) -> Difficulty:
        """Infer difficulty from task title + description + capabilities.

        Strategy: count keyword matches for each difficulty level and
        pick the highest level with at least one match. If no keywords
        match, fall back to MEDIUM.
        """
        text = f"{task.title} {task.description}".lower()
        caps = " ".join(task.required_capabilities).lower()
        text_full = f"{text} {caps}"
        # Iterate from highest to lowest difficulty; first match wins.
        for d in [
            Difficulty.EXPERT,
            Difficulty.HARD,
            Difficulty.MEDIUM,
            Difficulty.EASY,
            Difficulty.TRIVIAL,
        ]:
            patterns = self._compiled.get(d, [])
            for pat in patterns:
                if pat.search(text_full):
                    return d
        return Difficulty.MEDIUM

    # ------------------------------------------------------------------
    # Token accounting
    # ------------------------------------------------------------------

    def _description_token_count(self, task: Task) -> int:
        """Estimate tokens contributed by the description.

        Heuristic: ~0.75 tokens per word (English avg). Capped at
        ``config.max_description_tokens``.
        """
        text = (task.description or "") + " " + (task.title or "")
        words = text.split()
        return min(
            int(len(words) * 0.75),
            self._config.max_description_tokens,
        )

    def _capability_token_count(self, task: Task) -> int:
        """Each required capability adds ~50 tokens of context."""
        return int(50 * len(task.required_capabilities))

    # ------------------------------------------------------------------
    # Capability multipliers
    # ------------------------------------------------------------------

    def _capability_multiplier(
        self,
        task: Task,
        table: dict[str, float],
        *,
        default: float,
    ) -> float:
        """Return the average multiplier across the task's capabilities.

        Falls back to ``default`` when no capability matches.
        """
        multipliers: list[float] = []
        for cap in task.required_capabilities:
            # Try exact match, then wildcard (``code.*`` -> ``code.generate``).
            if cap in table:
                multipliers.append(table[cap])
                continue
            # Wildcard lookup.
            for pattern, mult in table.items():
                if pattern.endswith(".*"):
                    prefix = pattern[:-2]
                    if cap == prefix or cap.startswith(prefix + "."):
                        multipliers.append(mult)
                        break
        if not multipliers:
            return default
        return sum(multipliers) / len(multipliers)

    # ------------------------------------------------------------------
    # Confidence
    # ------------------------------------------------------------------

    def _confidence(self, task: Task, difficulty: Difficulty) -> float:
        """Heuristic confidence in ``[0.0, 1.0]``.

        Higher when:
        - Task has a non-empty description.
        - Task declares required_capabilities.
        - Difficulty was inferred (not the default MEDIUM fallback).
        """
        score = 0.3  # baseline
        if task.description.strip():
            score += 0.2
        if len(task.description.split()) >= 10:
            score += 0.15
        if task.required_capabilities:
            score += 0.2
        if len(task.required_capabilities) >= 2:
            score += 0.1
        if difficulty != Difficulty.MEDIUM:
            score += 0.05
        return max(0.0, min(1.0, score))

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        return self._metrics.to_dict()

    def _record(self, est: Estimate) -> None:
        self._metrics.estimates_total += 1
        self._metrics.total_duration_s += est.duration
        self._metrics.total_tokens += est.tokens
        self._metrics.total_cost_usd += est.cost
        self._metrics.by_difficulty[est.difficulty.value] = (
            self._metrics.by_difficulty.get(est.difficulty.value, 0) + 1
        )


__all__ = ["CostEstimator", "Estimate", "EstimatorConfig", "EstimatorMetrics"]
