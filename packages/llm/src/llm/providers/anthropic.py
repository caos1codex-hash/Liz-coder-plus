"""Anthropic Provider.

Connects to the Anthropic Claude API using httpx.
Supports chat, streaming, and function calling.

Anthropic uses a different message format:
- System message is a top-level parameter (not in the messages list).
- Messages alternate between user and assistant.
- Tool use results have a specific format.

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
    MessageRole,
)
from src.llm.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)


_ANTHROPIC_CAPABILITIES: dict[str, ModelCapabilities] = {
    "claude-sonnet-4-20250514": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=True,
        context_window=200000, max_output_tokens=16384,
    ),
    "claude-3-5-sonnet-20241022": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=True,
        context_window=200000, max_output_tokens=8192,
    ),
    "claude-3-haiku-20240307": ModelCapabilities(
        chat=True, streaming=True, embeddings=False,
        function_calling=True, vision=True,
        context_window=200000, max_output_tokens=4096,
    ),
}


class AnthropicProvider(BaseLLMProvider):
    """LLM provider for Anthropic Claude.

    Uses the Anthropic Messages API. System messages are extracted
    from the messages list and passed as a top-level parameter.

    Args:
        model_id:     Model identifier (e.g. "claude-sonnet-4-20250514").
        base_url:     API base URL (default: https://api.anthropic.com).
        api_key:      Anthropic API key.
        capabilities: Model capabilities.
        extra:        Additional configuration.
        timeout:      Request timeout in seconds.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        model_id: str = "claude-sonnet-4-20250514",
        base_url: str | None = None,
        api_key: str | None = None,
        capabilities: ModelCapabilities | None = None,
        extra: dict[str, Any] | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        caps = capabilities or _ANTHROPIC_CAPABILITIES.get(
            model_id,
            ModelCapabilities(
                chat=True, streaming=True, embeddings=False,
                function_calling=True, vision=True,
                context_window=200000, max_output_tokens=8192,
            ),
        )
        super().__init__(
            model_id=model_id,
            base_url=base_url or "https://api.anthropic.com",
            api_key=api_key,
            capabilities=caps,
            extra=extra,
            timeout=timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )
        self._anthropic_version = "2023-06-01"

    def _build_headers(self) -> dict[str, str]:
        """Anthropic requires x-api-key and anthropic-version headers."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "Liz-Coder-Plus/0.2.0",
            "anthropic-version": self._anthropic_version,
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def _convert_messages(
        self, messages: list[LLMMessage]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert LLMMessages to Anthropic format.

        Extracts the system message (if any) and returns it
        separately, along with the remaining messages.
        """
        system_text = ""
        anthropic_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                system_text += (msg.content + "\n") if system_text else msg.content
            else:
                anthropic_messages.append(msg.to_anthropic_format())

        return system_text, anthropic_messages

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
        """Send a chat completion via Anthropic Messages API."""
        client = self._get_client()
        system_text, anthropic_msgs = self._convert_messages(messages)

        payload: dict[str, Any] = {
            "model": model,
            "messages": anthropic_msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system_text:
            payload["system"] = system_text
        payload.update(kwargs)

        start = time.monotonic()
        response = await client.post("/v1/messages", json=payload)
        response.raise_for_status()
        data = response.json()
        duration_ms = int((time.monotonic() - start) * 1000)

        # Extract content from the response.
        content_blocks = data.get("content", [])
        text_parts = [
            block.get("text", "")
            for block in content_blocks
            if block.get("type") == "text"
        ]
        content = "\n".join(text_parts)

        usage = data.get("usage", {})

        return LLMResponse(
            content=content,
            model=data.get("model", model),
            provider=self.name,
            usage=self._build_usage(
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
            ),
            finish_reason=data.get("stop_reason", "end_turn"),
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
        """Stream via Anthropic SSE API."""
        client = self._get_client()
        system_text, anthropic_msgs = self._convert_messages(messages)

        payload: dict[str, Any] = {
            "model": model,
            "messages": anthropic_msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if system_text:
            payload["system"] = system_text
        payload.update(kwargs)

        async with client.stream(
            "POST", "/v1/messages", json=payload
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = data.get("type", "")

                if event_type == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield LLMChunk(
                            content=delta.get("text", ""),
                            model=data.get("model", model),
                            provider=self.name,
                        )
                elif event_type == "message_stop":
                    yield LLMChunk(
                        content="",
                        model=model,
                        provider=self.name,
                        finish_reason="end_turn",
                    )
                    return
                elif event_type == "message_delta":
                    # Contains usage info for the last chunk.
                    usage = data.get("usage", {})
                    yield LLMChunk(
                        content="",
                        model=model,
                        provider=self.name,
                        meta={"output_tokens": usage.get("output_tokens", 0)},
                    )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def _health_impl(self) -> bool:
        """Check Anthropic API health."""
        try:
            client = self._get_client()
            # Anthropic doesn't have a dedicated health endpoint.
            # Send a minimal messages request to test connectivity.
            payload = {
                "model": self._model_id,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
            response = await client.post(
                "/v1/messages", json=payload, timeout=15.0
            )
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False