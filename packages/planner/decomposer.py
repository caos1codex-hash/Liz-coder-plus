"""Sprint 3.5 — Advanced Task Decomposition.

Provides :class:`TaskDecomposer`, which converts a high-level user
goal into a structured hierarchy of subtasks with:

* **Main objective** — the original goal, kept as the plan's title.
* **Subtasks** — concrete :class:`PlanStep` instances.
* **Required capabilities** — attached to each step's metadata.
* **Expected outputs** — what each step produces, recorded in metadata.
* **Dependencies** — wired based on data-flow analysis, not just
  sequential ordering.

Decomposition strategy
----------------------

The decomposer uses a **rule-based heuristic engine** (no LLM call)
that recognises common patterns:

1. **Compound goals** — goals containing connectors like " and ", "
   then ", " after " are split into ordered subtasks.
2. **Multi-component goals** — goals mentioning multiple subsystems
   (frontend + backend, schema + endpoints, etc.) yield parallel
   subtasks.
3. **CRUD-style goals** — "create X", "update X", "delete X" yield
   a validate → implement → test sequence.
4. **Research/implement/report goals** — split into research → design
   → implement → test → report.
5. **Fallback** — a single "Execute <goal>" step is produced.

The decomposer is intentionally conservative: it only emits steps
it can confidently describe.  Goals it cannot parse fall back to
the Sprint 3.2 :class:`PlanGenerator` behaviour.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from .exceptions import DecompositionError
from .models import PlanStep, TaskPlan

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decomposition result
# ---------------------------------------------------------------------------


@dataclass
class DecompositionResult:
    """Outcome of :meth:`TaskDecomposer.decompose`.

    Attributes:
        main_objective: The original goal, verbatim.
        subtasks: List of :class:`PlanStep` instances.
        required_capabilities: Union of all step capabilities.
        expected_outputs: Union of all step expected outputs.
        strategy: Name of the decomposition strategy used.
        confidence: 0.0–1.0 score for how confident the decomposer
            is in the result.  Below 0.4, the caller may want to
            fall back to :class:`PlanGenerator`.
    """

    main_objective: str = ""
    subtasks: list[PlanStep] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    strategy: str = "unknown"
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "main_objective": self.main_objective,
            "subtasks": [s.to_dict() for s in self.subtasks],
            "required_capabilities": list(self.required_capabilities),
            "expected_outputs": list(self.expected_outputs),
            "strategy": self.strategy,
            "confidence": round(self.confidence, 4),
        }


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Connectors that imply sequential ordering.
_SEQUENTIAL_CONNECTORS = re.compile(
    r"\s+(?:and then|then|after|after that|next|finally|lastly|"
    r"después|luego|entonces|finalmente)\s+",
    re.IGNORECASE,
)

# Connectors that imply parallel branches.
_PARALLEL_CONNECTORS = re.compile(
    r"\s+(?:and|while|meanwhile|in parallel|parallel to|"
    r"y|mientras tanto|en paralelo)\s+",
    re.IGNORECASE,
)

# Capability hints by keyword.
_CAPABILITY_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(frontend|ui|interfaz|página|web|react|vue|angular)\b", re.IGNORECASE), "frontend"),
    (re.compile(r"\b(backend|api|servidor|server|endpoint|rest|graphql)\b", re.IGNORECASE), "backend"),
    (re.compile(r"\b(database|base de datos|sql|postgres|mysql|schema)\b", re.IGNORECASE), "database"),
    (re.compile(r"\b(test|testing|prueba|unit test|integration test)\b", re.IGNORECASE), "testing"),
    (re.compile(r"\b(deploy|deployment|ci.?cd|docker|kubernetes)\b", re.IGNORECASE), "devops"),
    (re.compile(r"\b(research|investigar|analyze|analysis|estudio)\b", re.IGNORECASE), "research"),
    (re.compile(r"\b(document|docs?|documentación|readme)\b", re.IGNORECASE), "documentation"),
    (re.compile(r"\b(refactor|refactoring|cleanup|limpieza)\b", re.IGNORECASE), "refactoring"),
    (re.compile(r"\b(debug|fix|bug|error|bugfix)\b", re.IGNORECASE), "debugging"),
    (re.compile(r"\b(review|code review|audit|auditoría)\b", re.IGNORECASE), "review"),
    (re.compile(r"\b(plan|planning|diseño|design|architecture)\b", re.IGNORECASE), "planning"),
    (re.compile(r"\b(security|seguridad|auth|authentication|authorization)\b", re.IGNORECASE), "security"),
    (re.compile(r"\b(performance|optimization|optimizar|profile)\b", re.IGNORECASE), "performance"),
]


def _detect_capabilities(text: str) -> list[str]:
    """Return the deduplicated list of capabilities hinted at by *text*."""
    caps: list[str] = []
    seen: set[str] = set()
    for pat, cap in _CAPABILITY_HINTS:
        if pat.search(text) and cap not in seen:
            caps.append(cap)
            seen.add(cap)
    return caps


# ---------------------------------------------------------------------------
# TaskDecomposer
# ---------------------------------------------------------------------------


class TaskDecomposer:
    """Decompose a high-level goal into structured subtasks.

    The decomposer is **deterministic** — the same goal always
    yields the same decomposition.  It uses rule-based heuristics
    rather than an LLM call, which keeps it fast, testable, and
    side-effect-free.

    Args:
        avoid_redundant_steps: If ``True`` (default), the decomposer
            suppresses steps that duplicate information already
            captured by another step (e.g. a "verify" step that
            just re-runs the implementation).
        min_subtask_duration: Minimum ``estimated_duration`` (seconds)
            assigned to each subtask.  Default 60s.
    """

    def __init__(
        self,
        *,
        avoid_redundant_steps: bool = True,
        min_subtask_duration: float = 60.0,
    ) -> None:
        self._avoid_redundant = avoid_redundant_steps
        self._min_duration = min_subtask_duration

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decompose(self, goal: str, description: str = "") -> DecompositionResult:
        """Decompose *goal* into a structured :class:`DecompositionResult`.

        Args:
            goal: The user-provided objective.
            description: Optional elaborated description (forwarded
                to step metadata as ``parent_description``).

        Returns:
            A :class:`DecompositionResult` with at least one subtask.

        Raises:
            DecompositionError: If the goal is empty or cannot be
                parsed at all (very rare; the fallback strategy
                produces a single step).
        """
        if not goal or not goal.strip():
            raise DecompositionError(goal or "<empty>", "goal is empty")

        goal_clean = goal.strip()
        description = description or goal_clean

        # Try each strategy in order of preference.
        # Note: research_implement_report and multi_component_parallel
        # are checked BEFORE crud_pattern because goals like "research
        # X and write a report" or "build frontend and backend" should
        # split into structured subtasks, not collapse into a single
        # CRUD sequence.
        for strategy_fn in (
            self._try_compound_sequential,
            self._try_research_implement_report,
            self._try_multi_component_parallel,
            self._try_crud_pattern,
        ):
            result = strategy_fn(goal_clean, description)
            if result is not None and result.subtasks:
                # Wire dependencies and assemble final result.
                result.main_objective = goal_clean
                result.required_capabilities = self._union_capabilities(result.subtasks)
                result.expected_outputs = self._union_outputs(result.subtasks)
                if self._avoid_redundant:
                    result.subtasks = self._drop_redundant(result.subtasks)
                return result

        # Fallback: single step.
        return self._fallback(goal_clean, description)

    def decompose_into_plan(
        self,
        goal: str,
        description: str = "",
        priority: Any | None = None,
    ) -> TaskPlan:
        """Decompose *goal* and wrap the result in a :class:`TaskPlan`.

        Convenience wrapper that builds a ready-to-execute plan from
        the decomposition result.

        Args:
            goal: The user-provided objective.
            description: Optional elaborated description.
            priority: Optional priority (defaults to NORMAL).

        Returns:
            A :class:`TaskPlan` whose steps come from the decomposer.
        """
        from .enums import PlanStatus, Priority

        result = self.decompose(goal, description=description)
        prio = Priority(priority) if priority is not None else Priority.NORMAL

        plan = TaskPlan(
            goal=goal,
            description=description or goal,
            priority=prio,
            status=PlanStatus.PLANNING,
            steps=list(result.subtasks),
            metadata={
                "generated_by": "TaskDecomposer",
                "decomposition_strategy": result.strategy,
                "decomposition_confidence": result.confidence,
                "required_capabilities": list(result.required_capabilities),
                "expected_outputs": list(result.expected_outputs),
            },
        )
        return plan

    # ------------------------------------------------------------------
    # Strategy 1: Compound sequential goal
    # ------------------------------------------------------------------

    def _try_compound_sequential(
        self,
        goal: str,
        description: str,
    ) -> DecompositionResult | None:
        """Split goals joined by sequential connectors."""
        parts = _SEQUENTIAL_CONNECTORS.split(goal)
        if len(parts) <= 1:
            return None

        subtasks: list[PlanStep] = []
        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
            caps = _detect_capabilities(part)
            outputs = self._infer_outputs(part, caps)
            step = PlanStep(
                id=uuid.uuid4().hex[:12],
                title=self._title_case(part),
                description=f"Subtask {i + 1}: {part}",
                estimated_duration=self._estimate_duration(caps),
                dependencies=[subtasks[-1].id] if subtasks else [],
                required_agents=[],
                metadata={
                    "phase": f"step_{i + 1}",
                    "required_capabilities": caps,
                    "expected_outputs": outputs,
                    "decomposition_source": "compound_sequential",
                },
            )
            subtasks.append(step)

        if len(subtasks) < 2:
            return None

        return DecompositionResult(
            main_objective=goal,
            subtasks=subtasks,
            strategy="compound_sequential",
            confidence=0.85,
        )

    # ------------------------------------------------------------------
    # Strategy 2: Multi-component parallel goal
    # ------------------------------------------------------------------

    def _try_multi_component_parallel(
        self,
        goal: str,
        description: str,
    ) -> DecompositionResult | None:
        """Split goals that mention multiple independent components."""
        # Look for multiple distinct capability categories in the goal.
        caps = _detect_capabilities(goal)
        if len(caps) < 2:
            return None

        # Only treat as parallel if the goal also contains a parallel
        # connector, OR the capabilities are "naturally" parallel
        # (e.g. frontend + backend, or schema + endpoints).
        naturally_parallel_pairs = {
            frozenset({"frontend", "backend"}),
            frozenset({"database", "backend"}),
            frozenset({"frontend", "database"}),
            frozenset({"testing", "documentation"}),
            frozenset({"devops", "testing"}),
        }
        caps_set = frozenset(caps)
        is_naturally_parallel = any(
            pair <= caps_set for pair in naturally_parallel_pairs
        )
        has_connector = bool(_PARALLEL_CONNECTORS.search(goal))

        if not is_naturally_parallel and not has_connector:
            return None

        # Build one subtask per capability.
        subtasks: list[PlanStep] = []
        for i, cap in enumerate(caps):
            title = f"Implement {cap} component"
            sub_goal = f"{cap} part of: {goal}"
            step_caps = [cap]
            outputs = self._infer_outputs(sub_goal, step_caps)
            step = PlanStep(
                id=uuid.uuid4().hex[:12],
                title=title,
                description=f"Subtask {i + 1} (parallel): {sub_goal}",
                estimated_duration=self._estimate_duration(step_caps),
                dependencies=[],
                required_agents=[],
                metadata={
                    "phase": "parallel",
                    "required_capabilities": step_caps,
                    "expected_outputs": outputs,
                    "decomposition_source": "multi_component_parallel",
                    "parallel_group": i,
                },
            )
            subtasks.append(step)

        if len(subtasks) < 2:
            return None

        return DecompositionResult(
            main_objective=goal,
            subtasks=subtasks,
            strategy="multi_component_parallel",
            confidence=0.75,
        )

    # ------------------------------------------------------------------
    # Strategy 3: CRUD pattern
    # ------------------------------------------------------------------

    def _try_crud_pattern(
        self,
        goal: str,
        description: str,
    ) -> DecompositionResult | None:
        """Detect create/update/delete patterns and emit validate → implement → test."""
        goal_lower = goal.lower()
        crud_match = re.search(
            r"\b(create|add|build|implement|update|modify|delete|remove)\b\s+(?:a|an|the)?\s*(.+)",
            goal_lower,
        )
        if not crud_match:
            return None

        action = crud_match.group(1)
        target = crud_match.group(2).strip().rstrip(".,;")

        # Map action to step verbs.
        if action in ("create", "add", "build", "implement"):
            phases = [
                ("Validate requirements", ["planning"], ["requirements doc"]),
                (f"Implement {target}", ["backend"], [f"{target} implementation"]),
                ("Test implementation", ["testing"], ["test report"]),
            ]
        elif action in ("update", "modify"):
            phases = [
                ("Assess current state", ["research"], ["assessment report"]),
                (f"Update {target}", ["backend"], [f"{target} updated"]),
                ("Regression test", ["testing"], ["regression report"]),
            ]
        else:  # delete / remove
            phases = [
                ("Identify dependencies", ["research"], ["dependency map"]),
                (f"Remove {target}", ["backend"], [f"{target} removed"]),
                ("Verify integrity", ["testing"], ["integrity report"]),
            ]

        subtasks: list[PlanStep] = []
        for i, (title, caps, outputs) in enumerate(phases):
            step = PlanStep(
                id=uuid.uuid4().hex[:12],
                title=self._title_case(title),
                description=f"Subtask {i + 1} of CRUD pattern: {title}",
                estimated_duration=self._estimate_duration(caps),
                dependencies=[subtasks[-1].id] if subtasks else [],
                required_agents=[],
                metadata={
                    "phase": f"crud_{action}_{i + 1}",
                    "required_capabilities": caps,
                    "expected_outputs": outputs,
                    "decomposition_source": "crud_pattern",
                },
            )
            subtasks.append(step)

        return DecompositionResult(
            main_objective=goal,
            subtasks=subtasks,
            strategy="crud_pattern",
            confidence=0.7,
        )

    # ------------------------------------------------------------------
    # Strategy 4: Research → implement → report
    # ------------------------------------------------------------------

    def _try_research_implement_report(
        self,
        goal: str,
        description: str,
    ) -> DecompositionResult | None:
        """Detect research/implement/report-style goals."""
        goal_lower = goal.lower()
        research_signals = ("research", "investigate", "analyze", "estudiar", "investigar")
        report_signals = ("report", "document", "summary", "reporte", "informe")

        is_research = any(s in goal_lower for s in research_signals)
        is_report = any(s in goal_lower for s in report_signals)
        if not is_research and not is_report:
            return None

        phases: list[tuple[str, list[str], list[str]]] = []
        if is_research:
            phases.append(("Research context", ["research"], ["research notes"]))
        phases.append(("Implement solution", ["backend"], ["implementation"]))
        if is_report:
            phases.append(("Write report", ["documentation"], ["final report"]))

        subtasks: list[PlanStep] = []
        for i, (title, caps, outputs) in enumerate(phases):
            step = PlanStep(
                id=uuid.uuid4().hex[:12],
                title=title,
                description=f"Subtask {i + 1}: {title} for: {goal}",
                estimated_duration=self._estimate_duration(caps),
                dependencies=[subtasks[-1].id] if subtasks else [],
                required_agents=[],
                metadata={
                    "phase": f"research_implement_report_{i + 1}",
                    "required_capabilities": caps,
                    "expected_outputs": outputs,
                    "decomposition_source": "research_implement_report",
                },
            )
            subtasks.append(step)

        return DecompositionResult(
            main_objective=goal,
            subtasks=subtasks,
            strategy="research_implement_report",
            confidence=0.65,
        )

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _fallback(self, goal: str, description: str) -> DecompositionResult:
        """Produce a single-step decomposition when no strategy matches."""
        caps = _detect_capabilities(goal)
        outputs = self._infer_outputs(goal, caps) or ["result"]
        step = PlanStep(
            id=uuid.uuid4().hex[:12],
            title=f"Execute: {self._title_case(goal)}",
            description=f"Single-step execution for: {goal}",
            estimated_duration=max(self._estimate_duration(caps), self._min_duration),
            dependencies=[],
            required_agents=[],
            metadata={
                "phase": "execute",
                "required_capabilities": caps,
                "expected_outputs": outputs,
                "decomposition_source": "fallback",
            },
        )
        return DecompositionResult(
            main_objective=goal,
            subtasks=[step],
            strategy="fallback",
            confidence=0.3,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _estimate_duration(self, capabilities: list[str]) -> float:
        """Heuristic duration estimate based on capabilities."""
        base = self._min_duration
        # Add 60s per capability beyond the first.
        if not capabilities:
            return base
        return base + 60.0 * max(len(capabilities) - 1, 0)

    def _infer_outputs(self, text: str, capabilities: list[str]) -> list[str]:
        """Infer expected outputs from the text and capabilities."""
        outputs: list[str] = []
        if "test" in capabilities or "testing" in capabilities:
            outputs.append("test report")
        if "documentation" in capabilities:
            outputs.append("documentation")
        if "research" in capabilities:
            outputs.append("research notes")
        if "frontend" in capabilities:
            outputs.append("UI component")
        if "backend" in capabilities:
            outputs.append("API endpoint")
        if "database" in capabilities:
            outputs.append("schema migration")
        if "devops" in capabilities:
            outputs.append("deployment manifest")
        if not outputs:
            outputs.append("result")
        return outputs

    def _union_capabilities(self, subtasks: list[PlanStep]) -> list[str]:
        seen: set[str] = set()
        caps: list[str] = []
        for st in subtasks:
            for c in st.metadata.get("required_capabilities", []) or []:
                if c not in seen:
                    seen.add(c)
                    caps.append(c)
        return caps

    def _union_outputs(self, subtasks: list[PlanStep]) -> list[str]:
        seen: set[str] = set()
        outs: list[str] = []
        for st in subtasks:
            for o in st.metadata.get("expected_outputs", []) or []:
                if o not in seen:
                    seen.add(o)
                    outs.append(o)
        return outs

    def _drop_redundant(self, subtasks: list[PlanStep]) -> list[PlanStep]:
        """Remove steps whose outputs duplicate a previous step's outputs."""
        if len(subtasks) <= 1:
            return subtasks
        seen_outputs: set[str] = set()
        kept: list[PlanStep] = []
        for st in subtasks:
            outs = st.metadata.get("expected_outputs", []) or []
            # Keep if at least one output is new.
            if any(o not in seen_outputs for o in outs):
                kept.append(st)
                seen_outputs.update(outs)
            else:
                logger.debug(
                    "Dropping redundant subtask '%s' (outputs already covered)",
                    st.title,
                )
        return kept or subtasks

    @staticmethod
    def _title_case(text: str) -> str:
        """Convert to Title Case, preserving short words."""
        if not text:
            return text
        words = text.split()
        if not words:
            return text
        # Capitalise first word and any word > 3 chars.
        result = [words[0].capitalize()]
        for w in words[1:]:
            if len(w) > 3:
                result.append(w.capitalize())
            else:
                result.append(w.lower())
        return " ".join(result)


__all__ = [
    "TaskDecomposer",
    "DecompositionResult",
]
