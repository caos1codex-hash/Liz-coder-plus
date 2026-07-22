"""NVIDIA NIM Provider.

Connects to the NVIDIA NIM API (build.nvidia.com) using httpx.
Supports chat, streaming, and function calling for NVIDIA's hosted models.

Sprint 5 — NVIDIA NIM Integration.
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


# Default capabilities for well-known NVIDIA NIM models.
_NVIDIA_CAPABILITIES: dict[str, ModelCapabilities] = {
    "nvidia/llama-3.1-nemotron-70b-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
    "nvidia/llama-3.1-nemotron-ultra-253b": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
    "nvidia/llama-3.1-nemotron-8b-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
    "nvidia/nemotron-4-340b-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
    "nvidia/phi-3-mini-128k-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=False, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
    "nvidia/phi-3-medium-128k-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
    "google/gemma-2-27b-it": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=False, vision=False,
        context_window=8192, max_output_tokens=4096,
    ),
    "meta/llama-3.1-405b-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
    "meta/llama-3.1-70b-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
    "meta/llama-3.1-8b-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
    "mistralai/mixtral-8x22b-instruct-v0.1": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=65536, max_output_tokens=4096,
    ),
    "mistralai/mistral-large-2-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
    "microsoft/phi-3.5-mini-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=False, vision=True,
        context_window=131072, max_output_tokens=4096,
    ),
    "google/gemma-2-9b-it": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=False, vision=False,
        context_window=8192, max_output_tokens=4096,
    ),
    "deepseek-ai/deepseek-coder-6.7b-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=False, vision=False,
        context_window=16384, max_output_tokens=4096,
    ),
    "qwen/qwen2.5-72b-instruct": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=False,
        context_window=131072, max_output_tokens=4096,
    ),
}


class NvidiaProvider(BaseLLMProvider):
    """LLM provider for NVIDIA NIM (build.nvidia.com).

    Uses the OpenAI-compatible chat completions API format.
    Supports both free and premium models hosted on NVIDIA's inference
    platform. Free models can be used with the standard NIM API key.

    Args:
        model_id:     Model identifier (e.g. "meta/llama-3.1-405b-instruct").
        base_url:     API base URL (default: https://integrate.api.nvidia.com/v1).
        api_key:      NVIDIA NIM API key (nvapi-...).
        capabilities: Model capabilities. Auto-detected for known models.
        extra:        Additional configuration.
        timeout:      Request timeout in seconds.
    """

    name = "nvidia"

    def __init__(
        self,
        *,
        model_id: str = "meta/llama-3.1-405b-instruct",
        base_url: str | None = None,
        api_key: str | None = None,
        capabilities: ModelCapabilities | None = None,
        extra: dict[str, Any] | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        caps = capabilities or _NVIDIA_CAPABILITIES.get(
            model_id,
            ModelCapabilities(
                chat=True, streaming=True, embeddings=False,
                function_calling=True, vision=False,
                context_window=131072, max_output_tokens=4096,
            ),
        )
        super().__init__(
            model_id=model_id,
            base_url=base_url or "https://integrate.api.nvidia.com/v1",
            api_key=api_key,
            capabilities=caps,
            extra=extra,
            timeout=timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )

    def _build_headers(self) -> dict[str, str]:
        """Override headers for NVIDIA NIM API."""
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
        """Send a chat completion via NVIDIA NIM API (OpenAI-compatible)."""
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
        """Stream a chat completion via NVIDIA NIM SSE API."""
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
        """Check NVIDIA NIM API health via /models endpoint."""
        try:
            client = self._get_client()
            response = await client.get("/models", timeout=15.0)
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # Model Discovery
    # ------------------------------------------------------------------

    @staticmethod
    async def discover_available_models(
        api_key: str,
        base_url: str = "https://integrate.api.nvidia.com/v1",
    ) -> list[dict[str, Any]]:
        """Query the NVIDIA NIM API for available models.

        Returns a list of model descriptors with id, object type,
        and any additional metadata provided by the API.

        Args:
            api_key: NVIDIA NIM API key.
            base_url: API base URL.

        Returns:
            List of model info dicts.
        """
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                timeout=30.0,
            ) as client:
                response = await client.get("/models")
                response.raise_for_status()
                data = response.json()
                return data.get("data", [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to discover NVIDIA models: %s", exc)
            return []
