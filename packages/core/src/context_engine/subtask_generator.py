"""Subtask Generator — automatic sub-task generation with metadata.

Sprint 3.9.

Generates SubTask entries compatible with the Planner's existing
``SubTask`` dataclass. Used by the ContextEngine to produce
decomposed sub-tasks that can be fed directly into the execution
pipeline.

The generator augments the TaskDecomposer output with tools,
agent assignments and token estimates based on the task type and
available project context.

Example::

    gen = SubTaskGenerator(decomposer=TaskDecomposer())
    subtasks = gen.generate(
        task="Add Google OAuth login",
        context={"project_files": ["src/auth/...", ...]},
    )
    # subtasks = [SubTaskMeta(...), SubTaskMeta(...), ...]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .models import SubTaskDecomposed, SubTaskPriority, SubTaskRisk, TaskBreakdown
from .task_decomposer import TaskDecomposer

logger = logging.getLogger(__name__)


@dataclass
class SubTaskMeta:
    """Extended sub-task metadata for execution.

    Attributes:
        name:              Short unique name.
        description:       What this sub-task should accomplish.
        suggested_agent:   Agent that should handle this.
        suggested_tools:   Tools that may be useful.
        depends_on:        Names of sibling sub-tasks that must complete first.
        priority:          Priority level.
        risk:              Risk assessment.
        estimated_tokens:  Token budget estimate for context.
        estimated_duration_sec: Estimated duration in seconds.
        is_parallelizable: Whether this can run in parallel with peers.
        metadata:          Extra metadata.
    """

    name: str
    description: str = ""
    suggested_agent: str = ""
    suggested_tools: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    priority: SubTaskPriority = SubTaskPriority.NORMAL
    risk: SubTaskRisk = SubTaskRisk.MEDIUM
    estimated_tokens: int = 0
    estimated_duration_sec: float = 0.0
    is_parallelizable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_planner_subtask_dict(self) -> dict[str, Any]:
        """Convert to a dict compatible with Planner.SubTask.to_dict() shape."""
        return {
            "name": self.name,
            "description": self.description,
            "suggested_agent": self.suggested_agent,
            "suggested_tools": list(self.suggested_tools),
            "depends_on": list(self.depends_on),
        }


# Agent-type mapping based on sub-task characteristics.
_AGENT_MAPPING: dict[str, str] = {
    "investigate": "research",
    "research": "research",
    "analyze": "research",
    "investigate_auth": "research",
    "investigate_api": "research",
    "investigate_schema": "research",
    "investigate_bug": "research",
    "analyze_code": "research",
    "research_feature": "research",
    "implement": "coder",
    "create": "coder",
    "design": "coder",
    "fix": "coder",
    "add": "coder",
    "update": "coder",
    "apply": "coder",
    "verify": "reviewer",
    "test": "reviewer",
    "run_tests": "reviewer",
    "document": "coder",
    "update_docs": "coder",
}

# Duration estimates by risk and priority.
_DURATION_BASE: dict[str, dict[str, float]] = {
    "high": {"critical": 120.0, "high": 60.0, "normal": 30.0, "low": 15.0},
    "medium": {"critical": 90.0, "high": 45.0, "normal": 20.0, "low": 10.0},
    "low": {"critical": 60.0, "high": 30.0, "normal": 15.0, "low": 5.0},
}


class SubTaskGenerator:
    """Generates rich sub-task metadata from task descriptions.

    Wraps TaskDecomposer and adds execution-relevant metadata:
    agent assignments, tool suggestions, parallelizability
    analysis, and duration estimates.

    Args:
        decomposer: Optional custom TaskDecomposer.
    """

    def __init__(
        self,
        decomposer: TaskDecomposer | None = None,
    ) -> None:
        self._decomposer = decomposer or TaskDecomposer()

    def generate(
        self,
        task: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> list[SubTaskMeta]:
        """Generate sub-tasks for a task.

        Args:
            task:    The high-level task or user request.
            context: Optional project context.

        Returns:
            List of SubTaskMeta in execution order.
        """
        ctx = context or {}

        # Decompose the task.
        breakdown = self._decomposer.decompose(task, context=ctx)

        # Convert to SubTaskMeta with enriched metadata.
        metas: list[SubTaskMeta] = []
        for st in breakdown.subtasks:
            meta = self._enrich(st, ctx)
            meta.is_parallelizable = self._check_parallelizable(st, breakdown)
            metas.append(meta)

        logger.info(
            "SubTaskGenerator: %d sub-tasks for '%s' (parallelizable=%d)",
            len(metas),
            task[:60],
            sum(1 for m in metas if m.is_parallelizable),
        )
        return metas

    def generate_breakdown(
        self,
        task: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> TaskBreakdown:
        """Generate a TaskBreakdown (raw decomposition without enrichment)."""
        return self._decomposer.decompose(task, context=context)

    def to_planner_subtasks(self, metas: list[SubTaskMeta]) -> list[dict[str, Any]]:
        """Convert SubTaskMeta list to Planner-compatible dicts."""
        return [m.to_planner_subtask_dict() for m in metas]

    # ------------------------------------------------------------------
    # Enrichment
    # ------------------------------------------------------------------

    def _enrich(self, st: SubTaskDecomposed, context: dict[str, Any]) -> SubTaskMeta:
        """Add agent, tools, duration, and tokens to a sub-task."""
        # Agent suggestion: prefer explicit, then heuristic.
        agent = st.agent_suggestion or _AGENT_MAPPING.get(st.name, "coder")

        # Tools: from the sub-task or heuristic.
        tools = list(st.tools_needed)
        if not tools:
            tools = self._suggest_tools(st)

        # Duration estimate.
        risk_key = st.risk.value
        prio_key = st.priority.value
        duration = _DURATION_BASE.get(risk_key, {}).get(prio_key, 30.0)

        # Token estimate: from sub-task or heuristic.
        tokens = st.estimated_tokens or max(200, len(st.description) * 3)

        return SubTaskMeta(
            name=st.name,
            description=st.description,
            suggested_agent=agent,
            suggested_tools=tools,
            depends_on=list(st.dependencies),
            priority=st.priority,
            risk=st.risk,
            estimated_tokens=tokens,
            estimated_duration_sec=duration,
            metadata=st.metadata,
        )

    def _suggest_tools(self, st: SubTaskDecomposed) -> list[str]:
        """Suggest tools based on sub-task characteristics."""
        name_lower = st.name.lower()
        desc_lower = st.description.lower()
        tools: list[str] = []

        if any(w in name_lower for w in ("test", "verify", "run")):
            tools.append("terminal_tool")
        if any(w in desc_lower for w in ("file", "implement", "create", "update")):
            tools.append("file_tool")
        if any(w in desc_lower for w in ("execute", "run", "command", "build")):
            tools.append("terminal_tool")
        if any(w in desc_lower for w in ("database", "query", "sql", "migration")):
            tools.append("system_tool")

        return tools or ["file_tool"]

    def _check_parallelizable(
        self, st: SubTaskDecomposed, breakdown: TaskBreakdown
    ) -> bool:
        """Determine if a sub-task can run in parallel.

        A sub-task is parallelizable if:
        - It has no dependencies, OR
        - Its dependencies are on sub-tasks in the same priority band
          and its risk is LOW.
        """
        if not st.dependencies:
            return True

        dep_map = {s.name: s for s in breakdown.subtasks}
        for dep_name in st.dependencies:
            dep = dep_map.get(dep_name)
            if dep is None:
                continue
            # High-risk dependencies block parallelism.
            if dep.risk == SubTaskRisk.HIGH:
                return False
            # Critical priority dependencies block parallelism.
            if dep.priority == SubTaskPriority.CRITICAL:
                return False

        return True


__all__ = [
    "SubTaskGenerator",
    "SubTaskMeta",
]
