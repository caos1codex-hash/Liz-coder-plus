"""Sistema de logs para agentes (Sprint 2.1).

Registra por cada ejecución:

- inicio (timestamp, agent, task_id, message)
- fin (timestamp, duration_ms, status)
- errores (stack trace, code)
- tiempo (durations por fase)
- uso de memoria (bytes RSS aproximado)
- tokens (prompt + completion si el agente usa LLM)
- herramientas usadas (nombre + duración + éxito)

El `AgentLogger` mantiene un ring buffer en memoria (tamaño configurable
vía `MultiAgentConfig.log_history_size`) y emite eventos al EventBus
global si está adjunto. Es asyncio-safe.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rss_kb() -> int:
    """Best-effort resident set size in KB. 0 si no se puede medir."""
    try:
        # /proc/self/status en Linux
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (FileNotFoundError, ValueError, OSError):
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:  # noqa: BLE001
        return 0


# ----------------------------------------------------------------------
# Entradas de log
# ----------------------------------------------------------------------


@dataclass
class LogEntry:
    """Una entrada de log de ejecución de agente."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    agent: str = ""
    task_id: str = ""
    phase: str = ""  # start, end, error, tool, metric
    timestamp: str = field(default_factory=_now_utc_iso)
    level: str = "INFO"
    message: str = ""
    duration_ms: float = 0.0
    memory_kb: int = 0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    tools_used: list[str] = field(default_factory=list)
    error: str | None = None
    stack: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent": self.agent,
            "task_id": self.task_id,
            "phase": self.phase,
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "memory_kb": self.memory_kb,
            "tokens_prompt": self.tokens_prompt,
            "tokens_completion": self.tokens_completion,
            "tools_used": list(self.tools_used),
            "error": self.error,
            "stack": self.stack,
            "metadata": dict(self.metadata),
        }


# ----------------------------------------------------------------------
# Logger
# ----------------------------------------------------------------------


