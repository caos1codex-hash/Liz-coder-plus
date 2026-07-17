"""Tool Selector for the Tool Intelligence package.

Sprint 4.1 — Tool Intelligence Foundation.

The ``ToolSelector`` ties together the :class:`CapabilityIndex`, the
:class:`CompatibilityChecker` and the :class:`ToolRanker` to produce
a ranked list of tool candidates for a given user intent, task and
context. It accepts structured Planner output (a list of sub-task
dicts) and returns the best candidates per sub-task or for the
overall intent.

The selector is intentionally synchronous: it operates on static
metadata only. Callers that need to integrate with the async
orchestrator should wrap calls in ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .capability_index import CapabilityIndex, extract_keywords
from .compatibility import CompatibilityChecker, CompatibilityReport
from .exceptions import CapabilityMissing, ToolSelectionError
from .models import (
    RankedCandidate,
    SelectionContext,
    ToolMetadata,
    UserIntent,
)
from .registry_cache import RegistryCache
from .tool_ranker import ToolRanker

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Result types
# ----------------------------------------------------------------------


@dataclass
class SelectionResult:
    """Outcome of a tool selection request.

    Attributes:
        intent:        The user intent used for selection.
        candidates:    Ranked list of compatible tool candidates.
        rejected:      Tools that failed compatibility, with reports.
        duration_ms:   Wall-clock time spent on selection.
        reason:        Human-readable explanation.
    """

    intent: UserIntent
    candidates: list[RankedCandidate] = field(default_factory=list)
    rejected: list[tuple[ToolMetadata, CompatibilityReport]] = field(
        default_factory=list
    )
    duration_ms: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.to_dict(),
            "candidates": [c.to_dict() for c in self.candidates],
            "rejected": [
                {"tool": m.name, "report": r.to_dict()} for m, r in self.rejected
            ],
            "duration_ms": round(self.duration_ms, 3),
            "reason": self.reason,
            "candidate_count": len(self.candidates),
        }

    @property
    def best(self) -> RankedCandidate | None:
        """Return the top candidate, or None if the list is empty."""
        return self.candidates[0] if self.candidates else None


@dataclass
class SubTaskSelection:
    """Selection result for a single planner sub-task.

    Attributes:
        subtask_name:  Name of the sub-task from the Plan.
        candidates:    Ranked candidates for this sub-task.
        reason:        Explanation if no candidates were found.
    """

    subtask_name: str
    candidates: list[RankedCandidate] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_name": self.subtask_name,
            "candidates": [c.to_dict() for c in self.candidates],
            "reason": self.reason,
            "candidate_count": len(self.candidates),
        }


# ----------------------------------------------------------------------
# ToolSelector
# ----------------------------------------------------------------------


class ToolSelector:
    """Select the best tools for a user intent.

    Args:
        index:      CapabilityIndex instance (or RegistryCache — both
                    expose ``get() -> CapabilityIndex``).
        ranker:     ToolRanker instance. Defaults to a new one.
        checker:    CompatibilityChecker instance. Defaults to a new one.
        top_k:      Maximum number of candidates to return. ``None``
                    returns all compatible candidates.
        min_score:  Minimum score for a candidate to be returned.
    """

    def __init__(
        self,
        index: CapabilityIndex | RegistryCache,
        *,
        ranker: ToolRanker | None = None,
        checker: CompatibilityChecker | None = None,
        top_k: int | None = 5,
        min_score: float = 0.0,
    ) -> None:
        self._index_ref = index
        self._ranker = ranker or ToolRanker()
        self._checker = checker or CompatibilityChecker()
        self._top_k = top_k
        self._min_score = max(0.0, min(1.0, min_score))
        # Metrics.
        self._metrics = {
            "selections_total": 0,
            "candidates_returned": 0,
            "no_candidate_count": 0,
            "total_duration_ms": 0.0,
        }

    @property
    def ranker(self) -> ToolRanker:
        return self._ranker

    @property
    def checker(self) -> CompatibilityChecker:
        return self._checker

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_index(self) -> CapabilityIndex:
        """Return the underlying CapabilityIndex.

        Accepts either a raw ``CapabilityIndex`` or a ``RegistryCache``
        (which exposes ``.get()``).
        """
        if isinstance(self._index_ref, RegistryCache):
            return self._index_ref.get()
        return self._index_ref

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select(
        self,
        intent: UserIntent,
        ctx: SelectionContext | None = None,
    ) -> SelectionResult:
        """Select tools for a single user intent.

        Args:
            intent: What the user wants to do.
            ctx:    Runtime context (platform, permissions, history...).

        Returns:
            A :class:`SelectionResult` with ranked candidates.
        """
        start = time.monotonic()
        ctx = ctx or SelectionContext()
        result = SelectionResult(intent=intent)

        try:
            index = self._resolve_index()
        except Exception as exc:  # noqa: BLE001
            raise ToolSelectionError(
                f"Failed to resolve index: {exc}",
                details={"exception": type(exc).__name__},
            ) from exc

        # 1. Find candidates by capability and keyword.
        candidates = self._gather_candidates(index, intent)
        if not candidates:
            result.reason = "no tools matched the requested capabilities/keywords"
            result.duration_ms = (time.monotonic() - start) * 1000.0
            self._record_selection(result)
            return result

        # 2. Filter by risk level.
        candidates = [
            m for m in candidates
            if m.risk_level.numeric() <= intent.max_risk_level.numeric()
        ]
        if not candidates:
            result.reason = (
                f"all candidates exceed max risk level '{intent.max_risk_level.value}'"
            )
            result.duration_ms = (time.monotonic() - start) * 1000.0
            self._record_selection(result)
            return result

        # 3. Filter by cost / latency / parallel constraints from ctx.
        candidates = self._apply_runtime_constraints(candidates, ctx)
        if not candidates:
            result.reason = "all candidates exceed runtime constraints (cost/latency/parallel)"
            result.duration_ms = (time.monotonic() - start) * 1000.0
            self._record_selection(result)
            return result

        # 4. Check compatibility.
        compatible: list[ToolMetadata] = []
        for meta in candidates:
            report = self._checker.check(meta, ctx)
            if report.compatible:
                compatible.append(meta)
            else:
                result.rejected.append((meta, report))
        if not compatible:
            result.reason = (
                f"all {len(candidates)} candidates failed compatibility checks"
            )
            result.duration_ms = (time.monotonic() - start) * 1000.0
            self._record_selection(result)
            return result

        # 5. Rank.
        limit = self._top_k
        ranked = self._ranker.rank(
            compatible, intent=intent, ctx=ctx, limit=limit
        )
        # Apply min_score filter.
        ranked = [c for c in ranked if c.score >= self._min_score]
        result.candidates = ranked
        if ranked:
            best = ranked[0]
            result.reason = (
                f"selected '{best.metadata.name}' "
                f"(score={best.score:.4f}, rank={best.rank})"
            )
        else:
            result.reason = (
                f"all {len(compatible)} compatible candidates scored "
                f"below min_score={self._min_score:.2f}"
            )

        result.duration_ms = (time.monotonic() - start) * 1000.0
        self._record_selection(result)
        return result

    def select_for_plan(
        self,
        plan: list[dict[str, Any]],
        ctx: SelectionContext | None = None,
    ) -> list[SubTaskSelection]:
        """Select tools for each sub-task in a Planner output.

        Args:
            plan: List of sub-task dicts as produced by the Planner
                  (``{"name": ..., "description": ..., "suggested_tools": ...}``).
            ctx:  Runtime context.

        Returns:
            A list of :class:`SubTaskSelection`, one per sub-task.
        """
        ctx = ctx or SelectionContext()
        results: list[SubTaskSelection] = []
        for subtask in plan:
            name = str(subtask.get("name", "subtask"))
            description = str(subtask.get("description", ""))
            suggested_tools = subtask.get("suggested_tools", []) or []
            # Build a UserIntent from the sub-task description and the
            # suggested tool names (used as keywords).
            intent = UserIntent(
                message=description,
                keywords=[str(t) for t in suggested_tools],
            )
            # Try a direct lookup by suggested tool name first.
            direct_matches = self._lookup_by_names(suggested_tools, ctx)
            if direct_matches:
                ranked = self._ranker.rank(
                    direct_matches, intent=intent, ctx=ctx, limit=self._top_k
                )
                ranked = [c for c in ranked if c.score >= self._min_score]
                results.append(
                    SubTaskSelection(
                        subtask_name=name,
                        candidates=ranked,
                        reason=(
                            f"matched {len(direct_matches)} suggested tools by name"
                            if ranked
                            else "suggested tools scored below min_score"
                        ),
                    )
                )
                continue
            # Fall back to a full select().
            selection = self.select(intent, ctx=ctx)
            results.append(
                SubTaskSelection(
                    subtask_name=name,
                    candidates=selection.candidates,
                    reason=selection.reason,
                )
            )
        return results

    def select_required_capability(
        self,
        capability: str,
        ctx: SelectionContext | None = None,
    ) -> RankedCandidate:
        """Select a single tool that provides ``capability``.

        Raises:
            CapabilityMissing: If no compatible tool provides it.
        """
        intent = UserIntent(capabilities=[capability])
        result = self.select(intent, ctx=ctx)
        if not result.candidates:
            available = [m.name for m in self._resolve_index().all()]
            raise CapabilityMissing(
                required_capability=capability,
                available_tools=available,
            )
        return result.candidates[0]

    def metrics(self) -> dict[str, Any]:
        """Return selector metrics."""
        avg = (
            self._metrics["total_duration_ms"] / self._metrics["selections_total"]
            if self._metrics["selections_total"] > 0
            else 0.0
        )
        return {
            **self._metrics,
            "avg_selection_time_ms": round(avg, 3),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _gather_candidates(
        self,
        index: CapabilityIndex,
        intent: UserIntent,
    ) -> list[ToolMetadata]:
        """Collect candidates by capability, keyword and category."""
        # Start from capability matches if the intent declared any.
        candidate_ids: set[str] = set()
        if intent.capabilities:
            for cap in intent.capabilities:
                for meta in index.by_capability(cap):
                    candidate_ids.add(meta.id)
        # Add keyword matches from explicit keywords and from message.
        keywords = list(intent.keywords)
        if intent.message:
            keywords.extend(extract_keywords(intent.message))
        for kw in keywords:
            for meta in index.by_keyword(kw):
                candidate_ids.add(meta.id)
        # Restrict to preferred category if set.
        if intent.preferred_category is not None:
            cat_ids = {
                m.id for m in index.by_category(intent.preferred_category)
            }
            if candidate_ids:
                candidate_ids &= cat_ids
            else:
                candidate_ids = cat_ids
        if not candidate_ids:
            return []
        # Build the result list. ``candidate_ids`` is already a set, so
        # no de-duplication is needed here; we preserve a deterministic
        # order by sorting on the id (stable for testing).
        results: list[ToolMetadata] = []
        for cid in sorted(candidate_ids):
            candidate: ToolMetadata | None = index.get(cid)
            if candidate is not None:
                results.append(candidate)
        return results

    def _apply_runtime_constraints(
        self,
        candidates: list[ToolMetadata],
        ctx: SelectionContext,
    ) -> list[ToolMetadata]:
        """Filter candidates by cost/latency/parallel constraints."""
        out: list[ToolMetadata] = []
        for meta in candidates:
            if meta.estimated_cost > ctx.max_cost:
                continue
            if meta.estimated_latency > ctx.max_latency:
                continue
            if ctx.require_parallel and not meta.supports_parallel:
                continue
            out.append(meta)
        return out

    def _lookup_by_names(
        self,
        names: list[str],
        ctx: SelectionContext,
    ) -> list[ToolMetadata]:
        """Look up tools by name, applying compatibility checks."""
        index = self._resolve_index()
        out: list[ToolMetadata] = []
        for name in names:
            meta = index.get_by_name(name)
            if meta is None:
                continue
            report = self._checker.check(meta, ctx)
            if report.compatible:
                out.append(meta)
        return out

    def _record_selection(self, result: SelectionResult) -> None:
        """Update internal metrics."""
        self._metrics["selections_total"] += 1
        self._metrics["candidates_returned"] += len(result.candidates)
        if not result.candidates:
            self._metrics["no_candidate_count"] += 1
        self._metrics["total_duration_ms"] += result.duration_ms


__all__ = [
    "SelectionResult",
    "SubTaskSelection",
    "ToolSelector",
]
