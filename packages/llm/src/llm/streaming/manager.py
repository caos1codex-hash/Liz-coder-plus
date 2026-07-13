"""Streaming Manager.

Manages real-time streaming of LLM responses. Supports:
- Incremental token streaming
- Cancellation of active streams
- Timeout enforcement
- Automatic retry on stream failures
- Backpressure via configurable buffer size
- Multiple concurrent clients (multiple WebSocket connections)

The StreamingManager integrates with the existing WebSocket
infrastructure (WebSocketManager) and the LLM providers.

Sprint 1.9 — Phase 6.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable

from src.llm.models import LLMChunk, LLMMessage, LLMResponse

logger = logging.getLogger(__name__)


class StreamState(str, Enum):
    """States of an active stream."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class StreamSession:
    """Tracks a single active streaming session.

    Attributes:
        id:              Unique stream identifier.
        session_id:      Conversation session this stream belongs to.
        state:           Current stream state.
        chunks_received: Number of chunks received so far.
        total_chars:     Total characters received.
        started_at:      Monotonic timestamp when the stream started.
        finished_at:     When the stream ended.
        cancel_event:    Event used to signal cancellation.
        model:           Model being used.
        provider:        Provider being used.
        error:           Last error message, if any.
    """

    id: str
    session_id: str
    state: StreamState = StreamState.PENDING
    chunks_received: int = 0
    total_chars: int = 0
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float = 0.0
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    model: str = ""
    provider: str = ""
    error: str = ""

    @property
    def duration_ms(self) -> int:
        if self.finished_at:
            return int((self.finished_at - self.started_at) * 1000)
        return int((time.monotonic() - self.started_at) * 1000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "state": self.state.value,
            "chunks_received": self.chunks_received,
            "total_chars": self.total_chars,
            "duration_ms": self.duration_ms,
            "model": self.model,
            "provider": self.provider,
            "error": self.error,
        }


@dataclass
class StreamingConfig:
    """Configuration for the StreamingManager.

    Attributes:
        default_timeout:      Default stream timeout in seconds.
        max_buffer_size:      Maximum chunks to buffer before applying
                              backpressure (pause reading from provider).
        retry_on_failure:     Whether to retry on stream setup failure.
        max_retries:          Maximum retries for stream setup.
        retry_delay:          Delay between retries in seconds.
        chunk_callback:       Optional async callback invoked per chunk.
                              Receives (stream_id, chunk_dict).
    """

    default_timeout: float = 120.0
    max_buffer_size: int = 100
    retry_on_failure: bool = True
    max_retries: int = 2
    retry_delay: float = 1.0
    chunk_callback: Callable[[str, dict[str, Any]], Any] | None = None


