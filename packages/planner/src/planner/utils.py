"""Utility helpers for the planner package (Sprint 2.8).

Small, dependency-free helpers used across the planner modules:

- :func:`new_task_id` — generate a short, sortable, unique id.
- :func:`now_iso` / :func:`now_epoch` — timezone-aware timestamps.
- :func:`parse_bool` — robust boolean parsing for configuration.
- :func:`clamp` — numeric clamping.
- :func:`merge_dicts` — deep merge for metadata.
- :func:`coerce_int` / :func:`coerce_float` — defensive numeric coercion.

These helpers are intentionally tiny and side-effect free so they can
be used in dataclass ``__post_init__`` and other hot paths without
worry.
"""

from __future__ import annotations

import secrets
import string
import time
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime
from typing import Any

# ----------------------------------------------------------------------
# ID generation
# ----------------------------------------------------------------------

_ID_ALPHABET = string.ascii_lowercase + string.digits


def new_task_id(prefix: str = "task") -> str:
    """Return a new opaque task id.

    The id is composed of a lowercase prefix, a timestamp component
    (base36-encoded epoch milliseconds, 6 chars) and a random suffix
    (4 chars). The result is short, URL-safe, sortable by creation
    time, and has negligible collision probability.

    Example::

        >>> new_task_id("plan")
        'plan-lq3k7a-9f2d'
    """
    epoch_ms = int(time.time() * 1000)
    ts = _to_base36(epoch_ms)[-6:]
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(4))
    return f"{prefix}-{ts}-{suffix}"


def _to_base36(n: int) -> str:
    if n == 0:
        return "0"
    chars: list[str] = []
    while n > 0:
        n, rem = divmod(n, 36)
        chars.append(_ID_ALPHABET[rem])
    return "".join(reversed(chars))


# ----------------------------------------------------------------------
# Time
# ----------------------------------------------------------------------


def now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def now_epoch() -> float:
    """Return the current UTC time as epoch seconds."""
    return time.time()


# ----------------------------------------------------------------------
# Numeric helpers
# ----------------------------------------------------------------------


def clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` to the closed range ``[low, high]``."""
    if low > high:
        low, high = high, low
    return max(low, min(high, value))


def coerce_int(value: Any, default: int = 0) -> int:
    """Coerce ``value`` to ``int``; return ``default`` on failure.

    Accepts int, float (truncated), and numeric strings. Non-numeric
    strings return ``default`` rather than raising.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return default
    return default


def coerce_float(value: Any, default: float = 0.0) -> float:
    """Coerce ``value`` to ``float``; return ``default`` on failure."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


# ----------------------------------------------------------------------
# Dict helpers
# ----------------------------------------------------------------------


def merge_dicts(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Return a new dict that is a deep-merge of ``base`` and ``overlay``.

    ``overlay`` wins on conflicts. Nested dicts are merged recursively.
    Lists and scalars from ``overlay`` replace those from ``base``.
    The inputs are not mutated.
    """
    result: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], Mapping) and isinstance(value, Mapping):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def parse_bool(value: Any, default: bool = False) -> bool:
    """Parse a permissive boolean value.

    Truthy strings: ``true``, ``yes``, ``1``, ``on`` (case-insensitive).
    Falsy strings: ``false``, ``no``, ``0``, ``off`` (case-insensitive).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "yes", "1", "on"}:
            return True
        if v in {"false", "no", "0", "off"}:
            return False
    return default


# ----------------------------------------------------------------------
# Misc
# ----------------------------------------------------------------------


def safe_get(mapping: Mapping[str, Any], path: str, default: Any = None) -> Any:
    """Get a nested value from ``mapping`` via a dotted ``path``.

    Example::

        >>> safe_get({"a": {"b": {"c": 1}}}, "a.b.c")
        1
        >>> safe_get({}, "a.b.c", default="x")
        'x'
    """
    current: Any = mapping
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def update_inplace(target: MutableMapping[str, Any], **updates: Any) -> None:
    """Set multiple keys on ``target`` and refresh an ``updated_at`` key."""
    target.update(updates)
    target["updated_at"] = now_iso()


__all__ = [
    "clamp",
    "coerce_float",
    "coerce_int",
    "merge_dicts",
    "new_task_id",
    "now_epoch",
    "now_iso",
    "parse_bool",
    "safe_get",
    "update_inplace",
]
