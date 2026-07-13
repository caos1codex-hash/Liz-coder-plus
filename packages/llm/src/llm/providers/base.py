"""Base LLM Provider.

Abstract base class that all concrete providers inherit from.
Provides common retry logic, timeout handling, and response parsing.
Concrete providers only need to implement `_chat_impl` and
`_stream_impl`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

import httpx

from src.llm.models import (
    LLMChunk,
    LLMMessage,
    LLMResponse,
    ModelCapabilities,
    TokenUsage,
)

logger = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    """Abstract base for all LLM providers.

    Handles:
        - Common initialization (model, base_url, api_key, httpx client).
        - Retry with exponential backoff on transient errors.
        - Timeout enforcement.
        - Response envelope construction.

    Concrete providers must implement:
        - `_chat_impl()`: Send a chat completion request.
        - `_stream_impl()`: Stream a chat completion request.
        - `_health_impl()`: Check provider health.
    """

    name: str = "base"

    def __init__(
        self,
        *,
        model_id: str = "",
        base_url: str | None = None,
        api_key: str | None = None,
        capabilities: ModelCapabilities | None = None,
        extra: dict[str, Any] | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        self._model_id = model_id
        self._base_url = base_url or ""
        self._api_key = api_key or ""
        self._capabilities = capabilities or ModelCapabilities()
        self._extra = extra or {}
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    # ------------------------------------------------------------------
    # HTTP Client
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared httpx client."""
        if self._client is None or self._client.is_closed:
            headers = self._build_headers()
            self._client = httpx.AsyncClient(
                base_url=self._base_url or None,
                headers=headers,
                timeout=httpx.Timeout(self._timeout, connect=15.0),
            )
        return self._client

    def _build_headers(self) -> dict[str, str]:
        """Build HTTP headers for requests.

        Override in subclasses for provider-specific headers.
        """
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "Liz-Coder-Plus/0.2.0",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    # ------------------------------------------------------------------
    # Public Interface (LLMProvider Protocol)
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat completion request with retry logic.

        Delegates to `_chat_impl()` with automatic retries on
        transient errors (connection, timeout, 429, 5xx).
        """
        effective_model = model or self._model_id
        return await self._retry_wrapper(
            self._chat_impl,
            messages=messages,
            model=effective_model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        """Stream a chat completion response.

        Delegates to `_stream_impl()`. Retries are handled at the
        stream level — if the stream fails to start, it retries.
        Once the stream has started, failures propagate to the caller.
        """
        effective_model = model or self._model_id

        # Retry the stream setup, not individual chunks.
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                async for chunk in self._stream_impl(
                    messages=messages,
                    model=effective_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                ):
                    yield chunk
                return  # Stream completed successfully
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning(
                    "Stream attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    self._max_retries,
                    effective_model,
                    exc,
                )
                if attempt < self._max_retries - 1:
                    delay = self._retry_base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)

        # All retries exhausted.
        error_msg = f"Stream failed after {self._max_retries} retries"
        if last_exc:
            error_msg += f": {last_exc}"
        yield LLMChunk(
            content="",
            model=effective_model,
            provider=self.name,
            meta={"error": error_msg},
        )

    async def embeddings(
        self,
        text: str,
        *,
        model: str = "",
    ) -> list[float]:
        """Generate embeddings. Not supported by default."""
        raise NotImplementedError(
            f"Embeddings not supported by {self.name} provider"
        )

    async def tokenize(
        self,
        text: str,
        *,
        model: str = "",
    ) -> list[str]:
        """Tokenize text. Not supported by default."""
        raise NotImplementedError(
            f"Tokenization not supported by {self.name} provider"
        )

    async def health(self) -> bool:
        """Check health with a short timeout."""
        try:
            return await asyncio.wait_for(
                self._health_impl(),
                timeout=min(self._timeout, 15.0),
            )
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            return False

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def capabilities_info(self, model: str = "") -> ModelCapabilities:
        """Return capabilities for a model."""
        return self._capabilities

    # ------------------------------------------------------------------
    # Abstract Implementations
    # ------------------------------------------------------------------

    @abstractmethod
    async def _chat_impl(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs: Any,
    ) -> LLMResponse:
        """Provider-specific chat completion implementation."""
        raise NotImplementedError

    @abstractmethod
    async def _stream_impl(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        """Provider-specific streaming implementation."""
        raise NotImplementedError

    @abstractmethod
    async def _health_impl(self) -> bool:
        """Provider-specific health check implementation."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    async def _retry_wrapper(
        self,
        func,
        **kwargs: Any,
    ) -> Any:
        """Execute a function with exponential backoff retry.

        Retries on transient errors: connection errors, timeouts,
        HTTP 429 (rate limit), and HTTP 5xx (server error).
        """
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return await func(**kwargs)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code
                if status_code == 429 or status_code >= 500:
                    logger.warning(
                        "HTTP %d on attempt %d/%d for %s",
                        status_code,
                        attempt + 1,
                        self._max_retries,
                        self._model_id,
                    )
                    if attempt < self._max_retries - 1:
                        delay = self._retry_base_delay * (2 ** attempt)
                        await asyncio.sleep(delay)
                    continue
                # Non-retryable HTTP error.
                raise
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning(
                    "Connection error on attempt %d/%d for %s: %s",
                    attempt + 1,
                    self._max_retries,
                    self._model_id,
                    exc,
                )
                if attempt < self._max_retries - 1:
                    delay = self._retry_base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                continue

        # All retries exhausted.
        raise RuntimeError(
            f"{self.name} request failed after {self._max_retries} "
            f"retries: {last_exc}"
        ) from last_exc

    def _build_usage(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> TokenUsage:
        """Build a TokenUsage instance."""
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )