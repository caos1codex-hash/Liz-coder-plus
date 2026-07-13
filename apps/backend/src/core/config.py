"""Configuration loader.

Reads JSON configuration files from the `config/` directory at the project
root and exposes them as strongly-typed Pydantic models. The active
environment is selected via the `LIZ_ENV` environment variable
(`development` by default).

Sprint 1.4 — Added LoggingConfig, BackendConfig, MemoryConfig.
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


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = "INFO"
    to_file: bool = False
    file_path: str = "logs/development.log"


class BackendConfig(BaseModel):
    """Backend server configuration."""

    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])


class MemoryConfig(BaseModel):
    """Persistent memory configuration."""

    enabled: bool = False
    provider: str = "sqlite"
    path: str = "data/liz_memory.db"
    max_history: int = 200


class AppConfig(BaseModel):
    """Top-level application configuration.

    model_config = ConfigDict(extra="ignore")
    """

    version: str = "0.1.2"
    environment: Environment = "development"
    ai: AIConfig = Field(default_factory=AIConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)


def _config_dir() -> Path:
    """Return the absolute path to the config directory at project root."""
    here = Path(__file__).resolve()
    # src/core/config.py -> ../../../config
    return here.parents[3] / "config"


def load_config(environment: str | None = None) -> AppConfig:
    """Load the configuration for the given (or default) environment.

    Args:
        environment: Optional environment override. When None, falls back
            to the ``LIZ_ENV`` environment variable, then to "development".

    Returns:
        The parsed AppConfig instance.
    """
    env: str = environment or os.getenv("LIZ_ENV", "development")
    config_path = _config_dir() / f"{env}.json"

    if not config_path.exists():
        # Fall back to development defaults if the file is missing.
        config_path = _config_dir() / "development.json"

    with config_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    return AppConfig.model_validate(raw)