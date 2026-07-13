"""Common utilities: logging setup, ID generation, timestamps."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logging with a consistent format."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def generate_id(prefix: str = "") -> str:
    """Generate a unique identifier with an optional prefix."""
    raw = uuid.uuid4().hex
    return f"{prefix}_{raw}" if prefix else raw


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)
