"""Qwen (Alibaba Cloud) Provider.

Connects to the Qwen API (dashscope.aliyuncs.com) using httpx.
Supports chat, streaming, and function calling for Qwen models.

Sprint 8 — Qwen Standalone Integration.
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


# Default capabilities for well-known Qwen models.
_QWEN_CAPABILITIES: dict[str, ModelCapabilities] = {
    "qwen-max": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=32768, max_output_tokens=8192,
    ),
    "qwen-plus": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=8192,
    ),
    "qwen-turbo": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=8192,
    ),
    "qwen-long": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=1048576, max_output_tokens=6000,
    ),
    "qwen2.5-72b-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
    "qwen2.5-coder-32b-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
    "qwen-vl-max": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=True,
        context_window=131072, max_output_tokens=4096,
    ),
    "qwen-vl-plus": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=True,
        context_window=131072, max_output_tokens=4096,
    ),
}


class QwenProvider(BaseLLMProvider):
    """LLM provider for Qwen by Alibaba Cloud (dashscope.aliyuncs.com).

    Uses the OpenAI-compatible chat completions API format.
    Supports Qwen2.5 series with function calling.

    Args:
        model_id:     Model identifier (e.g. "qwen-max").
        base_url:     API base URL (default: https://dashscope.aliyuncs.com/compatible-mode/v1).
        api_key:      Alibaba Cloud DashScope API key.
        capabilities: Model capabilities. Auto-detected for known models.
        extra:        Additional configuration.
        timeout:      Request timeout in seconds.
    """

    name = "qwen"

    def __init__(
        self,
        *,
        model_id: str = "qwen-max",
        base_url: str | None = None,
        api_key: str | None = None,
        capabilities: ModelCapabilities | None = None,
        extra: dict[str, Any] | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        caps = capabilities or _QWEN_CAPABILITIES.get(
            model_id,
            ModelCapabilities(
                chat=True, streaming=True, embeddings=False,
                function_calling=True, vision=False,
                context_window=32768, max_output_tokens=8192,
            ),
        )
        super().__init__(
            model_id=model_id,
            base_url=base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=api_key,
            capabilities=caps,
            extra=extra,
            timeout=timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )

    def _build_headers(self) -> dict[str, str]:
        """Override headers for Qwen API."""
        headers = super()._build_headers()
        headers["User-Agent"] = "Liz-Coder-Plus/1.0"
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
        """Send a chat completion via Qwen API (OpenAI-compatible)."""
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
        response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        duration_ms = int((time.monotonic() - start) * 1000)

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        # Parse tool calls if present.
        tool_calls = []
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls:
            for tc in raw_tool_calls:
                func = tc.get("function", {})
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "name": func.get("name", ""),
                    "arguments": func.get("arguments", "{}"),
                })

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
            tool_calls=tool_calls,
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
        """Stream a chat completion via Qwen SSE API."""
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
            "POST", "/chat/completions", json=payload
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
        """Check Qwen API health via /models endpoint."""
        try:
            client = self._get_client()
            response = await client.get("/models", timeout=15.0)
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False
