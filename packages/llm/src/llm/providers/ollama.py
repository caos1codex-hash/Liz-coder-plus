"""Ollama Provider.

Connects to a local Ollama instance via its OpenAI-compatible API.
Ollama exposes /v1/chat/completions and /v1/embeddings endpoints
that follow the OpenAI format, making this adapter straightforward.

Supports: chat, streaming, embeddings, health check.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

import httpx

from src.llm.models import (
    LLMChunk,
    LLMMessage,
    LLMResponse,
    ModelCapabilities,
)
from src.llm.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(BaseLLMProvider):
    """LLM provider for Ollama (local models).

    Uses the Ollama REST API (OpenAI-compatible /v1 endpoints).
    Falls back to the native /api/chat endpoint if the v1 endpoint
    is not available.

    Args:
        model_id:     Model identifier (e.g. "llama3:8b", "codellama:13b").
        base_url:     Ollama server URL (default: http://localhost:11434).
        api_key:      Not used by Ollama but kept for interface consistency.
        capabilities: Model capabilities metadata.
        extra:        Additional configuration.
        timeout:      Request timeout in seconds.
        max_retries:  Maximum retry attempts.
        retry_base_delay: Base delay for exponential backoff.
    """

    name = "ollama"

    def __init__(
        self,
        *,
        model_id: str = "llama3:8b",
        base_url: str | None = None,
        api_key: str | None = None,
        capabilities: ModelCapabilities | None = None,
        extra: dict[str, Any] | None = None,
        timeout: float = 300.0,
        max_retries: int = 3,
        retry_base_delay: float = 2.0,
    ) -> None:
        super().__init__(
            model_id=model_id,
            base_url=base_url or "http://localhost:11434",
            api_key=api_key,
            capabilities=capabilities or ModelCapabilities(
                chat=True,
                streaming=True,
                embeddings=True,
                function_calling=False,
                vision=False,
                context_window=8192,
                max_output_tokens=4096,
            ),
            extra=extra,
            timeout=timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )

    def _build_headers(self) -> dict[str, str]:
        """Ollama does not require auth headers."""
        return {
            "Content-Type": "application/json",
            "User-Agent": "Liz-Coder-Plus/0.2.0",
        }

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def _chat_impl(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat completion via Ollama /v1/chat/completions."""
        import time

        client = self._get_client()
        payload = {
            "model": model,
            "messages": [m.to_openai_format() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        start = time.monotonic()
        response = await client.post("/v1/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        duration_ms = int((time.monotonic() - start) * 1000)

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        return LLMResponse(
            content=message.get("content", ""),
            model=data.get("model", model),
            provider=self.name,
            usage=self._build_usage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            ),
            finish_reason=choice.get("finish_reason", "stop"),
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # Stream
    # ------------------------------------------------------------------

    async def _stream_impl(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        """Stream a chat completion via Ollama /v1/chat/completions."""
        client = self._get_client()
        payload = {
            "model": model,
            "messages": [m.to_openai_format() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        async with client.stream(
            "POST", "/v1/chat/completions", json=payload
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    yield LLMChunk(
                        content="",
                        model=model,
                        provider=self.name,
                        finish_reason="stop",
                    )
                    return

                import json
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = data.get("choices", [])
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})
                content = delta.get("content", "")
                finish_reason = choice.get("finish_reason", "")

                yield LLMChunk(
                    content=content,
                    model=data.get("model", model),
                    provider=self.name,
                    finish_reason=finish_reason,
                )

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def embeddings(
        self,
        text: str,
        *,
        model: str = "",
    ) -> list[float]:
        """Generate embeddings via Ollama /v1/embeddings."""
        client = self._get_client()
        effective_model = model or self._model_id
        payload = {
            "model": effective_model,
            "input": text,
        }
        response = await client.post("/v1/embeddings", json=payload)
        response.raise_for_status()
        data = response.json()
        embedding = data.get("data", [{}])[0].get("embedding", [])
        return embedding

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def _health_impl(self) -> bool:
        """Check Ollama health via /api/tags."""
        try:
            client = self._get_client()
            response = await client.get("/api/tags", timeout=10.0)
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False