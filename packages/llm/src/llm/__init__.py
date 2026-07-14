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

from src.llm.context.manager import ContextConfig, ContextManager, ContextResult
from src.llm.context.manager import estimate_message_tokens, estimate_tokens
from src.llm.manager import ModelManager
from src.llm.models import (
    LLMChunk,
    LLMMessage,
    LLMResponse,
    MessageRole,
    ModelCapabilities,
    ModelInfo,
    ModelProvider,
    ModelStatus,
    TokenUsage,
)
from src.llm.prompt.pipeline import DEFAULT_SYSTEM_PROMPT, PromptPipeline, PromptPipelineConfig
from src.llm.protocols import LLMProvider
from src.llm.streaming.manager import (
    StreamSession,
    StreamState,
    StreamingConfig,
    StreamingManager,
)

__all__ = [
    # Models
    "LLMChunk",
    "LLMMessage",
    "LLMProvider",
    "LLMResponse",
    "MessageRole",
    "ModelCapabilities",
    "ModelInfo",
    "ModelProvider",
    "ModelStatus",
    "TokenUsage",
    # Manager
    "ModelManager",
    # Context
    "ContextConfig",
    "ContextManager",
    "ContextResult",
    "estimate_message_tokens",
    "estimate_tokens",
    # Prompt Pipeline
    "DEFAULT_SYSTEM_PROMPT",
    "PromptPipeline",
    "PromptPipelineConfig",
    # Streaming
    "StreamSession",
    "StreamState",
    "StreamingConfig",
    "StreamingManager",
]

__version__ = "0.2.0"