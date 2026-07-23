"""Tests for GLM and Qwen providers.

Sprint 8 — New LLM Providers.
"""

from __future__ import annotations

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

from src.llm.models import ModelProvider  # noqa: E402
from src.llm.providers.glm import GlmProvider  # noqa: E402
from src.llm.providers.qwen import QwenProvider  # noqa: E402


# ======================================================================
# GLM Provider Tests
# ======================================================================


class TestGlmProvider:
    """Tests for the GLM/ZhipuAI provider."""

    def test_provider_name(self):
        assert GlmProvider.name == "glm"

    def test_default_model_id(self):
        provider = GlmProvider()
        assert provider.model_id == "glm-4-plus"

    def test_custom_model_id(self):
        provider = GlmProvider(model_id="glm-4-flash")
        assert provider.model_id == "glm-4-flash"

    def test_base_url_default(self):
        provider = GlmProvider()
        assert provider.base_url == "https://open.bigmodel.cn/api/paas/v4"

    def test_custom_base_url(self):
        provider = GlmProvider(base_url="https://custom.endpoint.com/v1")
        assert provider.base_url == "https://custom.endpoint.com/v1"

    def test_capabilities_glm4_plus(self):
        provider = GlmProvider(model_id="glm-4-plus")
        caps = provider.capabilities
        assert caps.chat is True
        assert caps.streaming is True
        assert caps.function_calling is True
        assert caps.vision is False
        assert caps.context_window == 128000

    def test_capabilities_glm4_long(self):
        provider = GlmProvider(model_id="glm-4-long")
        caps = provider.capabilities
        assert caps.context_window == 1048576  # 1M tokens

    def test_capabilities_glm4v(self):
        provider = GlmProvider(model_id="glm-4v")
        caps = provider.capabilities
        assert caps.vision is True

    def test_capabilities_unknown_model(self):
        provider = GlmProvider(model_id="unknown-model")
        caps = provider.capabilities
        assert caps.chat is True
        assert caps.streaming is True
        assert caps.context_window == 128000

    def test_headers_include_auth(self):
        provider = GlmProvider(api_key="test-key-123")
        headers = provider._build_headers()
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test-key-123"

    @pytest.mark.asyncio
    async def test_chat_success(self):
        provider = GlmProvider(api_key="test-key")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "chatcmpl-123",
            "model": "glm-4-plus",
            "choices": [
                {
                    "message": {"content": "Hello! How can I help?", "role": "assistant"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch.object(provider, "_get_client", return_value=mock_client):
            messages = [
                MagicMock(to_openai_format=lambda: {"role": "user", "content": "Hi"}),
            ]
            result = await provider.chat(messages)
            assert result.content == "Hello! How can I help?"
            assert result.model == "glm-4-plus"
            assert result.provider == "glm"

    @pytest.mark.asyncio
    async def test_health_success(self):
        provider = GlmProvider(api_key="test-key")
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch.object(provider, "_get_client", return_value=mock_client):
            healthy = await provider.health()
            assert healthy is True

    @pytest.mark.asyncio
    async def test_health_failure(self):
        provider = GlmProvider(api_key="test-key")

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")

        with patch.object(provider, "_get_client", return_value=mock_client):
            healthy = await provider.health()
            assert healthy is False

    @pytest.mark.asyncio
    async def test_close(self):
        provider = GlmProvider(api_key="test-key")
        mock_client = AsyncMock()
        provider._client = mock_client
        await provider.close()
        mock_client.aclose.assert_called_once()


# ======================================================================
# Qwen Provider Tests
# ======================================================================


class TestQwenProvider:
    """Tests for the Qwen/Alibaba Cloud provider."""

    def test_provider_name(self):
        assert QwenProvider.name == "qwen"

    def test_default_model_id(self):
        provider = QwenProvider()
        assert provider.model_id == "qwen-max"

    def test_custom_model_id(self):
        provider = QwenProvider(model_id="qwen-turbo")
        assert provider.model_id == "qwen-turbo"

    def test_base_url_default(self):
        provider = QwenProvider()
        assert provider.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_custom_base_url(self):
        provider = QwenProvider(base_url="https://custom.endpoint.com/v1")
        assert provider.base_url == "https://custom.endpoint.com/v1"

    def test_capabilities_qwen_max(self):
        provider = QwenProvider(model_id="qwen-max")
        caps = provider.capabilities
        assert caps.chat is True
        assert caps.streaming is True
        assert caps.function_calling is True
        assert caps.vision is False
        assert caps.context_window == 32768

    def test_capabilities_qwen_long(self):
        provider = QwenProvider(model_id="qwen-long")
        caps = provider.capabilities
        assert caps.context_window == 1048576  # 1M tokens

    def test_capabilities_qwen_vl(self):
        provider = QwenProvider(model_id="qwen-vl-max")
        caps = provider.capabilities
        assert caps.vision is True

    def test_capabilities_unknown_model(self):
        provider = QwenProvider(model_id="unknown-model")
        caps = provider.capabilities
        assert caps.chat is True
        assert caps.streaming is True
        assert caps.context_window == 32768

    def test_headers_include_auth(self):
        provider = QwenProvider(api_key="test-key-456")
        headers = provider._build_headers()
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test-key-456"

    @pytest.mark.asyncio
    async def test_chat_success(self):
        provider = QwenProvider(api_key="test-key")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "chatcmpl-456",
            "model": "qwen-max",
            "choices": [
                {
                    "message": {"content": "Hi! I'm Qwen.", "role": "assistant"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 15, "completion_tokens": 25},
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch.object(provider, "_get_client", return_value=mock_client):
            messages = [
                MagicMock(to_openai_format=lambda: {"role": "user", "content": "Hello"}),
            ]
            result = await provider.chat(messages)
            assert result.content == "Hi! I'm Qwen."
            assert result.model == "qwen-max"
            assert result.provider == "qwen"

    @pytest.mark.asyncio
    async def test_chat_with_tool_calls(self):
        provider = QwenProvider(api_key="test-key")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "chatcmpl-789",
            "model": "qwen-max",
            "choices": [
                {
                    "message": {
                        "content": "",
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "terminal",
                                    "arguments": '{"command": "ls"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch.object(provider, "_get_client", return_value=mock_client):
            messages = [
                MagicMock(to_openai_format=lambda: {"role": "user", "content": "List files"}),
            ]
            result = await provider.chat(messages)
            assert len(result.tool_calls) == 1
            assert result.tool_calls[0]["name"] == "terminal"

    @pytest.mark.asyncio
    async def test_health_success(self):
        provider = QwenProvider(api_key="test-key")
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch.object(provider, "_get_client", return_value=mock_client):
            healthy = await provider.health()
            assert healthy is True

    @pytest.mark.asyncio
    async def test_health_failure(self):
        provider = QwenProvider(api_key="test-key")

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")

        with patch.object(provider, "_get_client", return_value=mock_client):
            healthy = await provider.health()
            assert healthy is False

    @pytest.mark.asyncio
    async def test_close(self):
        provider = QwenProvider(api_key="test-key")
        mock_client = AsyncMock()
        provider._client = mock_client
        await provider.close()
        mock_client.aclose.assert_called_once()


# ======================================================================
# Enum Tests
# ======================================================================


class TestNewProvidersInEnum:
    """Verify GLM and Qwen are in the ModelProvider enum."""

    def test_glm_in_enum(self):
        assert hasattr(ModelProvider, "GLM")
        assert ModelProvider.GLM.value == "glm"

    def test_qwen_in_enum(self):
        assert hasattr(ModelProvider, "QWEN")
        assert ModelProvider.QWEN.value == "qwen"

    def test_total_provider_count(self):
        assert len(ModelProvider) == 10
