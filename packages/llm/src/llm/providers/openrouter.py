"""OpenRouter Provider.

Connects to the OpenRouter API, which provides access to many
models through a unified OpenAI-compatible API. Uses the same
format as OpenAI but routes to different backends.

Sprint 1.9 — Phase 3.
"""

from __future__ import annotations

import json
import logging
import time
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


class OpenRouterProvider(BaseLLMProvider):
    """LLM provider for OpenRouter.

    OpenRouter provides an OpenAI-compatible API that routes
    to many model providers. The interface is identical to OpenAI
    but with different authentication and base URL.

    Args:
        model_id:     Model identifier (e.g. "openai/gpt-4o",
                      "anthropic/claude-sonnet-4").
        base_url:     API base URL (default: https://openrouter.ai/api).
        api_key:      OpenRouter API key.
        capabilities: Model capabilities.
        extra:        Additional configuration (e.g. site URL for rankings).
        timeout:      Request timeout in seconds.
    """

    name = "openrouter"

    def __init__(
        self,
        *,
        model_id: str = "openai/gpt-4o-mini",
        base_url: str | None = None,
        api_key: str | None = None,
        capabilities: ModelCapabilities | None = None,
        extra: dict[str, Any] | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        caps = capabilities or ModelCapabilities(
            chat=True, streaming=True, embeddings=False,
            function_calling=True, vision=False,
            context_window=8192, max_output_tokens=4096,
        )
        super().__init__(
            model_id=model_id,
            base_url=base_url or "https://openrouter.ai/api",
            api_key=api_key,
            capabilities=caps,
            extra=extra,
            timeout=timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )

    def _build_headers(self) -> dict[str, str]:
        """OpenRouter uses Bearer token + optional HTTP-Referer."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "Liz-Coder-Plus/0.2.0",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        # OpenRouter optional headers for ranking.
        headers["HTTP-Referer"] = self._extra.get("site_url", "https://liz-coder-plus.local")
        headers["X-Title"] = self._extra.get("app_title", "Liz Coder Plus")
        return headers

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
        """Send a chat completion via OpenRouter API."""
        client = self._get_client()
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_openai_format() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        payload.update(kwargs)

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
            meta={"id": data.get("id", "")},
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
        """Stream via OpenRouter SSE API (OpenAI-compatible)."""
        client = self._get_client()
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_openai_format() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        payload.update(kwargs)

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
                    meta={"id": data.get("id", "")},
                )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def _health_impl(self) -> bool:
        """Check OpenRouter API health via /models endpoint."""
        try:
            client = self._get_client()
            response = await client.get("/v1/models", timeout=15.0)
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False