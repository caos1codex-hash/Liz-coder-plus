"""Complexity estimator.

Sprint 3.2 — Planner Engine Core.

Classifies a goal into one of four complexity levels based on
heuristic signals: text length, keyword count, action verbs,
and dependency density.  No LLM is used.
"""

from __future__ import annotations

from enum import Enum


class ComplexityLevel(str, Enum):
    """Complexity classification for a planning goal."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Keyword / signal tables
# ---------------------------------------------------------------------------

# Keywords that suggest higher complexity
_HIGH_COMPLEXITY_KEYWORDS: list[str] = [
    "migrate",
    "refactor",
    "redesign",
    "architecture",
    "microservice",
    "distributed",
    "scalable",
    "auth",
    "security",
    "encryption",
    "oauth",
    "integration",
    "pipeline",
    "deploy",
    "production",
    "database",
    "replication",
    "sharding",
    "real-time",
    "websocket",
    "streaming",
    "machine learning",
    "ai",
    "model",
    "training",
]

_CRITICAL_COMPLEXITY_KEYWORDS: list[str] = [
    "rewrite",
    "rebuild",
    "from scratch",
    "multi-system",
    "cross-platform",
    "polyglot",
    "kubernetes",
    "terraform",
    "infrastructure as code",
    "monitoring",
    "observability",
    "chaos engineering",
]

_LOW_COMPLEXITY_KEYWORDS: list[str] = [
    "fix",
    "patch",
    "typo",
    "rename",
    "format",
    "comment",
    "read",
    "check",
    "verify",
    "list",
    "log",
    "print",
    "display",
    "show",
]

# Action verbs that indicate work items
_ACTION_VERBS: list[str] = [
    "create",
    "build",
    "implement",
    "develop",
    "design",
    "add",
    "update",
    "modify",
    "refactor",
    "migrate",
    "test",
    "debug",
    "deploy",
    "configure",
    "install",
    "analyze",
    "research",
    "document",
    "review",
    "optimize",
    "integrate",
    "connect",
    "setup",
    "write",
    "generate",
    "extract",
    "transform",
    "validate",
    "monitor",
    "migrate",
]

# Task-type keywords
_TASK_TYPE_KEYWORDS: dict[str, list[str]] = {
    "development": [
        "create",
        "build",
        "implement",
        "develop",
        "add feature",
        "api",
        "endpoint",
        "page",
        "component",
        "module",
        "class",
        "function",
        "service",
        "web",
        "app",
        "frontend",
        "backend",
    ],
    "documentation": [
        "document the",
        "readme",
        "write docs",
        "write documentation",
        "documentar",
        "documentación",
        "wiki",
        "guide",
        "tutorial",
        "specification",
    ],
    "bugfix": [
        "fix",
        "bug",
        "error",
        "issue",
        "patch",
        "resolve",
        "crash",
        "broken",
        "fail",
        "incorrect",
        "wrong",
    ],
    "research": [
        "research",
        "investigate",
        "explore",
        "analyze",
        "evaluate",
        "compare",
        "benchmark",
        "study",
        "learn",
        "understand",
    ],
    "devops": [
        "deploy",
        "ci",
        "cd",
        "pipeline",
        "docker",
        "container",
        "kubernetes",
        "terraform",
        "monitor",
        "log",
        "infra",
    ],
    "testing": [
        "test",
        "spec",
        "coverage",
        "unit test",
        "integration test",
        "e2e",
        "benchmark",
        "validate",
        "verify",
    ],
}


class ComplexityEstimator:
    """Estimate complexity of a planning goal using rule-based heuristics.

    The estimator produces two outputs:

    1. A :class:`ComplexityLevel` classification.
    2. A numeric score (0.0 – 1.0+) used internally.

    Neither output requires an LLM.
    """

    def __init__(self) -> None:
        self._action_verbs = set(_ACTION_VERBS)
        self._high_kw = [kw.lower() for kw in _HIGH_COMPLEXITY_KEYWORDS]
        self._critical_kw = [kw.lower() for kw in _CRITICAL_COMPLEXITY_KEYWORDS]
        self._low_kw = [kw.lower() for kw in _LOW_COMPLEXITY_KEYWORDS]

    # -- public API -----------------------------------------------------------

    def estimate(self, goal: str) -> ComplexityLevel:
        """Classify *goal* into a :class:`ComplexityLevel`.

        Args:
            goal: The planning goal text.

        Returns:
            One of LOW, NORMAL, HIGH, CRITICAL.
        """
        score = self.score(goal)
        if score >= 0.75:
            return ComplexityLevel.CRITICAL
        if score >= 0.50:
            return ComplexityLevel.HIGH
        if score >= 0.25:
            return ComplexityLevel.NORMAL
        return ComplexityLevel.LOW

    def score(self, goal: str) -> float:
        """Compute a numeric complexity score for *goal*.

        The score combines four signals:

        * **Length factor** (0 – 0.25): longer goals are more complex.
        * **Action density** (0 – 0.25): more action verbs → more work.
        * **Keyword factor** (0 – 0.35): presence of high/critical keywords.
        * **Dependency factor** (0 – 0.15): conjunctions suggesting
          multi-step tasks.

        Returns:
            A float between 0.0 and ~1.25 (can exceed 1.0).
        """
        text = goal.lower().strip()
        if not text:
            return 0.0

        length_factor = self._length_factor(text)
        action_factor = self._action_density(text)
        keyword_factor = self._keyword_factor(text)
        dep_factor = self._dependency_factor(text)

        return length_factor + action_factor + keyword_factor + dep_factor

    def detect_keywords(self, goal: str) -> list[str]:
        """Return all recognised keywords found in *goal*."""
        text = goal.lower()
        found: list[str] = []
        all_keywords = self._high_kw + self._critical_kw + self._low_kw
        for kw in all_keywords:
            if kw in text:
                found.append(kw)
        return sorted(set(found))

    def count_actions(self, goal: str) -> int:
        """Count how many recognised action verbs appear in *goal*."""
        text = goal.lower().split()
        return sum(1 for word in text if word in self._action_verbs)

    def suggest_step_count(self, goal: str) -> int:
        """Suggest a reasonable number of plan steps for *goal*.

        Based on complexity level and action count.
        """
        level = self.estimate(goal)
        actions = self.count_actions(goal)
        base = {
            ComplexityLevel.LOW: 2,
            ComplexityLevel.NORMAL: 4,
            ComplexityLevel.HIGH: 6,
            ComplexityLevel.CRITICAL: 8,
        }
        return min(base[level] + actions, 20)

    def detect_task_type(self, goal: str) -> str:
        """Detect the most likely task type for *goal*.

        Returns the key from :data:`_TASK_TYPE_KEYWORDS` with the most
        matches, or ``"general"`` if none match.
        """
        text = goal.lower()
        best_type = "general"
        best_count = 0
        for task_type, keywords in _TASK_TYPE_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in text)
            if count > best_count:
                best_count = count
                best_type = task_type
        return best_type

    # -- internal signals -----------------------------------------------------

    @staticmethod
    def _length_factor(text: str) -> float:
        """Score 0 – 0.25 based on character length."""
        length = len(text)
        if length <= 20:
            return 0.05
        if length <= 50:
            return 0.10
        if length <= 100:
            return 0.15
        if length <= 200:
            return 0.20
        return 0.25

    def _action_density(self, text: str) -> float:
        """Score 0 – 0.25 based on number of action verbs."""
        words = text.split()
        if not words:
            return 0.0
        count = sum(1 for w in words if w in self._action_verbs)
        ratio = count / len(words)
        return min(ratio * 2.5, 0.25)

    def _keyword_factor(self, text: str) -> float:
        """Score 0 – 0.35 based on keyword presence."""
        critical_hits = sum(1 for kw in self._critical_kw if kw in text)
        high_hits = sum(1 for kw in self._high_kw if kw in text)
        low_hits = sum(1 for kw in self._low_kw if kw in text)

        score = critical_hits * 0.15 + high_hits * 0.07
        score -= low_hits * 0.05  # low keywords reduce complexity
        return max(min(score, 0.35), 0.0)

    @staticmethod
    def _dependency_factor(text: str) -> float:
        """Score 0 – 0.15 based on conjunctions suggesting dependencies."""
        # Conjunctions that suggest multi-step or multi-concern tasks
        conjunctions = [
            "and then",
            "after that",
            "followed by",
            "and also",
            "as well as",
            "plus",
            "also",
            "finally",
            "lastly",
            "first",
            "second",
            "third",
            "next",
            "before",
        ]
        hits = sum(1 for c in conjunctions if c in text)
        # Comma count as a weak signal
        comma_count = text.count(",")
        return min(hits * 0.05 + comma_count * 0.01, 0.15)
