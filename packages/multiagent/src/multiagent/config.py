"""Configuración del sistema multi-agente (Sprint 2.1).

Centraliza los parámetros operativos:

- máximo de agentes
- timeouts (por agente y por tarea)
- prioridades por defecto
- política de reintentos
- intervalo de heartbeat

La configuración puede cargarse desde un dict, un JSON, o construirse
programáticamente. Es inmutable después de creada para evitar races
durante la ejecución.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RetryPolicy:
    """Política de reintentos para tareas fallidas.

    Attributes:
        max_attempts:     Número máximo de intentos (incluye el primero).
        backoff_base:     Segundos base para backoff exponencial.
        backoff_cap:      Tope máximo de espera entre reintentos.
        retry_on_timeout: Reintentar si la tarea agotó el timeout.
        retry_on_error:   Reintentar si la tarea lanzó una excepción.
    """

    max_attempts: int = 3
    backoff_base: float = 0.5
    backoff_cap: float = 30.0
    retry_on_timeout: bool = True
    retry_on_error: bool = False

    def backoff_seconds(self, attempt: int) -> float:
        """Calcula el backoff exponencial para un intento dado."""
        if attempt <= 0:
            return 0.0
        return min(self.backoff_cap, self.backoff_base * (2 ** (attempt - 1)))


@dataclass(frozen=True)
class MultiAgentConfig:
    """Configuración global del sistema multi-agente.

    Inmutable para que los agentes puedan cachearla sin races.
    """

    max_agents: int = 32
    default_timeout: float = 60.0
    task_timeout: float = 120.0
    heartbeat_interval: float = 5.0
    queue_max_size: int = 1000
    log_history_size: int = 500
    memory_short_capacity: int = 64
    memory_long_capacity: int = 1024
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    enable_heartbeat: bool = True
    enable_metrics: bool = True

    # ------------------------------------------------------------------
    # Constructores
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MultiAgentConfig:
        """Construye la configuración desde un dict plano.

        Acepta claves anidadas para `retry`.
        """
        retry_data = data.get("retry", {})
        if isinstance(retry_data, dict):
            retry = RetryPolicy(**{
                k: v for k, v in retry_data.items()
                if k in RetryPolicy.__dataclass_fields__  # type: ignore[attr-defined]
            })
        else:
            retry = RetryPolicy()
        fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
        kwargs: dict[str, Any] = {"retry": retry}
        for k, v in data.items():
            if k == "retry":
                continue
            if k in fields:
                kwargs[k] = v
        return cls(**kwargs)

    @classmethod
    def from_json_file(cls, path: str | Path) -> MultiAgentConfig:
        """Carga la configuración desde un archivo JSON."""
        text = Path(path).read_text(encoding="utf-8")
        return cls.from_dict(json.loads(text))

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serializa la configuración a un dict plano."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Serializa la configuración a JSON."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


__all__ = ["MultiAgentConfig", "RetryPolicy"]
