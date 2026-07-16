"""History Selector — relevance-based conversation history selection.

Sprint 3.9.

Selects only conversation turns relevant to a given task. Avoids
sending the entire chat history to agents.

Scoring signals:
    1. Keyword overlap with the current task.
    2. Recency — more recent messages get a bonus.
    3. Role relevance — system/assistant messages with relevant content.

Example::

    selector = HistorySelector()
    entries = selector.select(
        task="Fix the login error",
        history=[
            {"role": "user", "content": "Fix the login error", "timestamp": "..."},
            {"role": "assistant", "content": "I found the bug...", "timestamp": "..."},
            {"role": "user", "content": "What is the weather?", "timestamp": "..."},
        ],
    )
    # Returns only login-related messages, not weather.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import ContextHistory, RelevanceLevel

logger = logging.getLogger(__name__)


@dataclass
class HistoryScore:
    """Scored history entry.

    Attributes:
        role:       Message role.
        content:    Message content.
        score:      Relevance score (0.0 - 1.0).
        reasons:    Scoring reasons.
        tokens:     Estimated token count.
        timestamp:  Original message timestamp.
    """

    role: str = "user"
    content: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    tokens: int = 0
    timestamp: str = ""


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


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


class HistorySelector:
    """Selects and scores history entries relevant to a task.

    Scoring signals:

    1. **Keyword match** — task terms found in the message content.
    2. **Recency** — more recent messages get a bonus.
    3. **Position** — the last N messages get a minimum recency bonus
       (ensures some recent context even without keyword matches).

    Args:
        min_score:     Minimum score to include a message.
        max_entries:   Maximum history entries (0 = unlimited).
        recent_window: Number of most-recent messages to always consider.
        weights:       Signal weights: keyword, recency, position.
    """

    def __init__(
        self,
        *,
        min_score: float = 0.05,
        max_entries: int = 30,
        recent_window: int = 5,
        weights: dict[str, float] | None = None,
    ) -> None:
        self._min_score = min_score
        self._max_entries = max_entries
        self._recent_window = recent_window
        self._weights = {
            "keyword": 0.55,
            "recency": 0.30,
            "position": 0.15,
            **(weights or {}),
        }

    def select(
        self,
        task: str,
        history: list[dict[str, Any]],
    ) -> list[HistoryScore]:
        """Score and rank history entries by relevance to the task.

        Args:
            task:    The task description.
            history: List of dicts with 'role', 'content'.
                     Optional: 'timestamp'.

        Returns:
            List of ``HistoryScore`` sorted by score (descending).
        """
        task_lower = task.lower()
        task_terms = self._extract_terms(task_lower)
        total = len(history)

        scored: list[HistoryScore] = []
        for idx, entry in enumerate(history):
            role = str(entry.get("role", "user"))
            content = str(entry.get("content", ""))
            timestamp = str(entry.get("timestamp", ""))

            score, reasons = self._score_entry(
                role=role,
                content=content,
                timestamp=timestamp,
                task_terms=task_terms,
                task_lower=task_lower,
                position=idx,
                total=total,
            )

            if score >= self._min_score:
                scored.append(
                    HistoryScore(
                        role=role,
                        content=content,
                        score=round(score, 4),
                        reasons=reasons,
                        tokens=_estimate_tokens(content),
                        timestamp=timestamp,
                    )
                )

        scored.sort(key=lambda h: h.score, reverse=True)

        if self._max_entries > 0:
            scored = scored[: self._max_entries]

        logger.debug(
            "HistorySelector: %d/%d entries selected for task",
            len(scored),
            total,
        )
        return scored

    def to_context_history(self, scores: list[HistoryScore]) -> list[ContextHistory]:
        """Convert HistoryScore list to ContextHistory list."""
        result: list[ContextHistory] = []
        for hs in scores:
            result.append(
                ContextHistory(
                    role=hs.role,
                    content=hs.content,
                    score=hs.score,
                    relevance=_classify_relevance(hs.score),
                    tokens=hs.tokens,
                    timestamp=hs.timestamp,
                    metadata={"reasons": hs.reasons},
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

    def _score_entry(
        self,
        role: str,
        content: str,
        timestamp: str,
        task_terms: set[str],
        task_lower: str,
        position: int,
        total: int,
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        content_lower = content.lower()

        # 1. Keyword match.
        hits = sum(1 for t in task_terms if t in content_lower)
        if hits > 0:
            kw_score = min(1.0, hits / max(1, len(task_terms)))
            score += kw_score * self._weights["keyword"]
            reasons.append(f"keywords({hits})")

        # 2. Recency based on timestamp.
        recency = self._recency_score(timestamp)
        if recency > 0:
            score += recency * self._weights["recency"]

        # 3. Position bonus: messages near the end of history get a boost.
        pos_score = self._position_score(position, total)
        if pos_score > 0:
            score += pos_score * self._weights["position"]
            if position >= total - self._recent_window:
                reasons.append("recent_window")

        return min(score, 1.0), reasons

    def _recency_score(self, timestamp: str) -> float:
        """Score based on message timestamp."""
        dt = _parse_timestamp(timestamp)
        if dt is None:
            return 0.0
        age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
        if age_seconds < 0:
            return 0.0
        # Exponential decay: 1.0 at 0s, ~0.5 at 1800s, ~0.1 at 7200s.
        return max(0.0, math.exp(-age_seconds / 3600.0))

    def _position_score(self, position: int, total: int) -> float:
        """Score based on position in the history list."""
        if total == 0:
            return 0.0
        # 1.0 for the last message, linearly decreasing to 0.0.
        return (position + 1) / total


__all__ = [
    "HistoryScore",
    "HistorySelector",
]
