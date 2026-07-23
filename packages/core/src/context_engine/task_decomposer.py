"""Task Decomposer — intelligent task decomposition.

Sprint 3.9.

Converts high-level user requests into structured, prioritized
sub-task breakdowns. Uses heuristic rules and keyword analysis
to decompose tasks into logical steps.

Example::

    decomposer = TaskDecomposer()
    breakdown = decomposer.decompose("Add login with Google OAuth")
    # breakdown.subtasks = [
    #   SubTaskDecomposed(name="investigate_oauth", priority=HIGH, ...),
    #   SubTaskDecomposed(name="create_config", ...),
    #   SubTaskDecomposed(name="implement_backend", ...),
    #   ...
    # ]
"""

from __future__ import annotations

import logging
from typing import Any

from .models import SubTaskDecomposed, SubTaskPriority, SubTaskRisk, TaskBreakdown

logger = logging.getLogger(__name__)


# Heuristic decomposition rules.
# Each rule maps a pattern to a list of sub-task templates.
_DECOMPOSITION_RULES: list[tuple[list[str], list[dict[str, Any]]]] = [
    (
        # Authentication-related patterns.
        ["login", "auth", "oauth", "registro", "autentic", "sign.up"],
        [
            {
                "name": "investigate_auth",
                "description": "Investigate authentication requirements and existing implementations.",
                "priority": "high",
                "risk": "low",
                "tools": ["file_tool"],
                "agent_suggestion": "research",
            },
            {
                "name": "create_auth_config",
                "description": "Create or update authentication configuration (credentials, providers).",
                "priority": "high",
                "risk": "medium",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["investigate_auth"],
            },
            {
                "name": "implement_auth_backend",
                "description": "Implement authentication backend logic (endpoints, validation, tokens).",
                "priority": "critical",
                "risk": "high",
                "tools": ["file_tool", "terminal_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["create_auth_config"],
            },
            {
                "name": "implement_auth_frontend",
                "description": "Implement authentication frontend (forms, buttons, state management).",
                "priority": "high",
                "risk": "medium",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["implement_auth_backend"],
            },
            {
                "name": "add_auth_tests",
                "description": "Write tests for authentication flow.",
                "priority": "normal",
                "risk": "low",
                "tools": ["file_tool", "terminal_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["implement_auth_backend", "implement_auth_frontend"],
            },
            {
                "name": "update_auth_docs",
                "description": "Update documentation for authentication features.",
                "priority": "low",
                "risk": "low",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["add_auth_tests"],
            },
        ],
    ),
    (
        # API/endpoint patterns.
        ["endpoint", "api", "ruta", "route", "crear.api", "create.api"],
        [
            {
                "name": "investigate_api",
                "description": "Investigate existing API structure and conventions.",
                "priority": "high",
                "risk": "low",
                "tools": ["file_tool"],
                "agent_suggestion": "research",
            },
            {
                "name": "design_endpoint",
                "description": "Design endpoint contract (URL, method, request/response schema).",
                "priority": "high",
                "risk": "medium",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["investigate_api"],
            },
            {
                "name": "implement_endpoint",
                "description": "Implement the endpoint logic.",
                "priority": "critical",
                "risk": "high",
                "tools": ["file_tool", "terminal_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["design_endpoint"],
            },
            {
                "name": "add_endpoint_tests",
                "description": "Write tests for the new endpoint.",
                "priority": "normal",
                "risk": "low",
                "tools": ["file_tool", "terminal_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["implement_endpoint"],
            },
            {
                "name": "update_api_docs",
                "description": "Update API documentation.",
                "priority": "low",
                "risk": "low",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["add_endpoint_tests"],
            },
        ],
    ),
    (
        # Database/schema patterns.
        ["database", "migrat", "schema", "modelo", "model", "tabla", "table"],
        [
            {
                "name": "investigate_schema",
                "description": "Investigate existing database schema and conventions.",
                "priority": "high",
                "risk": "low",
                "tools": ["file_tool"],
                "agent_suggestion": "research",
            },
            {
                "name": "design_schema",
                "description": "Design new or modified database schema.",
                "priority": "high",
                "risk": "high",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["investigate_schema"],
            },
            {
                "name": "create_migration",
                "description": "Create database migration scripts.",
                "priority": "critical",
                "risk": "high",
                "tools": ["file_tool", "terminal_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["design_schema"],
            },
            {
                "name": "update_models",
                "description": "Update application models to match new schema.",
                "priority": "high",
                "risk": "medium",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["create_migration"],
            },
            {
                "name": "add_schema_tests",
                "description": "Write tests for schema changes.",
                "priority": "normal",
                "risk": "low",
                "tools": ["file_tool", "terminal_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["update_models"],
            },
        ],
    ),
    (
        # Bug fix patterns.
        ["fix", "corrige", "error", "bug", "fallo", "problema", "issue"],
        [
            {
                "name": "investigate_bug",
                "description": "Investigate and reproduce the reported bug.",
                "priority": "critical",
                "risk": "low",
                "tools": ["file_tool", "terminal_tool"],
                "agent_suggestion": "research",
            },
            {
                "name": "identify_root_cause",
                "description": "Identify the root cause of the bug.",
                "priority": "critical",
                "risk": "medium",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["investigate_bug"],
            },
            {
                "name": "implement_fix",
                "description": "Implement the bug fix.",
                "priority": "critical",
                "risk": "high",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["identify_root_cause"],
            },
            {
                "name": "verify_fix",
                "description": "Verify the fix resolves the issue.",
                "priority": "high",
                "risk": "medium",
                "tools": ["terminal_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["implement_fix"],
            },
            {
                "name": "add_regression_tests",
                "description": "Add tests to prevent regression.",
                "priority": "high",
                "risk": "low",
                "tools": ["file_tool", "terminal_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["implement_fix"],
            },
        ],
    ),
    (
        # Feature implementation patterns.
        ["feature", "funcionalidad", "agrega", "add", "implementa", "create", "build"],
        [
            {
                "name": "research_feature",
                "description": "Research requirements and existing patterns for the feature.",
                "priority": "high",
                "risk": "low",
                "tools": ["file_tool"],
                "agent_suggestion": "research",
            },
            {
                "name": "design_feature",
                "description": "Design the feature architecture and API.",
                "priority": "high",
                "risk": "medium",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["research_feature"],
            },
            {
                "name": "implement_feature",
                "description": "Implement the core feature logic.",
                "priority": "critical",
                "risk": "high",
                "tools": ["file_tool", "terminal_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["design_feature"],
            },
            {
                "name": "add_feature_tests",
                "description": "Write tests for the feature.",
                "priority": "normal",
                "risk": "low",
                "tools": ["file_tool", "terminal_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["implement_feature"],
            },
            {
                "name": "update_documentation",
                "description": "Update documentation for the feature.",
                "priority": "low",
                "risk": "low",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["add_feature_tests"],
            },
        ],
    ),
    (
        # Refactoring patterns.
        ["refactor", "refactoriza", "mejora", "improve", "optimize", "limpia", "clean"],
        [
            {
                "name": "analyze_code",
                "description": "Analyze the code to identify refactoring opportunities.",
                "priority": "high",
                "risk": "low",
                "tools": ["file_tool"],
                "agent_suggestion": "research",
            },
            {
                "name": "plan_refactoring",
                "description": "Plan the refactoring steps and identify risks.",
                "priority": "high",
                "risk": "medium",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["analyze_code"],
            },
            {
                "name": "apply_refactoring",
                "description": "Apply the refactoring changes.",
                "priority": "critical",
                "risk": "high",
                "tools": ["file_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["plan_refactoring"],
            },
            {
                "name": "run_tests",
                "description": "Run existing tests to verify no regressions.",
                "priority": "high",
                "risk": "medium",
                "tools": ["terminal_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["apply_refactoring"],
            },
            {
                "name": "fix_regressions",
                "description": "Fix any regressions found during testing.",
                "priority": "normal",
                "risk": "medium",
                "tools": ["file_tool", "terminal_tool"],
                "agent_suggestion": "coder",
                "dependencies": ["run_tests"],
            },
        ],
    ),
]


class TaskDecomposer:
    """Decomposes high-level tasks into structured sub-task breakdowns.

    Uses heuristic rules to detect task type and produce an appropriate
    decomposition. When no rule matches, a generic fallback decomposition
    is generated.

    The decomposer is designed so a future LLM-backed version can be
    dropped in without changing the interface.
    """

    def decompose(
        self,
        task: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> TaskBreakdown:
        """Decompose a task into sub-tasks.

        Args:
            task:    The high-level task or user request.
            context: Optional context for additional signals.

        Returns:
            A TaskBreakdown with ordered sub-tasks.
        """
        task_lower = task.lower().strip()
        _ = context or {}  # Reserved for future LLM-backed decomposition.

        # Try to match a decomposition rule.
        for patterns, templates in _DECOMPOSITION_RULES:
            if any(p in task_lower for p in patterns):
                subtasks = self._apply_templates(task, templates)
                logger.info(
                    "TaskDecomposer: decomposed '%s' into %d sub-tasks (strategy=%s)",
                    task[:60],
                    len(subtasks),
                    patterns[0],
                )
                return TaskBreakdown(
                    original_task=task,
                    subtasks=subtasks,
                    strategy=f"heuristic_{patterns[0]}",
                )

        # Fallback: generic decomposition.
        subtasks = self._generic_decomposition(task)
        logger.info(
            "TaskDecomposer: generic decomposition of '%s' into %d sub-tasks",
            task[:60],
            len(subtasks),
        )
        return TaskBreakdown(
            original_task=task,
            subtasks=subtasks,
            strategy="generic",
        )

    def _apply_templates(
        self, task: str, templates: list[dict[str, Any]]
    ) -> list[SubTaskDecomposed]:
        """Convert rule templates into SubTaskDecomposed instances."""
        subtasks: list[SubTaskDecomposed] = []
        for tmpl in templates:
            subtasks.append(
                SubTaskDecomposed(
                    name=tmpl["name"],
                    description=tmpl.get("description", ""),
                    priority=SubTaskPriority(tmpl.get("priority", "normal")),
                    risk=SubTaskRisk(tmpl.get("risk", "medium")),
                    estimated_tokens=len(tmpl.get("description", "")) // 4 * 3,
                    dependencies=list(tmpl.get("dependencies", [])),
                    tools_needed=list(tmpl.get("tools", [])),
                    agent_suggestion=tmpl.get("agent_suggestion", ""),
                )
            )
        return subtasks

    def _generic_decomposition(self, task: str) -> list[SubTaskDecomposed]:
        """Produce a generic 3-step decomposition."""
        return [
            SubTaskDecomposed(
                name="investigate",
                description=f"Investigate requirements for: {task}",
                priority=SubTaskPriority.HIGH,
                risk=SubTaskRisk.LOW,
                estimated_tokens=max(200, len(task) * 3),
                tools_needed=["file_tool"],
                agent_suggestion="research",
            ),
            SubTaskDecomposed(
                name="implement",
                description=f"Implement the requested changes: {task}",
                priority=SubTaskPriority.CRITICAL,
                risk=SubTaskRisk.HIGH,
                estimated_tokens=max(500, len(task) * 5),
                dependencies=["investigate"],
                tools_needed=["file_tool", "terminal_tool"],
                agent_suggestion="coder",
            ),
            SubTaskDecomposed(
                name="verify",
                description=f"Verify and test the implementation: {task}",
                priority=SubTaskPriority.HIGH,
                risk=SubTaskRisk.MEDIUM,
                estimated_tokens=max(300, len(task) * 2),
                dependencies=["implement"],
                tools_needed=["terminal_tool"],
                agent_suggestion="coder",
            ),
        ]


__all__ = [
    "TaskDecomposer",
]
