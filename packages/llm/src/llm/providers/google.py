"""Google Gemini Provider.

Connects to the Google Gemini API (Generative Language API)
using httpx. Supports chat, streaming, and embeddings.

Google uses a different message format:
- Roles are "user" and "model" (not "assistant").
- Messages have a "parts" array instead of "content".
- System instructions are passed separately.

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


_GOOGLE_CAPABILITIES: dict[str, ModelCapabilities] = {
    "gemini-2.5-flash": ModelCapabilities(
        chat=True, streaming=True, embeddings=True,
        function_calling=True, vision=True,
        context_window=1048576, max_output_tokens=65536,
    ),
    "gemini-2.5-pro": ModelCapabilities(
        chat=True, streaming=True, embeddings=True,
        function_calling=True, vision=True,
        context_window=1048576, max_output_tokens=65536,
    ),
    "gemini-2.0-flash": ModelCapabilities(
        chat=True, streaming=True, embeddings=True,
        function_calling=True, vision=True,
        context_window=1048576, max_output_tokens=8192,
    ),
}


class GoogleProvider(BaseLLMProvider):
    """LLM provider for Google Gemini.

    Uses the Google Generative Language REST API.

    Args:
        model_id:     Model identifier (e.g. "gemini-2.5-flash").
        base_url:     API base URL.
        api_key:      Google API key.
        capabilities: Model capabilities.
        extra:        Additional configuration.
        timeout:      Request timeout in seconds.
    """

    name = "google"

    def __init__(
        self,
        *,
        model_id: str = "gemini-2.5-flash",
        base_url: str | None = None,
        api_key: str | None = None,
        capabilities: ModelCapabilities | None = None,
        extra: dict[str, Any] | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        caps = capabilities or _GOOGLE_CAPABILITIES.get(
            model_id,
            ModelCapabilities(
                chat=True, streaming=True, embeddings=True,
                function_calling=True, vision=True,
                context_window=1048576, max_output_tokens=8192,
            ),
        )
        super().__init__(
            model_id=model_id,
            base_url=base_url or "https://generativelanguage.googleapis.com",
            api_key=api_key,
            capabilities=caps,
            extra=extra,
            timeout=timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )

    def _build_headers(self) -> dict[str, str]:
        """Google uses x-goog-api-key header (no Bearer)."""
        return {
            "Content-Type": "application/json",
            "User-Agent": "Liz-Coder-Plus/0.2.0",
        }

    def _convert_messages(
        self, messages: list[LLMMessage]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert LLMMessages to Google Gemini format.

        Returns:
            Tuple of (system_instruction, gemini_contents).
        """
        system_text = ""
        contents: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                system_text += (msg.content + "\n") if system_text else msg.content
            else:
                contents.append(msg.to_google_format())

        return system_text, contents

    def _api_url(self, action: str, stream: bool = False) -> str:
        """Build the API URL with key parameter."""
        suffix = f"?key={self._api_key}" if self._api_key else ""
        stream_suffix = "&alt=sse" if stream else ""
        base = self._base_url.rstrip("/")
        return f"{base}/v1beta/models/{self._model_id}:{action}{suffix}{stream_suffix}"

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
        """Send a chat completion via Google Gemini API."""
        client = self._get_client()
        system_text, contents = self._convert_messages(messages)

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_text:
            payload["systemInstruction"] = {
                "parts": [{"text": system_text}]
            }
        payload.update(kwargs)

        url = self._api_url("generateContent")
        start = time.monotonic()
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        duration_ms = int((time.monotonic() - start) * 1000)

        # Extract text from response.
        candidates = data.get("candidates", [])
        text_parts: list[str] = []
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text_parts = [p.get("text", "") for p in parts if "text" in p]

        content = "\n".join(text_parts)

        usage_meta = data.get("usageMetadata", {})

        return LLMResponse(
            content=content,
            model=model,
            provider=self.name,
            usage=self._build_usage(
                prompt_tokens=usage_meta.get("promptTokenCount", 0),
                completion_tokens=usage_meta.get("candidatesTokenCount", 0),
            ),
            finish_reason=candidates[0].get("finishReason", "stop") if candidates else "stop",
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
        """Stream via Google Gemini SSE API."""
        client = self._get_client()
        system_text, contents = self._convert_messages(messages)

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_text:
            payload["systemInstruction"] = {
                "parts": [{"text": system_text}]
            }
        payload.update(kwargs)

        url = self._api_url("streamGenerateContent", stream=True)

        async with client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                candidates = data.get("candidates", [])
                if not candidates:
                    continue

                parts = candidates[0].get("content", {}).get("parts", [])
                for part in parts:
                    text = part.get("text", "")
                    if text:
                        yield LLMChunk(
                            content=text,
                            model=model,
                            provider=self.name,
                        )

                finish_reason = candidates[0].get("finishReason", "")
                if finish_reason and finish_reason != "FINISH_REASON_UNSPECIFIED":
                    yield LLMChunk(
                        content="",
                        model=model,
                        provider=self.name,
                        finish_reason=finish_reason,
                    )
                    return

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def embeddings(
        self,
        text: str,
        *,
        model: str = "",
    ) -> list[float]:
        """Generate embeddings via Google Embedding API."""
        client = self._get_client()
        embedding_model = model or "text-embedding-004"
        url = (
            f"{self._base_url.rstrip('/')}/v1beta/models/"
            f"{embedding_model}:embedContent"
            f"?key={self._api_key}"
        )
        payload = {
            "model": f"models/{embedding_model}",
            "content": {"parts": [{"text": text}]},
        }
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        embedding = data.get("embedding", {}).get("values", [])
        return embedding

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def _health_impl(self) -> bool:
        """Check Google API health via get model endpoint."""
        try:
            client = self._get_client()
            url = f"{self._base_url.rstrip('/')}/v1beta/models/{self._model_id}"
            if self._api_key:
                url += f"?key={self._api_key}"
            response = await client.get(url, timeout=15.0)
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False