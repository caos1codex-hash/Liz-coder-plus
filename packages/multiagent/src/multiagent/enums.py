"""Enumeraciones compartidas por el sistema multi-agente (Sprint 2.1).

Centralizar los enums en un solo módulo evita dependencias circulares
entre `base`, `messages`, `queue`, `health` y `orchestrator`.
"""

from __future__ import annotations

from enum import Enum


# ----------------------------------------------------------------------
# Estado de un agente
# ----------------------------------------------------------------------


class AgentStatus(str, Enum):
    """Estados de lifecycle de un agente.

    Transiciones válidas::

        CREATED → IDLE ⇄ BUSY → STOPPED
                          ↓
                         ERROR
                          ↓
                       STOPPED

    `PAUSED` es un sub-estado operativo: el agente sigue vivo pero no
    acepta nuevas tareas hasta reanudarse.
    """

    CREATED = "created"
    IDLE = "idle"
    BUSY = "busy"
    PAUSED = "paused"
    ERROR = "error"
    STOPPED = "stopped"


# Mapa de transiciones permitidas para validación.
_ALLOWED_AGENT_TRANSITIONS: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.CREATED: frozenset({AgentStatus.IDLE, AgentStatus.STOPPED, AgentStatus.ERROR}),
    AgentStatus.IDLE: frozenset({
        AgentStatus.BUSY, AgentStatus.PAUSED, AgentStatus.STOPPED, AgentStatus.ERROR,
    }),
    AgentStatus.BUSY: frozenset({
        AgentStatus.IDLE, AgentStatus.PAUSED, AgentStatus.ERROR, AgentStatus.STOPPED,
    }),
    AgentStatus.PAUSED: frozenset({
        AgentStatus.IDLE, AgentStatus.STOPPED, AgentStatus.ERROR,
    }),
    AgentStatus.ERROR: frozenset({AgentStatus.IDLE, AgentStatus.STOPPED}),
    AgentStatus.STOPPED: frozenset(),  # terminal
}


def can_transition(current: AgentStatus, target: AgentStatus) -> bool:
    """Devuelve True si el agente puede transicionar de `current` a `target`."""
    return target in _ALLOWED_AGENT_TRANSITIONS.get(current, frozenset())


# ----------------------------------------------------------------------
# Estado de una tarea en la cola
# ----------------------------------------------------------------------


class TaskStatus(str, Enum):
    """Estados de una tarea en la cola prioritaria."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset({
        TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED,
    }),
    TaskStatus.RUNNING: frozenset({
        TaskStatus.PAUSED, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED,
    }),
    TaskStatus.PAUSED: frozenset({
        TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.FAILED,
    }),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.QUEUED}),  # retry
    TaskStatus.CANCELLED: frozenset(),
}


def can_transition_task(current: TaskStatus, target: TaskStatus) -> bool:
    """Devuelve True si la tarea puede transicionar de `current` a `target`."""
    return target in _ALLOWED_TASK_TRANSITIONS.get(current, frozenset())


# ----------------------------------------------------------------------
# Prioridad de mensajes y tareas
# ----------------------------------------------------------------------


class Priority(str, Enum):
    """Niveles de prioridad (más bajo = más prioritario, estilo `nice`)."""

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


_PRIORITY_WEIGHT: dict[Priority, int] = {
    Priority.CRITICAL: 0,
    Priority.HIGH: 1,
    Priority.NORMAL: 2,
    Priority.LOW: 3,
}


def priority_weight(p: Priority) -> int:
    """Peso numérico de una prioridad para ordenar colas."""
    return _PRIORITY_WEIGHT[p]


# ----------------------------------------------------------------------
# Tipos de mensaje entre agentes
# ----------------------------------------------------------------------


class MessageType(str, Enum):
    """Tipos canónicos de mensaje en el bus de agentes."""

    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"
    ERROR = "error"
    SYSTEM = "system"


# ----------------------------------------------------------------------
# Permisos de un agente
# ----------------------------------------------------------------------


class Permission(str, Enum):
    """Permisos declarables por un agente.

    Un agente sólo puede invocar herramientas o realizar acciones para
    las que tenga el permiso correspondiente. El orquestador valida los
    permisos antes de despachar.
    """

    READ_FILES = "read_files"
    WRITE_FILES = "write_files"
    EXECUTE_COMMANDS = "execute_commands"
    NETWORK_ACCESS = "network_access"
    GIT_OPERATIONS = "git_operations"
    MEMORY_WRITE = "memory_write"
    MEMORY_READ = "memory_read"
    SPAWN_AGENTS = "spawn_agents"
    MANAGE_AGENTS = "manage_agents"
    UNRESTRICTED = "unrestricted"


# ----------------------------------------------------------------------
# Estado de salud devuelto por health()
# ----------------------------------------------------------------------


class HealthState(str, Enum):
    """Resultado del health check de un agente."""

    ALIVE = "alive"
    BUSY = "busy"
    IDLE = "idle"
    ERROR = "error"
    DEAD = "dead"


__all__ = [
    "AgentStatus",
    "HealthState",
    "MessageType",
    "Permission",
    "Priority",
    "TaskStatus",
    "can_transition",
    "can_transition_task",
    "priority_weight",
]
