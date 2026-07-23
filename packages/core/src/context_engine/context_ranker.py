"""Context Ranker — unified scoring and ranking algorithm.

Sprint 3.9.

Provides a configurable ranking pipeline that can re-score and
re-rank items from any context source (files, memories, history).
Supports diversity boosting to avoid returning only items of one type.

The ranking formula::

    score = w_sim * similarity
          + w_dep * dependency_proximity
          + w_rec * recency_signal
          + w_freq * frequency_signal
          + w_div * diversity_boost
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .models import RankableItem, RankedContext

logger = logging.getLogger(__name__)


@dataclass
class RankConfig:
    """Configuration for the ranking algorithm.

    Attributes:
        weight_similarity:     Weight for textual similarity.
        weight_dependency:     Weight for dependency proximity.
        weight_recency:        Weight for recency signal.
        weight_frequency:      Weight for frequency signal.
        weight_diversity:      Weight for diversity boosting.
        diversity_window:      How many top items to check for diversity.
        min_diversity_gap:     Minimum score gap to allow same-category neighbors.
    """

    weight_similarity: float = 0.35
    weight_dependency: float = 0.20
    weight_recency: float = 0.15
    weight_frequency: float = 0.15
    weight_diversity: float = 0.15
    diversity_window: int = 5
    min_diversity_gap: float = 0.05


class ContextRanker:
    """Unified context ranking engine.

    Re-scores and re-ranks a mixed list of ``RankableItem`` instances
    using the configurable formula. Also applies diversity boosting to
    prevent the top-N from being dominated by a single category.

    Args:
        config: Ranking configuration.
    """

    def __init__(self, config: RankConfig | None = None) -> None:
        self._config = config or RankConfig()

    def rank(self, items: list[RankableItem]) -> list[RankableItem]:
        """Rank items by composite score with diversity boosting.

        Args:
            items: List of items to rank (will be mutated in place).

        Returns:
            The same list, sorted by final score (descending).
        """
        if not items:
            return items

        # Apply composite re-scoring.
        for item in items:
            item.score = self._composite_score(item)

        # Sort by score descending.
        items.sort(key=lambda i: i.score, reverse=True)

        # Apply diversity boosting.
        self._apply_diversity_boost(items)

        # Final sort after diversity adjustment.
        items.sort(key=lambda i: i.score, reverse=True)

        logger.debug(
            "ContextRanker: ranked %d items (top=%.4f, bottom=%.4f)",
            len(items),
            items[0].score if items else 0.0,
            items[-1].score if items else 0.0,
        )
        return items

    def rank_context(self, context: RankedContext) -> RankedContext:
        """Re-rank all items within a RankedContext."""
        all_items = context.all_items()
        ranked = self.rank(all_items)

        # Rebuild the RankedContext from ranked items.
        new_context = RankedContext()
        for item in ranked:
            if item.category == "file":
                # Find and update the original ContextFile.
                for f in context.files:
                    if f.path == item.key:
                        new_context.files.append(f)
                        break
            elif item.category == "memory":
                for m in context.memories:
                    if m.key == item.key:
                        new_context.memories.append(m)
                        break
            elif item.category == "history":
                for h in context.history:
                    if f"{h.role}:{h.timestamp[:19]}" == item.key:
                        new_context.history.append(h)
                        break
        return new_context

    def _composite_score(self, item: RankableItem) -> float:
        """Compute the composite score for a single item.

        The base score from the selector is treated as the similarity
        component. Other signals are computed or extracted from metadata.
        """
        cfg = self._config
        similarity = item.score  # Already 0-1 from selector.

        # Dependency proximity: check metadata.
        dep_score = float(item.metadata.get("dependency_score", 0.0))

        # Recency signal: check metadata.
        recency_score = float(item.metadata.get("recency_score", 0.0))

        # Frequency signal: check metadata.
        freq_score = float(item.metadata.get("frequency_score", 0.0))

        composite = (
            cfg.weight_similarity * similarity
            + cfg.weight_dependency * dep_score
            + cfg.weight_recency * recency_score
            + cfg.weight_frequency * freq_score
        )
        return min(composite, 1.0)

    def _apply_diversity_boost(self, items: list[RankableItem]) -> None:
        """Boost items that add category diversity to the top results.

        Walk through the top ``diversity_window`` items. If the next
        candidate is a different category from the previous
        ``min_diversity_gap`` items, give it a small boost.
        """
        cfg = self._config
        window = min(cfg.diversity_window, len(items))

        for i in range(1, window):
            item = items[i]
            # Check how many of the previous items share the same category.
            same_category_count = 0
            for j in range(max(0, i - cfg.diversity_window), i):
                if items[j].category == item.category:
                    same_category_count += 1

            # If too many of the same category precede this item, reduce its score.
            # If this is a diverse category, boost it.
            if same_category_count == 0:
                # No neighbors of same category: small diversity boost.
                boost = cfg.weight_diversity * 0.3
                item.score = min(1.0, item.score + boost)
            elif same_category_count >= 3:
                # Too many neighbors of same category: slight penalty.
                penalty = cfg.weight_diversity * 0.1 * same_category_count
                item.score = max(0.0, item.score - penalty)


__all__ = [
    "ContextRanker",
    "RankConfig",
]
