"""Configuration loader.

Reads JSON configuration files from the `config/` directory at the project
root and exposes them as strongly-typed Pydantic models. The active
environment is selected via the `LIZ_ENV` environment variable
(`development` by default).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Environment = Literal["development", "production"]


class AIConfig(BaseModel):
    """AI provider configuration."""

    provider: str = "openai"
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 4096
    api_key_env: str = "LIZ_AI_API_KEY"


class PermissionsConfig(BaseModel):
    """Permission policy configuration."""

    mode: Literal["Confirmation", "Automatic"] = "Confirmation"
    allowed_commands: list[str] = Field(default_factory=list)
    denied_commands: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    """Top-level application configuration."""

    version: str = "0.1.1"
    environment: Environment = "development"
    ai: AIConfig = Field(default_factory=AIConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)


def _config_dir() -> Path:
    """Return the absolute path to the config directory at project root."""
    here = Path(__file__).resolve()
    # src/core/config.py -> ../../../config
    return here.parents[3] / "config"


def load_config(environment: Environment | None = None) -> AppConfig:
    """Load the configuration for the given (or default) environment.

    Args:
        environment: Optional environment override. When None, falls back
            to the `LIZ_ENV` environment variable, then to "development".

    Returns:
        The parsed AppConfig instance.
    """
    env = environment or os.getenv("LIZ_ENV", "development")  # type: ignore[assignment]
    config_path = _config_dir() / f"{env}.json"

    if not config_path.exists():
        # Fall back to development defaults if the file is missing.
        config_path = _config_dir() / "development.json"

    with config_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    return AppConfig.model_validate(raw)
