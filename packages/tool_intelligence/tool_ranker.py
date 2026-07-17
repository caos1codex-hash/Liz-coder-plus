"""Tool Ranker for the Tool Intelligence package.

Sprint 4.1 — Tool Intelligence Foundation.

The ``ToolRanker`` scores and sorts tool candidates according to six
factors:

  1. **Semantic similarity** — keyword overlap between user intent and
     tool metadata.
  2. **Capability match**    — how directly the tool provides the
     requested capabilities.
  3. **Historical success**  — observed success rate from past runs.
  4. **Cost**                — lower declared cost wins.
  5. **Latency**             — lower declared latency wins.
  6. **Reliability**         — declared reliability score.

Each factor is normalized to ``[0.0, 1.0]`` and combined with
configurable weights (see :class:`RankerWeights`). The final score is
also in ``[0.0, 1.0]`` and the breakdown is returned alongside each
ranked candidate for transparency.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .capability_index import extract_keywords
from .exceptions import RankingError
from .models import (
    RankedCandidate,
    SelectionContext,
    ToolHistoryStats,
    ToolMetadata,
    UserIntent,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Weights
# ----------------------------------------------------------------------


@dataclass
class RankerWeights:
    """Weights for the ToolRanker. Normalized before use.

    Defaults favor semantic match and capability match (the most
    important signals), then historical success, then cost/latency.
    """

    w_semantic: float = 0.25
    w_capability: float = 0.25
    w_history: float = 0.20
    w_cost: float = 0.10
    w_latency: float = 0.10
    w_reliability: float = 0.10

    def normalized(self) -> "RankerWeights":
        """Return a copy with weights normalized so they sum to 1.0."""
        total = (
            self.w_semantic
            + self.w_capability
            + self.w_history
            + self.w_cost
            + self.w_latency
            + self.w_reliability
        )
        if total <= 0:
            return RankerWeights()
        return RankerWeights(
            w_semantic=self.w_semantic / total,
            w_capability=self.w_capability / total,
            w_history=self.w_history / total,
            w_cost=self.w_cost / total,
            w_latency=self.w_latency / total,
            w_reliability=self.w_reliability / total,
        )


# ----------------------------------------------------------------------
# Ranker
# ----------------------------------------------------------------------


class ToolRanker:
    """Score and sort tool candidates.

    Usage::

        ranker = ToolRanker()
        ranked = ranker.rank(candidates, intent=intent, ctx=ctx)
        best = ranked[0] if ranked else None
    """

    def __init__(
        self,
        *,
        weights: RankerWeights | None = None,
    ) -> None:
        self._weights = (weights or RankerWeights()).normalized()
        # Metrics.
        self._metrics = {
            "rankings_total": 0,
            "tools_scored": 0,
            "total_duration_ms": 0.0,
            "errors": 0,
        }

    @property
    def weights(self) -> RankerWeights:
        return self._weights

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rank(
        self,
        candidates: list[ToolMetadata],
        *,
        intent: UserIntent | None = None,
        ctx: SelectionContext | None = None,
        limit: int | None = None,
    ) -> list[RankedCandidate]:
        """Score and sort ``candidates``.

        Args:
            candidates: Tools to rank.
            intent:     User intent (drives semantic + capability scoring).
            ctx:        Runtime context (provides historical stats).
            limit:      Optional cap on the returned list size.

        Returns:
            List of :class:`RankedCandidate` sorted by score descending.
        """
        start = time.monotonic()
        if not candidates:
            self._metrics["rankings_total"] += 1
            self._metrics["total_duration_ms"] += (time.monotonic() - start) * 1000.0
            return []

        intent = intent or UserIntent()
        ctx = ctx or SelectionContext()

        # Pre-compute normalization bounds.
        max_cost = max((m.estimated_cost for m in candidates), default=0.0) or 1.0
        max_latency = (
            max((m.estimated_latency for m in candidates), default=0.0) or 1.0
        )

        scored: list[RankedCandidate] = []
        for meta in candidates:
            try:
                score, breakdown = self._score_one(
                    meta, intent, ctx, max_cost, max_latency
                )
            except Exception as exc:  # noqa: BLE001
                self._metrics["errors"] += 1
                logger.exception(
                    "Failed to score tool '%s': %s", meta.name, exc
                )
                raise RankingError(
                    f"Failed to score tool '{meta.name}': {exc}",
                    details={"tool_id": meta.id, "tool_name": meta.name},
                ) from exc
            scored.append(
                RankedCandidate(
                    metadata=meta,
                    score=score,
                    breakdown=breakdown,
                )
            )

        # Sort by score desc, then by name asc (deterministic tiebreaker).
        scored.sort(key=lambda c: (-c.score, c.metadata.name))
        # Assign ranks (1-indexed).
        for i, candidate in enumerate(scored, start=1):
            candidate.rank = i

        if limit is not None and limit > 0:
            scored = scored[:limit]

        self._metrics["rankings_total"] += 1
        self._metrics["tools_scored"] += len(candidates)
        self._metrics["total_duration_ms"] += (time.monotonic() - start) * 1000.0
        return scored

    def score_one(
        self,
        metadata: ToolMetadata,
        *,
        intent: UserIntent | None = None,
        ctx: SelectionContext | None = None,
        max_cost: float = 1.0,
        max_latency: float = 1.0,
    ) -> tuple[float, dict[str, float]]:
        """Score a single tool. Exposed for testing and introspection."""
        intent = intent or UserIntent()
        ctx = ctx or SelectionContext()
        return self._score_one(metadata, intent, ctx, max_cost, max_latency)

    def metrics(self) -> dict[str, Any]:
        """Return ranker metrics."""
        avg = (
            self._metrics["total_duration_ms"] / self._metrics["rankings_total"]
            if self._metrics["rankings_total"] > 0
            else 0.0
        )
        return {
            **self._metrics,
            "avg_ranking_time_ms": round(avg, 3),
        }

    # ------------------------------------------------------------------
    # Scoring internals
    # ------------------------------------------------------------------

    def _score_one(
        self,
        meta: ToolMetadata,
        intent: UserIntent,
        ctx: SelectionContext,
        max_cost: float,
        max_latency: float,
    ) -> tuple[float, dict[str, float]]:
        """Compute the final score and per-factor breakdown."""
        w = self._weights

        semantic = self._semantic_score(meta, intent)
        capability = self._capability_score(meta, intent)
        history = self._history_score(meta, ctx)
        cost = self._cost_score(meta, max_cost)
        latency = self._latency_score(meta, max_latency)
        reliability = self._reliability_score(meta)

        breakdown = {
            "semantic": round(semantic, 4),
            "capability": round(capability, 4),
            "history": round(history, 4),
            "cost": round(cost, 4),
            "latency": round(latency, 4),
            "reliability": round(reliability, 4),
        }
        score = (
            w.w_semantic * semantic
            + w.w_capability * capability
            + w.w_history * history
            + w.w_cost * cost
            + w.w_latency * latency
            + w.w_reliability * reliability
        )
        # Clamp to [0.0, 1.0] to guard against floating-point drift.
        score = max(0.0, min(1.0, score))
        return score, breakdown

    # -- Factor implementations ---------------------------------------

    def _semantic_score(self, meta: ToolMetadata, intent: UserIntent) -> float:
        """Keyword-overlap score in [0.0, 1.0].

        Tokens from the user message and explicit keywords are matched
        against the tool's name, description, aliases, tags and
        keywords. The score is the fraction of intent tokens that
        appear in the tool's vocabulary.
        """
        # Build the intent token set.
        intent_tokens: set[str] = set()
        if intent.message:
            intent_tokens.update(extract_keywords(intent.message))
        intent_tokens.update(t.lower() for t in intent.keywords)
        if not intent_tokens:
            return 0.0

        # Build the tool token set.
        tool_tokens: set[str] = set()
        tool_tokens.update(extract_keywords(meta.name))
        tool_tokens.update(extract_keywords(meta.description))
        tool_tokens.update(a.lower() for a in meta.aliases)
        tool_tokens.update(t.lower() for t in meta.tags)
        tool_tokens.update(k.lower() for k in meta.keywords)

        if not tool_tokens:
            return 0.0
        overlap = intent_tokens & tool_tokens
        return len(overlap) / len(intent_tokens)

    def _capability_score(self, meta: ToolMetadata, intent: UserIntent) -> float:
        """Capability match score in [0.0, 1.0].

        Scoring:
          - 1.0 if the tool provides an exact requested capability.
          - 0.8 if the tool provides a parent capability.
          - 0.6 if the tool provides a sub-capability.
          - 0.0 if the intent has no requested capabilities.
        """
        if not intent.capabilities:
            return 0.0
        best = 0.0
        for requested in intent.capabilities:
            for provided in meta.capabilities:
                if provided == requested:
                    best = max(best, 1.0)
                elif provided.startswith(requested + "."):
                    # Tool provides a sub-capability of the request.
                    best = max(best, 0.6)
                elif requested.startswith(provided + "."):
                    # Tool provides a parent of the request.
                    best = max(best, 0.8)
        return best

    def _history_score(
        self, meta: ToolMetadata, ctx: SelectionContext
    ) -> float:
        """Historical success rate in [0.0, 1.0].

        Falls back to the tool's declared ``reliability`` if no history
        is available.
        """
        stats: ToolHistoryStats | None = ctx.history.get(meta.name)
        if stats is None or stats.executions == 0:
            return meta.reliability
        return stats.success_rate

    def _cost_score(self, meta: ToolMetadata, max_cost: float) -> float:
        """Cost score in [0.0, 1.0] (lower cost → higher score)."""
        if max_cost <= 0:
            return 1.0
        return max(0.0, 1.0 - (meta.estimated_cost / max_cost))

    def _latency_score(self, meta: ToolMetadata, max_latency: float) -> float:
        """Latency score in [0.0, 1.0] (lower latency → higher score)."""
        if max_latency <= 0:
            return 1.0
        return max(0.0, 1.0 - (meta.estimated_latency / max_latency))

    def _reliability_score(self, meta: ToolMetadata) -> float:
        """Declared reliability in [0.0, 1.0]."""
        return float(meta.reliability)


__all__ = ["RankerWeights", "ToolRanker"]
