"""Tests para MultiAgentConfig y RetryPolicy (Sprint 2.1)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.config import MultiAgentConfig, RetryPolicy  # noqa: E402


# --- Defaults ---


def test_defaults() -> None:
    c = MultiAgentConfig()
    assert c.max_agents == 32
    assert c.default_timeout == 60.0
    assert c.task_timeout == 120.0
    assert c.heartbeat_interval == 5.0
    assert c.queue_max_size == 1000
    assert c.log_history_size == 500
    assert c.memory_short_capacity == 64
    assert c.memory_long_capacity == 1024
    assert c.enable_heartbeat is True
    assert c.enable_metrics is True


def test_retry_defaults() -> None:
    r = RetryPolicy()
    assert r.max_attempts == 3
    assert r.backoff_base == 0.5
    assert r.backoff_cap == 30.0
    assert r.retry_on_timeout is True
    assert r.retry_on_error is False


# --- Backoff ---


def test_backoff_seconds() -> None:
    r = RetryPolicy()
    assert r.backoff_seconds(0) == 0.0
    assert r.backoff_seconds(1) == 0.5
    assert r.backoff_seconds(2) == 1.0
    assert r.backoff_seconds(3) == 2.0
    assert r.backoff_seconds(10) <= r.backoff_cap  # capped


def test_backoff_custom() -> None:
    r = RetryPolicy(backoff_base=1.0, backoff_cap=10.0)
    assert r.backoff_seconds(1) == 1.0
    assert r.backoff_seconds(2) == 2.0
    assert r.backoff_seconds(4) == 8.0
    assert r.backoff_seconds(8) == 10.0  # capped


# --- from_dict ---


def test_from_dict_basic() -> None:
    c = MultiAgentConfig.from_dict({"max_agents": 64, "task_timeout": 30.0})
    assert c.max_agents == 64
    assert c.task_timeout == 30.0
    # Defaults preserved
    assert c.heartbeat_interval == 5.0


def test_from_dict_with_retry() -> None:
    c = MultiAgentConfig.from_dict({
        "retry": {
            "max_attempts": 5,
            "backoff_base": 1.0,
        }
    })
    assert c.retry.max_attempts == 5
    assert c.retry.backoff_base == 1.0
    # Defaults preserved
    assert c.retry.backoff_cap == 30.0


def test_from_dict_ignores_unknown_keys() -> None:
    c = MultiAgentConfig.from_dict({"max_agents": 10, "unknown_key": "ignored"})
    assert c.max_agents == 10


# --- Serialization ---


def test_to_dict_roundtrip() -> None:
    c1 = MultiAgentConfig(max_agents=10, task_timeout=15.0)
    d = c1.to_dict()
    c2 = MultiAgentConfig.from_dict(d)
    assert c2.max_agents == c1.max_agents
    assert c2.task_timeout == c1.task_timeout
    assert c2.retry.max_attempts == c1.retry.max_attempts


def test_to_json() -> None:
    c = MultiAgentConfig(max_agents=5)
    s = c.to_json()
    # Es JSON válido.
    d = json.loads(s)
    assert d["max_agents"] == 5
    assert "retry" in d


def test_from_json_file(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "max_agents": 8,
        "task_timeout": 45.0,
        "retry": {"max_attempts": 7},
    }))
    c = MultiAgentConfig.from_json_file(p)
    assert c.max_agents == 8
    assert c.task_timeout == 45.0
    assert c.retry.max_attempts == 7


# --- Inmutabilidad ---


def test_config_is_frozen() -> None:
    c = MultiAgentConfig()
    with pytest.raises((AttributeError, Exception)):
        c.max_agents = 100  # type: ignore[misc]


def test_retry_is_frozen() -> None:
    r = RetryPolicy()
    with pytest.raises((AttributeError, Exception)):
        r.max_attempts = 10  # type: ignore[misc]
