"""Cola prioritaria de tareas para el sistema multi-agente (Sprint 2.1).

Estados de tarea:

- QUEUED → RUNNING → COMPLETED
                  → FAILED (→ QUEUED en retry)
                  → CANCELLED
        → PAUSED → RUNNING (resume)

La cola es asyncio-safe. Las tareas se ordenan por prioridad
(critical > high > normal > low) y luego por `created_at` (FIFO).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .enums import Priority, TaskStatus, can_transition_task, priority_weight

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------------
# Tarea
# ----------------------------------------------------------------------


@dataclass
class Task:
    """Una unidad de trabajo encolable.

    Attributes:
        id:           UUID4 generado automáticamente.
        name:         Nombre corto.
        description:  Descripción más larga.
        priority:     Prioridad de scheduling.
        status:       Estado actual.
        agent_name:   Agente asignado (vacío = sin asignar).
        payload:      Datos arbitrarios para el agente.
        created_at:   Timestamp UTC.
        started_at:   Timestamp UTC (None hasta RUNNING).
        completed_at: Timestamp UTC (None hasta terminal).
        attempts:     Número de intentos realizados.
        max_attempts: Tope de intentos.
        result:       Salida del agente al completar.
        error:        Mensaje de error al fallar.
        parent_id:    ID de tarea padre (para subtareas).
        correlation_id: Para emparejar con mensajes.
    """

    name: str
    description: str = ""
    priority: Priority = Priority.NORMAL
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: TaskStatus = TaskStatus.QUEUED
    agent_name: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now_utc)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempts: int = 0
    max_attempts: int = 3
    result: Any = None
    error: str | None = None
    parent_id: str | None = None
    correlation_id: str | None = None

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED
        )

    @property
    def is_active(self) -> bool:
        return self.status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.PAUSED)

    def can_retry(self) -> bool:
        """Devuelve True si la tarea puede reencolarse tras un fallo."""
        return (
            self.status == TaskStatus.FAILED
            and self.attempts < self.max_attempts
        )

    def transition_to(self, new_status: TaskStatus) -> None:
        """Transiciona de estado con validación."""
        if not can_transition_task(self.status, new_status):
            raise ValueError(
                f"Task '{self.name}' ({self.id}) cannot transition "
                f"{self.status.value} -> {new_status.value}"
            )
        old = self.status
        self.status = new_status
        if new_status == TaskStatus.RUNNING and self.started_at is None:
            self.started_at = _now_utc()
        if new_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            self.completed_at = _now_utc()
        logger.debug(
            "Task '%s' %s -> %s", self.name, old.value, new_status.value
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "priority": self.priority.value,
            "status": self.status.value,
            "agent_name": self.agent_name,
            "payload": dict(self.payload),
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "result": self.result if _jsonable(self.result) else str(self.result),
            "error": self.error,
            "parent_id": self.parent_id,
            "correlation_id": self.correlation_id,
        }


def _jsonable(v: Any) -> bool:
    import json
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


# ----------------------------------------------------------------------
# Cola prioritaria
# ----------------------------------------------------------------------


class PriorityQueue:
    """Cola prioritaria asyncio-safe de tareas.

    Operaciones:

    - `enqueue(task)`: añade una tarea QUEUED.
    - `dequeue()`: saca la tarea de mayor prioridad (bloqueante).
    - `dequeue_nowait()`: saca sin bloquear; devuelve None si vacía.
    - `peek()`: mira la siguiente sin sacarla.
    - `cancel(task_id)`, `pause(task_id)`, `resume(task_id)`.
    - `get(task_id)`, `list_by_status(status)`, `summary()`.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._tasks: dict[str, Task] = {}
        self._queue: asyncio.PriorityQueue[tuple[int, datetime, str]] = (
            asyncio.PriorityQueue(maxsize=max_size)
        )
        self._lock = asyncio.Lock()
        self._max_size = max_size

    # ------------------------------------------------------------------
    # Operaciones de cola
    # ------------------------------------------------------------------

    async def enqueue(self, task: Task) -> Task:
        """Añade una tarea a la cola.

        Raises:
            ValueError: si la tarea no está en QUEUED.
        """
        if task.status != TaskStatus.QUEUED:
            raise ValueError(
                f"Task '{task.name}' must be QUEUED to enqueue "
                f"(current: {task.status.value})"
            )
        async with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"Task '{task.id}' already in queue")
            self._tasks[task.id] = task
        await self._queue.put((
            priority_weight(task.priority),
            task.created_at,
            task.id,
        ))
        logger.info(
            "Enqueued task '%s' (id=%s priority=%s)",
            task.name, task.id, task.priority.value,
        )
        return task

    async def dequeue(self, timeout: float | None = None) -> Task | None:
        """Saca la siguiente tarea de mayor prioridad.

        Bloquea hasta que haya una disponible o se agote `timeout`.
        """
        try:
            if timeout is None:
                _, _, task_id = await self._queue.get()
            else:
                _, _, task_id = await asyncio.wait_for(
                    self._queue.get(), timeout=timeout
                )
        except asyncio.TimeoutError:
            return None
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                # Fue removida; intentar la siguiente.
                return await self.dequeue(timeout=0.0)
            if task.status == TaskStatus.CANCELLED:
                # Saltar canceladas.
                self._queue.task_done()
                return await self.dequeue(timeout=0.0)
            if task.status == TaskStatus.QUEUED:
                task.transition_to(TaskStatus.RUNNING)
            return task
        return None  # unreachable

    def dequeue_nowait(self) -> Task | None:
        """Saca sin bloquear. Devuelve None si la cola está vacía."""
        try:
            _, _, task_id = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        task = self._tasks.get(task_id)
        if task is None:
            return self.dequeue_nowait()
        if task.status == TaskStatus.CANCELLED:
            return self.dequeue_nowait()
        if task.status == TaskStatus.QUEUED:
            task.transition_to(TaskStatus.RUNNING)
        return task

    async def peek(self) -> Task | None:
        """Mira la siguiente tarea sin sacarla."""
        try:
            _, _, task_id = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        # Volver a meter.
        await self._queue.put((priority_weight(self._tasks[task_id].priority),
                                self._tasks[task_id].created_at, task_id))
        return self._tasks.get(task_id)

    # ------------------------------------------------------------------
    # Operaciones por ID
    # ------------------------------------------------------------------

    def get(self, task_id: str) -> Task | None:
        """Devuelve la tarea por ID, o None."""
        return self._tasks.get(task_id)

    async def cancel(self, task_id: str, reason: str = "") -> Task:
        """Cancela una tarea."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task '{task_id}' not found")
            if task.is_terminal:
                raise ValueError(
                    f"Task '{task.name}' is terminal ({task.status.value})"
                )
            if reason:
                task.error = f"cancelled: {reason}"
            task.transition_to(TaskStatus.CANCELLED)
        return task

    async def pause(self, task_id: str) -> Task:
        """Pausa una tarea RUNNING."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task '{task_id}' not found")
            if task.status != TaskStatus.RUNNING:
                raise ValueError(
                    f"Task '{task.name}' can only be paused from RUNNING "
                    f"(current: {task.status.value})"
                )
            task.transition_to(TaskStatus.PAUSED)
        return task

    async def resume(self, task_id: str) -> Task:
        """Reanuda una tarea PAUSED."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task '{task_id}' not found")
            if task.status != TaskStatus.PAUSED:
                raise ValueError(
                    f"Task '{task.name}' can only be resumed from PAUSED "
                    f"(current: {task.status.value})"
                )
            task.transition_to(TaskStatus.RUNNING)
        return task

    async def complete(self, task_id: str, result: Any = None) -> Task:
        """Marca una tarea como completada."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task '{task_id}' not found")
            task.result = result
            task.transition_to(TaskStatus.COMPLETED)
        return task

    async def fail(self, task_id: str, error: str) -> Task:
        """Marca una tarea como fallida."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task '{task_id}' not found")
            task.error = error
            task.attempts += 1
            task.transition_to(TaskStatus.FAILED)
        return task

    async def retry(self, task_id: str) -> Task:
        """Reencola una tarea fallida (si le quedan intentos)."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task '{task_id}' not found")
            if not task.can_retry():
                raise ValueError(
                    f"Task '{task.name}' cannot be retried "
                    f"(attempts={task.attempts}/{task.max_attempts})"
                )
            task.error = None
            task.transition_to(TaskStatus.QUEUED)
        await self._queue.put((
            priority_weight(task.priority),
            _now_utc(),
            task.id,
        ))
        return task

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def list_by_status(self, status: TaskStatus) -> list[Task]:
        """Devuelve todas las tareas en un estado."""
        return [t for t in self._tasks.values() if t.status == status]

    def list_all(self) -> list[Task]:
        """Devuelve todas las tareas conocidas."""
        return list(self._tasks.values())

    def summary(self) -> dict[str, Any]:
        """Estadísticas de la cola."""
        counts: dict[str, int] = {s.value: 0 for s in TaskStatus}
        for t in self._tasks.values():
            counts[t.status.value] += 1
        return {
            "total": len(self._tasks),
            "by_status": counts,
            "queue_size": self._queue.qsize(),
            "max_size": self._max_size,
        }

    async def clear(self) -> None:
        """Vacía la cola (útil para tests)."""
        async with self._lock:
            self._tasks.clear()
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break


__all__ = ["PriorityQueue", "Task"]
