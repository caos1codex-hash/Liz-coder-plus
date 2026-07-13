"""Context Manager.

Manages the LLM context window: builds messages from conversation
history, Memory, Planner, and Tool results. Handles token budgeting,
smart trimming, and automatic summarization.

The ContextManager sits between the Orchestrator/Pipeline and the
LLM provider, ensuring that every request to the LLM has an
optimally constructed context within the model's token budget.

Sprint 1.9 — Phase 4.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.llm.models import LLMMessage, MessageRole, ModelCapabilities

if TYPE_CHECKING:
    from src.llm.manager import ModelManager

logger = logging.getLogger(__name__)


# ======================================================================
# Token Estimation
# ======================================================================


def estimate_tokens(text: str, chars_per_token: float = 4.0) -> int:
    """Estimate the number of tokens in a text string.

    Uses a simple heuristic: tokens ≈ characters / chars_per_token.
    This is a rough estimate suitable for budget planning. For
    precise counts, use the provider's tokenize() method.

    Args:
        text:            The text to estimate.
        chars_per_token: Average characters per token.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    return max(1, int(len(text) / chars_per_token))


def estimate_message_tokens(msg: LLMMessage) -> int:
    """Estimate tokens for a single message including overhead.

    Each message has ~4 tokens of overhead (role, delimiters).
    """
    overhead = 4
    content_tokens = estimate_tokens(msg.content)
    name_tokens = estimate_tokens(msg.name) + 2 if msg.name else 0
    return content_tokens + overhead + name_tokens


# ======================================================================
# Configuration
# ======================================================================


@dataclass
class ContextConfig:
    """Configuration for the ContextManager.

    Attributes:
        max_context_tokens:  Hard limit on total context tokens.
        reserved_for_response: Tokens reserved for the model response.
        system_prompt_tokens: Estimated tokens for the system prompt.
        summary_threshold:    When history exceeds this fraction of
                              the budget, trigger summarization.
        max_history_turns:    Maximum conversation turns to include.
        include_tool_results: Whether to include tool execution results.
        memory_max_entries:   Max memory entries to retrieve.
        auto_summarize:       Whether to auto-summarize old messages.
    """

    max_context_tokens: int = 8192
    reserved_for_response: int = 1024
    system_prompt_tokens: int = 200
    summary_threshold: float = 0.7
    max_history_turns: int = 50
    include_tool_results: bool = True
    memory_max_entries: int = 10
    auto_summarize: bool = True


# ======================================================================
# Context Result
# ======================================================================


@dataclass
class ContextResult:
    """Result of context construction.

    Attributes:
        messages:        The final list of LLMMessage objects.
        token_count:     Estimated total tokens in the context.
        budget_used:     Fraction of the available budget used (0.0 - 1.0).
        was_trimmed:     Whether messages were trimmed to fit the budget.
        was_summarized:  Whether old messages were summarized.
        summary:         The generated summary text (if summarization ran).
        meta:            Additional metadata for observability.
    """

    messages: list[LLMMessage] = field(default_factory=list)
    token_count: int = 0
    budget_used: float = 0.0
    was_trimmed: bool = False
    was_summarized: bool = False
    summary: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


# ======================================================================
# ContextManager
# ======================================================================


