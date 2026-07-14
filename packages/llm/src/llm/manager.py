"""Model Manager.

Central registry for all LLM models. Handles registration, activation,
deactivation, capability queries, provider selection, model caching,
warmup, and health checking.

This is the single entry point for the rest of Liz Coder Plus to
interact with the LLM subsystem. The Orchestrator holds a reference
to the ModelManager and uses it to obtain providers.

Sprint 1.9 — Phase 2.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import time
from typing import Any

from src.llm.models import (
    ModelCapabilities,
    ModelInfo,
    ModelProvider,
    ModelStatus,
)

logger = logging.getLogger(__name__)

# Provider class map — avoids reconstruction on every _create_provider call.
_PROVIDER_CLASS_MAP: dict[ModelProvider, tuple[str, str]] = {
    ModelProvider.OLLAMA: ("src.llm.providers.ollama", "OllamaProvider"),
    ModelProvider.OPENAI: ("src.llm.providers.openai", "OpenAIProvider"),
    ModelProvider.ANTHROPIC: ("src.llm.providers.anthropic", "AnthropicProvider"),
    ModelProvider.GOOGLE: ("src.llm.providers.google", "GoogleProvider"),
    ModelProvider.OPENROUTER: ("src.llm.providers.openrouter", "OpenRouterProvider"),
}


class ModelManager:
    """Central registry and lifecycle manager for LLM models.

    Responsibilities:
        - Register models with their provider, capabilities, and config.
        - Activate / deactivate models.
        - Select the best provider for a given request.
        - Cache provider instances for reuse.
        - Warmup models (pre-load weights / connections).
        - Periodic health checking.

    Usage::

        mm = ModelManager()
        mm.register(ModelInfo(model_id="gpt-4o", provider=ModelProvider.OPENAI, ...))
        mm.register(ModelInfo(model_id="llama3:8b", provider=ModelProvider.OLLAMA, ...))
        mm.activate("gpt-4o")

        provider = mm.get_provider("gpt-4o")
        response = await provider.chat(messages)

        await mm.close()
    """

    def __init__(
        self,
        *,
        default_model: str = "",
        health_check_interval: float = 120.0,
        health_check_timeout: float = 10.0,
    ) -> None:
        self._models: dict[str, ModelInfo] = {}
        self._provider_cache: dict[str, Any] = {}
        self._default_model = default_model
        self._health_check_interval = health_check_interval
        self._health_check_timeout = health_check_timeout
        self._initialized = False
        self._health_task: asyncio.Task[Any] | None = None
        self._shutdown = False
        logger.info("ModelManager created (default=%s)", default_model or "none")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def default_model(self) -> str:
        """The currently active default model identifier."""
        return self._default_model

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialize the ModelManager and start health checking.

        Safe to call multiple times.
        """
        if self._initialized:
            return
        self._initialized = True
        self._health_task = asyncio.create_task(self._health_loop())
        logger.info("ModelManager initialized with %d model(s)", len(self._models))

    async def close(self) -> None:
        """Shut down all cached providers and stop health checking.

        Safe to call multiple times.
        """
        if self._shutdown:
            return
        self._shutdown = True

        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        for model_id, provider in self._provider_cache.items():
            try:
                await provider.close()
            except Exception:  # noqa: BLE001
                logger.exception("Error closing provider for %s", model_id)

        self._provider_cache.clear()
        self._initialized = False
        logger.info("ModelManager closed")

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, model_info: ModelInfo) -> None:
        """Register a model with the manager.

        Args:
            model_info: Full model metadata including provider and capabilities.

        Raises:
            ValueError: If a model with the same ID is already registered.
        """
        if model_info.model_id in self._models:
            raise ValueError(
                f"Model '{model_info.model_id}' is already registered. "
                f"Use update() to modify it."
            )
        self._models[model_info.model_id] = model_info

        # Auto-set as default if this is the first model registered.
        if not self._default_model:
            self._default_model = model_info.model_id

        logger.info(
            "Registered model '%s' (provider=%s, ctx=%d)",
            model_info.model_id,
            model_info.provider.value,
            model_info.capabilities.context_window,
        )

    def update(self, model_id: str, **kwargs: Any) -> None:
        """Update fields on an existing model registration.

        Args:
            model_id: The model to update.
            **kwargs:  Fields to update (display_name, base_url, etc.).

        Raises:
            KeyError: If the model is not registered.
        """
        info = self._models.get(model_id)
        if info is None:
            raise KeyError(f"Model '{model_id}' is not registered")

        for key, value in kwargs.items():
            if hasattr(info, key):
                setattr(info, key, value)
            else:
                logger.warning("Unknown field '%s' for model '%s'", key, model_id)

        logger.info("Updated model '%s': %s", model_id, list(kwargs.keys()))

    def unregister(self, model_id: str) -> bool:
        """Remove a model from the registry.

        Also removes the cached provider if one exists.

        Args:
            model_id: The model to remove.

        Returns:
            True if the model was found and removed.
        """
        if model_id not in self._models:
            return False

        # Close and remove cached provider.
        provider = self._provider_cache.pop(model_id, None)
        if provider is not None:
            try:
                if hasattr(provider, "close") and callable(provider.close):
                    asyncio.get_event_loop().create_task(provider.close())
            except RuntimeError:
                pass

        del self._models[model_id]

        if self._default_model == model_id:
            remaining = list(self._models.keys())
            self._default_model = remaining[0] if remaining else ""

        logger.info("Unregistered model '%s'", model_id)
        return True

    # ------------------------------------------------------------------
    # Activation / Deactivation
    # ------------------------------------------------------------------

    def activate(self, model_id: str) -> None:
        """Activate a registered model.

        Args:
            model_id: The model to activate.

        Raises:
            KeyError: If the model is not registered.
        """
        info = self._models.get(model_id)
        if info is None:
            raise KeyError(f"Model '{model_id}' is not registered")
        info.status = ModelStatus.ACTIVE
        self._default_model = model_id
        logger.info("Activated model '%s'", model_id)

    def deactivate(self, model_id: str) -> None:
        """Deactivate a registered model.

        Args:
            model_id: The model to deactivate.

        Raises:
            KeyError: If the model is not registered.
        """
        info = self._models.get(model_id)
        if info is None:
            raise KeyError(f"Model '{model_id}' is not registered")
        info.status = ModelStatus.INACTIVE
        if self._default_model == model_id:
            remaining_active = [
                m.model_id for m in self._models.values()
                if m.status == ModelStatus.ACTIVE
            ]
            self._default_model = remaining_active[0] if remaining_active else ""
        logger.info("Deactivated model '%s'", model_id)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get(self, model_id: str) -> ModelInfo | None:
        """Get model info by ID.

        Args:
            model_id: The model identifier.

        Returns:
            ModelInfo if found, None otherwise.
        """
        return self._models.get(model_id)

    def list_models(
        self,
        *,
        provider: ModelProvider | None = None,
        status: ModelStatus | None = None,
    ) -> list[ModelInfo]:
        """List registered models with optional filters.

        Args:
            provider: Filter by provider.
            status:   Filter by status.

        Returns:
            List of matching ModelInfo instances.
        """
        results = list(self._models.values())
        if provider is not None:
            results = [m for m in results if m.provider == provider]
        if status is not None:
            results = [m for m in results if m.status == status]
        return results

    def list_active(self) -> list[ModelInfo]:
        """List all active models."""
        return self.list_models(status=ModelStatus.ACTIVE)

    def list_by_provider(self, provider: ModelProvider) -> list[ModelInfo]:
        """List all models for a given provider."""
        return self.list_models(provider=provider)

    def capabilities(self, model_id: str) -> ModelCapabilities:
        """Get the capabilities of a model.

        Args:
            model_id: The model identifier.

        Returns:
            ModelCapabilities for the model.

        Raises:
            KeyError: If the model is not registered.
        """
        info = self._models.get(model_id)
        if info is None:
            raise KeyError(f"Model '{model_id}' is not registered")
        return info.capabilities

    def select_provider(
        self,
        model_id: str = "",
        *,
        require_streaming: bool = False,
        require_function_calling: bool = False,
    ) -> str:
        """Select the best model for a given request.

        Selection logic:
            1. If model_id is given and registered, use it.
            2. If no model_id, use the default model.
            3. If requirements (streaming, function_calling) are given
               and the selected model doesn't support them, find one
               that does.

        Args:
            model_id: Preferred model identifier.
            require_streaming: Whether streaming is required.
            require_function_calling: Whether function calling is required.

        Returns:
            The selected model identifier.

        Raises:
            ValueError: If no suitable model is found.
        """
        candidates = self.list_active()

        # Try the preferred model first.
        target_id = model_id or self._default_model
        if target_id:
            info = self._models.get(target_id)
            if info is not None and info.status == ModelStatus.ACTIVE:
                if self._meets_requirements(
                    info, require_streaming, require_function_calling
                ):
                    return target_id

        # Search for a model that meets requirements.
        for info in candidates:
            if self._meets_requirements(
                info, require_streaming, require_function_calling
            ):
                return info.model_id

        raise ValueError(
            f"No active model meets requirements "
            f"(streaming={require_streaming}, "
            f"function_calling={require_function_calling})"
        )

    @staticmethod
    def _meets_requirements(
        info: ModelInfo,
        streaming: bool,
        function_calling: bool,
    ) -> bool:
        """Check if a model meets the given capability requirements."""
        caps = info.capabilities
        if streaming and not caps.streaming:
            return False
        if function_calling and not caps.function_calling:
            return False
        return True

    # ------------------------------------------------------------------
    # Provider Cache
    # ------------------------------------------------------------------

    def get_provider(self, model_id: str = "") -> Any:
        """Get a cached provider instance for a model.

        If no cached instance exists, creates one by importing and
        instantiating the appropriate provider class.

        Args:
            model_id: The model identifier. Falls back to default.

        Returns:
            A provider instance satisfying the LLMProvider protocol.

        Raises:
            ValueError: If the model is not registered.
            RuntimeError: If the provider cannot be instantiated.
        """
        effective_id = model_id or self._default_model
        if not effective_id:
            raise ValueError("No model specified and no default model set")

        info = self._models.get(effective_id)
        if info is None:
            raise ValueError(f"Model '{effective_id}' is not registered")

        # Return cached provider if available.
        cached = self._provider_cache.get(effective_id)
        if cached is not None:
            return cached

        # Create new provider instance.
        provider = self._create_provider(info)
        self._provider_cache[effective_id] = provider
        return provider

    def _create_provider(self, info: ModelInfo) -> Any:
        """Instantiate the correct provider class for a model.

        Uses lazy imports to avoid requiring all provider SDKs.
        """
        module_path, class_name = _PROVIDER_CLASS_MAP.get(
            info.provider, ("", "")
        )
        if not module_path:
            raise RuntimeError(
                f"Unknown provider '{info.provider.value}' for model '{info.model_id}'"
            )

        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(
                f"Cannot import provider '{class_name}' from '{module_path}': {exc}"
            ) from exc

        # Resolve API key from environment.
        api_key = ""
        if info.api_key_env:
            api_key = os.getenv(info.api_key_env, "")

        return cls(
            model_id=info.model_id,
            base_url=info.base_url or None,
            api_key=api_key or None,
            capabilities=info.capabilities,
            extra=info.extra,
        )

    # ------------------------------------------------------------------
    # Warmup
    # ------------------------------------------------------------------

    async def warmup(self, model_id: str = "") -> bool:
        """Warm up a model by sending a lightweight request.

        This pre-loads model weights (local) or establishes
        connection pools (cloud).

        Args:
            model_id: Model to warm up. Falls back to default.

        Returns:
            True if warmup succeeded.
        """
        effective_id = model_id or self._default_model
        if not effective_id:
            logger.warning("No model to warmup")
            return False

        info = self._models.get(effective_id)
        if info is None:
            logger.warning("Model '%s' not registered for warmup", effective_id)
            return False

        info.status = ModelStatus.WARMING
        logger.info("Warming up model '%s'...", effective_id)

        try:
            provider = self.get_provider(effective_id)
            healthy = await provider.health()
            if healthy:
                info.status = ModelStatus.ACTIVE
                logger.info("Model '%s' warmed up successfully", effective_id)
                return True
            else:
                info.status = ModelStatus.UNHEALTHY
                logger.warning("Model '%s' health check failed during warmup", effective_id)
                return False
        except Exception:  # noqa: BLE001
            info.status = ModelStatus.UNHEALTHY
            logger.exception("Warmup failed for model '%s'", effective_id)
            return False

    # ------------------------------------------------------------------
    # Health Checking
    # ------------------------------------------------------------------

    async def health_check(self, model_id: str = "") -> bool:
        """Check health of a specific model or all active models.

        Args:
            model_id: Model to check. Empty means all active models.

        Returns:
            True if all checked models are healthy.
        """
        if model_id:
            info = self._models.get(model_id)
            if info is None:
                return False
            return await self._check_single(info)

        all_healthy = True
        for info in self.list_active():
            if not await self._check_single(info):
                all_healthy = False
        return all_healthy

    async def _check_single(self, info: ModelInfo) -> bool:
        """Check health of a single model."""
        try:
            provider = self.get_provider(info.model_id)
            healthy = await asyncio.wait_for(
                provider.health(),
                timeout=self._health_check_timeout,
            )
            info.status = ModelStatus.ACTIVE if healthy else ModelStatus.UNHEALTHY
            return healthy
        except asyncio.TimeoutError:
            info.status = ModelStatus.UNHEALTHY
            logger.warning("Health check timeout for '%s'", info.model_id)
            return False
        except Exception:  # noqa: BLE001
            info.status = ModelStatus.UNHEALTHY
            logger.exception("Health check failed for '%s'", info.model_id)
            return False

    async def _health_loop(self) -> None:
        """Background loop that periodically checks model health."""
        while not self._shutdown:
            try:
                await asyncio.sleep(self._health_check_interval)
                if self._shutdown:
                    break
                await self.health_check()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.exception("Health check loop error")

    # ------------------------------------------------------------------
    # Status / Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a summary of the ModelManager state."""
        models_by_status: dict[str, int] = {}
        for info in self._models.values():
            status = info.status.value
            models_by_status[status] = models_by_status.get(status, 0) + 1

        return {
            "default_model": self._default_model,
            "total_models": len(self._models),
            "cached_providers": len(self._provider_cache),
            "models_by_status": models_by_status,
            "models": [m.to_dict() for m in self._models.values()],
        }