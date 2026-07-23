"""LLM-powered agent.

Sprint 4 — Phase 1.
Sprint 6 — Phase 1: Tool-use loop with function calling.

Replaces the EchoAgent as the default conversational agent. This agent
uses the ModelManager to obtain a provider, builds a prompt with context,
and streams/fetches the response from the configured LLM.

Supports OpenAI-compatible function calling: when the LLM returns a
tool_calls response, the agent executes the requested tools and feeds
the results back to the LLM in a multi-turn loop until the model
produces a final text response.

The agent satisfies the ``Agent`` Protocol and integrates with the
existing ExecutionPipeline, Planner, and ContextEngine.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.llm.manager import ModelManager
    from src.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

# Default system prompt for Liz in Spanish.
DEFAULT_SYSTEM_PROMPT = (
    "Eres Liz, una asistente de inteligencia artificial personal avanzada. "
    "Tu interfaz está en español. Eres útil, concisa y profesional. "
    "Puedes ayudar con programación, análisis, investigación, y tareas generales. "
    "Si el usuario escribe en otro idioma, responde en ese idioma. "
    "Cuando uses herramientas, explica brevemente lo que vas a hacer."
)


class LLMAgent:
    """Agent that uses an LLM via the ModelManager for responses.

    This agent satisfies the Agent Protocol (name + async handle).
    It integrates with the ModelManager to select the best model,
    builds a conversation prompt from session history and context,
    and delegates the actual generation to the LLM provider.

    Attributes:
        name: Agent identifier (used by the AgentRouter).
        description: Human-readable description.
        version: Agent version.
        capabilities: List of capability strings.
    """

    name = "llm"
    description = "LLM-powered conversational agent"
    version = "1.0.0"
    capabilities = [
        "conversation",
        "coding",
        "analysis",
        "research",
        "summarization",
        "translation",
        "tool_use",
        "function_calling",
    ]

    # Maximum number of tool-use iterations to prevent infinite loops.
    MAX_TOOL_ROUNDS = 10

    def __init__(
        self,
        *,
        model_manager: ModelManager | None = None,
        system_prompt: str | None = None,
        default_model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tool_executor: Any | None = None,
    ) -> None:
        """Initialize the LLM agent.

        Args:
            model_manager: The ModelManager for obtaining LLM providers.
            system_prompt: Custom system prompt. Uses default Liz prompt if None.
            default_model: Override the default model selection.
            temperature: Default temperature for generation.
            max_tokens: Default max tokens for generation.
        """
        self._model_manager = model_manager
        self._system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self._default_model = default_model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._tool_executor = tool_executor
        self._initialized = False
        self._shutdown = False

    @property
    def model_manager(self) -> ModelManager | None:
        """The ModelManager instance."""
        return self._model_manager

    @model_manager.setter
    def model_manager(self, value: ModelManager | None) -> None:
        """Set the ModelManager after construction."""
        self._model_manager = value

    @property
    def tool_executor(self) -> Any | None:
        """The ToolExecutor instance for tool-use loop."""
        return self._tool_executor

    @tool_executor.setter
    def tool_executor(self, value: Any | None) -> None:
        """Set the ToolExecutor after construction."""
        self._tool_executor = value

    def get_tools_schema(self) -> list[dict[str, Any]]:
        """Build OpenAI-compatible tool schemas from registered tools.

        Returns a list of tool definitions that can be sent to the LLM
        as the 'tools' parameter. Only includes tools that have a
        parameters_schema defined.
        """
        tools: list[dict[str, Any]] = []
        if self._tool_executor is None:
            return tools

        # Get tools from the orchestrator's tool registry if available.
        orchestrator_tools = getattr(self._tool_executor, '_orchestrator_tools', {})
        if not orchestrator_tools:
            orchestrator_tools = getattr(self._tool_executor, '_registered_tools', {})
        if not orchestrator_tools:
            return tools

        for tool_name, tool in orchestrator_tools.items():
            schema = getattr(tool, 'parameters_schema', {})
            if not schema:
                continue
            tool_def = {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": getattr(tool, 'description', ''),
                    "parameters": schema,
                },
            }
            tools.append(tool_def)

        return tools

    @property
    def metadata(self) -> dict[str, Any]:
        """Agent metadata for the orchestrator."""
        return {
            "name": self.name,
            "type": "LLMAgent",
            "version": self.version,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "default_model": self._default_model or "auto",
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "has_model_manager": self._model_manager is not None,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Prepare the agent for receiving tasks.

        If a ModelManager is attached and not yet initialized, initializes it.
        """
        if self._initialized:
            return

        if self._model_manager is not None and not self._model_manager.is_initialized:
            await self._model_manager.initialize()

        self._initialized = True
        logger.info("LLMAgent initialized (model=%s)", self._default_model or "auto")

    async def shutdown(self) -> None:
        """Release resources."""
        if self._shutdown:
            return
        self._shutdown = True
        logger.info("LLMAgent shut down")

    # ------------------------------------------------------------------
    # Agent Protocol
    # ------------------------------------------------------------------

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        """Process a user message using the LLM.

        Falls back gracefully if no LLM is available.

        Args:
            message: The raw user input.
            context: Additional context (session, history, language, mode).

        Returns:
            The LLM response, or a fallback message.
        """
        session_id = context.get("session_id", "unknown")
        language = context.get("user_language", "es")

        logger.info(
            "LLMAgent handling message for session %s (lang=%s, len=%d)",
            session_id,
            language,
            len(message),
        )

        # Try to use the LLM.
        if self._model_manager is not None and self._model_manager.is_initialized:
            # Check if tools are available for function calling.
            has_tools = (
                self._tool_executor is not None
                and (
                    getattr(self._tool_executor, '_orchestrator_tools', {})
                    or getattr(self._tool_executor, '_registered_tools', {})
                )
            )
            if has_tools:
                return await self._handle_with_llm_tools(message, context)
            return await self._handle_with_llm(message, context)

        # Fallback: try direct httpx call if no manager but env vars exist.
        fallback_response = await self._handle_fallback(message, context)
        if fallback_response is not None:
            return fallback_response

        # Ultimate fallback.
        return self._fallback_echo(message, context)

    # ------------------------------------------------------------------
    # LLM-based handling
    # ------------------------------------------------------------------

    async def _handle_with_llm(
        self, message: str, context: dict[str, Any]
    ) -> str:
        """Generate a response using the ModelManager and LLM provider."""
        try:
            model_id = self._default_model or ""

            # Select the best provider.
            if model_id:
                provider = self._model_manager.get_provider(model_id)
            else:
                selected_id = self._model_manager.select_provider()
                provider = self._model_manager.get_provider(selected_id)
                model_id = selected_id

            # Build messages from context and history.
            messages = self._build_messages(message, context)

            # Determine temperature and max_tokens from context if available.
            temperature = context.get("temperature", self._temperature)
            max_tokens = context.get("max_tokens", self._max_tokens)

            # Call the LLM.
            response = await provider.chat(
                messages,
                model=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            logger.info(
                "LLM response received: model=%s tokens=%d duration=%dms",
                response.model,
                response.usage.total_tokens,
                response.duration_ms,
            )

            return response.content

        except Exception:
            logger.exception(
                "LLM generation failed for session %s", 
                context.get("session_id", "unknown")
            )
            return self._fallback_error(context)

    # ------------------------------------------------------------------
    # Message building
    # ------------------------------------------------------------------

    def _build_messages(
        self, message: str, context: dict[str, Any]
    ) -> list:
        """Build the LLM message list from context and history.

        Includes system prompt, conversation history, and the current
        user message.
        """
        from src.llm.models import LLMMessage, MessageRole

        messages: list[LLMMessage] = []

        # System prompt with context enrichment.
        system_text = self._system_prompt

        # Enrich with mode information.
        mode = context.get("mode", "confirmation")
        if mode == "automatic":
            system_text += (
                "\n\nEstás en modo automático. Puedes ejecutar acciones "
                "sin pedir confirmación, pero sé cuidadosa."
            )
        else:
            system_text += (
                "\n\nEstás en modo confirmación. Si necesitas ejecutar "
                "acciones que puedan afectar el sistema, pregunta al "
                "usuario antes de proceder."
            )

        # Enrich with task context if available.
        task = context.get("task")
        if task:
            system_text += f"\n\nTarea actual del usuario: {task}"

        # Enrich with agent context from ContextEngine.
        agent_ctx = context.get("agent_context")
        if agent_ctx and hasattr(agent_ctx, "system_prompt") and agent_ctx.system_prompt:
            system_text += f"\n\n{agent_ctx.system_prompt}"

        messages.append(
            LLMMessage(role=MessageRole.SYSTEM, content=system_text)
        )

        # Add conversation history (limit to avoid token overflow).
        history = context.get("history", [])
        # Also check for persistent_history.
        persistent = context.get("persistent_history", [])
        all_history = persistent + [
            {"role": h.get("role", "user"), "content": h.get("content", "")}
            for h in history
            if isinstance(h, dict)
        ]

        # Keep only the last N exchanges (where N = 20 messages).
        max_history = 20
        recent_history = all_history[-max_history:]

        for turn in recent_history:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if not content or not content.strip():
                continue

            if role == "user":
                messages.append(
                    LLMMessage(role=MessageRole.USER, content=content)
                )
            elif role == "assistant":
                messages.append(
                    LLMMessage(role=MessageRole.ASSISTANT, content=content)
                )
            elif role == "system":
                messages.append(
                    LLMMessage(role=MessageRole.SYSTEM, content=content)
                )

        # Add the current user message.
        messages.append(
            LLMMessage(role=MessageRole.USER, content=message)
        )

        return messages

    # ------------------------------------------------------------------
    # Tool-use loop with function calling
    # ------------------------------------------------------------------

    async def _handle_with_llm_tools(
        self, message: str, context: dict[str, Any]
    ) -> str:
        """Handle message with LLM + tool-use loop.

        Implements the OpenAI function-calling pattern:
        1. Send message + tool definitions to LLM.
        2. If LLM returns tool_calls, execute each tool.
        3. Feed tool results back to LLM as assistant+tool messages.
        4. Repeat until LLM produces a final text response.
        5. Return the final response text.

        Stops after MAX_TOOL_ROUNDS iterations to prevent infinite loops.
        """
        try:
            model_id = self._default_model or ""
            if model_id:
                provider = self._model_manager.get_provider(model_id)
            else:
                selected_id = self._model_manager.select_provider()
                provider = self._model_manager.get_provider(selected_id)
                model_id = selected_id

            messages = self._build_messages(message, context)
            tools_schema = self.get_tools_schema()

            temperature = context.get("temperature", self._temperature)
            max_tokens = context.get("max_tokens", self._max_tokens)

            for round_num in range(self.MAX_TOOL_ROUNDS):
                # Call LLM with tools.
                response = await provider.chat(
                    messages,
                    model=model_id,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools_schema if tools_schema else None,
                )

                # Check for tool calls in the response.
                tool_calls = getattr(response, 'tool_calls', None)
                if not tool_calls:
                    # No tool calls — return the final text response.
                    logger.info(
                        "LLM tool-use loop completed in %d round(s), "
                        "model=%s tokens=%d",
                        round_num + 1, model_id, response.usage.total_tokens,
                    )
                    return response.content

                # Process tool calls.
                # Add the assistant message with tool_calls to conversation.
                assistant_msg = {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": tc.get("id", f"call_{round_num}_{i}"),
                            "type": "function",
                            "function": {
                                "name": tc.get("name", ""),
                                "arguments": tc.get("arguments", "{}"),
                            },
                        }
                        for i, tc in enumerate(tool_calls)
                    ],
                }
                messages.append(assistant_msg)

                # Execute each tool call and add results.
                for tc in tool_calls:
                    tool_name = tc.get("name", "")
                    arguments_str = tc.get("arguments", "{}")
                    call_id = tc.get("id", f"call_{round_num}")

                    try:
                        arguments = json.loads(arguments_str)
                    except json.JSONDecodeError:
                        arguments = {}

                    logger.info(
                        "Tool call: %s(args=%s) [round %d]",
                        tool_name, list(arguments.keys()), round_num + 1,
                    )

                    # Execute tool via ToolExecutor.
                    try:
                        tool_result = await self._execute_tool(
                            tool_name, arguments, context
                        )
                        result_content = json.dumps(
                            tool_result, ensure_ascii=False, default=str
                        )
                    except Exception as exc:
                        logger.exception(
                            "Tool execution failed: %s", tool_name
                        )
                        result_content = json.dumps({
                            "success": False,
                            "error": str(exc),
                        })

                    # Add tool result message.
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": result_content,
                    })

            # Max rounds reached — ask LLM for final summary.
            logger.warning(
                "Tool-use loop reached max rounds (%d) for session %s",
                self.MAX_TOOL_ROUNDS,
                context.get("session_id", "unknown"),
            )
            messages.append({
                "role": "user",
                "content": (
                    "Has alcanzado el límite de iteraciones de herramientas. "
                    "Por favor, proporciona una respuesta final basada en "
                    "los resultados obtenidos hasta ahora."
                ),
            })
            final_response = await provider.chat(
                messages,
                model=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return final_response.content

        except Exception:
            logger.exception(
                "LLM tool-use loop failed for session %s",
                context.get("session_id", "unknown"),
            )
            # Fall back to non-tool LLM call.
            return await self._handle_with_llm(message, context)

    async def _execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a tool by name using the ToolExecutor.

        Looks up the tool from the orchestrator's registry and
        executes it via the ToolExecutor for permission gating.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool parameters.
            context: Execution context.

        Returns:
            Tool execution result dict.
        """
        # Get tool from the ToolExecutor's tools registry.
        tools = (
            getattr(self._tool_executor, '_orchestrator_tools', {})
            or getattr(self._tool_executor, '_registered_tools', {})
        )
        tool = tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Tool '{tool_name}' not found")

        session_id = context.get("session_id", "")
        result = await self._tool_executor.execute(
            tool, arguments, session_id=session_id
        )
        return result.to_dict()

    # ------------------------------------------------------------------
    # Fallback handling
    # ------------------------------------------------------------------

    async def _handle_fallback(
        self, message: str, context: dict[str, Any]
    ) -> str | None:
        """Try to use environment variables for a direct LLM call.

        This allows the agent to work even without a ModelManager,
        using the OPENAI_API_KEY environment variable directly.
        """
        import os

        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LIZ_AI_API_KEY")
        if not api_key:
            return None

        try:
            import httpx

            model = self._default_model or os.getenv("LIZ_AI_MODEL", "gpt-4o-mini")
            base_url = os.getenv(
                "LIZ_AI_BASE_URL", "https://api.openai.com/v1"
            )

            messages = self._build_messages(message, context)

            async with httpx.AsyncClient(
                base_url=base_url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": "Liz-Coder-Plus/1.0",
                },
                timeout=120.0,
            ) as client:
                payload = {
                    "model": model,
                    "messages": [m.to_openai_format() for m in messages],
                    "temperature": self._temperature,
                    "max_tokens": self._max_tokens,
                }
                response = await client.post(
                    "/chat/completions", json=payload
                )
                response.raise_for_status()
                data = response.json()

                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )

                logger.info(
                    "Fallback LLM response received: model=%s len=%d",
                    model,
                    len(content),
                )
                return content

        except Exception:
            logger.exception("Fallback LLM call failed")
            return None

    @staticmethod
    def _fallback_echo(message: str, context: dict[str, Any]) -> str:
        """Ultimate fallback when no LLM is available."""
        session_id = context.get("session_id", "unknown")
        history_count = len(context.get("history", []))
        mode = context.get("mode", "confirmation")

        if history_count > 0:
            return (
                f"Hola, soy Liz. Recibí tu mensaje: «{message}». "
                f"Vamos {history_count} intercambios en esta conversación. "
                f"Modo actual: {mode}. "
                f"Nota: No tengo un modelo de IA configurado. "
                f"Configura tu API key para habilitar respuestas inteligentes."
            )

        return (
            f"Hola, soy Liz. Recibí tu mensaje: «{message}». "
            f"Nota: No tengo un modelo de IA configurado todavía. "
            f"Configura tu API key para habilitar respuestas inteligentes."
        )

    @staticmethod
    def _fallback_error(context: dict[str, Any]) -> str:
        """Response when LLM generation fails."""
        return (
            "Lo siento, ocurrió un error al generar mi respuesta. "
            "Por favor, intenta de nuevo. Si el problema persiste, "
            "verifica que tu API key esté configurada correctamente."
        )


__all__ = ["LLMAgent", "DEFAULT_SYSTEM_PROMPT"]
