"""LLM Provider protocol.

Every LLM provider (Ollama, OpenAI, Anthropic, Google, OpenRouter)
must satisfy this Protocol so the ModelManager can use them
interchangeably.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable

from src.llm.models import LLMChunk, LLMMessage, LLMResponse, ModelCapabilities


@runtime_checkable
class LLMProvider(Protocol):
    """Contract every LLM provider must satisfy.

    Providers are thin adapters that translate the unified interface
    into provider-specific API calls. They must support synchronous
    chat completion, streaming, and basic health checking.
    """

    name: str

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat completion request and return the full response.

        Args:
            messages:    Conversation messages (system, user, assistant).
            model:       Model identifier. Falls back to provider default.
            temperature: Sampling temperature (0.0 - 2.0).
            max_tokens:  Maximum tokens to generate.
            **kwargs:    Provider-specific parameters.

        Returns:
            An LLMResponse with content, usage, and metadata.
        """

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        """Stream a chat completion response token by token.

        Yields:
            LLMChunk objects with incremental content deltas.
        """

    async def embeddings(
        self,
        text: str,
        *,
        model: str = "",
    ) -> list[float]:
        """Generate embeddings for the given text.

        Args:
            text:  Input text to embed.
            model: Embedding model identifier.

        Returns:
            A list of floats representing the embedding vector.

        Raises:
            NotImplementedError: If the provider does not support embeddings.
        """

    async def tokenize(
        self,
        text: str,
        *,
        model: str = "",
    ) -> list[str]:
        """Tokenize text into token strings.

        Args:
            text:  Input text to tokenize.
            model: Model whose tokenizer to use.

        Returns:
            A list of token strings.

        Raises:
            NotImplementedError: If the provider does not support tokenization.
        """

    async def health(self) -> bool:
        """Check if the provider backend is reachable and healthy.

        Returns:
            True if the provider is healthy, False otherwise.
        """

    async def close(self) -> None:
        """Release resources held by the provider (connections, pools)."""

    def capabilities(self, model: str = "") -> ModelCapabilities:
        """Return the capabilities of a model.

        Args:
            model: Model identifier. Falls back to default model.

        Returns:
            A ModelCapabilities instance describing what the model supports.
        """