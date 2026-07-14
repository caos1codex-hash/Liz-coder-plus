"""TaskDecomposer — splits a high-level objective into subtasks (Sprint 2.8).

The :class:`TaskDecomposer` takes a free-form objective string (e.g.
"Create an AI code editor") and produces a list of
:class:`~planner.task.Task` objects that, when executed in dependency
order, accomplish the objective.

Decomposition strategies:

1. **Templates** — built-in templates for common project types
   (``software_project``, ``research``, ``refactor``, ``bugfix``,
   ``documentation``). Templates define an ordered list of subtasks
   with default capabilities and dependencies.

2. **Keyword detection** — if no template matches, the decomposer
   scans the objective for keywords (e.g. "test", "deploy", "design")
   and emits one subtask per detected keyword.

3. **Single-task fallback** — if no signal is present, the objective
   is wrapped into a single task with ``required_capabilities=['*']``
   so that any agent can pick it up.

The decomposer is **deterministic**: given the same objective and
config, it always produces the same subtasks (modulo id generation).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .models import TaskPriority
from .task import Task
from .utils import new_task_id

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Template
# ----------------------------------------------------------------------


@dataclass
class DecompositionTemplate:
    """A reusable decomposition template.

    Attributes:
        id:          Template identifier (e.g. ``software_project``).
        name:        Human-readable name.
        match_keywords: Keywords that trigger this template.
        subtasks:    Ordered list of subtask specs.
    """

    id: str
    name: str
    match_keywords: list[str]
    subtasks: list[dict[str, Any]]


# ----------------------------------------------------------------------
# Built-in templates
# ----------------------------------------------------------------------


def _builtin_templates() -> list[DecompositionTemplate]:
    """Return the built-in decomposition templates.

    Each subtask spec is a dict with keys:
        title, description, required_capabilities, phase, priority
    """
    return [
        DecompositionTemplate(
            id="software_project",
            name="Software Project",
            match_keywords=[
                "create",
                "build",
                "develop",
                "implement",
                "editor",
                "application",
                "app",
                "system",
                "platform",
                "tool",
            ],
            subtasks=[
                {
                    "title": "Requirements",
                    "description": "Gather and document functional/non-functional requirements.",
                    "required_capabilities": ["plan", "decompose"],
                    "phase": 0,
                    "priority": TaskPriority.HIGH,
                },
                {
                    "title": "Architecture",
                    "description": "Design the system architecture and choose the tech stack.",
                    "required_capabilities": ["plan", "research"],
                    "phase": 1,
                    "priority": TaskPriority.HIGH,
                },
                {
                    "title": "Backend",
                    "description": "Implement backend services, APIs and data layer.",
                    "required_capabilities": ["code.generate", "filesystem.write"],
                    "phase": 2,
                    "priority": TaskPriority.NORMAL,
                },
                {
                    "title": "Frontend",
                    "description": "Implement the frontend UI and integrate with backend.",
                    "required_capabilities": ["code.generate", "filesystem.write"],
                    "phase": 3,
                    "priority": TaskPriority.NORMAL,
                },
                {
                    "title": "AI Integration",
                    "description": "Integrate AI features (LLM, embeddings, retrieval).",
                    "required_capabilities": ["code.generate", "research"],
                    "phase": 4,
                    "priority": TaskPriority.NORMAL,
                },
                {
                    "title": "Testing",
                    "description": "Write and run unit, integration and e2e tests.",
                    "required_capabilities": ["code.test", "terminal.execute"],
                    "phase": 5,
                    "priority": TaskPriority.NORMAL,
                },
                {
                    "title": "Packaging",
                    "description": "Package the application for distribution.",
                    "required_capabilities": ["terminal.execute", "filesystem.write"],
                    "phase": 6,
                    "priority": TaskPriority.LOW,
                },
                {
                    "title": "Documentation",
                    "description": "Write user and developer documentation.",
                    "required_capabilities": ["filesystem.write", "research"],
                    "phase": 7,
                    "priority": TaskPriority.LOW,
                },
            ],
        ),
        DecompositionTemplate(
            id="research",
            name="Research Task",
            match_keywords=["research", "investigate", "analyze", "study", "survey"],
            subtasks=[
                {
                    "title": "Gather",
                    "description": "Gather sources and references.",
                    "required_capabilities": ["research", "web.search"],
                    "phase": 0,
                    "priority": TaskPriority.HIGH,
                },
                {
                    "title": "Analyze",
                    "description": "Analyze and synthesize findings.",
                    "required_capabilities": ["research"],
                    "phase": 1,
                    "priority": TaskPriority.NORMAL,
                },
                {
                    "title": "Summarize",
                    "description": "Produce a summary report.",
                    "required_capabilities": ["filesystem.write"],
                    "phase": 2,
                    "priority": TaskPriority.NORMAL,
                },
            ],
        ),
        DecompositionTemplate(
            id="refactor",
            name="Refactor",
            match_keywords=["refactor", "cleanup", "reorganize", "restructure"],
            subtasks=[
                {
                    "title": "Read",
                    "description": "Read existing code to understand current structure.",
                    "required_capabilities": ["filesystem.read"],
                    "phase": 0,
                    "priority": TaskPriority.HIGH,
                },
                {
                    "title": "Plan",
                    "description": "Plan the refactoring strategy.",
                    "required_capabilities": ["plan"],
                    "phase": 1,
                    "priority": TaskPriority.HIGH,
                },
                {
                    "title": "Refactor",
                    "description": "Apply the refactoring changes.",
                    "required_capabilities": ["code.refactor", "filesystem.write"],
                    "phase": 2,
                    "priority": TaskPriority.NORMAL,
                },
                {
                    "title": "Verify",
                    "description": "Run tests to verify behaviour is preserved.",
                    "required_capabilities": ["code.test", "terminal.execute"],
                    "phase": 3,
                    "priority": TaskPriority.NORMAL,
                },
            ],
        ),
        DecompositionTemplate(
            id="bugfix",
            name="Bug Fix",
            match_keywords=["fix", "bug", "error", "crash", "broken", "fail"],
            subtasks=[
                {
                    "title": "Reproduce",
                    "description": "Reproduce the bug locally.",
                    "required_capabilities": ["terminal.execute", "filesystem.read"],
                    "phase": 0,
                    "priority": TaskPriority.CRITICAL,
                },
                {
                    "title": "Diagnose",
                    "description": "Diagnose root cause.",
                    "required_capabilities": ["research", "filesystem.read"],
                    "phase": 1,
                    "priority": TaskPriority.HIGH,
                },
                {
                    "title": "Fix",
                    "description": "Implement the fix.",
                    "required_capabilities": ["code.fix", "filesystem.write"],
                    "phase": 2,
                    "priority": TaskPriority.HIGH,
                },
                {
                    "title": "Test",
                    "description": "Add regression tests and verify.",
                    "required_capabilities": ["code.test", "terminal.execute"],
                    "phase": 3,
                    "priority": TaskPriority.NORMAL,
                },
            ],
        ),
        DecompositionTemplate(
            id="documentation",
            name="Documentation",
            match_keywords=["document", "docs", "readme", "manual", "guide"],
            subtasks=[
                {
                    "title": "Outline",
                    "description": "Outline documentation structure.",
                    "required_capabilities": ["plan"],
                    "phase": 0,
                    "priority": TaskPriority.NORMAL,
                },
                {
                    "title": "Write",
                    "description": "Write the documentation.",
                    "required_capabilities": ["filesystem.write", "research"],
                    "phase": 1,
                    "priority": TaskPriority.NORMAL,
                },
                {
                    "title": "Review",
                    "description": "Review for accuracy and clarity.",
                    "required_capabilities": ["code.review"],
                    "phase": 2,
                    "priority": TaskPriority.LOW,
                },
            ],
        ),
    ]


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


@dataclass
class DecomposerConfig:
    """Configuration for :class:`TaskDecomposer`.

    Attributes:
        templates:           List of templates to consider. If empty,
                             the built-in templates are used.
        use_builtin_templates: If True (default), prepend the built-in
                             templates to any user-supplied ones.
        fallback_capability: Capability assigned to fallback tasks.
        max_subtasks:        Hard cap on the number of subtasks produced.
    """

    templates: list[DecompositionTemplate] = field(default_factory=list)
    use_builtin_templates: bool = True
    fallback_capability: str = "*"
    max_subtasks: int = 32


# ----------------------------------------------------------------------
# TaskDecomposer
# ----------------------------------------------------------------------


class TaskDecomposer:
    """Decomposes a high-level objective into :class:`Task` objects.

    Usage::

        decomposer = TaskDecomposer()
        subtasks = decomposer.decompose("Create an AI code editor")
        # -> [Task('Requirements'), Task('Architecture'), ...]
    """

    def __init__(self, *, config: DecomposerConfig | None = None) -> None:
        self._config = config or DecomposerConfig()
        templates: list[DecompositionTemplate] = []
        if self._config.use_builtin_templates:
            templates.extend(_builtin_templates())
        templates.extend(self._config.templates)
        self._templates = templates

    @property
    def config(self) -> DecomposerConfig:
        return self._config

    @property
    def templates(self) -> list[DecompositionTemplate]:
        return list(self._templates)

    def register_template(self, template: DecompositionTemplate) -> None:
        """Add a custom template (prepended so it wins over built-ins)."""
        self._templates.insert(0, template)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decompose(
        self,
        objective: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> list[Task]:
        """Decompose ``objective`` into a list of :class:`Task` objects.

        Args:
            objective: The high-level goal (e.g.
                ``"Create an AI code editor"``).
            context:   Optional dict with extra hints. Recognised keys:
                ``template`` (force a template id), ``tags`` (list of
                strings added to every subtask's metadata).

        Returns:
            A list of :class:`Task` objects with stable, sorted ids
            but no dependencies set (use
            :class:`~planner.dependency_analyzer.DependencyAnalyzer`
            to infer deps).
        """
        if not objective or not objective.strip():
            raise ValueError("objective cannot be empty")
        ctx = context or {}
        # 1. Find a matching template.
        template = self._select_template(objective, ctx)
        if template is None:
            # 2. Keyword-based decomposition.
            subtasks = self._decompose_by_keywords(objective)
        else:
            subtasks = self._instantiate_template(template, objective)
        # 3. Fallback: single task.
        if not subtasks:
            subtasks = [self._fallback_task(objective)]
        # 4. Apply context overrides.
        tags = ctx.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        for t in subtasks:
            if tags:
                t.metadata.setdefault("tags", []).extend(tags)
            t.metadata["source_objective"] = objective
            # Only set template_id if not already set by the
            # decomposition method (fallback / keyword / template).
            if "template_id" not in t.metadata:
                t.metadata["template_id"] = template.id if template else "keyword"
        # 5. Cap subtask count.
        if len(subtasks) > self._config.max_subtasks:
            subtasks = subtasks[: self._config.max_subtasks]
        # 6. Sort by phase then title for deterministic output.
        subtasks.sort(
            key=lambda t: (
                int(t.metadata.get("phase", 9999)),
                t.title.lower(),
            )
        )
        return subtasks

    # ------------------------------------------------------------------
    # Template selection
    # ------------------------------------------------------------------

    def _select_template(
        self,
        objective: str,
        ctx: dict[str, Any],
    ) -> DecompositionTemplate | None:
        """Return the best matching template, or None."""
        # Explicit override.
        forced = ctx.get("template")
        if forced:
            for tpl in self._templates:
                if tpl.id == forced:
                    return tpl
        # Keyword match: count hits per template; pick the highest.
        text = objective.lower()
        best: DecompositionTemplate | None = None
        best_hits = 0
        for tpl in self._templates:
            hits = sum(1 for kw in tpl.match_keywords if kw in text)
            if hits > best_hits:
                best_hits = hits
                best = tpl
        return best if best_hits > 0 else None

    # ------------------------------------------------------------------
    # Template instantiation
    # ------------------------------------------------------------------

    def _instantiate_template(
        self,
        template: DecompositionTemplate,
        objective: str,
    ) -> list[Task]:
        """Instantiate the subtasks of ``template`` for ``objective``."""
        tasks: list[Task] = []
        for i, spec in enumerate(template.subtasks):
            title = spec.get("title", f"Step {i + 1}")
            t = Task(
                id=new_task_id("task"),
                title=title,
                description=(f"{spec.get('description', '')} " f"[objective: {objective}]").strip(),
                required_capabilities=list(spec.get("required_capabilities", [])),
                priority=spec.get("priority", TaskPriority.NORMAL),
                metadata={
                    "phase": int(spec.get("phase", i)),
                    "template_step": i,
                    "template_id": template.id,
                },
            )
            tasks.append(t)
        return tasks

    # ------------------------------------------------------------------
    # Keyword-based decomposition
    # ------------------------------------------------------------------

    # Mapping: keyword -> (title, capability, phase).
    _KEYWORD_MAP: list[tuple[str, str, str, int]] = [
        ("design", "Design", "plan", 0),
        ("architect", "Architecture", "plan", 0),
        ("plan", "Plan", "plan", 0),
        ("research", "Research", "research", 1),
        ("investigate", "Investigate", "research", 1),
        ("implement", "Implement", "code.generate", 2),
        ("build", "Build", "code.generate", 2),
        ("develop", "Develop", "code.generate", 2),
        ("write", "Write", "filesystem.write", 2),
        ("refactor", "Refactor", "code.refactor", 2),
        ("test", "Test", "code.test", 3),
        ("verify", "Verify", "code.test", 3),
        ("review", "Review", "code.review", 4),
        ("audit", "Audit", "code.review", 4),
        ("deploy", "Deploy", "terminal.execute", 5),
        ("publish", "Publish", "terminal.execute", 5),
        ("document", "Document", "filesystem.write", 5),
    ]

    def _decompose_by_keywords(self, objective: str) -> list[Task]:
        """Decompose by scanning ``objective`` for known keywords."""
        text = objective.lower()
        tasks: list[Task] = []
        seen_titles: set[str] = set()
        for kw, title, cap, phase in self._KEYWORD_MAP:
            if kw in text and title not in seen_titles:
                t = Task(
                    id=new_task_id("task"),
                    title=title,
                    description=f"{title} phase for: {objective}",
                    required_capabilities=[cap],
                    metadata={
                        "phase": phase,
                        "matched_keyword": kw,
                        "template_id": "keyword",
                    },
                )
                tasks.append(t)
                seen_titles.add(title)
        return tasks

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _fallback_task(self, objective: str) -> Task:
        """Return a single task wrapping the entire objective."""
        return Task(
            id=new_task_id("task"),
            title=objective[:80],
            description=objective,
            required_capabilities=[self._config.fallback_capability],
            metadata={"phase": 0, "template_id": "fallback"},
        )


__all__ = [
    "DecomposerConfig",
    "DecompositionTemplate",
    "TaskDecomposer",
]
