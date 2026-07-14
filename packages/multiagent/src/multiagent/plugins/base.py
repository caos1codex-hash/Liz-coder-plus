"""Plugin base class (Sprint 2.5).

Clase abstracta que todo plugin debe implementar. Define el contrato
de ciclo de vida y las propiedades comunes a todos los plugins.

Ciclo de vida::

    DISCOVERED → LOADED → INITIALIZED → STARTED → ENABLED
                                                 ⇄
                                              DISABLED

El plugin expone:

- `manifest()` — su PluginManifest declarativo
- `initialize()` — setup asincrono
- `start()` / `stop()` — control de ejecucion
- `health_check()` — estado de salud
- `shutdown()` — limpieza al descargar
- `get_agents()` — agentes que el plugin registra (si los tiene)
- `get_tools()` — herramientas que el plugin registra (si las tiene)
- `metrics()` — metricas del plugin
"""

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .enums import PluginState, can_transition_plugin
from .manifest import PluginManifest

if TYPE_CHECKING:
    from ..base import BaseAgent

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return time.time()


# ----------------------------------------------------------------------
# PluginHealth
# ----------------------------------------------------------------------


@dataclass
class PluginHealth:
    """Resultado del health_check() de un plugin.

    Attributes:
        plugin_id:       ID del plugin.
        plugin_name:     Nombre del plugin.
        state:           PluginState actual.
        healthy:         True si el plugin esta operativo.
        uptime_s:        Segundos desde la carga.
        last_state_change: ISO timestamp del ultimo cambio de estado.
        error_count:     Numero de errores registrados.
        last_error:      Ultimo error, o None.
        agents_count:    Numero de agentes expuestos.
        tools_count:     Numero de herramientas expuestas.
    """

    plugin_id: str = ""
    plugin_name: str = ""
    state: PluginState = PluginState.DISCOVERED
    healthy: bool = True
    uptime_s: float = 0.0
    last_state_change: str = ""
    error_count: int = 0
    last_error: str | None = None
    agents_count: int = 0
    tools_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "plugin_name": self.plugin_name,
            "state": self.state.value,
            "healthy": self.healthy,
            "uptime_s": round(self.uptime_s, 2),
            "last_state_change": self.last_state_change,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "agents_count": self.agents_count,
            "tools_count": self.tools_count,
        }


# ----------------------------------------------------------------------
# Plugin
# ----------------------------------------------------------------------


