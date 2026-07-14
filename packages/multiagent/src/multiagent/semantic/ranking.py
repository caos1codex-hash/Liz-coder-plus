"""Ranking engine — combines multiple signals to rank search results.

Provides configurable multi-signal ranking that combines:
- Semantic similarity (cosine similarity from vector search)
- Priority (from MemoryRecord.priority)
- Recency (based on updated_at timestamp)
- Frequency of use (how often a record appears in results)
- Memory type (configurable type preferences)

The ranking is fully configurable via weights.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from ..memctx.models import MemoryRecord, MemoryType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class RankingSignal(str, Enum):
    """Individual ranking signals that can be weighted."""

    SEMANTIC_SIMILARITY = "semantic_similarity"
    PRIORITY = "priority"
    RECENCY = "recency"
    FREQUENCY = "frequency"
    MEMORY_TYPE = "memory_type"


@dataclass
class RankingConfig:
    """Configuration for the ranking engine.

    Attributes:
        weights: Mapping from RankingSignal to its weight (0.0-1.0).
        type_preferences: Per-type bonus scores (default 0.0 for all).
        recency_decay_hours: Half-life for recency decay in hours.
            Records newer than this get full recency score.
        max_priority: The maximum expected priority value (for normalization).
        max_frequency: The maximum expected frequency count (for normalization).
        min_score_threshold: Minimum final score to include a result.
    """

    weights: Dict[str, float] = field(default_factory=lambda: {
        RankingSignal.SEMANTIC_SIMILARITY.value: 0.5,
        RankingSignal.PRIORITY.value: 0.2,
        RankingSignal.RECENCY.value: 0.15,
        RankingSignal.FREQUENCY.value: 0.1,
        RankingSignal.MEMORY_TYPE.value: 0.05,
    })
    type_preferences: Dict[str, float] = field(default_factory=dict)
    recency_decay_hours: float = 24.0
    max_priority: int = 100
    max_frequency: int = 1000
    min_score_threshold: float = 0.0

    def get_weight(self, signal: RankingSignal) -> float:
        return self.weights.get(signal.value, 0.0)

    def set_weight(self, signal: RankingSignal, weight: float) -> None:
        if not (0.0 <= weight <= 1.0):
            raise ValueError(f"weight must be between 0.0 and 1.0, got {weight}")
        self.weights[signal.value] = weight

    def to_dict(self) -> Dict[str, Any]:
        return {
            "weights": dict(self.weights),
            "type_preferences": dict(self.type_preferences),
            "recency_decay_hours": self.recency_decay_hours,
            "max_priority": self.max_priority,
            "max_frequency": self.max_frequency,
            "min_score_threshold": self.min_score_threshold,
        }


# ---------------------------------------------------------------------------
# Ranked result
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RankedResult:
    """A search result with ranking information.

    Attributes:
        record: The memory record.
        final_score: The final combined ranking score.
        signal_scores: Individual signal scores for transparency.
        semantic_score: Raw semantic similarity score (if available).
        matched_by: List of signals that contributed positively.
    """

    record: MemoryRecord
    final_score: float = 0.0
    signal_scores: Dict[str, float] = field(default_factory=dict)
    semantic_score: float = 0.0
    matched_by: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.record.id,
            "final_score": round(self.final_score, 4),
            "signal_scores": {k: round(v, 4) for k, v in self.signal_scores.items()},
            "semantic_score": round(self.semantic_score, 4),
            "matched_by": self.matched_by,
            "memory_type": self.record.memory_type.value,
            "owner": self.record.owner,
        }


# ---------------------------------------------------------------------------
# Ranking engine
# ---------------------------------------------------------------------------


class RankingEngine:
    """Combines multiple signals to rank search results.

    Usage::

        engine = RankingEngine(config)
        ranked = engine.rank(
            records=records,
            semantic_scores={"id1": 0.95, "id2": 0.80},
            use_counts={"id1": 5, "id2": 2},
        )
    """

    def __init__(self, config: Optional[RankingConfig] = None) -> None:
        self._config = config or RankingConfig()

    @property
    def config(self) -> RankingConfig:
        return self._config

    def rank(
        self,
        records: List[MemoryRecord],
        semantic_scores: Optional[Dict[str, float]] = None,
        use_counts: Optional[Dict[str, int]] = None,
    ) -> List[RankedResult]:
        """Rank a list of memory records using all configured signals.

        Args:
            records: The records to rank.
            semantic_scores: Optional mapping of record id -> semantic similarity.
            use_counts: Optional mapping of record id -> access frequency.

        Returns:
            List of RankedResult sorted by final_score descending.
        """
        semantic_scores = semantic_scores or {}
        use_counts = use_counts or {}

        results: List[RankedResult] = []
        for record in records:
            scores: Dict[str, float] = {}
            matched: List[str] = []

            # 1. Semantic similarity (0.0-1.0, already normalized)
            sem_score = semantic_scores.get(record.id, 0.0)
            scores[RankingSignal.SEMANTIC_SIMILARITY.value] = sem_score
            if sem_score > 0:
                matched.append(f"semantic:{sem_score:.2f}")

            # 2. Priority (normalized to 0.0-1.0)
            priority_norm = min(record.priority / self._config.max_priority, 1.0) if self._config.max_priority > 0 else 0.0
            scores[RankingSignal.PRIORITY.value] = priority_norm
            if record.priority > 0:
                matched.append(f"priority:{record.priority}")

            # 3. Recency (exponential decay from updated_at)
            recency_score = self._compute_recency(record.updated_at)
            scores[RankingSignal.RECENCY.value] = recency_score
            if recency_score > 0.5:
                matched.append("recent")

            # 4. Frequency (normalized access count)
            freq = use_counts.get(record.id, 0)
            freq_norm = min(freq / self._config.max_frequency, 1.0) if self._config.max_frequency > 0 else 0.0
            scores[RankingSignal.FREQUENCY.value] = freq_norm
            if freq > 0:
                matched.append(f"freq:{freq}")

            # 5. Memory type preference
            type_pref = self._config.type_preferences.get(record.memory_type.value, 0.0)
            scores[RankingSignal.MEMORY_TYPE.value] = type_pref
            if type_pref > 0:
                matched.append(f"type:{record.memory_type.value}")

            # Compute weighted final score
            final = 0.0
            total_weight = 0.0
            for signal, weight in self._config.weights.items():
                signal_val = scores.get(signal, 0.0)
                final += signal_val * weight
                total_weight += weight

            # Normalize by total weight if needed
            if total_weight > 0:
                final = final / total_weight

            results.append(
                RankedResult(
                    record=record,
                    final_score=final,
                    signal_scores=scores,
                    semantic_score=sem_score,
                    matched_by=matched,
                )
            )

        # Filter by minimum threshold
        if self._config.min_score_threshold > 0:
            results = [
                r for r in results
                if r.final_score >= self._config.min_score_threshold
            ]

        # Sort by final score descending
        results.sort(key=lambda r: r.final_score, reverse=True)
        return results

    def _compute_recency(self, updated_at: str) -> float:
        """Compute recency score using exponential decay.

        Returns 1.0 for very recent records, decaying toward 0.0
        for older records. Uses the configured decay half-life.
        """
        try:
            updated = datetime.fromisoformat(updated_at)
            now = datetime.now(timezone.utc)
            age_hours = (now - updated).total_seconds() / 3600.0
            if age_hours <= 0:
                return 1.0
            # Exponential decay: score = e^(-ln(2) * age / half_life)
            decay = math.exp(-0.693147 * age_hours / self._config.recency_decay_hours)
            return max(0.0, min(1.0, decay))
        except (ValueError, TypeError):
            return 0.5  # Default middle value for unparseable dates