class ContextManager:
    """Manages the LLM context window.

    Responsibilities:
        - Build the message list from multiple sources (system prompt,
          conversation history, memory, planner, tool results).
        - Enforce token budgets.
        - Smart trimming: remove oldest messages first, preserving
          system prompt and recent context.
        - Auto-summarization: summarize old messages when the context
          grows too large, replacing them with a compact summary.

    Integration:
        Conversation → Memory → Planner → LLM

    Usage::

        ctx = ContextManager(model_manager)
        result = await ctx.build(
            session_id="s1",
            user_message="Hola Liz",
            history=[...],
            system_prompt="Eres Liz...",
        )
        # result.messages is ready to send to the LLM provider.
    """

    def __init__(
        self,
        model_manager: ModelManager | None = None,
        *,
        config: ContextConfig | None = None,
    ) -> None:
        self._model_manager = model_manager
        self._config = config or ContextConfig()
        self._summaries: dict[str, str] = {}
        logger.info(
            "ContextManager created (max_ctx=%d, reserved=%d)",
            self._config.max_context_tokens,
            self._config.reserved_for_response,
        )

    @property
    def config(self) -> ContextConfig:
        return self._config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build(
        self,
        *,
        session_id: str,
        user_message: str,
        system_prompt: str = "",
        history: list[dict[str, Any]] | None = None,
        memory_context: list[dict[str, Any]] | None = None,
        planner_context: dict[str, Any] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        model_id: str = "",
    ) -> ContextResult:
        """Build the complete LLM context.

        Assembles messages in the canonical order:
            1. System prompt
            2. Previous summary (if any)
            3. Conversation history
            4. Memory context
            5. Planner context (as a system note)
            6. Tool results
            7. Current user message

        Then trims and/or summarizes to fit the token budget.

        Args:
            session_id:       Conversation session ID.
            user_message:     The current user message.
            system_prompt:    System prompt for the LLM.
            history:          Previous conversation turns.
            memory_context:   Retrieved memory entries.
            planner_context:  Planner decisions (plan, sub-tasks).
            tool_results:     Results from tool executions.
            model_id:         Target model for capability-aware budgeting.

        Returns:
            A ContextResult with the final message list and metadata.
        """
        # Determine the effective token budget.
        budget = self._get_budget(model_id)

        # Start building the message list.
        messages: list[LLMMessage] = []

        # 1. System prompt.
        if system_prompt:
            messages.append(
                LLMMessage(role=MessageRole.SYSTEM, content=system_prompt)
            )

        # 2. Previous summary.
        prev_summary = self._summaries.get(session_id, "")
        if prev_summary:
            messages.append(
                LLMMessage(
                    role=MessageRole.SYSTEM,
                    content=f"[Resumen de conversación anterior]: {prev_summary}",
                )
            )

        # 3. Conversation history.
        if history:
            for turn in history:
                role_str = turn.get("role", "user")
                content = turn.get("content", "")
                if not content:
                    continue
                role = MessageRole(role_str) if role_str in ("user", "assistant", "system", "tool") else MessageRole.USER
                messages.append(
                    LLMMessage(
                        role=role,
                        content=content,
                        name=turn.get("name", ""),
                        meta=turn.get("metadata", {}),
                    )
                )

        # 4. Memory context.
        if memory_context:
            memory_text = self._format_memory(memory_context)
            if memory_text:
                messages.append(
                    LLMMessage(
                        role=MessageRole.SYSTEM,
                        content=f"[Memoria recuperada]:\n{memory_text}",
                    )
                )

        # 5. Planner context.
        if planner_context:
            plan_text = self._format_planner(planner_context)
            if plan_text:
                messages.append(
                    LLMMessage(
                        role=MessageRole.SYSTEM,
                        content=f"[Plan de ejecución]:\n{plan_text}",
                    )
                )

        # 6. Tool results.
        if tool_results and self._config.include_tool_results:
            for tr in tool_results:
                messages.append(
                    LLMMessage(
                        role=MessageRole.TOOL,
                        content=tr.get("output", tr.get("content", str(tr))),
                        name=tr.get("tool_name", ""),
                        meta=tr,
                    )
                )

        # 7. Current user message (always last).
        messages.append(
            LLMMessage(role=MessageRole.USER, content=user_message)
        )

        # Calculate initial token count.
        initial_tokens = sum(estimate_message_tokens(m) for m in messages)

        result = ContextResult(
            messages=messages,
            token_count=initial_tokens,
            meta={"session_id": session_id, "model_id": model_id},
        )

        # Trim and/or summarize if over budget.
        if initial_tokens > budget:
            result = await self._fit_to_budget(result, budget, session_id)

        result.budget_used = result.token_count / budget if budget > 0 else 0.0
        return result

    def get_summary(self, session_id: str) -> str:
        """Get the current summary for a session."""
        return self._summaries.get(session_id, "")

    def set_summary(self, session_id: str, summary: str) -> None:
        """Set the summary for a session (e.g., from an external summarizer)."""
        self._summaries[session_id] = summary

    def clear_summary(self, session_id: str) -> None:
        """Remove the summary for a session."""
        self._summaries.pop(session_id, None)

    # ------------------------------------------------------------------
    # Budget Management
    # ------------------------------------------------------------------

    def _get_budget(self, model_id: str) -> int:
        """Calculate the effective token budget for a model.

        Uses the model's context window if a ModelManager is available,
        otherwise falls back to the config's max_context_tokens.
        """
        if self._model_manager and model_id:
            try:
                caps = self._model_manager.capabilities(model_id)
                total_window = caps.context_window
            except KeyError:
                total_window = self._config.max_context_tokens
        else:
            total_window = self._config.max_context_tokens

        available = total_window - self._config.reserved_for_response
        return max(available, 512)  # Minimum 512 tokens for context.

    async def _fit_to_budget(
        self,
        result: ContextResult,
        budget: int,
        session_id: str,
    ) -> ContextResult:
        """Trim or summarize messages to fit within the budget.

        Strategy:
            1. First try summarization if enabled and threshold exceeded.
            2. Then trim oldest non-system messages until within budget.
        """
        messages = result.messages
        current_tokens = result.token_count

        # Step 1: Try summarization.
        if self._config.auto_summarize and current_tokens > budget * self._config.summary_threshold:
            messages, current_tokens, summary = self._summarize_old_messages(
                messages, budget, session_id
            )
            if summary:
                result.was_summarized = True
                result.summary = summary

        # Step 2: Trim if still over budget.
        if current_tokens > budget:
            messages, current_tokens = self._trim_messages(messages, budget)

        result.messages = messages
        result.token_count = current_tokens
        result.was_trimmed = current_tokens != sum(
            estimate_message_tokens(m) for m in messages
        ) if not result.was_trimmed else True
        return result

    def _summarize_old_messages(
        self,
        messages: list[LLMMessage],
        budget: int,
        session_id: str,
    ) -> tuple[list[LLMMessage], int, str]:
        """Summarize old messages, keeping recent ones.

        Keeps the system prompt, replaces old conversation messages
        with a summary, and preserves the most recent turns.

        Returns:
            Tuple of (updated_messages, new_token_count, summary_text).
        """
        # Identify system messages and the user message (always last).
        system_msgs: list[LLMMessage] = []
        conversation_msgs: list[LLMMessage] = []
        last_msg: LLMMessage | None = None

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                system_msgs.append(msg)
            elif last_msg is None and msg.role == MessageRole.USER:
                # Keep track — but we need to check if it's the last user msg.
                conversation_msgs.append(msg)
            else:
                conversation_msgs.append(msg)

        # The current user message is always the last message.
        if messages and messages[-1].role == MessageRole.USER:
            last_msg = messages[-1]
            if conversation_msgs and conversation_msgs[-1] is last_msg:
                conversation_msgs.pop()

        # Determine how many recent turns to keep.
        target_budget = int(budget * 0.5)  # Use 50% of budget for recent messages.
        recent_msgs: list[LLMMessage] = []
        recent_tokens = 0
        for msg in reversed(conversation_msgs):
            msg_tokens = estimate_message_tokens(msg)
            if recent_tokens + msg_tokens > target_budget:
                break
            recent_msgs.insert(0, msg)
            recent_tokens += msg_tokens

        # Messages to summarize (everything not in recent_msgs).
        old_msgs = [
            m for m in conversation_msgs
            if m not in recent_msgs
        ]

        # Build a simple extractive summary.
        summary = self._extractive_summary(old_msgs)
        if summary:
            self._summaries[session_id] = summary
            summary_msg = LLMMessage(
                role=MessageRole.SYSTEM,
                content=f"[Resumen de conversación anterior]: {summary}",
            )
        else:
            summary_msg = None

        # Reassemble.
        final: list[LLMMessage] = list(system_msgs)
        if summary_msg:
            final.append(summary_msg)
        final.extend(recent_msgs)
        if last_msg:
            final.append(last_msg)

        total_tokens = sum(estimate_message_tokens(m) for m in final)
        return final, total_tokens, summary

    @staticmethod
    def _extractive_summary(messages: list[LLMMessage]) -> str:
        """Create a simple extractive summary from old messages.

        Takes the first user message and last assistant message
        to create a compact summary of what was discussed.
        """
        if not messages:
            return ""

        # Find first user message and last assistant message.
        first_user = ""
        last_assistant = ""
        topics: list[str] = []

        for msg in messages:
            if msg.role == MessageRole.USER and not first_user:
                # Truncate long messages.
                first_user = msg.content[:200]
            if msg.role == MessageRole.ASSISTANT:
                last_assistant = msg.content[:200]
            # Extract potential topics from user messages.
            if msg.role == MessageRole.USER and len(msg.content) < 100:
                topics.append(msg.content[:80])

        parts: list[str] = []
        if topics:
            unique_topics = list(dict.fromkeys(topics))[:5]
            parts.append(f"Temas discutidos: {'; '.join(unique_topics)}")
        if first_user:
            parts.append(f"Primera consulta: {first_user}")
        if last_assistant:
            parts.append(f"Última respuesta: {last_assistant}")

        return " | ".join(parts) if parts else ""

    @staticmethod
    def _trim_messages(
        messages: list[LLMMessage],
        budget: int,
    ) -> tuple[list[LLMMessage], int]:
        """Remove oldest non-system messages until within budget.

        Always preserves:
            - System messages (first messages).
            - The last user message (current turn).

        Returns:
            Tuple of (trimmed_messages, new_token_count).
        """
        # Separate system messages, conversation, and last user msg.
        system_msgs: list[LLMMessage] = []
        other_msgs: list[LLMMessage] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                system_msgs.append(msg)
            else:
                other_msgs.append(msg)

        # The last message (current user turn) must be preserved.
        last_msg = None
        if other_msgs and other_msgs[-1].role == MessageRole.USER:
            last_msg = other_msgs.pop()

        # Calculate system message tokens.
        system_tokens = sum(estimate_message_tokens(m) for m in system_msgs)
        last_tokens = estimate_message_tokens(last_msg) if last_msg else 0
        available = budget - system_tokens - last_tokens

        if available <= 0:
            # Only system + last message fit.
            final = list(system_msgs)
            if last_msg:
                final.append(last_msg)
            return final, system_tokens + last_tokens

        # Keep as many messages as possible from the end.
        kept: list[LLMMessage] = []
        kept_tokens = 0
        for msg in reversed(other_msgs):
            msg_tokens = estimate_message_tokens(msg)
            if kept_tokens + msg_tokens > available:
                break
            kept.insert(0, msg)
            kept_tokens += msg_tokens

        final = list(system_msgs)
        final.extend(kept)
        if last_msg:
            final.append(last_msg)

        total_tokens = sum(estimate_message_tokens(m) for m in final)
        return final, total_tokens

    # ------------------------------------------------------------------
    # Formatting Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_memory(entries: list[dict[str, Any]]) -> str:
        """Format memory entries into a concise text block."""
        if not entries:
            return ""
        parts: list[str] = []
        for entry in entries[:10]:  # Limit to 10 entries.
            content = entry.get("content", str(entry))
            role = entry.get("role", "memory")
            parts.append(f"- [{role}] {content[:200]}")
        return "\n".join(parts)

    @staticmethod
    def _format_planner(context: dict[str, Any]) -> str:
        """Format planner context into a concise text block."""
        if not context:
            return ""
        parts: list[str] = []
        plan = context.get("plan")
        if plan and isinstance(plan, (list, tuple)):
            for i, item in enumerate(plan):
                if isinstance(item, dict):
                    desc = item.get("description", item.get("task", str(item)))
                    parts.append(f"  {i + 1}. {desc}")
                else:
                    parts.append(f"  {i + 1}. {item}")
        elif plan:
            parts.append(f"  Plan: {plan}")

        decision = context.get("decision", "")
        if decision:
            parts.append(f"  Decisión: {decision}")

        return "\n".join(parts) if parts else ""

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def estimate_context_tokens(
        self,
        messages: list[LLMMessage],
    ) -> int:
        """Estimate total tokens for a list of messages."""
        return sum(estimate_message_tokens(m) for m in messages)

    def remaining_budget(
        self,
        messages: list[LLMMessage],
        model_id: str = "",
    ) -> int:
        """Calculate remaining token budget after messages."""
        budget = self._get_budget(model_id)
        used = self.estimate_context_tokens(messages)
        return max(0, budget - used)