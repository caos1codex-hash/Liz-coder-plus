"""Unit tests for the Observability layer.

Sprint 1.7 — Phase 7.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.event_bus import EventBus  # noqa: E402
from src.observability import (  # noqa: E402
    AuditRecorder,
    Counter,
    Histogram,
    MetricsCollector,
    StructuredLogger,
)
from src.task import TaskManager, TaskPriority  # noqa: E402


# ----------------------------------------------------------------------
# Counter / Histogram primitives
# ----------------------------------------------------------------------


def test_counter_increments() -> None:
    c = Counter()
    c.inc()
    assert c.value == 1
    c.inc(5)
    assert c.value == 6


def test_histogram_observes_values() -> None:
    h = Histogram()
    h.observe(10)
    h.observe(20)
    h.observe(30)
    assert h.count == 3
    assert h.sum == 60
    assert h.min == 10
    assert h.max == 30
    assert h.avg == 20


def test_histogram_empty_returns_zeros() -> None:
    h = Histogram()
    d = h.to_dict()
    assert d["count"] == 0
    assert d["avg"] == 0.0


# ----------------------------------------------------------------------
# MetricsCollector
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_collector_subscribes_to_events() -> None:
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    tm = TaskManager(event_bus=bus)
    t = await tm.create("a")
    await tm.start(t.id)
    await tm.complete(t.id, result="ok")

    snap = metrics.snapshot()
    assert snap["counters"]["tasks_created"] == 1
    assert snap["counters"]["tasks_started"] == 1
    assert snap["counters"]["tasks_completed"] == 1

    await metrics.stop()


@pytest.mark.asyncio
async def test_metrics_collector_tracks_failures_by_error_type() -> None:
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    tm = TaskManager(event_bus=bus)
    t = await tm.create("a")
    await tm.start(t.id)
    await tm.fail(t.id, "boom")

    snap = metrics.snapshot()
    # The TaskManager.fail emits TASK_FAILED with no error_type field,
    # so the bucket is "unknown".
    assert snap["counters"]["tasks_failed"] == 1
    assert snap["counters"].get("task_errors_by_type.unknown", 0) == 1

    await metrics.stop()


@pytest.mark.asyncio
async def test_metrics_collector_records_audit_trail() -> None:
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    tm = TaskManager(event_bus=bus)
    t = await tm.create("a")
    await tm.start(t.id)
    await tm.complete(t.id, result="ok")

    snap = metrics.snapshot()
    assert snap["audit_count"] == 3
    assert len(snap["audit_last_20"]) <= 20

    await metrics.stop()


@pytest.mark.asyncio
async def test_metrics_collector_manual_recording() -> None:
    metrics = MetricsCollector()  # no bus
    metrics.inc_counter("custom")
    metrics.inc_counter("custom", 4)
    metrics.observe("latency_ms", 42.0)

    snap = metrics.snapshot()
    assert snap["counters"]["custom"] == 5
    assert snap["histograms"]["latency_ms"]["count"] == 1
    assert snap["histograms"]["latency_ms"]["max"] == 42.0


@pytest.mark.asyncio
async def test_metrics_collector_start_stop_idempotent() -> None:
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()
    await metrics.start()  # second call is no-op
    await metrics.stop()
    await metrics.stop()  # second call is no-op


@pytest.mark.asyncio
async def test_metrics_counter_value_and_histogram_value() -> None:
    metrics = MetricsCollector()
    metrics.inc_counter("x")
    metrics.observe("y", 10)
    assert metrics.counter_value("x") == 1
    hv = metrics.histogram_value("y")
    assert hv["count"] == 1
    assert hv["max"] == 10


# ----------------------------------------------------------------------
# StructuredLogger
# ----------------------------------------------------------------------


def test_structured_logger_emits_json(capsys: pytest.CaptureFixture) -> None:
    slog = StructuredLogger("test.obs")
    slog.info("hello", task_id="t1", session_id="s1")
    captured = capsys.readouterr()
    assert captured.out.strip()
    import json
    line = json.loads(captured.out.strip().splitlines()[-1])
    assert line["msg"] == "hello"
    assert line["task_id"] == "t1"
    assert line["session_id"] == "s1"
    assert line["level"] == "INFO"


def test_structured_logger_with_correlation_id(
    capsys: pytest.CaptureFixture,
) -> None:
    slog = StructuredLogger("test.cid", correlation_id="cid-1")
    slog.info("hello")
    captured = capsys.readouterr()
    import json
    line = json.loads(captured.out.strip().splitlines()[-1])
    assert line["correlation_id"] == "cid-1"


def test_structured_logger_with_correlation_id_method(
    capsys: pytest.CaptureFixture,
) -> None:
    slog = StructuredLogger("test.cid2")
    child = slog.with_correlation_id("cid-2")
    child.info("hello")
    captured = capsys.readouterr()
    import json
    line = json.loads(captured.out.strip().splitlines()[-1])
    assert line["correlation_id"] == "cid-2"


# ----------------------------------------------------------------------
# AuditRecorder
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_recorder_records_events() -> None:
    rec = AuditRecorder()
    await rec.record("task.created", {"task_id": "t1"})
    await rec.record("task.completed", {"task_id": "t1"})
    assert len(rec) == 2
    assert len(rec.by_type("task.created")) == 1
    rec.clear()
    assert len(rec) == 0


@pytest.mark.asyncio
async def test_audit_recorder_capacity_bounds_buffer() -> None:
    rec = AuditRecorder(capacity=3)
    for i in range(10):
        await rec.record("evt", {"i": i})
    assert len(rec) == 3


# ----------------------------------------------------------------------
# Integration: TaskManager + MetricsCollector
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_integration_with_task_lifecycle() -> None:
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()
    tm = TaskManager(event_bus=bus)

    # Create several tasks with mixed outcomes.
    t1 = await tm.create("ok")
    t2 = await tm.create("fail")
    t3 = await tm.create("cancel")

    await tm.start(t1.id)
    await tm.complete(t1.id, result="ok")

    await tm.start(t2.id)
    await tm.fail(t2.id, "boom")

    await tm.start(t3.id)
    await tm.cancel(t3.id, reason="user")

    snap = metrics.snapshot()
    assert snap["counters"]["tasks_created"] == 3
    assert snap["counters"]["tasks_started"] == 3
    assert snap["counters"]["tasks_completed"] == 1
    assert snap["counters"]["tasks_failed"] == 1
    assert snap["counters"]["tasks_cancelled"] == 1

    await metrics.stop()
