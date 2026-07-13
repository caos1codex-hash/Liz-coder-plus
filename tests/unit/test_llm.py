"""Tests for the LLM package — models, manager, and providers.

Sprint 1.9 — Phase 7.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the LLM package is importable.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LLM_ROOT = _PROJECT_ROOT / "packages" / "llm"
if str(_LLM_ROOT) not in sys.path:
    sys.path.insert(0, str(_LLM_ROOT))

# Clear any cached src modules.
for key in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(key, None)

from src.llm.models import (  # noqa: E402
    LLMChunk,
    LLMMessage,
    LLMResponse,
    MessageRole,
    ModelCapabilities,
    ModelInfo,
    ModelProvider,
    ModelStatus,
    TokenUsage,
)
from src.llm.manager import ModelManager  # noqa: E402



# ======================================================================
# Model Tests
# ======================================================================


class TestLLMMessage:
    """Tests for the LLMMessage dataclass."""

    def test_user_message(self):
        msg = LLMMessage(role=MessageRole.USER, content="Hello")
        assert msg.role == MessageRole.USER
        assert msg.content == "Hello"
        assert msg.name == ""

    def test_to_dict(self):
        msg = LLMMessage(role=MessageRole.ASSISTANT, content="Hi there", name="liz")
        d = msg.to_dict()
        assert d["role"] == "assistant"
        assert d["content"] == "Hi there"
        assert d["name"] == "liz"

    def test_to_openai_format(self):
        msg = LLMMessage(role=MessageRole.SYSTEM, content="Be helpful")
        d = msg.to_openai_format()
        assert d["role"] == "system"
        assert d["content"] == "Be helpful"

    def test_to_anthropic_format_system_converted(self):
        msg = LLMMessage(role=MessageRole.SYSTEM, content="Be helpful")
        d = msg.to_anthropic_format()
        assert d["role"] == "user"
        assert "[System]:" in d["content"]

    def test_to_anthropic_format_user_unchanged(self):
        msg = LLMMessage(role=MessageRole.USER, content="Hello")
        d = msg.to_anthropic_format()
        assert d["role"] == "user"
        assert d["content"] == "Hello"

    def test_to_google_format_assistant_is_model(self):
        msg = LLMMessage(role=MessageRole.ASSISTANT, content="Hi")
        d = msg.to_google_format()
        assert d["role"] == "model"
        assert d["parts"][0]["text"] == "Hi"


class TestModelInfo:
    """Tests for the ModelInfo dataclass."""

    def test_defaults(self):
        info = ModelInfo(
            model_id="test-model",
            provider=ModelProvider.OPENAI,
        )
        assert info.model_id == "test-model"
        assert info.provider == ModelProvider.OPENAI
        assert info.status == ModelStatus.INACTIVE
        assert info.display_name == ""
        assert info.capabilities.chat is True

    def test_to_dict(self):
        info = ModelInfo(
            model_id="gpt-4o",
            provider=ModelProvider.OPENAI,
            display_name="GPT-4o",
            capabilities=ModelCapabilities(context_window=128000),
        )
        d = info.to_dict()
        assert d["model_id"] == "gpt-4o"
        assert d["provider"] == "openai"
        assert d["display_name"] == "GPT-4o"
        assert d["capabilities"]["context_window"] == 128000
        assert d["status"] == "inactive"


class TestLLMResponse:
    """Tests for the LLMResponse dataclass."""

    def test_basic_response(self):
        resp = LLMResponse(
            content="Hello!",
            model="gpt-4o-mini",
            provider="openai",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
        )
        assert resp.content == "Hello!"
        assert resp.usage.total_tokens == 15
        assert resp.duration_ms == 0

    def test_to_dict(self):
        resp = LLMResponse(content="Hi", model="test", provider="test")
        d = resp.to_dict()
        assert d["content"] == "Hi"
        assert d["usage"]["total_tokens"] == 0


class TestLLMChunk:
    """Tests for the LLMChunk dataclass."""

    def test_is_final(self):
        chunk = LLMChunk(content="Hello", finish_reason="stop")
        assert chunk.is_final is True

    def test_is_not_final(self):
        chunk = LLMChunk(content="Hello")
        assert chunk.is_final is False


# ======================================================================
# ModelManager Tests
# ======================================================================


class TestModelManager:
    """Tests for the ModelManager."""

    def _make_manager(self) -> ModelManager:
        return ModelManager(default_model="", health_check_interval=9999)

    def _make_model_info(
        self,
        model_id: str = "test-model",
        provider: ModelProvider = ModelProvider.OPENAI,
        status: ModelStatus = ModelStatus.INACTIVE,
    ) -> ModelInfo:
        return ModelInfo(
            model_id=model_id,
            provider=provider,
            display_name=model_id,
            capabilities=ModelCapabilities(
                chat=True, streaming=True, context_window=4096,
            ),
            status=status,
        )

    def test_register_model(self):
        mm = self._make_manager()
        info = self._make_model_info("gpt-4o-mini")
        mm.register(info)
        assert mm.get("gpt-4o-mini") is not None

    def test_register_duplicate_raises(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("m1"))
        with pytest.raises(ValueError, match="already registered"):
            mm.register(self._make_model_info("m1"))

    def test_register_sets_default_on_first(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("first-model"))
        assert mm.default_model == "first-model"

    def test_unregister(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("m1"))
        assert mm.unregister("m1") is True
        assert mm.get("m1") is None

    def test_unregister_nonexistent(self):
        mm = self._make_manager()
        assert mm.unregister("nope") is False

    def test_activate(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("m1"))
        mm.activate("m1")
        assert mm.get("m1").status == ModelStatus.ACTIVE
        assert mm.default_model == "m1"

    def test_deactivate(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("m1"))
        mm.activate("m1")
        mm.deactivate("m1")
        assert mm.get("m1").status == ModelStatus.INACTIVE

    def test_list_models(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("m1", provider=ModelProvider.OPENAI))
        mm.register(self._make_model_info("m2", provider=ModelProvider.OLLAMA))
        assert len(mm.list_models()) == 2
        assert len(mm.list_models(provider=ModelProvider.OPENAI)) == 1

    def test_list_active(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("m1"))
        mm.register(self._make_model_info("m2"))
        mm.activate("m1")
        assert len(mm.list_active()) == 1

    def test_capabilities(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("m1"))
        caps = mm.capabilities("m1")
        assert caps.chat is True
        assert caps.context_window == 4096

    def test_capabilities_nonexistent_raises(self):
        mm = self._make_manager()
        with pytest.raises(KeyError):
            mm.capabilities("nope")

    def test_select_provider_default(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("m1"))
        mm.activate("m1")
        assert mm.select_provider() == "m1"

    def test_select_provider_specific(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("m1"))
        mm.activate("m1")
        assert mm.select_provider("m1") == "m1"

    def test_select_provider_with_requirements(self):
        mm = self._make_manager()
        # m1 has streaming=False, m2 has streaming=True
        info1 = self._make_model_info("m1")
        info1.capabilities.streaming = False
        info2 = self._make_model_info("m2", provider=ModelProvider.OLLAMA)
        info2.capabilities.streaming = True
        mm.register(info1)
        mm.register(info2)
        mm.activate("m1")
        mm.activate("m2")
        # Should skip m1 and select m2 for streaming requirement.
        assert mm.select_provider(require_streaming=True) == "m2"

    def test_select_provider_no_match_raises(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("m1"))
        mm.activate("m1")
        # m1 doesn't have function_calling.
        with pytest.raises(ValueError, match="No active model"):
            mm.select_provider(require_function_calling=True)

    def test_update(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("m1"))
        mm.update("m1", display_name="New Name")
        assert mm.get("m1").display_name == "New Name"

    def test_update_nonexistent_raises(self):
        mm = self._make_manager()
        with pytest.raises(KeyError):
            mm.update("nope", display_name="X")

    def test_summary(self):
        mm = self._make_manager()
        mm.register(self._make_model_info("m1"))
        mm.activate("m1")
        s = mm.summary()
        assert s["total_models"] == 1
        assert s["default_model"] == "m1"
        assert "active" in s["models_by_status"]

    async def test_initialize_and_close(self):
        mm = self._make_manager()
        await mm.initialize()
        assert mm.is_initialized
        await mm.close()
        assert not mm.is_initialized

    async def test_health_check_no_models(self):
        mm = self._make_manager()
        assert await mm.health_check() is True  # No models = vacuously true

    async def test_warmup_no_model(self):
        mm = self._make_manager()
        assert await mm.warmup() is False