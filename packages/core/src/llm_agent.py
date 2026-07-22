"""LLM-powered agent.

Sprint 4 — Phase 1.

Replaces the EchoAgent as the default conversational agent. This agent
uses the ModelManager to obtain a provider, builds a prompt with context,
and streams/fetches the response from the configured LLM.

The agent satisfies the ``Agent`` Protocol and integrates with the
existing ExecutionPipeline, Planner, and ContextEngine.
"""

from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.llm.manager import ModelManager

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
    ]

    def __init__(
        self,
        *,
        model_manager: ModelManager | None = None,
        system_prompt: str | None = None,
        default_model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
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


__all__ = ["LLMAgent"]
