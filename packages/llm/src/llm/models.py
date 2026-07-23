"""LLM data models.

Defines the canonical data structures used by all LLM providers
and the ModelManager. These models are provider-agnostic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ======================================================================
# Enums
# ======================================================================


class ModelProvider(str, Enum):
    """Supported LLM provider identifiers."""

    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENROUTER = "openrouter"
    NVIDIA = "nvidia"
    DEEPSEEK = "deepseek"
    MISTRAL = "mistral"
    GLM = "glm"
    QWEN = "qwen"


class ModelStatus(str, Enum):
    """Lifecycle status of a registered model."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    WARMING = "warming"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class MessageRole(str, Enum):
    """Roles in an LLM conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


# ======================================================================
# Data Classes
# ======================================================================


@dataclass(slots=True)
class ModelCapabilities:
    """Describes what a model can do.

    Attributes:
        chat:            Supports chat completion.
        streaming:       Supports streaming responses.
        embeddings:      Supports text embeddings.
        function_calling: Supports tool/function calling.
        vision:          Supports image inputs.
        context_window:  Maximum context window size in tokens.
        max_output_tokens: Maximum output tokens per response.
    """

    chat: bool = True
    streaming: bool = True
    embeddings: bool = False
    function_calling: bool = False
    vision: bool = False
    context_window: int = 4096
    max_output_tokens: int = 4096

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "chat": self.chat,
            "streaming": self.streaming,
            "embeddings": self.embeddings,
            "function_calling": self.function_calling,
            "vision": self.vision,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
        }


@dataclass(slots=True)
class ModelInfo:
    """Metadata about a registered model.

    Attributes:
        model_id:    Unique identifier (e.g. "gpt-4o-mini", "llama3:8b").
        provider:    Provider enum value.
        display_name: Human-readable name.
        description: Brief model description.
        capabilities: What the model supports.
        status:      Current lifecycle status.
        base_url:    Custom API endpoint (for Ollama, Azure, etc.).
        api_key_env: Environment variable holding the API key.
        extra:       Provider-specific configuration.
    """

    model_id: str
    provider: ModelProvider
    display_name: str = ""
    description: str = ""
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    status: ModelStatus = ModelStatus.INACTIVE
    base_url: str = ""
    api_key_env: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "model_id": self.model_id,
            "provider": self.provider.value,
            "display_name": self.display_name or self.model_id,
            "description": self.description,
            "capabilities": self.capabilities.to_dict(),
            "status": self.status.value,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "extra": self.extra,
        }


@dataclass(slots=True)
class LLMMessage:
    """A single message in an LLM conversation.

    Attributes:
        role:    Who sent the message (system, user, assistant, tool).
        content: The message text.
        name:    Optional tool/function name (for tool role).
        meta:    Additional metadata (token_count, timestamps, etc.).
    """

    role: MessageRole
    content: str
    name: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a provider-agnostic dictionary."""
        d: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.meta:
            d["meta"] = self.meta
        return d

    def to_openai_format(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible message format."""
        d: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.name:
            d["name"] = self.name
        return d

    def to_anthropic_format(self) -> dict[str, Any]:
        """Convert to Anthropic-compatible message format.

        Anthropic expects system messages separately, so this method
        converts system messages to user messages with a prefix.
        """
        role = self.role.value
        if role == "system":
            return {"role": "user", "content": f"[System]: {self.content}"}
        return {"role": role, "content": self.content}

    def to_google_format(self) -> dict[str, Any]:
        """Convert to Google Gemini-compatible content format."""
        role = "model" if self.role == MessageRole.ASSISTANT else "user"
        return {"role": role, "parts": [{"text": self.content}]}


@dataclass(slots=True)
class TokenUsage:
    """Token usage statistics for an LLM response.

    Attributes:
        prompt_tokens:     Tokens in the input (messages).
        completion_tokens: Tokens in the generated output.
        total_tokens:      Sum of prompt and completion tokens.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        if self.total_tokens == 0 and (self.prompt_tokens or self.completion_tokens):
            object.__setattr__(self, "total_tokens", self.prompt_tokens + self.completion_tokens)

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(slots=True)
class LLMResponse:
    """Full response from an LLM chat completion.

    Attributes:
        content:   The generated text.
        model:     Model identifier used for generation.
        provider:  Provider that generated the response.
        usage:     Token usage statistics.
        finish_reason: Why generation stopped (stop, length, etc.).
        meta:      Provider-specific metadata.
        duration_ms: Wall-clock time for the request in ms.
        tool_calls: List of tool/function calls requested by the model.
                    Each item is a dict with keys: id, name, arguments.
    """

    content: str = ""
    model: str = ""
    provider: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str = "stop"
    meta: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "content": self.content,
            "model": self.model,
            "provider": self.provider,
            "usage": self.usage.to_dict(),
            "finish_reason": self.finish_reason,
            "meta": self.meta,
            "duration_ms": self.duration_ms,
        }
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        return d


@dataclass(slots=True)
class LLMChunk:
    """A single chunk in a streamed LLM response.

    Attributes:
        content:   Incremental text delta.
        model:     Model identifier.
        provider:  Provider name.
        finish_reason: Non-empty on the final chunk.
        meta:      Provider-specific metadata.
    """

    content: str = ""
    model: str = ""
    provider: str = ""
    finish_reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_final(self) -> bool:
        """Whether this is the last chunk in the stream."""
        return self.finish_reason != ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "content": self.content,
            "model": self.model,
            "provider": self.provider,
        }
        if self.finish_reason:
            d["finish_reason"] = self.finish_reason
        if self.meta:
            d["meta"] = self.meta
        return d