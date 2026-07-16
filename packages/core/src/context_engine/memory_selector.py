"""Memory Selector — relevance-based memory selection and scoring.

Sprint 3.9.

Selects only memory entries relevant to a given task. Scores memories
based on keyword overlap, category relevance and recency.

Example::

    selector = MemorySelector()
    memories = selector.select(
        task="Create a new API endpoint",
        available_memories=[
            {"key": "api_patterns", "value": "Use FastAPI routers", "category": "api"},
            {"key": "ui_theme", "value": "Dark mode preferred", "category": "ui"},
        ],
    )
    # memories = [MemoryScore(key="api_patterns", score=0.88, ...), ...]
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import ContextMemory, RelevanceLevel

logger = logging.getLogger(__name__)


@dataclass
class MemoryScore:
    """Scored memory candidate.

    Attributes:
        key:        Memory key.
        value:      Memory value.
        category:   Memory category.
        score:      Relevance score (0.0 - 1.0).
        reasons:    Human-readable list of scoring reasons.
        tokens:     Estimated token count.
    """

    key: str
    value: Any = None
    category: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    tokens: int = 0


# Category-topic mapping for heuristic scoring.
_CATEGORY_TOPICS: dict[str, list[str]] = {
    "api": [
        "api",
        "endpoint",
        "route",
        "rest",
        "graphql",
        "http",
        "request",
        "response",
        "controller",
    ],
    "auth": [
        "auth",
        "login",
        "password",
        "token",
        "session",
        "oauth",
        "jwt",
        "permission",
    ],
    "database": [
        "database",
        "db",
        "model",
        "schema",
        "query",
        "migration",
        "sql",
        "table",
    ],
    "ui": [
        "ui",
        "component",
        "style",
        "css",
        "layout",
        "theme",
        "design",
        "view",
        "page",
    ],
    "config": ["config", "setting", "env", "variable", "deployment", "server"],
    "domain": [
        "business",
        "rule",
        "logic",
        "validation",
        "requirement",
        "specification",
    ],
    "testing": ["test", "spec", "unit", "integration", "coverage", "mock"],
}


def _estimate_tokens(text: str) -> int:
    return max(1, len(str(text)) // 4) if text else 0


def _classify_relevance(score: float) -> RelevanceLevel:
    if score >= 0.8:
        return RelevanceLevel.CRITICAL
    if score >= 0.6:
        return RelevanceLevel.HIGH
    if score >= 0.4:
        return RelevanceLevel.MEDIUM
    if score >= 0.2:
        return RelevanceLevel.LOW
    return RelevanceLevel.NONE


def _parse_timestamp(ts: Any) -> datetime | None:
    """Try to parse various timestamp formats."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
    return None


class MemorySelector:
    """Selects and scores memory entries relevant to a task.

    Scoring signals:

    1. **Keyword match** — task terms found in memory key, value, or category.
    2. **Category relevance** — memory category aligns with task domain.
    3. **Recency** — more recently stored memories get a small bonus.

    Args:
        min_score:    Minimum score to include a memory.
        max_memories: Maximum number of memories (0 = unlimited).
        weights:      Signal weights: keyword, category, recency.
    """

    def __init__(
        self,
        *,
        min_score: float = 0.1,
        max_memories: int = 15,
        weights: dict[str, float] | None = None,
    ) -> None:
        self._min_score = min_score
        self._max_memories = max_memories
        self._weights = {
            "keyword": 0.50,
            "category": 0.35,
            "recency": 0.15,
            **(weights or {}),
        }

    def select(
        self,
        task: str,
        available_memories: list[dict[str, Any]],
    ) -> list[MemoryScore]:
        """Score and rank memories by relevance to the task.

        Args:
            task:                The task description.
            available_memories:  List of dicts with at least 'key' field.
                                 Optional: 'value', 'category', 'created_at'.

        Returns:
            List of ``MemoryScore`` sorted by score (descending).
        """
        task_lower = task.lower()
        task_terms = self._extract_terms(task_lower)

        scored: list[MemoryScore] = []
        for mem in available_memories:
            key = str(mem.get("key", ""))
            value = mem.get("value")
            category = str(mem.get("category", ""))
            created_at = mem.get("created_at")

            score, reasons = self._score_memory(
                key=key,
                value=value,
                category=category,
                task_terms=task_terms,
                task_lower=task_lower,
                created_at=created_at,
            )

            if score >= self._min_score:
                scored.append(
                    MemoryScore(
                        key=key,
                        value=value,
                        category=category,
                        score=round(score, 4),
                        reasons=reasons,
                        tokens=_estimate_tokens(
                            str(value) if value is not None else ""
                        ),
                    )
                )

        scored.sort(key=lambda m: m.score, reverse=True)

        if self._max_memories > 0:
            scored = scored[: self._max_memories]

        logger.debug(
            "MemorySelector: %d/%d memories selected (top=%s)",
            len(scored),
            len(available_memories),
            scored[0].key if scored else "none",
        )
        return scored

    def to_context_memories(self, scores: list[MemoryScore]) -> list[ContextMemory]:
        """Convert MemoryScore list to ContextMemory list."""
        result: list[ContextMemory] = []
        for ms in scores:
            result.append(
                ContextMemory(
                    key=ms.key,
                    value=ms.value,
                    category=ms.category,
                    score=ms.score,
                    relevance=_classify_relevance(ms.score),
                    tokens=ms.tokens,
                    metadata={"reasons": ms.reasons},
                )
            )
        return result

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _extract_terms(self, text: str) -> set[str]:
        terms: set[str] = set()
        for token in re.split(r"[^a-z0-9]+", text):
            if len(token) >= 2:
                terms.add(token)
        return terms

    def _score_memory(
        self,
        key: str,
        value: Any,
        category: str,
        task_terms: set[str],
        task_lower: str,
        created_at: Any = None,
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        key_lower = key.lower()
        value_str = str(value).lower() if value is not None else ""
        cat_lower = category.lower()

        # 1. Keyword match.
        key_hits = sum(1 for t in task_terms if t in key_lower)
        value_hits = sum(1 for t in task_terms if t in value_str)
        total_hits = key_hits + value_hits

        if total_hits > 0:
            kw_score = min(1.0, total_hits / max(1, len(task_terms)))
            score += kw_score * self._weights["keyword"]
            if key_hits:
                reasons.append(f"key_keywords({key_hits})")
            if value_hits:
                reasons.append(f"value_keywords({value_hits})")

        # 2. Category relevance.
        cat_score = self._category_score(cat_lower, task_lower)
        if cat_score > 0:
            score += cat_score * self._weights["category"]
            reasons.append(f"category_{cat_lower}")

        # 3. Recency.
        recency = self._recency_score(created_at)
        if recency > 0:
            score += recency * self._weights["recency"]

        return min(score, 1.0), reasons

    def _category_score(self, cat_lower: str, task_lower: str) -> float:
        """Score based on category-to-task-topic alignment."""
        for cat, topics in _CATEGORY_TOPICS.items():
            if cat not in cat_lower:
                continue
            for topic in topics:
                if topic in task_lower:
                    return 0.8
        return 0.0

    def _recency_score(self, created_at: Any) -> float:
        """Score based on how recently the memory was stored."""
        dt = _parse_timestamp(created_at)
        if dt is None:
            return 0.0
        age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
        if age_seconds < 0:
            return 0.0
        # Exponential decay: 1.0 at 0s, ~0.5 at 3600s, ~0.1 at 86400s.
        import math

        return max(0.0, math.exp(-age_seconds / 7200.0))


__all__ = [
    "MemoryScore",
    "MemorySelector",
]
