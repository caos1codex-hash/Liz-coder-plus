"""Tests for the ContextManager, PromptPipeline, and StreamingManager.

Sprint 1.9 — Phase 7.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure the LLM package is importable.
_LLM_ROOT = Path(__file__).resolve().parents[2] / "packages" / "llm" / "src"
if str(_LLM_ROOT) not in sys.path:
    sys.path.insert(0, str(_LLM_ROOT))

# Clear cached src modules.
for key in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(key, None)

from src.llm.context.manager import (  # noqa: E402
    ContextConfig,
    ContextManager,
    ContextResult,
    estimate_tokens,
)
from src.llm.models import (  # noqa: E402
    LLMMessage,
    MessageRole,
    ModelCapabilities,
)
from src.llm.prompt.pipeline import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT,
    PromptPipeline,
    PromptPipelineConfig,
)
from src.llm.streaming.manager import (  # noqa: E402
    StreamSession,
    StreamState,
    StreamingConfig,
    StreamingManager,
)


# ======================================================================
# Token Estimation Tests
# ======================================================================


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_short_text(self):
        tokens = estimate_tokens("Hello world")
        assert tokens >= 1

    def test_longer_text(self):
        text = "Hello world " * 100
        tokens = estimate_tokens(text)
        assert tokens > 10

    def test_custom_chars_per_token(self):
        text = "Hello"
        t1 = estimate_tokens(text, chars_per_token=2.0)
        t2 = estimate_tokens(text, chars_per_token=10.0)
        assert t1 > t2


# ======================================================================
# ContextManager Tests
# ======================================================================


class TestContextManager:
    def _make_context(self, **kwargs) -> ContextManager:
        return ContextManager(config=ContextConfig(**kwargs))

    async def test_basic_build(self):
        ctx = self._make_context()
        result = await ctx.build(
            session_id="s1",
            user_message="Hello",
            system_prompt="Be helpful",
        )
        assert isinstance(result, ContextResult)
        assert len(result.messages) >= 2  # system + user
        assert result.messages[0].role == MessageRole.SYSTEM
        assert result.messages[-1].role == MessageRole.USER
        assert result.messages[-1].content == "Hello"

    async def test_build_with_history(self):
        ctx = self._make_context()
        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        result = await ctx.build(
            session_id="s1",
            user_message="How are you?",
            history=history,
        )
        roles = [m.role for m in result.messages]
        assert MessageRole.USER in roles
        assert MessageRole.ASSISTANT in roles

    async def test_build_with_memory(self):
        ctx = self._make_context()
        memory = [
            {"role": "memory", "content": "User prefers Spanish"},
        ]
        result = await ctx.build(
            session_id="s1",
            user_message="Hello",
            memory_context=memory,
        )
        # Should have system message with memory content.
        system_msgs = [m for m in result.messages if m.role == MessageRole.SYSTEM]
        has_memory = any("Memoria recuperada" in m.content for m in system_msgs)
        assert has_memory

    async def test_build_with_planner(self):
        ctx = self._make_context()
        plan = {"plan": ["Write code", "Test code"]}
        result = await ctx.build(
            session_id="s1",
            user_message="Create a web app",
            planner_context=plan,
        )
        system_msgs = [m for m in result.messages if m.role == MessageRole.SYSTEM]
        has_plan = any("Plan de ejecución" in m.content for m in system_msgs)
        assert has_plan

    async def test_build_with_tool_results(self):
        ctx = self._make_context()
        tools = [
            {"tool_name": "terminal", "output": "Hello, World!\n"},
        ]
        result = await ctx.build(
            session_id="s1",
            user_message="Run the code",
            tool_results=tools,
        )
        tool_msgs = [m for m in result.messages if m.role == MessageRole.TOOL]
        assert len(tool_msgs) == 1

    async def test_build_without_tools_when_disabled(self):
        ctx = self._make_context(include_tool_results=False)
        tools = [{"tool_name": "terminal", "output": "result"}]
        result = await ctx.build(
            session_id="s1",
            user_message="Run",
            tool_results=tools,
        )
        tool_msgs = [m for m in result.messages if m.role == MessageRole.TOOL]
        assert len(tool_msgs) == 0

    async def test_trimming_when_over_budget(self):
        ctx = self._make_context(
            max_context_tokens=100,
            reserved_for_response=50,
        )
        # Create a lot of history.
        history = [
            {"role": "user", "content": f"Message {i} " * 50}
            for i in range(100)
        ]
        result = await ctx.build(
            session_id="s1",
            user_message="Hello",
            history=history,
        )
        # Should be trimmed to fit the budget.
        assert result.was_trimmed or result.token_count <= 100

    async def test_token_count(self):
        ctx = self._make_context()
        result = await ctx.build(
            session_id="s1",
            user_message="Hello",
            system_prompt="Be helpful",
        )
        assert result.token_count > 0

    async def test_budget_used(self):
        ctx = self._make_context()
        result = await ctx.build(
            session_id="s1",
            user_message="Hello",
            system_prompt="Be helpful",
        )
        assert 0.0 < result.budget_used <= 1.0

    def test_summary_management(self):
        ctx = self._make_context()
        ctx.set_summary("s1", "User asked about Python")
        assert ctx.get_summary("s1") == "User asked about Python"
        ctx.clear_summary("s1")
        assert ctx.get_summary("s1") == ""

    def test_remaining_budget(self):
        ctx = self._make_context(max_context_tokens=1000, reserved_for_response=200)
        msgs = [LLMMessage(role=MessageRole.USER, content="Hello")]
        remaining = ctx.remaining_budget(msgs)
        assert remaining > 0

    async def test_with_model_manager(self):
        """Test that ModelManager is used for capability-aware budgeting."""
        mm = MagicMock()
        mm.capabilities.return_value = ModelCapabilities(context_window=50000)
        ctx = ContextManager(model_manager=mm, config=ContextConfig(reserved_for_response=1000))
        result = await ctx.build(session_id="s1", user_message="Hello", model_id="gpt-4o")
        # Budget should be based on model capabilities.
        assert result.token_count > 0


# ======================================================================
# PromptPipeline Tests
# ======================================================================


class TestPromptPipeline:
    def _make_pipeline(self, **config_kwargs) -> PromptPipeline:
        ctx = ContextManager(config=ContextConfig())
        config = PromptPipelineConfig(**config_kwargs) if config_kwargs else None
        return PromptPipeline(ctx, config=config)

    async def test_basic_build(self):
        pp = self._make_pipeline()
        result = await pp.build(
            session_id="s1",
            user_message="Hello",
        )
        assert len(result.messages) >= 2
        # Should have default system prompt.
        assert result.messages[0].role == MessageRole.SYSTEM
        assert "Liz" in result.messages[0].content

    async def test_system_prompt_override(self):
        pp = self._make_pipeline()
        result = await pp.build(
            session_id="s1",
            user_message="Hello",
            system_prompt_override="Custom prompt",
        )
        assert result.messages[0].content == "Custom prompt"

    async def test_with_all_contexts(self):
        pp = self._make_pipeline()
        result = await pp.build(
            session_id="s1",
            user_message="Hello",
            history=[{"role": "user", "content": "Previous"}],
            memory_context=[{"role": "memory", "content": "Fact"}],
            plan=["Step 1", "Step 2"],
            task_context={"name": "Write code", "state": "running"},
            workflow_context={"name": "Build", "current_step": 2, "total_steps": 5},
            tool_results=[{"tool_name": "terminal", "output": "done"}],
        )
        assert result.token_count > 0
        pipeline_meta = result.meta.get("pipeline", {})
        assert pipeline_meta.get("history_turns") == 1
        assert pipeline_meta.get("memory_entries") == 1
        assert pipeline_meta.get("tool_results") == 1
        assert pipeline_meta.get("has_plan") is True
        assert pipeline_meta.get("has_task") is True
        assert pipeline_meta.get("has_workflow") is True

    async def test_memory_disabled(self):
        pp = self._make_pipeline(include_memory=False)
        result = await pp.build(
            session_id="s1",
            user_message="Hello",
            memory_context=[{"role": "memory", "content": "Fact"}],
        )
        # Memory should not appear.
        has_memory = any("Memoria" in m.content for m in result.messages if m.role == MessageRole.SYSTEM)
        assert not has_memory

    async def test_default_system_prompt_content(self):
        assert "Liz" in DEFAULT_SYSTEM_PROMPT
        assert "español" in DEFAULT_SYSTEM_PROMPT


# ======================================================================
# StreamingManager Tests
# ======================================================================


class TestStreamingManager:
    def _make_manager(self, **config_kwargs) -> StreamingManager:
        config = StreamingConfig(**config_kwargs) if config_kwargs else None
        return StreamingManager(config=config)

    async def test_create_and_cancel(self):
        sm = self._make_manager()
        # Create a mock provider.
        provider = AsyncMock()
        provider.name = "test"
        provider.stream = AsyncMock()

        # Start a stream but immediately cancel.
        cancel_event = asyncio.Event()
        chunks_yielded = []

        async def mock_stream(**kwargs):
            yield LLMMessage(role=MessageRole.ASSISTANT, content="chunk1").__dict__
            yield LLMMessage(role=MessageRole.ASSISTANT, content="chunk2").__dict__

        provider.stream.return_value = mock_stream()

        # We can't easily test the full stream without a real provider,
        # but we can test the manager's metadata.
        assert sm.active_count == 0
        assert sm.summary()["active_streams"] == 0

    def test_summary(self):
        sm = self._make_manager()
        s = sm.summary()
        assert s["active_streams"] == 0
        assert "timeout" in s

    def test_stream_session_creation(self):
        ss = StreamSession(id="test-123", session_id="s1")
        assert ss.state == StreamState.PENDING
        assert ss.chunks_received == 0
        assert ss.duration_ms >= 0

    def test_stream_session_to_dict(self):
        ss = StreamSession(id="test-123", session_id="s1", model="gpt-4o")
        d = ss.to_dict()
        assert d["id"] == "test-123"
        assert d["session_id"] == "s1"
        assert d["model"] == "gpt-4o"

    async def test_cancel_nonexistent(self):
        sm = self._make_manager()
        assert await sm.cancel("nonexistent") is False

    async def test_cancel_by_session(self):
        sm = self._make_manager()
        # No active streams, so nothing to cancel.
        count = await sm.cancel_by_session("s1")
        assert count == 0

    async def test_list_active(self):
        sm = self._make_manager()
        assert sm.list_active() == []

    def test_stream_config_defaults(self):
        config = StreamingConfig()
        assert config.default_timeout == 120.0
        assert config.max_buffer_size == 100
        assert config.retry_on_failure is True

    async def test_full_stream_with_mock_provider(self):
        """Test a complete stream using a mock provider."""
        sm = self._make_manager(default_timeout=10.0)

        # Create a mock provider that yields chunks.
        async def mock_stream(messages, **kwargs):
            for word in ["Hello", " ", "world", "!"]:
                from src.llm.models import LLMChunk
                yield LLMChunk(content=word, model="test", provider="mock")
            # Final chunk.
            from src.llm.models import LLMChunk
            yield LLMChunk(content="", model="test", provider="mock", finish_reason="stop")

        provider = AsyncMock()
        provider.name = "mock"
        provider.stream = mock_stream

        envelopes = []
        async for envelope in sm.stream(
            provider=provider,
            messages=[LLMMessage(role=MessageRole.USER, content="Say hello")],
            session_id="s1",
            model="test",
        ):
            envelopes.append(envelope)

        # Should have chunks + final message.
        chunk_envelopes = [e for e in envelopes if e["type"] == "chunk"]
        final_envelopes = [e for e in envelopes if e["type"] == "message"]
        assert len(chunk_envelopes) == 4
        assert len(final_envelopes) == 1
        assert final_envelopes[0]["content"] == "Hello world!"
        assert final_envelopes[0]["status"] == "completed"

    async def test_stream_cancellation(self):
        """Test that stream can be cancelled mid-stream."""
        sm = self._make_manager(default_timeout=10.0)
        cancel_after = asyncio.Event()

        async def mock_stream(messages, **kwargs):
            for i in range(100):
                if cancel_after.is_set():
                    return
                from src.llm.models import LLMChunk
                yield LLMChunk(content=f"chunk{i}", model="test", provider="mock")
                await asyncio.sleep(0.01)

        provider = AsyncMock()
        provider.name = "mock"
        provider.stream = mock_stream

        # Start the stream in a task.
        envelopes = []
        stream_task = asyncio.create_task(
            self._collect_envelopes(sm, provider, envelopes)
        )

        # Wait a bit then cancel.
        await asyncio.sleep(0.05)
        # Find the stream session and cancel it.
        active = sm.list_active()
        if active:
            await sm.cancel(active[0].id)
        cancel_after.set()

        await stream_task
        # Should have received some chunks before cancellation.
        assert len(envelopes) > 0

    @staticmethod
    async def _collect_envelopes(sm, provider, envelopes):
        async for envelope in sm.stream(
            provider=provider,
            messages=[LLMMessage(role=MessageRole.USER, content="Test")],
            session_id="s-cancel",
            model="test",
        ):
            envelopes.append(envelope)