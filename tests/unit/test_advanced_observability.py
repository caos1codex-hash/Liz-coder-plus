"""Unit tests for advanced observability features (Sprint 1.8).

Sprint 1.8 — Phase 7.

Cubre los nuevos métodos de MetricsCollector: task_summary,
workflow_summary, agent_summary, dashboard_snapshot, export_json,
export_prometheus y reset, además de los handlers de eventos de
scheduler y recovery.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.event_bus import EventBus  # noqa: E402
from src.observability import MetricsCollector  # noqa: E402
from src.task import TaskManager, TaskState  # noqa: E402


# ======================================================================
# task_summary
# ======================================================================


@pytest.mark.asyncio
async def test_task_summary_returns_events_for_task() -> None:
    """task_summary() filtra eventos por task_id."""
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    tm = TaskManager(event_bus=bus)
    t = await tm.create("a", agent_name="echo")
    await tm.start(t.id)
    await tm.complete(t.id, result="ok")

    summary = metrics.task_summary(t.id)
    assert summary["task_id"] == t.id
    assert summary["event_count"] == 3  # created, started, completed.
    event_types = {e["type"] for e in summary["events"]}
    assert "task.created" in event_types
    assert "task.started" in event_types
    assert "task.completed" in event_types

    await metrics.stop()


@pytest.mark.asyncio
async def test_task_summary_unknown_id() -> None:
    """task_summary() devuelve 0 eventos para un id inexistente."""
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    summary = metrics.task_summary("nonexistent")
    assert summary["task_id"] == "nonexistent"
    assert summary["event_count"] == 0
    assert summary["events"] == []

    await metrics.stop()


# ======================================================================
# workflow_summary
# ======================================================================


@pytest.mark.asyncio
async def test_workflow_summary_returns_events_for_workflow() -> None:
    """workflow_summary() filtra eventos por workflow_id."""
    metrics = MetricsCollector()
    # Registrar manualmente eventos con workflow_id.
    metrics.record_audit("workflow.created", {"workflow_id": "wf-1", "name": "test"})
    metrics.record_audit("workflow.started", {"workflow_id": "wf-1", "name": "test"})
    metrics.record_audit("workflow.created", {"workflow_id": "wf-2", "name": "other"})

    summary = metrics.workflow_summary("wf-1")
    assert summary["workflow_id"] == "wf-1"
    assert summary["event_count"] == 2


# ======================================================================
# agent_summary
# ======================================================================


@pytest.mark.asyncio
async def test_agent_summary_returns_stats() -> None:
    """agent_summary() devuelve estadísticas para un agente."""
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    # Emitir eventos de agente manualmente.
    await bus.publish("agent.invoked", {"agent_name": "code-agent"})
    await bus.publish("agent.completed", {"agent_name": "code-agent", "duration_ms": 200})
    await bus.publish("agent.invoked", {"agent_name": "echo-agent"})

    # Esperar un poco para que se procesen.
    await metrics.stop()

    summary = metrics.agent_summary("code-agent")
    assert summary["agent_name"] == "code-agent"
    assert summary["invocations"] == 1
    assert summary["duration_ms"]["count"] == 1
    assert summary["duration_ms"]["max"] == 200.0


@pytest.mark.asyncio
async def test_agent_summary_unknown_agent() -> None:
    """agent_summary() devuelve ceros para un agente desconocido."""
    metrics = MetricsCollector()
    summary = metrics.agent_summary("ghost")
    assert summary["invocations"] == 0
    assert summary["duration_ms"]["count"] == 0


# ======================================================================
# dashboard_snapshot
# ======================================================================


@pytest.mark.asyncio
async def test_dashboard_snapshot_structure() -> None:
    """dashboard_snapshot() devuelve las claves esperadas."""
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    await bus.publish("task.created", {"task_id": "t1"})
    await bus.publish("task.failed", {"task_id": "t1", "error_type": "runtime"})
    await bus.publish("agent.invoked", {"agent_name": "echo"})
    await bus.publish("tool.executed", {"tool_name": "bash", "duration_ms": 50})

    await metrics.stop()

    dash = metrics.dashboard_snapshot()
    # Verificar estructura.
    assert "counters" in dash
    assert "histograms" in dash
    assert "agents" in dash
    assert "tools" in dash
    assert "recent_errors" in dash
    assert "audit_count" in dash
    assert "generated_at" in dash

    # Verificar datos.
    assert dash["counters"]["tasks_created"] == 1
    assert dash["counters"]["tasks_failed"] == 1
    assert "echo" in dash["agents"]
    assert "bash" in dash["tools"]
    assert len(dash["recent_errors"]) >= 1  # task.failed es un error.


# ======================================================================
# export_json
# ======================================================================


@pytest.mark.asyncio
async def test_export_json_returns_valid_json() -> None:
    """export_json() devuelve un string JSON válido."""
    metrics = MetricsCollector()
    metrics.inc_counter("x", 10)
    metrics.observe("latency", 42.0)

    raw = metrics.export_json()
    parsed = json.loads(raw)
    assert parsed["counters"]["x"] == 10
    assert parsed["histograms"]["latency"]["max"] == 42.0


# ======================================================================
# export_prometheus
# ======================================================================


@pytest.mark.asyncio
async def test_export_prometheus_format() -> None:
    """export_prometheus() sigue el formato de texto Prometheus."""
    metrics = MetricsCollector()
    metrics.inc_counter("tasks_created", 5)
    metrics.inc_counter("tasks_failed", 2)
    metrics.observe("agent_duration_ms_echo", 100.0)
    metrics.observe("agent_duration_ms_echo", 200.0)

    output = metrics.export_prometheus()

    # Debe contener HELP/TYPE.
    assert "# HELP liz_tasks_total" in output
    assert "# TYPE liz_tasks_total counter" in output
    # Contadores.
    assert "liz_tasks_created 5" in output
    assert "liz_tasks_failed 2" in output
    # Histogramas.
    assert "liz_agent_duration_ms_echo_avg" in output
    assert "liz_agent_duration_ms_echo_count 2" in output


# ======================================================================
# reset
# ======================================================================


@pytest.mark.asyncio
async def test_reset_clears_all_metrics() -> None:
    """reset() pone todos los contadores, histogramas y auditoría a cero."""
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    tm = TaskManager(event_bus=bus)
    t = await tm.create("a")
    await tm.start(t.id)
    await tm.complete(t.id, result="ok")

    assert metrics.counter_value("tasks_created") == 1
    assert metrics.counter_value("tasks_completed") == 1

    metrics.reset()

    snap = metrics.snapshot()
    assert snap["counters"] == {}
    assert snap["histograms"] == {}
    assert snap["audit_count"] == 0

    await metrics.stop()


# ======================================================================
# Handlers de eventos de scheduler
# ======================================================================


@pytest.mark.asyncio
async def test_scheduler_job_scheduled_handler() -> None:
    """El handler de scheduler.job.scheduled incrementa el contador."""
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    await bus.publish("scheduler.job.scheduled", {"job_id": "j1", "name": "daily"})

    assert metrics.counter_value("scheduler_jobs_scheduled") == 1

    await metrics.stop()


@pytest.mark.asyncio
async def test_scheduler_job_cancelled_handler() -> None:
    """El handler de scheduler.job.cancelled incrementa el contador."""
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    await bus.publish("scheduler.job.cancelled", {"job_id": "j1"})

    assert metrics.counter_value("scheduler_jobs_cancelled") == 1

    await metrics.stop()


@pytest.mark.asyncio
async def test_scheduler_job_retried_handler() -> None:
    """El handler de scheduler.job.retried incrementa el contador."""
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    await bus.publish("scheduler.job.retried", {"job_id": "j1", "attempt": 2})

    assert metrics.counter_value("scheduler_job_retries") == 1

    await metrics.stop()


# ======================================================================
# Handlers de eventos de recovery
# ======================================================================


@pytest.mark.asyncio
async def test_recovery_started_handler() -> None:
    """El handler de recovery.started incrementa el contador."""
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    await bus.publish("recovery.started", {"started_at": "2026-01-01T00:00:00+00:00"})

    assert metrics.counter_value("recoveries_started") == 1

    await metrics.stop()


@pytest.mark.asyncio
async def test_recovery_completed_handler_records_duration() -> None:
    """El handler de recovery.completed registra duración como histograma."""
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    await bus.publish("recovery.completed", {"duration_ms": 150})

    assert metrics.counter_value("recoveries_completed") == 1
    dur = metrics.histogram_value("recovery_duration_ms")
    assert dur["count"] == 1
    assert dur["sum"] == 150.0

    await metrics.stop()


@pytest.mark.asyncio
async def test_checkpoint_saved_handler() -> None:
    """El handler de checkpoint.saved incrementa el contador."""
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    await bus.publish("checkpoint.saved", {"checkpoint_id": "cp-1", "name": "auto"})

    assert metrics.counter_value("checkpoints_saved") == 1

    await metrics.stop()


@pytest.mark.asyncio
async def test_checkpoint_restored_handler() -> None:
    """El handler de checkpoint.restored incrementa el contador."""
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    await bus.publish("checkpoint.restored", {"checkpoint_id": "cp-1"})

    assert metrics.counter_value("checkpoints_restored") == 1

    await metrics.stop()