class Plugin(ABC):
    """Clase base abstracta para todos los plugins.

    Todo plugin debe:

    1. Implementar `manifest()` para retornar su PluginManifest.
    2. Implementar `initialize()` para el setup asincrono.
    3. Opcionalmente implementar `start()`, `stop()`, `shutdown()`.
    4. Opcionalmente override `get_agents()` y `get_tools()`.

    El PluginManager gestiona las transiciones de estado.
    """

    MAX_ERRORS = 50

    def __init__(self) -> None:
        self._id: str = str(uuid.uuid4())
        self._state: PluginState = PluginState.DISCOVERED
        self._created_at: float = _now_epoch()
        self._created_at_iso: str = _now_iso()
        self._last_state_change: str = _now_iso()
        self._errors: list[str] = []
        self._initialized: bool = False
        self._started: bool = False
        self._enabled: bool = False
        self._load_count: int = 0

        # Metricas internas
        self._init_duration_ms: float = 0.0
        self._start_count: int = 0
        self._stop_count: int = 0

    # ------------------------------------------------------------------
    # Propiedades
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        """UUID interno del plugin."""
        return self._id

    @property
    def state(self) -> PluginState:
        return self._state

    @state.setter
    def state(self, value: PluginState) -> None:
        if not can_transition_plugin(self._state, value):
            logger.warning(
                "Plugin '%s' invalid transition %s -> %s (forcing)",
                self.manifest().id, self._state.value, value.value,
            )
        self._state = value
        self._last_state_change = _now_iso()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def uptime_s(self) -> float:
        return _now_epoch() - self._created_at

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    @property
    def last_error(self) -> str | None:
        return self._errors[-1] if self._errors else None

    @property
    def error_count(self) -> int:
        return len(self._errors)

    # ------------------------------------------------------------------
    # Contracto abstracto
    # ------------------------------------------------------------------

    @abstractmethod
    def manifest(self) -> PluginManifest:
        """Devuelve el manifest declarativo del plugin."""
        raise NotImplementedError

    async def initialize(self) -> None:
        """Inicializa el plugin. Override en subclases para setup.

        El PluginManager transiciona DISCOVERED -> LOADED -> INITIALIZED
        y llama este metodo.
        """
        self._initialized = True

    async def start(self) -> None:
        """Inicia el plugin. Override en subclases."""
        self._started = True
        self._start_count += 1

    async def stop(self) -> None:
        """Detiene el plugin. Override en subclases."""
        self._started = False
        self._stop_count += 1

    async def shutdown(self) -> None:
        """Limpieza final antes de descargar. Override en subclases."""
        self._initialized = False
        self._started = False
        self._enabled = False

    # ------------------------------------------------------------------
    # Agentes y herramientas
    # ------------------------------------------------------------------

    def get_agents(self) -> list[BaseAgent]:
        """Devuelve los agentes que este plugin expone al sistema.

        Override en subclases para registrar agentes dinamicamente.
        El PluginManager llamara este metodo y registrara los agentes
        en el AgentRegistry.
        """
        return []

    def get_tools(self) -> list[Any]:
        """Devuelve las herramientas que este plugin expone al sistema.

        Override en subclases para registrar herramientas dinamicamente.
        Las herramientas deben seguir el protocolo BaseTool.
        """
        return []

    # ------------------------------------------------------------------
    # Health y metricas
    # ------------------------------------------------------------------

    def health_check(self) -> PluginHealth:
        """Devuelve un snapshot de salud del plugin."""
        mf = self.manifest()
        return PluginHealth(
            plugin_id=mf.id,
            plugin_name=mf.name,
            state=self._state,
            healthy=self._state not in (
                PluginState.ERROR, PluginState.UNLOADED,
            ),
            uptime_s=self.uptime_s,
            last_state_change=self._last_state_change,
            error_count=self.error_count,
            last_error=self.last_error,
            agents_count=len(self.get_agents()),
            tools_count=len(self.get_tools()),
        )

    def metrics(self) -> dict[str, Any]:
        """Devuelve metricas del plugin."""
        return {
            "id": self.manifest().id,
            "state": self._state.value,
            "initialized": self._initialized,
            "started": self._started,
            "enabled": self._enabled,
            "uptime_s": round(self.uptime_s, 2),
            "error_count": self.error_count,
            "start_count": self._start_count,
            "stop_count": self._stop_count,
            "init_duration_ms": round(self._init_duration_ms, 2),
            "load_count": self._load_count,
        }

    # ------------------------------------------------------------------
    # Serializacion
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        mf = self.manifest()
        return {
            "id": self._id,
            "manifest": mf.to_dict(),
            "state": self._state.value,
            "initialized": self._initialized,
            "started": self._started,
            "enabled": self._enabled,
            "created_at": self._created_at_iso,
            "last_state_change": self._last_state_change,
            "uptime_s": round(self.uptime_s, 2),
            "error_count": self.error_count,
            "last_error": self.last_error,
            "metrics": self.metrics(),
        }

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _add_error(self, error: str) -> None:
        self._errors.append(error)
        if len(self._errors) > self.MAX_ERRORS:
            self._errors = self._errors[-self.MAX_ERRORS:]

    def __repr__(self) -> str:
        mf_id = self.manifest().id if hasattr(self, "manifest") else "?"
        return (
            f"<{self.__class__.__name__} id={mf_id!r} "
            f"state={self._state.value}>"
        )


__all__ = ["Plugin", "PluginHealth"]