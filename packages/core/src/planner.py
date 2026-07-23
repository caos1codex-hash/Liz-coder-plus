"""Planner.

Sprint 1.7 — Phase 5.
Sprint 6 — Phase 2: LLM-backed planning.

The Planner decomposes a high-level user request into a list of
sub-tasks and decides which agent and tools each sub-task needs.

The planner supports two modes:
  1. **LLM mode** (default when ModelManager is available): Uses the
     LLM to intelligently decompose complex requests into sub-tasks
     with dependencies, agent assignments, and tool suggestions.
     Works with any language, not just Spanish.

  2. **Heuristic mode** (fallback): Rule-based planning using regex
     patterns. Only handles Spanish-language patterns. Used when
     no LLM is available.

Inputs:
    - message: the raw user input.
    - context: agent context (history, language, mode).

Outputs:
    - A ``Plan`` containing:
        * subtasks: list of SubTask descriptors (name, description,
          suggested_agent, suggested_tools, dependencies).
        * strategy: short label describing the chosen plan.

The Planner consults:
    - ModelManager (optional): for LLM-backed planning.
    - MemoryManager (optional): for historical context.
    - AgentRegistry (optional): for known agent capabilities.
    - ToolRegistry (optional): for known tool capabilities.

Decision records are emitted as ``planner.decision`` events.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from src.events import PLANNER_DECISION, PLANNER_PLAN_CREATED

if TYPE_CHECKING:
    from src.llm.manager import ModelManager

logger = logging.getLogger(__name__)

# Pre-compiled regex patterns for rule matching (avoids re-compilation per call).
_FILE_PATTERNS = [re.compile(p) for p in [
    r"\b(lee|leer|le\xe9)\w*\s+(el\s+)?archivo",
    r"\b(escrib\w*|guarda\w*)\s+.+\s+(en\s+)?archivo",
    r"\b(list\w*|lista\w*)\s+(los\s+)?archivos",
]]
_TERMINAL_PATTERNS = [re.compile(p) for p in [
    r"\b(ejecut\w*|corr\w*|run)\w*\s+(el\s+)?comando",
    r"\b(terminal|shell|bash|cmd|powershell)\b",
]]
_RESEARCH_PATTERNS = [re.compile(p) for p in [
    r"\b(busca\w*|investiga\w*|analiza\w*)\b.*\b(y|luego|despu\u00e9s)\b",
    r"\b(investiga\w*).+\b(y\s+resume\w*|y\s+explica\w*)\b",
]]
_SYSTEM_PATTERNS = [re.compile(p) for p in [
    r"\b(informaci\u00f3n|info|estado)\s+del\s+sistema",
    r"\b(qu\u00e9|cuanta|cu\u00e1nta)\s+(memoria|cpu|disco)\b",
]]


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
    """Intelligent planner with LLM and heuristic fallback.

    The planner first attempts LLM-backed planning when a ModelManager
    is available. The LLM decomposes complex requests into sub-tasks with
    dependencies, agent assignments, and tool suggestions.

    Falls back to heuristic (regex-based) planning when no LLM is
    available or when the LLM call fails.

    Args:
        agent_registry: Optional registry of known agents.
        tool_registry:  Optional registry of known tools.
        memory:         Optional memory manager.
        event_bus:      Optional event bus for decision recording.
        model_manager:  Optional ModelManager for LLM-backed planning.
    """

    # Prompt for LLM-based planning.
    _PLANNING_PROMPT = (
        "Eres un planificador de tareas. Descompone la solicitud del "
        "usuario en subtareas ejecutables. Responde SOLO con un JSON "
        "válido con esta estructura exacta (sin markdown, sin backticks):\n"
        "{\n"
        '  "strategy": "nombre_de_estrategia",\n'
        '  "rationale": "explicación breve de un renglón",\n'
        '  "subtasks": [\n'
        '    {\n'
        '      "name": "nombre_unico",\n'
        '      "description": "qué hacer",\n'
        '      "suggested_agent": "llm",\n'
        '      "suggested_tools": [],\n'
        '      "depends_on": []\n'
        '    }\n'
        "  ]\n"
        "}\n\n"
        "Agentes disponibles: llm (conversacional, coding, análisis, "
        "investigación).\n"
        "Herramientas disponibles: terminal, file, system, code_execution, "
        "web_search.\n"
        "Reglas:\n"
        "- Para tareas simples (una sola acción), devuelve UNA subtarea.\n"
        "- Para tareas complejas, divídelas en pasos con depends_on.\n"
        "- Usa suggested_tools cuando sea relevante.\n"
        "- Responde en el mismo idioma que la solicitud del usuario.\n"
    )

    def __init__(
        self,
        *,
        agent_registry: Any | None = None,
        tool_registry: Any | None = None,
        memory: Any | None = None,
        event_bus: Any | None = None,
        model_manager: ModelManager | None = None,
    ) -> None:
        self._agents = agent_registry
        self._tools = tool_registry
        self._memory = memory
        self._bus = event_bus
        self._model_manager = model_manager
        self._use_llm_planning = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def plan(
        self, message: str, context: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Produce a plan for the given message.

        Sprint 6: When a ModelManager is available, uses LLM for
        intelligent planning. Falls back to heuristic rules.

        Returns:
            A list of sub-task descriptor dicts (suitable for the
            WorkflowBuilder). The list always has at least one item.

        Emits ``planner.plan.created`` and one ``planner.decision``
        per sub-task.
        """
        ctx = context or {}

        # Sprint 6: Try LLM-backed planning when available.
        if self._use_llm_planning and self._model_manager is not None:
            try:
                plan = await self._build_plan_llm_async(message, ctx)
                await self._emit_plan_events(plan)
                return [st.to_dict() for st in plan.subtasks]
            except Exception:
                logger.exception(
                    "LLM planning failed; falling back to heuristic rules"
                )

        plan = self._build_plan(message, ctx)
        await self._emit_plan_events(plan)

        return [st.to_dict() for st in plan.subtasks]

    async def _emit_plan_events(self, plan: Plan) -> None:
        """Emit plan creation and decision events."""
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

    async def plan_detailed(
        self, message: str, context: dict[str, Any] | None = None
    ) -> Plan:
        """Like ``plan`` but returns the full ``Plan`` object."""
        ctx = context or {}

        # Sprint 6: Try LLM-backed planning when available.
        if self._use_llm_planning and self._model_manager is not None:
            try:
                plan = await self._build_plan_llm_async(message, ctx)
                await self._emit_plan_events(plan)
                return plan
            except Exception:
                logger.exception(
                    "LLM planning failed; falling back to heuristic rules"
                )

        plan = self._build_plan(message, ctx)
        await self._emit_plan_events(plan)
        return plan

    # ------------------------------------------------------------------
    # Heuristics
    # ------------------------------------------------------------------

    def _build_plan(self, message: str, context: dict[str, Any]) -> Plan:
        """Build a plan using LLM or heuristic fallback.

        Sprint 6: When a ModelManager is available, attempts LLM
        planning via _build_plan_llm_async (scheduled as a future task).
        Falls back to heuristic rules when no LLM is available.
        Note: The actual LLM call is deferred to the async plan() method.
        """
        # Mark that we should try LLM planning in the async plan() call.
        if self._model_manager is not None and self._model_manager.is_initialized:
            self._use_llm_planning = True

        msg_lower = message.lower().strip()

        # Rule 1: file operations (pre-compiled patterns).
        if any(p.search(msg_lower) for p in _FILE_PATTERNS):
            return self._plan_file_operation(message)

        # Rule 2: terminal / shell commands (pre-compiled patterns).
        if any(p.search(msg_lower) for p in _TERMINAL_PATTERNS):
            return self._plan_terminal_command(message)

        # Rule 3: research / multi-step queries (pre-compiled patterns).
        if any(p.search(msg_lower) for p in _RESEARCH_PATTERNS):
            return self._plan_research(message)

        # Rule 4: system info (pre-compiled patterns).
        if any(p.search(msg_lower) for p in _SYSTEM_PATTERNS):
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

    # ------------------------------------------------------------------
    # LLM-backed planning (async)
    # ------------------------------------------------------------------

    async def _build_plan_llm_async(
        self, message: str, context: dict[str, Any]
    ) -> Plan:
        """Use the LLM to decompose the message into a plan.

        Sends the planning prompt + user message to the LLM and
        parses the JSON response into a Plan object.

        Args:
            message: The raw user input.
            context: Agent context.

        Returns:
            A Plan object with subtasks.

        Raises:
            Exception: If the LLM call or JSON parsing fails.
        """
        from src.llm.models import LLMMessage, MessageRole

        messages = [
            LLMMessage(
                role=MessageRole.SYSTEM,
                content=self._PLANNING_PROMPT,
            ),
            LLMMessage(
                role=MessageRole.USER,
                content=f"Solicitud del usuario: {message}",
            ),
        ]

        # Add recent context for better planning.
        history = context.get("history", [])
        if history:
            recent = history[-3:]
            context_summary = "\n".join(
                f"{'Usuario' if h.get('role') == 'user' else 'Asistente'}: "
                f"{h.get('content', '')[:200]}"
                for h in recent
            )
            messages.append(
                LLMMessage(
                    role=MessageRole.USER,
                    content=f"Contexto reciente:\n{context_summary}",
                )
            )

        # Get provider from ModelManager.
        model_id = self._model_manager.default_model or ""
        if not model_id:
            model_id = self._model_manager.select_provider()
        provider = self._model_manager.get_provider(model_id)

        response = await provider.chat(
            messages,
            model=model_id,
            temperature=0.3,
            max_tokens=2048,
        )

        content = response.content.strip()

        # Strip markdown code fences if present.
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        # Parse JSON response.
        data = json.loads(content)

        # Build Plan from parsed data.
        strategy = data.get("strategy", "llm_planned")
        rationale = data.get("rationale", "LLM-generated plan")
        subtasks_data = data.get("subtasks", [])

        subtasks = []
        for st_data in subtasks_data:
            subtasks.append(SubTask(
                name=st_data.get("name", "task"),
                description=st_data.get("description", ""),
                suggested_agent=st_data.get("suggested_agent", "llm"),
                suggested_tools=st_data.get("suggested_tools", []),
                depends_on=st_data.get("depends_on", []),
            ))

        if not subtasks:
            subtasks = [SubTask(
                name="handle",
                description="Handle the user message directly.",
                suggested_agent="llm",
                suggested_tools=[],
            )]

        logger.info(
            "LLM planner produced %d subtask(s) with strategy '%s'",
            len(subtasks), strategy,
        )

        return Plan(
            strategy=strategy,
            rationale=rationale,
            subtasks=subtasks,
        )

    def attach_model_manager(self, model_manager: ModelManager) -> None:
        """Attach a ModelManager for LLM-backed planning."""
        self._model_manager = model_manager
        logger.info("ModelManager attached to Planner for LLM-backed planning")


# ----------------------------------------------------------------------
# Regex helpers
# ----------------------------------------------------------------------


__all__ = [
    "Plan",
    "Planner",
    "SubTask",
]
