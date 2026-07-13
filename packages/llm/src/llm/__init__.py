"""LLM engine module.

Provides the unified LLM interface for Liz Coder Plus:
- ModelManager: register, activate, select, and health-check models.
- LLMProvider Protocol: contract every provider must satisfy.
- Concrete providers: Ollama, OpenAI, Anthropic, Google, OpenRouter.
- ContextManager: token-aware context window management.
- StreamingManager: real-time streaming with cancellation and backpressure.
- PromptPipeline: unified prompt construction pipeline.

Sprint 1.9 — Motor LLM, Gestión de Modelos, Contexto y Conversación.
"""

from src.llm.models import (
    LLMChunk,
    LLMMessage,
    LLMResponse,
    ModelCapabilities,
    ModelInfo,
    ModelStatus,
)
from src.llm.protocols import LLMProvider

__all__ = [
    "LLMChunk",
    "LLMMessage",
    "LLMProvider",
    "LLMResponse",
    "ModelCapabilities",
    "ModelInfo",
    "ModelStatus",
]

__version__ = "0.2.0"