class AgentLogger:
    """Logger en memoria + EventBus opcional.

    Uso típico::

        logger = AgentLogger(history_size=500)
        await logger.log_start("planner", "task-123", "decomposing request")
        # ... agente trabaja ...
        await logger.log_end("planner", "task-123", duration_ms=42.0,
                              tokens_prompt=10, tokens_completion=20,
                              tools_used=["search"])
    """

    def __init__(
        self,
        *,
        history_size: int = 500,
        event_bus: Any | None = None,
    ) -> None:
        self._history: deque[LogEntry] = deque(maxlen=history_size)
        self._lock = asyncio.Lock()
        self._bus = event_bus
        self._phase_starts: dict[tuple[str, str], float] = {}

    # ------------------------------------------------------------------
    # Phases
    # ------------------------------------------------------------------

    async def log_start(
        self,
        agent: str,
        task_id: str,
        message: str = "",
        *,
        metadata: dict[str, Any] | None = None,
    ) -> LogEntry:
        """Registra el inicio de una ejecución."""
        start = time.monotonic()
        self._phase_starts[(agent, task_id)] = start
        entry = LogEntry(
            agent=agent,
            task_id=task_id,
            phase="start",
            level="INFO",
            message=message,
            memory_kb=_rss_kb(),
            metadata=dict(metadata or {}),
        )
        await self._append(entry)
        logger.debug("[START] %s task=%s: %s", agent, task_id, message)
        return entry

    async def log_end(
        self,
        agent: str,
        task_id: str,
        *,
        duration_ms: float | None = None,
        tokens_prompt: int = 0,
        tokens_completion: int = 0,
        tools_used: list[str] | None = None,
        message: str = "completed",
    ) -> LogEntry:
        """Registra el fin exitoso de una ejecución."""
        if duration_ms is None:
            start = self._phase_starts.pop((agent, task_id), None)
            duration_ms = (
                (time.monotonic() - start) * 1000.0 if start else 0.0
            )
        else:
            self._phase_starts.pop((agent, task_id), None)
        entry = LogEntry(
            agent=agent,
            task_id=task_id,
            phase="end",
            level="INFO",
            message=message,
            duration_ms=duration_ms,
            memory_kb=_rss_kb(),
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            tools_used=list(tools_used or []),
        )
        await self._append(entry)
        return entry

    async def log_error(
        self,
        agent: str,
        task_id: str,
        error: str | BaseException,
        *,
        message: str = "error",
    ) -> LogEntry:
        """Registra un error durante la ejecución."""
        if isinstance(error, BaseException):
            err_str = f"{type(error).__name__}: {error}"
            stack = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
        else:
            err_str = str(error)
            stack = None
        entry = LogEntry(
            agent=agent,
            task_id=task_id,
            phase="error",
            level="ERROR",
            message=message,
            error=err_str,
            stack=stack,
            memory_kb=_rss_kb(),
        )
        await self._append(entry)
        logger.error("[ERROR] %s task=%s: %s", agent, task_id, err_str)
        return entry

    async def log_tool(
        self,
        agent: str,
        task_id: str,
        tool: str,
        *,
        duration_ms: float = 0.0,
        success: bool = True,
        error: str | None = None,
    ) -> LogEntry:
        """Registra el uso de una herramienta."""
        entry = LogEntry(
            agent=agent,
            task_id=task_id,
            phase="tool",
            level="INFO" if success else "WARN",
            message=f"tool: {tool}",
            duration_ms=duration_ms,
            tools_used=[tool],
            error=error,
            memory_kb=_rss_kb(),
        )
        await self._append(entry)
        return entry

    async def log_metric(
        self,
        agent: str,
        metric: str,
        value: float,
        *,
        task_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> LogEntry:
        """Registra una métrica ad-hoc."""
        entry = LogEntry(
            agent=agent,
            task_id=task_id,
            phase="metric",
            level="INFO",
            message=f"metric: {metric}={value}",
            metadata={**(metadata or {}), "metric": metric, "value": value},
            memory_kb=_rss_kb(),
        )
        await self._append(entry)
        return entry

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def history(self, limit: int = 100) -> list[LogEntry]:
        """Devuelve los últimos `limit` logs."""
        if limit <= 0:
            return []
        return list(self._history)[-limit:]

    def by_agent(self, agent: str, limit: int = 50) -> list[LogEntry]:
        """Filtra logs por agente."""
        return [e for e in self._history if e.agent == agent][-limit:]

    def by_task(self, task_id: str) -> list[LogEntry]:
        """Filtra logs por task_id."""
        return [e for e in self._history if e.task_id == task_id]

    def errors(self, limit: int = 50) -> list[LogEntry]:
        """Devuelve solo los logs de error."""
        return [e for e in self._history if e.phase == "error"][-limit:]

    def stats(self) -> dict[str, Any]:
        """Estadísticas agregadas."""
        total = len(self._history)
        errors = sum(1 for e in self._history if e.phase == "error")
        total_duration = sum(e.duration_ms for e in self._history)
        total_tokens_p = sum(e.tokens_prompt for e in self._history)
        total_tokens_c = sum(e.tokens_completion for e in self._history)
        tools: dict[str, int] = {}
        for e in self._history:
            for t in e.tools_used:
                tools[t] = tools.get(t, 0) + 1
        return {
            "total_entries": total,
            "errors": errors,
            "total_duration_ms": total_duration,
            "total_tokens_prompt": total_tokens_p,
            "total_tokens_completion": total_tokens_c,
            "tools_used": tools,
            "history_capacity": self._history.maxlen,
        }

    async def clear(self) -> None:
        async with self._lock:
            self._history.clear()
            self._phase_starts.clear()

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    async def _append(self, entry: LogEntry) -> None:
        async with self._lock:
            self._history.append(entry)
        if self._bus is not None:
            try:
                await self._bus.publish("agent.log", entry.to_dict())
            except Exception:  # noqa: BLE001
                logger.exception("Failed to publish log entry to bus")


__all__ = ["AgentLogger", "LogEntry"]
