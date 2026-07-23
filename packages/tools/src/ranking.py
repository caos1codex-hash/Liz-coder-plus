"""Ranking logic for the Tool Selection Engine.

Sprint 4.2 — Tool Intelligence: Tool Selection Engine.

This module provides:

- :class:`RankingWeights` — configurable weight for each ranking factor.
- :class:`RankingResult` — per-tool score with human-readable reason.
- :class:`ToolRanker` — computes a normalised score (0.0 – 1.0) for
  every candidate tool based on multiple weighted factors.

Ranking factors
---------------
1. **Capability Match** — how well the tool's capabilities overlap
   with the requirements.
2. **Priority** — explicit priority declared in tool metadata
   (``metadata["priority"]``, 0.0 – 1.0, default 0.5).
3. **Reliability** — derived from success rate
   (``executions / max(1, executions + failures)``).
4. **Execution Cost** — lower is better; read from
   ``metadata["cost"]`` (default 0).
5. **Latency** — lower is better; derived from average execution
   time or ``metadata["avg_latency_ms"]``.
6. **Safety** — higher is better; from ``metadata["safety_score"]``
   or a permission-level-based default.
7. **Historical Performance** — weighted blend of success rate and
   total execution count (more executions → more confidence).
8. **Manual Boost** — a direct bonus from
   ``metadata["manual_boost"]`` or user preference.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from src.base import BaseTool, PermissionLevel
from src.exceptions import RankingError
from src.filters import SelectionContext

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class RankingWeights:
    """Configurable weights for each ranking factor.

    All weights must be non-negative.  The :meth:`normalised`
    property returns a copy where the weights sum to 1.0.

    Attributes:
        capability_weight:   Weight for capability-match score.
        priority_weight:     Weight for explicit priority metadata.
        latency_weight:      Weight for low-latency preference.
        cost_weight:         Weight for low-cost preference.
        reliability_weight:  Weight for success-rate / reliability.
        history_weight:      Weight for historical performance.
        safety_weight:       Weight for safety score.
        manual_boost_weight: Weight for explicit manual boost.
    """

    capability_weight: float = 0.30
    priority_weight: float = 0.15
    latency_weight: float = 0.10
    cost_weight: float = 0.10
    reliability_weight: float = 0.15
    history_weight: float = 0.10
    safety_weight: float = 0.05
    manual_boost_weight: float = 0.05

    def __post_init__(self) -> None:
        weights = [
            self.capability_weight,
            self.priority_weight,
            self.latency_weight,
            self.cost_weight,
            self.reliability_weight,
            self.history_weight,
            self.safety_weight,
            self.manual_boost_weight,
        ]
        for w in weights:
            if w < 0:
                raise ValueError(f"All ranking weights must be non-negative, got {w}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def as_tuple(self) -> tuple[float, ...]:
        """Return weights in the canonical factor order."""
        return (
            self.capability_weight,
            self.priority_weight,
            self.latency_weight,
            self.cost_weight,
            self.reliability_weight,
            self.history_weight,
            self.safety_weight,
            self.manual_boost_weight,
        )

    @property
    def total(self) -> float:
        return sum(self.as_tuple)

    def normalised(self) -> RankingWeights:
        """Return a copy whose weights sum to exactly 1.0.

        If the total is zero, falls back to uniform weights.
        """
        t = self.total
        if t == 0:
            return RankingWeights(
                capability_weight=0.125,
                priority_weight=0.125,
                latency_weight=0.125,
                cost_weight=0.125,
                reliability_weight=0.125,
                history_weight=0.125,
                safety_weight=0.125,
                manual_boost_weight=0.125,
            )
        return RankingWeights(
            capability_weight=self.capability_weight / t,
            priority_weight=self.priority_weight / t,
            latency_weight=self.latency_weight / t,
            cost_weight=self.cost_weight / t,
            reliability_weight=self.reliability_weight / t,
            history_weight=self.history_weight / t,
            safety_weight=self.safety_weight / t,
            manual_boost_weight=self.manual_boost_weight / t,
        )


@dataclass
class RankingResult:
    """Score and explanation for a single tool.

    Attributes:
        tool:    The tool that was scored.
        score:   Normalised score in [0.0, 1.0].
        reason:  Human-readable summary of why this score.
        details: Optional dict with per-factor sub-scores.
    """

    tool: BaseTool
    score: float
    reason: str
    details: dict[str, float] = field(default_factory=dict)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RankingResult):
            return NotImplemented
        return self.score < other.score

    def __le__(self, other: object) -> bool:
        if not isinstance(other, RankingResult):
            return NotImplemented
        return self.score <= other.score

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, RankingResult):
            return NotImplemented
        return self.score > other.score

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, RankingResult):
            return NotImplemented
        return self.score >= other.score


# ----------------------------------------------------------------------
# Ranker
# ----------------------------------------------------------------------


class ToolRanker:
    """Score and rank a list of tools against a selection context.

    The ranker does **not** filter — it assumes all tools passed to
    :meth:`rank` are already viable candidates.  It computes a
    weighted, normalised score for each tool and returns the results
    sorted from best to worst.
    """

    # Permission-level safety defaults (same as SafetyFilter).
    _DEFAULT_SAFETY: dict[PermissionLevel, float] = {
        PermissionLevel.LOW: 1.0,
        PermissionLevel.MEDIUM: 0.7,
        PermissionLevel.HIGH: 0.4,
    }

    def __init__(
        self,
        *,
        weights: RankingWeights | None = None,
        max_cost: float | None = None,
        max_latency_ms: float | None = None,
    ) -> None:
        self._weights = (weights or RankingWeights()).normalised()
        self._max_cost = max_cost  # used for cost normalisation
        self._max_latency_ms = max_latency_ms  # used for latency norm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rank(
        self,
        tools: list[BaseTool],
        context: SelectionContext,
    ) -> list[RankingResult]:
        """Score and rank *tools* in descending order.

        Returns an empty list if *tools* is empty.
        """
        if not tools:
            return []

        results: list[RankingResult] = []
        for tool in tools:
            try:
                result = self.score_tool(tool, context)
            except RankingError:
                logger.warning(
                    "Failed to score tool '%s', skipping",
                    tool.name,
                    exc_info=True,
                )
                continue
            results.append(result)

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def score_tool(
        self,
        tool: BaseTool,
        context: SelectionContext,
    ) -> RankingResult:
        """Compute a single normalised score for *tool*.

        Raises:
            RankingError: If the score cannot be computed.
        """
        w = self._weights
        try:
            factors: dict[str, float] = {
                "capability_match": self._capability_score(tool, context),
                "priority": self._priority_score(tool),
                "latency": self._latency_score(tool),
                "cost": self._cost_score(tool),
                "reliability": self._reliability_score(tool),
                "history": self._history_score(tool),
                "safety": self._safety_score(tool),
                "manual_boost": self._manual_boost_score(tool, context),
            }
        except Exception as exc:
            raise RankingError(
                f"Cannot compute score for tool '{tool.name}': {exc}",
                tool_name=tool.name,
            ) from exc

        raw = (
            factors["capability_match"] * w.capability_weight
            + factors["priority"] * w.priority_weight
            + factors["latency"] * w.latency_weight
            + factors["cost"] * w.cost_weight
            + factors["reliability"] * w.reliability_weight
            + factors["history"] * w.history_weight
            + factors["safety"] * w.safety_weight
            + factors["manual_boost"] * w.manual_boost_weight
        )

        # Clamp to [0.0, 1.0].
        score = max(0.0, min(1.0, raw))

        reason = self._build_reason(tool.name, score, factors)

        logger.debug(
            "Tool '%s' scored %.4f — %s",
            tool.name,
            score,
            reason,
        )

        return RankingResult(
            tool=tool,
            score=score,
            reason=reason,
            details=factors,
        )

    # ------------------------------------------------------------------
    # Factor scorers (each returns 0.0 – 1.0)
    # ------------------------------------------------------------------

    @staticmethod
    def _capability_score(
        tool: BaseTool,
        context: SelectionContext,
    ) -> float:
        """Jaccard-like overlap of tool capabilities vs requirements."""
        if not context.capability_requirements:
            return 1.0  # no requirements → perfect match

        tool_caps: set[str] = {tool.category.value}
        caps_from_meta = tool.metadata.get("capabilities")
        if isinstance(caps_from_meta, list):
            tool_caps.update(str(c) for c in caps_from_meta)

        intersection = tool_caps & context.capability_requirements
        union = tool_caps | context.capability_requirements
        return len(intersection) / len(union) if union else 1.0

    @staticmethod
    def _priority_score(tool: BaseTool) -> float:
        """Read explicit priority from metadata (default 0.5)."""
        return float(tool.metadata.get("priority", 0.5))

    def _latency_score(self, tool: BaseTool) -> float:
        """Normalised latency — lower latency → higher score."""
        avg = self._effective_latency(tool)
        ceiling = self._max_latency_ms or max(avg * 2.0, 1.0)
        # Invert: score = 1 - (avg / ceiling), clamped.
        return max(0.0, 1.0 - (avg / ceiling))

    @staticmethod
    def _effective_latency(tool: BaseTool) -> float:
        declared = tool.metadata.get("avg_latency_ms")
        if declared is not None:
            return float(declared)
        total = float(tool.metadata.get("total_time_ms", 0))
        execs = max(1, int(tool.metadata.get("executions", 0)))
        return total / execs

    def _cost_score(self, tool: BaseTool) -> float:
        """Normalised cost — lower cost → higher score."""
        cost = float(tool.metadata.get("cost", 0))
        ceiling = self._max_cost or max(cost * 2.0, 1.0)
        return max(0.0, 1.0 - (cost / ceiling))

    @staticmethod
    def _reliability_score(tool: BaseTool) -> float:
        """Success rate = executions / (executions + failures)."""
        execs = tool.metadata.get("executions", 0)
        fails = tool.metadata.get("failures", 0)
        total = int(execs) + int(fails)
        if total == 0:
            return 0.5  # no data → neutral
        return int(execs) / total

    @staticmethod
    def _history_score(tool: BaseTool) -> float:
        """Confidence from execution count (sigmoid-like).

        More executions → more confidence → higher score.
        Uses a log-saturating curve: ``min(1.0, log10(n+1) / 3)``.
        """
        execs = tool.metadata.get("executions", 0)
        n = max(0, int(execs))
        if n == 0:
            return 0.3  # unknown tool gets modest score
        return min(1.0, math.log10(n + 1) / 3.0)

    def _safety_score(self, tool: BaseTool) -> float:
        """Safety score from metadata or permission-level default."""
        score = tool.metadata.get("safety_score")
        if score is not None:
            return float(score)
        return self._DEFAULT_SAFETY[tool.permission_level]

    @staticmethod
    def _manual_boost_score(
        tool: BaseTool,
        context: SelectionContext,
    ) -> float:
        """Extra boost from metadata or user preference."""
        boost = float(tool.metadata.get("manual_boost", 0.0))
        if tool.name in context.preferred_tools:
            boost = max(boost, 0.5)
        return min(1.0, boost)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _build_reason(
        tool_name: str,
        score: float,
        factors: dict[str, float],
    ) -> str:
        """Produce a concise human-readable reason string."""
        # Identify the strongest and weakest factors.
        if not factors:
            return f"Tool '{tool_name}' scored {score:.2f} (no factors)"
        best = max(factors, key=factors.get)  # type: ignore[arg-type]
        worst = min(factors, key=factors.get)  # type: ignore[arg-type]
        return (
            f"Tool '{tool_name}' scored {score:.4f} "
            f"(best={best}:{factors[best]:.2f}, "
            f"worst={worst}:{factors[worst]:.2f})"
        )


__all__ = [
    "RankingResult",
    "RankingWeights",
    "ToolRanker",
]
