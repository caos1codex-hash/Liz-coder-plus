"""Prompt Pipeline.

Centralizes all prompt generation for Liz Coder Plus. Ensures that
every LLM request goes through the same construction process,
eliminating scattered prompt-building code.

Pipeline stages:
    1. System Prompt       — base personality and instructions
    2. Memory              — retrieved context from Memory
    3. Conversation        — history from SessionManager
    4. Planner             — task decomposition from Planner
    5. Task                — current task context
    6. Workflow            — workflow step context
    7. Tool Results        — results from tool executions
    8. User Message        — the current user input
    9. Token Budget        — final trimming via ContextManager

Sprint 1.9 — Phase 5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.llm.context.manager import ContextConfig, ContextManager, ContextResult
from src.llm.models import LLMMessage, MessageRole

logger = logging.getLogger(__name__)


# ======================================================================
# Default System Prompt
# ======================================================================

DEFAULT_SYSTEM_PROMPT = (
    "Eres Liz, una asistente de programación inteligente y amigable. "
    "Tu interfaz está en español. Ayudas al usuario con tareas de "
    "desarrollo de software, incluyendo escritura de código, depuración, "
    "explicación de conceptos, y ejecución de herramientas. "
    "Sigue las instrucciones del usuario de forma precisa. "
    "Si necesitas ejecutar una acción que podría tener consecuencias, "
    "solicita confirmación antes de proceder."
)


# ======================================================================
# Pipeline Configuration
# ======================================================================


@dataclass
class PromptPipelineConfig:
    """Configuration for the PromptPipeline.

    Attributes:
        system_prompt:       Default system prompt.
        include_memory:      Whether to include memory context.
        include_planner:     Whether to include planner output.
        include_tools:       Whether to include tool results.
        include_task:        Whether to include current task context.
        include_workflow:    Whether to include workflow step context.
        context_config:      Configuration for the ContextManager.
    """

    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    include_memory: bool = True
    include_planner: bool = True
    include_tools: bool = True
    include_task: bool = True
    include_workflow: bool = True
    context_config: ContextConfig = field(default_factory=ContextConfig)


# ======================================================================
# Prompt Pipeline
# ======================================================================


class PromptPipeline:
    """Unified prompt construction pipeline.

    This is the single entry point for building LLM requests.
    It assembles all context sources into a coherent message list,
    then delegates to the ContextManager for token budgeting.

    Usage::

        pipeline = PromptPipeline(context_manager)
        result = await pipeline.build(
            session_id="s1",
            user_message="Escribe un hola mundo en Python",
            history=session_manager.history("s1"),
            memory_context=await memory.get_context("s1"),
            plan=planner_output,
            tool_results=[...],
        )
        # result.messages -> send to LLM provider
    """

    def __init__(
        self,
        context_manager: ContextManager,
        *,
        config: PromptPipelineConfig | None = None,
    ) -> None:
        self._ctx = context_manager
        self._config = config or PromptPipelineConfig()
        logger.info("PromptPipeline created")

    @property
    def config(self) -> PromptPipelineConfig:
        return self._config

    @property
    def context_manager(self) -> ContextManager:
        return self._ctx

    async def build(
        self,
        *,
        session_id: str,
        user_message: str,
        history: list[dict[str, Any]] | None = None,
        memory_context: list[dict[str, Any]] | None = None,
        plan: Any = None,
        task_context: dict[str, Any] | None = None,
        workflow_context: dict[str, Any] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        system_prompt_override: str = "",
        model_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ContextResult:
        """Build the complete prompt for the LLM.

        This is the main entry point. It collects all context sources,
        formats them, and passes them to the ContextManager for
        token-budget-aware assembly.

        Args:
            session_id:            Conversation session identifier.
            user_message:          The current user message.
            history:               Conversation history from SessionManager.
            memory_context:        Retrieved memory entries.
            plan:                  Planner output (list of sub-tasks or dict).
            task_context:          Current task info (name, description, etc.).
            workflow_context:      Current workflow step info.
            tool_results:          Results from tool executions.
            system_prompt_override: Override the default system prompt.
            model_id:              Target model for capability-aware budgeting.
            metadata:              Additional metadata passed through.

        Returns:
            A ContextResult with the final message list and metadata.
        """
        # 1. System prompt.
        system_prompt = system_prompt_override or self._config.system_prompt

        # 2. Memory context (filtered by config).
        mem_ctx = memory_context if self._config.include_memory else None

        # 3. Planner context.
        planner_ctx: dict[str, Any] = {}
        if plan is not None and self._config.include_planner:
            if isinstance(plan, (list, tuple)):
                planner_ctx["plan"] = plan
            elif isinstance(plan, dict):
                planner_ctx = plan
            else:
                planner_ctx["plan"] = str(plan)

        # 4. Task context (injected as system note).
        if task_context and self._config.include_task:
            task_note = self._format_task(task_context)
            if task_note and planner_ctx is not None:
                planner_ctx["task"] = task_note

        # 5. Workflow context (injected as system note).
        if workflow_context and self._config.include_workflow:
            wf_note = self._format_workflow(workflow_context)
            if wf_note and planner_ctx is not None:
                planner_ctx["workflow"] = wf_note

        # 6. Tool results (filtered by config).
        tool_ctx = tool_results if self._config.include_tools else None

        # Delegate to ContextManager for assembly and budgeting.
        result = await self._ctx.build(
            session_id=session_id,
            user_message=user_message,
            system_prompt=system_prompt,
            history=history,
            memory_context=mem_ctx,
            planner_context=planner_ctx if planner_ctx else None,
            tool_results=tool_ctx,
            model_id=model_id,
        )

        # Attach pipeline metadata.
        result.meta["pipeline"] = {
            "system_prompt_length": len(system_prompt),
            "history_turns": len(history) if history else 0,
            "memory_entries": len(memory_context) if memory_context else 0,
            "tool_results": len(tool_results) if tool_results else 0,
            "has_plan": plan is not None,
            "has_task": task_context is not None,
            "has_workflow": workflow_context is not None,
            "metadata": metadata or {},
        }

        logger.debug(
            "PromptPipeline: session=%s tokens=%d budget=%.1f%% trimmed=%s summarized=%s",
            session_id,
            result.token_count,
            result.budget_used * 100,
            result.was_trimmed,
            result.was_summarized,
        )

        return result

    # ------------------------------------------------------------------
    # Formatting Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_task(task: dict[str, Any]) -> str:
        """Format task context into a concise text block."""
        parts: list[str] = []
        name = task.get("name", "")
        desc = task.get("description", "")
        state = task.get("state", "")
        if name:
            parts.append(f"Tarea actual: {name}")
        if desc:
            parts.append(f"Descripción: {desc}")
        if state:
            parts.append(f"Estado: {state}")
        return " | ".join(parts)

    @staticmethod
    def _format_workflow(wf: dict[str, Any]) -> str:
        """Format workflow context into a concise text block."""
        parts: list[str] = []
        name = wf.get("name", "")
        step = wf.get("current_step", "")
        total = wf.get("total_steps", "")
        if name:
            parts.append(f"Workflow: {name}")
        if step and total:
            parts.append(f"Paso {step} de {total}")
        elif step:
            parts.append(f"Paso actual: {step}")
        return " | ".join(parts)