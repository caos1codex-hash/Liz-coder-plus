"""Planner.

Sprint 1.7 — Phase 5.

The Planner decomposes a high-level user request into a list of
sub-tasks and decides which agent and tools each sub-task needs.

Today's implementation is **heuristic** (rule-based) so the system
can plan without an LLM. The interface is designed so a future
LLM-backed planner can be dropped in without changing callers.

Inputs:
    - message: the raw user input.
    - context: agent context (history, language, mode).

Outputs:
    - A ``Plan`` containing:
        * subtasks: list of SubTask descriptors (name, description,
          suggested_agent, suggested_tools, dependencies).
        * strategy: short label describing the chosen plan.

The Planner consults:
    - MemoryManager (optional): for historical context.
    - AgentRegistry (optional): for known agent capabilities.
    - ToolRegistry (optional): for known tool capabilities.

Decision records are emitted as ``planner.decision`` events.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.events import PLANNER_DECISION, PLANNER_PLAN_CREATED

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Plan data model
# ----------------------------------------------------------------------


@dataclass
class SubTask:
    """A single sub-task within a plan.

    Attributes:
        name:            Short identifier (unique within the plan).
        description:     What the sub-task should accomplish.
        suggested_agent: Name of the agent that should handle it.
        suggested_tools: List of tool names that may be useful.
        depends_on:      Names of sibling sub-tasks that must complete
                         before this one can start.
    """

    name: str
    description: str = ""
    suggested_agent: str = ""
    suggested_tools: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "suggested_agent": self.suggested_agent,
            "suggested_tools": list(self.suggested_tools),
            "depends_on": list(self.depends_on),
        }


@dataclass
class Plan:
    """A plan: a list of sub-tasks with a strategy label.

    Attributes:
        strategy:  Short label describing the planning strategy
                   (e.g. ``"single"``, ``"research_then_summarize"``).
        subtasks:  Ordered list of SubTask instances.
        rationale: Human-readable explanation of the plan.
    """

    strategy: str
    subtasks: list[SubTask] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "rationale": self.rationale,
            "subtasks": [s.to_dict() for s in self.subtasks],
        }

    def __len__(self) -> int:
        return len(self.subtasks)


# ----------------------------------------------------------------------
# Planner
# ----------------------------------------------------------------------


class Planner:
    """Heuristic planner.

    The planner inspects the message with a small set of regex-based
    rules and produces a ``Plan``. Each rule is a tuple
    ``(predicate, plan_factory)``.

    When no rule matches, the planner falls back to a single-subtask
    plan that lets the default agent handle the message directly.

    Args:
        agent_registry: Optional registry of known agents.
        tool_registry:  Optional registry of known tools.
        memory:         Optional memory manager.
        event_bus:      Optional event bus for decision recording.
    """

    def __init__(
        self,
        *,
        agent_registry: Any | None = None,
        tool_registry: Any | None = None,
        memory: Any | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self._agents = agent_registry
        self._tools = tool_registry
        self._memory = memory
        self._bus = event_bus

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def plan(
        self, message: str, context: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Produce a plan for the given message.

        Returns:
            A list of sub-task descriptor dicts (suitable for the
            WorkflowBuilder). The list always has at least one item.

        Emits ``planner.plan.created`` and one ``planner.decision``
        per sub-task.
        """
        ctx = context or {}
        plan = self._build_plan(message, ctx)

        await self._emit(PLANNER_PLAN_CREATED, {
            "strategy": plan.strategy,
            "rationale": plan.rationale,
            "subtask_count": len(plan),
        })

        for st in plan.subtasks:
            await self._emit(PLANNER_DECISION, {
                "subtask": st.name,
                "suggested_agent": st.suggested_agent,
                "suggested_tools": list(st.suggested_tools),
                "depends_on": list(st.depends_on),
            })

        # Return the list form so callers (e.g. WorkflowBuilder) can
        # consume it directly.
        return [st.to_dict() for st in plan.subtasks]

    async def plan_detailed(
        self, message: str, context: dict[str, Any] | None = None
    ) -> Plan:
        """Like ``plan`` but returns the full ``Plan`` object."""
        ctx = context or {}
        plan = self._build_plan(message, ctx)

        await self._emit(PLANNER_PLAN_CREATED, {
            "strategy": plan.strategy,
            "rationale": plan.rationale,
            "subtask_count": len(plan),
        })
        for st in plan.subtasks:
            await self._emit(PLANNER_DECISION, {
                "subtask": st.name,
                "suggested_agent": st.suggested_agent,
                "suggested_tools": list(st.suggested_tools),
                "depends_on": list(st.depends_on),
            })
        return plan

    # ------------------------------------------------------------------
    # Heuristics
    # ------------------------------------------------------------------

    def _build_plan(self, message: str, context: dict[str, Any]) -> Plan:
        """Apply rules to build a plan.

        The first matching rule wins. Rules are tried in order. If
        none match, the fallback ``"single"`` plan is returned.
        """
        msg_lower = message.lower().strip()

        # Rule 1: file operations.
        if _matches_any(msg_lower, [r"\b(lee|leer|leé)\w*\s+(el\s+)?archivo",
                                     r"\b(escrib\w*|guarda\w*)\s+.+\s+(en\s+)?archivo",
                                     r"\b(list\w*|lista\w*)\s+(los\s+)?archivos"]):
            return self._plan_file_operation(message)

        # Rule 2: terminal / shell commands.
        if _matches_any(msg_lower, [r"\b(ejecut\w*|corr\w*|run)\w*\s+(el\s+)?comando",
                                     r"\b(terminal|shell|bash|cmd|powershell)\b"]):
            return self._plan_terminal_command(message)

        # Rule 3: research / multi-step queries.
        if _matches_any(msg_lower, [r"\b(busca\w*|investiga\w*|analiza\w*)\b.*\b(y|luego|después)\b",
                                     r"\b(investiga\w*).+\b(y\s+resume\w*|y\s+explica\w*)\b"]):
            return self._plan_research(message)

        # Rule 4: system info.
        if _matches_any(msg_lower, [r"\b(información|info|estado)\s+del\s+sistema",
                                     r"\b(qué|cuanta|cuánta)\s+(memoria|cpu|disco)\b"]):
            return self._plan_system_info(message)

        # Fallback: single sub-task, default agent.
        return Plan(
            strategy="single",
            rationale="No specific rule matched; default agent handles the message.",
            subtasks=[
                SubTask(
                    name="handle",
                    description="Handle the user message directly.",
                    suggested_agent=self._default_agent_name(),
                    suggested_tools=[],
                )
            ],
        )

    # ------------------------------------------------------------------
    # Plan factories
    # ------------------------------------------------------------------

    def _plan_file_operation(self, message: str) -> Plan:
        """Plan for file operations: read → (optional) process → write."""
        tools = self._suggest_tools(["file_tool"])
        return Plan(
            strategy="file_operation",
            rationale="Message mentions file operations; planning read/process/write.",
            subtasks=[
                SubTask(
                    name="read",
                    description="Read the requested file.",
                    suggested_agent=self._default_agent_name(),
                    suggested_tools=tools,
                ),
                SubTask(
                    name="process",
                    description="Process the file contents (parse, transform).",
                    suggested_agent=self._default_agent_name(),
                    suggested_tools=[],
                    depends_on=["read"],
                ),
                SubTask(
                    name="respond",
                    description="Compose and return the response to the user.",
                    suggested_agent=self._default_agent_name(),
                    suggested_tools=[],
                    depends_on=["process"],
                ),
            ],
        )

    def _plan_terminal_command(self, message: str) -> Plan:
        """Plan for terminal commands: validate → execute → capture."""
        tools = self._suggest_tools(["terminal_tool"])
        return Plan(
            strategy="terminal_command",
            rationale="Message mentions terminal/shell; planning safe execution.",
            subtasks=[
                SubTask(
                    name="validate",
                    description="Validate the requested command against the allow-list.",
                    suggested_agent=self._default_agent_name(),
                    suggested_tools=[],
                ),
                SubTask(
                    name="execute",
                    description="Execute the command with permission gating.",
                    suggested_agent=self._default_agent_name(),
                    suggested_tools=tools,
                    depends_on=["validate"],
                ),
                SubTask(
                    name="respond",
                    description="Format the command output for the user.",
                    suggested_agent=self._default_agent_name(),
                    suggested_tools=[],
                    depends_on=["execute"],
                ),
            ],
        )

    def _plan_research(self, message: str) -> Plan:
        """Plan for research tasks: gather → analyze → summarize."""
        return Plan(
            strategy="research_then_summarize",
            rationale="Message indicates multi-step research; planning gather/analyze/summarize.",
            subtasks=[
                SubTask(
                    name="gather",
                    description="Collect raw information from memory and tools.",
                    suggested_agent=self._default_agent_name(),
                    suggested_tools=self._suggest_tools(["file_tool", "system_tool"]),
                ),
                SubTask(
                    name="analyze",
                    description="Analyze the gathered information.",
                    suggested_agent=self._default_agent_name(),
                    suggested_tools=[],
                    depends_on=["gather"],
                ),
                SubTask(
                    name="summarize",
                    description="Summarize the analysis for the user.",
                    suggested_agent=self._default_agent_name(),
                    suggested_tools=[],
                    depends_on=["analyze"],
                ),
            ],
        )

    def _plan_system_info(self, message: str) -> Plan:
        """Plan for system info requests."""
        return Plan(
            strategy="system_info",
            rationale="Message asks for system info; planning direct query.",
            subtasks=[
                SubTask(
                    name="query",
                    description="Query the system tool for info.",
                    suggested_agent=self._default_agent_name(),
                    suggested_tools=self._suggest_tools(["system_tool"]),
                ),
                SubTask(
                    name="respond",
                    description="Format the system info for the user.",
                    suggested_agent=self._default_agent_name(),
                    suggested_tools=[],
                    depends_on=["query"],
                ),
            ],
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _default_agent_name(self) -> str:
        """Return the default agent name from the registry, or 'echo'."""
        if self._agents is not None:
            try:
                names = list(self._agents.names()) if hasattr(self._agents, "names") else []
                if names:
                    return names[0]
            except Exception:  # noqa: BLE001
                pass
        return "echo"

    def _suggest_tools(self, candidates: list[str]) -> list[str]:
        """Return the subset of ``candidates`` that exist in the registry."""
        if self._tools is None:
            return list(candidates)
        try:
            known: set[str] = set()
            if hasattr(self._tools, "names"):
                known.update(self._tools.names())
            elif hasattr(self._tools, "enabled_names"):
                known.update(self._tools.enabled_names())
            return [c for c in candidates if c in known] or list(candidates)
        except Exception:  # noqa: BLE001
            return list(candidates)

    async def _emit(self, event_type: str, payload: Any) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit event '%s'", event_type)


# ----------------------------------------------------------------------
# Regex helpers
# ----------------------------------------------------------------------


def _matches_any(text: str, patterns: list[str]) -> bool:
    """Return True if any pattern matches ``text``."""
    return any(re.search(p, text) for p in patterns)


__all__ = [
    "Plan",
    "Planner",
    "SubTask",
]