class StreamingManager:
    """Manages LLM response streaming.

    Responsibilities:
        - Create and track active stream sessions.
        - Stream responses from LLM providers with cancellation support.
        - Enforce timeouts.
        - Apply backpressure when the consumer is slow.
        - Support multiple concurrent streams.
        - Provide full and aggregated responses after streaming.

    Usage::

        sm = StreamingManager()
        provider = model_manager.get_provider("gpt-4o-mini")

        # Stream to a WebSocket client.
        async for envelope in sm.stream(
            provider=provider,
            messages=messages,
            session_id="s1",
            on_chunk=lambda chunk: ws_manager.send_to(conn_id, envelope),
        ):
            pass  # Chunks are sent via callback.

        # Or use as an async iterator directly.
        async for chunk in sm.stream_iterator(provider, messages):
            print(chunk.content, end="", flush=True)
    """

    def __init__(
        self,
        *,
        config: StreamingConfig | None = None,
    ) -> None:
        self._config = config or StreamingConfig()
        self._active_streams: dict[str, StreamSession] = {}
        self._lock = asyncio.Lock()
        logger.info("StreamingManager created (timeout=%.1fs)", self._config.default_timeout)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def active_count(self) -> int:
        """Number of currently active streams."""
        return sum(
            1 for s in self._active_streams.values()
            if s.state == StreamState.ACTIVE
        )

    @property
    def config(self) -> StreamingConfig:
        return self._config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def stream(
        self,
        *,
        provider: Any,
        messages: list[LLMMessage],
        session_id: str,
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        on_chunk: Callable[[dict[str, Any]], Any] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream an LLM response and yield envelope dicts.

        Each yielded dict is compatible with the WebSocket response
        format: {"type": "chunk"|"message", "content": ..., "status": ..., ...}.

        Args:
            provider:   LLM provider instance.
            messages:   Messages to send to the LLM.
            session_id: Conversation session ID.
            model:      Model to use.
            temperature: Sampling temperature.
            max_tokens: Max tokens to generate.
            on_chunk:   Optional callback invoked per chunk.
            timeout:    Stream timeout. Defaults to config.
            **kwargs:   Additional provider parameters.

        Yields:
            Envelope dicts suitable for WebSocket transmission.
        """
        stream_session = await self._create_session(session_id, model, provider)

        effective_timeout = timeout if timeout is not None else self._config.default_timeout
        full_content = ""
        start = time.monotonic()

        try:
            # Set up the provider stream with retry.
            stream_iter = self._get_stream_iterator(
                provider, messages, model, temperature, max_tokens, **kwargs
            )

            stream_session.state = StreamState.ACTIVE

            async for chunk in stream_iter:
                # Check for cancellation.
                if stream_session.cancel_event.is_set():
                    stream_session.state = StreamState.CANCELLED
                    yield {
                        "type": "status",
                        "content": "",
                        "status": "cancelled",
                        "session_id": session_id,
                        "stream_id": stream_session.id,
                    }
                    return

                # Check for timeout.
                elapsed = time.monotonic() - start
                if elapsed > effective_timeout:
                    stream_session.state = StreamState.TIMEOUT
                    yield {
                        "type": "error",
                        "content": "",
                        "status": "failed",
                        "session_id": session_id,
                        "error": f"Stream timeout after {effective_timeout}s",
                    }
                    return

                # Handle error chunks from the provider.
                if chunk.meta.get("error"):
                    stream_session.state = StreamState.FAILED
                    stream_session.error = chunk.meta["error"]
                    yield {
                        "type": "error",
                        "content": "",
                        "status": "failed",
                        "session_id": session_id,
                        "error": chunk.meta["error"],
                    }
                    return

                # Accumulate content.
                full_content += chunk.content
                stream_session.chunks_received += 1
                stream_session.total_chars = len(full_content)

                # Build the envelope.
                if chunk.is_final:
                    stream_session.state = StreamState.COMPLETED
                    stream_session.finished_at = time.monotonic()
                    yield {
                        "type": "message",
                        "content": full_content,
                        "status": "completed",
                        "session_id": session_id,
                        "stream_id": stream_session.id,
                        "model": chunk.model or model,
                        "provider": chunk.provider or getattr(provider, "name", ""),
                        "duration_ms": stream_session.duration_ms,
                        "chunks": stream_session.chunks_received,
                    }
                else:
                    envelope = {
                        "type": "chunk",
                        "content": chunk.content,
                        "status": "streaming",
                        "session_id": session_id,
                        "stream_id": stream_session.id,
                        "model": chunk.model or model,
                        "provider": chunk.provider or getattr(provider, "name", ""),
                    }
                    yield envelope

                    # Invoke callback if provided.
                    if on_chunk:
                        try:
                            if asyncio.iscoroutinefunction(on_chunk):
                                await on_chunk(envelope)
                            else:
                                on_chunk(envelope)
                        except Exception:  # noqa: BLE001
                            logger.exception("Chunk callback error")

                    # Invoke global chunk callback.
                    if self._config.chunk_callback:
                        try:
                            cb = self._config.chunk_callback
                            if asyncio.iscoroutinefunction(cb):
                                await cb(stream_session.id, envelope)
                            else:
                                cb(stream_session.id, envelope)
                        except Exception:  # noqa: BLE001
                            logger.exception("Global chunk callback error")

                    # Backpressure: if buffer is full, wait a bit.
                    if stream_session.chunks_received % self._config.max_buffer_size == 0:
                        await asyncio.sleep(0.01)

        except Exception as exc:  # noqa: BLE001
            stream_session.state = StreamState.FAILED
            stream_session.error = str(exc)
            logger.exception("Stream error for session %s", session_id)
            yield {
                "type": "error",
                "content": "",
                "status": "failed",
                "session_id": session_id,
                "error": str(exc),
            }
        finally:
            await self._cleanup_session(stream_session.id)

    async def stream_iterator(
        self,
        *,
        provider: Any,
        messages: list[LLMMessage],
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        """Low-level stream iterator that yields raw LLMChunk objects.

        Useful for internal consumers that don't need the WebSocket
        envelope format.
        """
        stream_iter = self._get_stream_iterator(
            provider, messages, model, temperature, max_tokens, **kwargs
        )
        async for chunk in stream_iter:
            yield chunk

    async def cancel(self, stream_id: str) -> bool:
        """Cancel an active stream.

        Args:
            stream_id: The stream to cancel.

        Returns:
            True if the stream was found and cancellation was signaled.
        """
        async with self._lock:
            session = self._active_streams.get(stream_id)
            if session and session.state == StreamState.ACTIVE:
                session.cancel_event.set()
                session.state = StreamState.CANCELLED
                logger.info("Stream cancelled: %s", stream_id)
                return True
        return False

    async def cancel_by_session(self, session_id: str) -> int:
        """Cancel all active streams for a given session.

        Returns:
            Number of streams cancelled.
        """
        cancelled = 0
        async with self._lock:
            for s in self._active_streams.values():
                if s.session_id == session_id and s.state == StreamState.ACTIVE:
                    s.cancel_event.set()
                    s.state = StreamState.CANCELLED
                    cancelled += 1
        if cancelled:
            logger.info("Cancelled %d stream(s) for session %s", cancelled, session_id)
        return cancelled

    def get_session(self, stream_id: str) -> StreamSession | None:
        """Get a stream session by ID."""
        return self._active_streams.get(stream_id)

    def list_active(self) -> list[StreamSession]:
        """List all active stream sessions."""
        return [
            s for s in self._active_streams.values()
            if s.state == StreamState.ACTIVE
        ]

    def summary(self) -> dict[str, Any]:
        """Return a summary of the StreamingManager state."""
        by_state: dict[str, int] = {}
        for s in self._active_streams.values():
            st = s.state.value
            by_state[st] = by_state.get(st, 0) + 1

        return {
            "active_streams": self.active_count,
            "total_tracked": len(self._active_streams),
            "by_state": by_state,
            "timeout": self._config.default_timeout,
            "max_buffer": self._config.max_buffer_size,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _create_session(
        self,
        session_id: str,
        model: str,
        provider: Any,
    ) -> StreamSession:
        """Create a new stream session."""
        stream_id = str(uuid.uuid4())[:12]
        ss = StreamSession(
            id=stream_id,
            session_id=session_id,
            model=model,
            provider=getattr(provider, "name", ""),
        )
        async with self._lock:
            self._active_streams[stream_id] = ss
        logger.debug("Stream session created: %s (session=%s)", stream_id, session_id)
        return ss

    async def _cleanup_session(self, stream_id: str) -> None:
        """Remove a completed stream session."""
        async with self._lock:
            self._active_streams.pop(stream_id, None)

    async def _get_stream_iterator(
        self,
        provider: Any,
        messages: list[LLMMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        """Get a stream iterator with retry on setup failure.

        Retries the stream setup (not individual chunks) if it fails
        due to connection errors or timeouts.
        """
        last_exc: Exception | None = None

        for attempt in range(self._config.max_retries + 1):
            try:
                async for chunk in provider.stream(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                ):
                    yield chunk
                return  # Stream completed successfully.

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not self._config.retry_on_failure:
                    raise
                if attempt < self._config.max_retries:
                    logger.warning(
                        "Stream setup attempt %d/%d failed: %s",
                        attempt + 1,
                        self._config.max_retries + 1,
                        exc,
                    )
                    await asyncio.sleep(self._config.retry_delay)
                else:
                    raise

        # Should not reach here, but just in case.
        if last_exc:
            raise last_exc