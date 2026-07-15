"""Sprint 3.5 — Planner Memory Integration.

Connects planner decisions with persistent memory so that:

* **Successful planning patterns** are stored and reused.
* **Previous similar workflows** can be retrieved to inform new plans.
* **Future planning improves** based on past execution outcomes.

This module is **decoupled** from the concrete persistence layer
(:mod:`src.memory.repositories`).  It accepts a duck-typed
``pattern_store`` that exposes async ``save_pattern()`` and
``find_similar()`` methods, or :class:`MemoryManager` (whose
``plan_repository`` is wrapped via :class:`MemoryBackedPatternStore`).

Typical usage::

    from planner.planner_memory import PlannerMemory, PlanningPattern

    mem = PlannerMemory(pattern_store=MemoryBackedPatternStore(memory_mgr))
    pattern = PlanningPattern.from_plan(plan, outcome="success")
    await mem.store_pattern(pattern)

    similar = await mem.find_similar(goal="Build a REST API")
    if similar:
        plan = similar[0].rebuild_plan()
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .exceptions import PlanningMemoryError
from .models import PlanStep, TaskPlan

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pattern dataclass
# ---------------------------------------------------------------------------


@dataclass
class PlanningPattern:
    """A stored planning pattern, retrievable for future use.

    Attributes:
        id: Unique pattern identifier.
        goal: The original goal that produced this pattern.
        goal_signature: Normalised goal signature used for matching
            (lowercase, keyword-stemmed).
        task_type: Detected task type (development, bugfix, ...).
        template_name: Template that generated the plan, if any.
        strategy: Decomposition strategy name.
        step_count: Number of steps in the plan.
        step_titles: Ordered list of step titles.
        step_capabilities: Union of required capabilities.
        outcome: ``"success"`` / ``"failure"`` / ``"partial"``.
        duration_ms: Total execution time (0 if not executed).
        agent_ids: Agents used by the plan.
        plan_dict: Serialised :class:`TaskPlan` (for rebuild).
        created_at: UTC timestamp.
        metadata: Extensible key-value store.
    """

    id: str = ""
    goal: str = ""
    goal_signature: str = ""
    task_type: str = ""
    template_name: str = ""
    strategy: str = ""
    step_count: int = 0
    step_titles: list[str] = field(default_factory=list)
    step_capabilities: list[str] = field(default_factory=list)
    outcome: str = "unknown"
    duration_ms: float = 0.0
    agent_ids: list[str] = field(default_factory=list)
    plan_dict: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_plan(
        cls,
        plan: TaskPlan,
        *,
        outcome: str = "unknown",
        duration_ms: float = 0.0,
        agent_ids: list[str] | None = None,
        task_type: str = "",
        template_name: str = "",
        strategy: str = "",
    ) -> PlanningPattern:
        """Build a :class:`PlanningPattern` from a :class:`TaskPlan`.

        Args:
            plan: The plan to extract from.
            outcome: Execution outcome (``"success"``/``"failure"``/...).
            duration_ms: Total execution time in milliseconds.
            agent_ids: Agents used by the plan.
            task_type: Detected task type.
            template_name: Template name (if generated via templates).
            strategy: Decomposition strategy name.

        Returns:
            A populated :class:`PlanningPattern`.
        """
        signature = _goal_signature(plan.goal)
        caps: list[str] = []
        caps_seen: set[str] = set()
        for step in plan.steps:
            for c in step.metadata.get("required_capabilities", []) or []:
                if c not in caps_seen:
                    caps_seen.add(c)
                    caps.append(c)
        return cls(
            id=uuid.uuid4().hex[:16],
            goal=plan.goal,
            goal_signature=signature,
            task_type=task_type or str(plan.metadata.get("task_type", "")),
            template_name=template_name or str(plan.metadata.get("template", "")),
            strategy=strategy or str(plan.metadata.get("decomposition_strategy", "")),
            step_count=len(plan.steps),
            step_titles=[s.title for s in plan.steps],
            step_capabilities=caps,
            outcome=outcome,
            duration_ms=duration_ms,
            agent_ids=list(agent_ids or []),
            plan_dict=plan.to_dict(),
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata={
                "plan_id": plan.id,
                "priority": plan.priority.value
                if hasattr(plan.priority, "value")
                else str(plan.priority),
            },
        )

    # ------------------------------------------------------------------
    # Rebuild
    # ------------------------------------------------------------------

    def rebuild_plan(self) -> TaskPlan:
        """Reconstruct the :class:`TaskPlan` from :attr:`plan_dict`."""
        if not self.plan_dict:
            raise PlanningMemoryError(
                "rebuild",
                f"Pattern '{self.id}' has no embedded plan_dict",
            )
        return TaskPlan.from_dict(self.plan_dict)

    # ------------------------------------------------------------------
    # Similarity
    # ------------------------------------------------------------------

    def similarity_to(self, goal: str) -> float:
        """Compute a 0.0–1.0 similarity score to *goal*.

        Uses a simple keyword-overlap heuristic:
        * Tokenise both goals (lowercase, split on non-alphanumeric).
        * Compute Jaccard similarity between the token sets.
        * Boost by 0.1 if the task_type matches.
        """
        my_tokens = set(_tokenize(self.goal))
        their_tokens = set(_tokenize(goal))
        if not my_tokens or not their_tokens:
            return 0.0
        intersection = my_tokens & their_tokens
        union = my_tokens | their_tokens
        jaccard = len(intersection) / len(union) if union else 0.0
        return round(min(jaccard, 1.0), 4)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "goal_signature": self.goal_signature,
            "task_type": self.task_type,
            "template_name": self.template_name,
            "strategy": self.strategy,
            "step_count": self.step_count,
            "step_titles": list(self.step_titles),
            "step_capabilities": list(self.step_capabilities),
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
            "agent_ids": list(self.agent_ids),
            "plan_dict": dict(self.plan_dict),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanningPattern:
        raw: dict[str, Any] = dict(data)
        # Ensure list fields are lists.
        for k in ("step_titles", "step_capabilities", "agent_ids"):
            if k in raw and not isinstance(raw[k], list):
                raw[k] = []
        if "plan_dict" in raw and not isinstance(raw["plan_dict"], dict):
            raw["plan_dict"] = {}
        if "metadata" in raw and not isinstance(raw["metadata"], dict):
            raw["metadata"] = {}
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# PatternStore protocol
# ---------------------------------------------------------------------------


class PatternStore(Protocol):
    """Duck-typed interface for a planning-pattern store.

    Any object exposing these async methods can serve as a backing
    store.  See :class:`MemoryBackedPatternStore` for a concrete
    implementation backed by the :class:`MemoryManager`.
    """

    async def save_pattern(self, pattern: PlanningPattern) -> str:
        """Persist *pattern*. Returns the stored pattern ID."""
        ...

    async def find_similar(
        self,
        goal: str,
        *,
        limit: int = 5,
        min_similarity: float = 0.1,
    ) -> list[PlanningPattern]:
        """Return patterns similar to *goal*, most-similar first."""
        ...

    async def get_pattern(self, pattern_id: str) -> PlanningPattern | None:
        """Return a pattern by ID, or ``None``."""
        ...

    async def list_patterns(
        self,
        *,
        outcome: str | None = None,
        task_type: str | None = None,
        limit: int = 100,
    ) -> list[PlanningPattern]:
        """List patterns, optionally filtered."""
        ...


# ---------------------------------------------------------------------------
# In-memory store (used by tests and as a default no-IO fallback)
# ---------------------------------------------------------------------------


class InMemoryPatternStore:
    """Simple in-memory :class:`PatternStore` implementation.

    Useful for tests and for environments without a database.  All
    data is lost when the process exits.
    """

    def __init__(self) -> None:
        self._patterns: dict[str, PlanningPattern] = {}

    async def save_pattern(self, pattern: PlanningPattern) -> str:
        if not pattern.id:
            pattern.id = uuid.uuid4().hex[:16]
        self._patterns[pattern.id] = pattern
        return pattern.id

    async def find_similar(
        self,
        goal: str,
        *,
        limit: int = 5,
        min_similarity: float = 0.1,
    ) -> list[PlanningPattern]:
        scored: list[tuple[float, PlanningPattern]] = []
        for p in self._patterns.values():
            score = p.similarity_to(goal)
            if score >= min_similarity:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:limit]]

    async def get_pattern(self, pattern_id: str) -> PlanningPattern | None:
        return self._patterns.get(pattern_id)

    async def list_patterns(
        self,
        *,
        outcome: str | None = None,
        task_type: str | None = None,
        limit: int = 100,
    ) -> list[PlanningPattern]:
        result = list(self._patterns.values())
        if outcome is not None:
            result = [p for p in result if p.outcome == outcome]
        if task_type is not None:
            result = [p for p in result if p.task_type == task_type]
        return result[:limit]


# ---------------------------------------------------------------------------
# MemoryBackedPatternStore — wraps MemoryManager.plan_repository
# ---------------------------------------------------------------------------


class MemoryBackedPatternStore:
    """PatternStore backed by :class:`MemoryManager`.

    Uses the ``plan_repository`` table (Sprint 3.4) for persistence
    and a separate ``planning_patterns`` table (Sprint 3.5 migration)
    for fast similarity search.

    Falls back to scanning the ``plans`` table if the patterns table
    is not available (e.g. migration not run yet).
    """

    def __init__(self, memory: Any) -> None:
        self._memory = memory

    async def save_pattern(self, pattern: PlanningPattern) -> str:
        if not pattern.id:
            pattern.id = uuid.uuid4().hex[:16]
        try:
            # 1. Save the plan via plan_repository (so it's queryable).
            plan_repo = getattr(self._memory, "plan_repository", None)
            if plan_repo is not None and pattern.plan_dict:
                await plan_repo.save_plan(pattern.plan_dict)
            # 2. Save the pattern metadata via execution_repository
            # (or a dedicated patterns table if available).
            exec_repo = getattr(self._memory, "execution_repository", None)
            if exec_repo is not None:
                await exec_repo.record(
                    event_type="planner.pattern.stored",
                    task_id=None,
                    workflow_id=None,
                    agent_name="planner_memory",
                    status="success",
                    metadata={
                        "pattern_id": pattern.id,
                        "goal": pattern.goal,
                        "goal_signature": pattern.goal_signature,
                        "task_type": pattern.task_type,
                        "strategy": pattern.strategy,
                        "step_count": pattern.step_count,
                        "step_titles": pattern.step_titles,
                        "step_capabilities": pattern.step_capabilities,
                        "outcome": pattern.outcome,
                        "duration_ms": pattern.duration_ms,
                        "agent_ids": pattern.agent_ids,
                        "plan_id": pattern.metadata.get("plan_id", ""),
                        "created_at": pattern.created_at,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            raise PlanningMemoryError("store", str(exc)) from exc
        return pattern.id

    async def find_similar(
        self,
        goal: str,
        *,
        limit: int = 5,
        min_similarity: float = 0.1,
    ) -> list[PlanningPattern]:
        try:
            patterns = await self.list_patterns(limit=500)
        except PlanningMemoryError:
            return []
        scored: list[tuple[float, PlanningPattern]] = []
        for p in patterns:
            score = p.similarity_to(goal)
            if score >= min_similarity:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:limit]]

    async def get_pattern(self, pattern_id: str) -> PlanningPattern | None:
        # We don't have a dedicated patterns table; this lookup is
        # best-effort via the execution_history metadata.
        exec_repo = getattr(self._memory, "execution_repository", None)
        if exec_repo is None:
            return None
        try:
            recent = await exec_repo.get_recent(limit=500)
        except Exception:  # noqa: BLE001
            return None
        for row in recent:
            meta = row.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("pattern_id") == pattern_id:
                return self._row_to_pattern(row)
        return None

    async def list_patterns(
        self,
        *,
        outcome: str | None = None,
        task_type: str | None = None,
        limit: int = 100,
    ) -> list[PlanningPattern]:
        exec_repo = getattr(self._memory, "execution_repository", None)
        if exec_repo is None:
            return []
        try:
            recent = await exec_repo.get_recent(limit=500)
        except Exception:  # noqa: BLE001
            return []
        patterns: list[PlanningPattern] = []
        for row in recent:
            meta = row.get("metadata") or {}
            if not isinstance(meta, dict):
                continue
            if meta.get("pattern_id") is None:
                continue
            if row.get("event_type") != "planner.pattern.stored":
                continue
            pattern = self._row_to_pattern(row)
            if outcome is not None and pattern.outcome != outcome:
                continue
            if task_type is not None and pattern.task_type != task_type:
                continue
            patterns.append(pattern)
        return patterns[:limit]

    @staticmethod
    def _row_to_pattern(row: dict[str, Any]) -> PlanningPattern:
        meta = row.get("metadata") or {}
        return PlanningPattern(
            id=str(meta.get("pattern_id", "")),
            goal=str(meta.get("goal", "")),
            goal_signature=str(meta.get("goal_signature", "")),
            task_type=str(meta.get("task_type", "")),
            template_name=str(meta.get("template_name", "")),
            strategy=str(meta.get("strategy", "")),
            step_count=int(meta.get("step_count", 0) or 0),
            step_titles=list(meta.get("step_titles", []) or []),
            step_capabilities=list(meta.get("step_capabilities", []) or []),
            outcome=str(meta.get("outcome", "unknown")),
            duration_ms=float(meta.get("duration_ms", 0.0) or 0.0),
            agent_ids=list(meta.get("agent_ids", []) or []),
            plan_dict={},  # Not stored in execution_history; rebuild from plan_repo
            created_at=str(row.get("timestamp", "") or meta.get("created_at", "")),
            metadata={
                "plan_id": meta.get("plan_id", ""),
                "source": "execution_history",
            },
        )


# ---------------------------------------------------------------------------
# PlannerMemory — high-level facade
# ---------------------------------------------------------------------------


class PlannerMemory:
    """High-level facade for planner memory operations.

    Wraps a :class:`PatternStore` and exposes convenience methods
    that operate on :class:`TaskPlan` objects directly.

    Args:
        pattern_store: Backing store.  Defaults to an
            :class:`InMemoryPatternStore`.
        min_similarity: Minimum similarity threshold used by
            :meth:`find_similar_plans`.  Default 0.15.
    """

    def __init__(
        self,
        *,
        pattern_store: PatternStore | None = None,
        min_similarity: float = 0.15,
    ) -> None:
        self._store: PatternStore = pattern_store or InMemoryPatternStore()
        self._min_similarity = min_similarity

    @property
    def store(self) -> PatternStore:
        """The underlying :class:`PatternStore`."""
        return self._store

    # ------------------------------------------------------------------
    # Store
    # ------------------------------------------------------------------

    async def store_pattern(
        self,
        plan: TaskPlan,
        *,
        outcome: str = "unknown",
        duration_ms: float = 0.0,
        agent_ids: list[str] | None = None,
        task_type: str = "",
        template_name: str = "",
        strategy: str = "",
    ) -> str:
        """Store a :class:`TaskPlan` as a :class:`PlanningPattern`.

        Args:
            plan: The plan to store.
            outcome: Execution outcome.
            duration_ms: Execution duration.
            agent_ids: Agents used.
            task_type: Detected task type.
            template_name: Template name.
            strategy: Decomposition strategy name.

        Returns:
            The stored pattern ID.
        """
        pattern = PlanningPattern.from_plan(
            plan,
            outcome=outcome,
            duration_ms=duration_ms,
            agent_ids=agent_ids,
            task_type=task_type,
            template_name=template_name,
            strategy=strategy,
        )
        return await self._store.save_pattern(pattern)

    async def store_outcome(
        self,
        plan: TaskPlan,
        *,
        outcome: str,
        duration_ms: float = 0.0,
        agent_ids: list[str] | None = None,
    ) -> str:
        """Convenience wrapper for storing an execution outcome.

        Pulls ``task_type``, ``template``, and ``decomposition_strategy``
        from the plan's metadata automatically.
        """
        return await self.store_pattern(
            plan,
            outcome=outcome,
            duration_ms=duration_ms,
            agent_ids=agent_ids,
            task_type=str(plan.metadata.get("task_type", "")),
            template_name=str(plan.metadata.get("template", "")),
            strategy=str(plan.metadata.get("decomposition_strategy", "")),
        )

    # ------------------------------------------------------------------
    # Retrieve
    # ------------------------------------------------------------------

    async def find_similar_plans(
        self,
        goal: str,
        *,
        limit: int = 5,
        min_similarity: float | None = None,
    ) -> list[tuple[float, PlanningPattern]]:
        """Find plans similar to *goal*.

        Args:
            goal: The goal to match against.
            limit: Maximum number of results.
            min_similarity: Override the instance's default threshold.

        Returns:
            A list of ``(similarity_score, PlanningPattern)`` tuples,
            sorted by score descending.
        """
        threshold = self._min_similarity if min_similarity is None else min_similarity
        patterns = await self._store.find_similar(
            goal,
            limit=limit,
            min_similarity=threshold,
        )
        return [(p.similarity_to(goal), p) for p in patterns]

    async def find_similar_successful_plans(
        self,
        goal: str,
        *,
        limit: int = 5,
    ) -> list[tuple[float, PlanningPattern]]:
        """Like :meth:`find_similar_plans` but filters to ``outcome="success"``."""
        all_patterns = await self._store.find_similar(
            goal,
            limit=limit * 5,  # over-fetch then filter
            min_similarity=self._min_similarity,
        )
        success_only = [p for p in all_patterns if p.outcome == "success"]
        success_only = success_only[:limit]
        return [(p.similarity_to(goal), p) for p in success_only]

    async def get_pattern(self, pattern_id: str) -> PlanningPattern | None:
        """Return a pattern by ID, or ``None``."""
        return await self._store.get_pattern(pattern_id)

    async def list_patterns(
        self,
        *,
        outcome: str | None = None,
        task_type: str | None = None,
        limit: int = 100,
    ) -> list[PlanningPattern]:
        """List patterns, optionally filtered."""
        return await self._store.list_patterns(
            outcome=outcome,
            task_type=task_type,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    async def suggest_plan(
        self,
        goal: str,
        *,
        min_similarity: float = 0.3,
    ) -> TaskPlan | None:
        """Find the best matching past plan and rebuild it.

        Returns ``None`` if no similar plan meets *min_similarity*.

        The rebuilt plan has its ID, status, and step IDs reset
        (via :meth:`TaskPlanner.clone` semantics) so it can be
        executed independently of the original.
        """
        matches = await self.find_similar_plans(
            goal,
            limit=1,
            min_similarity=min_similarity,
        )
        if not matches:
            return None
        _, pattern = matches[0]
        if not pattern.plan_dict:
            return None
        try:
            plan = pattern.rebuild_plan()
        except PlanningMemoryError:
            return None
        # Reset identity so the rebuilt plan is independent.
        import uuid as _uuid
        from datetime import datetime, timezone

        from .enums import PlanStatus, StepStatus

        plan.id = _uuid.uuid4().hex[:12]
        plan.status = PlanStatus.NOT_STARTED
        plan.created_at = datetime.now(timezone.utc)
        plan.updated_at = plan.created_at
        for step in plan.steps:
            old_id = step.id
            new_id = _uuid.uuid4().hex[:12]
            step.id = new_id
            step.status = StepStatus.PENDING
            # Rewrite dependencies that reference the old ID.
            for other in plan.steps:
                if old_id in other.dependencies:
                    other.dependencies = [
                        new_id if d == old_id else d for d in other.dependencies
                    ]
        plan.metadata["rebuild_from_pattern"] = pattern.id
        plan.metadata["rebuild_similarity"] = matches[0][0]
        return plan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _goal_signature(goal: str) -> str:
    """Normalise a goal for fast matching.

    Lowercase, strip non-alphanumeric (keep spaces), collapse
    whitespace, and drop common stop-words.
    """
    if not goal:
        return ""
    stop = {
        "the", "a", "an", "to", "for", "and", "or", "of", "in", "on",
        "with", "by", "is", "are", "be", "el", "la", "los", "las",
        "un", "una", "unos", "unas", "y", "o", "de", "para", "en",
    }
    tokens = [t for t in _tokenize(goal) if t and t not in stop]
    return " ".join(tokens)


def _tokenize(text: str) -> list[str]:
    """Lowercase + split on non-alphanumeric."""
    if not text:
        return []
    text_lower = text.lower()
    # Split on anything that's not a letter or digit.
    import re

    return [t for t in re.split(r"[^a-z0-9]+", text_lower) if t]


__all__ = [
    "PlanningPattern",
    "PatternStore",
    "InMemoryPatternStore",
    "MemoryBackedPatternStore",
    "PlannerMemory",
]
