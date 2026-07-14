"""Plugin events integration (Sprint 2.5).

Integra los eventos del sistema de plugins con el EventBus existente.
Los plugins pueden publicar y suscribirse a eventos de lifecycle.

Eventos definidos en PluginEvent:

    plugin.discovered, plugin.loaded, plugin.unloaded,
    plugin.enabled, plugin.disabled, plugin.failed,
    plugin.initialized, plugin.started, plugin.stopped,
    plugin.reloaded, plugin.installed, plugin.removed,
    plugin.agent.registered, plugin.tool.registered,
    plugin.workflow.registered
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .enums import PluginEvent

if TYPE_CHECKING:
    from .manifest import PluginManifest

logger = logging.getLogger(__name__)

# Re-use EventHandler type from workflow.event_bus.
PluginEventHandler = Callable[["PluginSystemEvent"], Awaitable[None]]
PluginEventFilter = Callable[["PluginSystemEvent"], bool]


class PluginSystemEvent:
    """Evento del sistema de plugins.

    Attributes:
        type:          Tipo de evento (PluginEvent o string).
        timestamp:     ISO-8601 UTC.
        source:        Quien emitio el evento.
        plugin_id:     ID del plugin afectado.
        payload:       Datos adicionales del evento.
        correlation_id: Para emparejar eventos.
        sequence:      Numero de secuencia (si hay bus).
    """

    def __init__(
        self,
        event_type: PluginEvent | str,
        *,
        source: str = "PluginManager",
        plugin_id: str = "",
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        sequence: int = 0,
    ) -> None:
        if hasattr(event_type, "value"):
            self.type: str = event_type.value  # type: ignore[union-attr]
        else:
            self.type = str(event_type)
        self.timestamp: str = ""
        self.source = source
        self.plugin_id = plugin_id
        self.payload: dict[str, Any] = payload or {}
        self.correlation_id = correlation_id
        self.sequence = sequence
        self._set_timestamp()

    def _set_timestamp(self) -> None:
        from datetime import datetime, timezone
        self.timestamp = datetime.now(timezone.utc).isoformat()

    @property
    def type_str(self) -> str:
        return self.type

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "source": self.source,
            "plugin_id": self.plugin_id,
            "payload": dict(self.payload),
            "correlation_id": self.correlation_id,
            "sequence": self.sequence,
        }

    def __repr__(self) -> str:
        return (
            f"<PluginSystemEvent type={self.type!r} "
            f"plugin_id={self.plugin_id!r}>"
        )


class PluginEventBus:
    """Bus pub/sub simplificado para eventos de plugins.

    Puede integrarse con el workflow EventBus o funcionar de forma
    independiente. Provee la misma interfaz basica: subscribe,
    unsubscribe, publish.
    """

    def __init__(self) -> None:
        self._handlers: list[tuple[
            str | None,  # event_type filter (None = all)
            PluginEventHandler,
            PluginEventFilter | None,
            str,  # token
        ]] = []
        self._sequence: int = 0
        self._history: list[PluginSystemEvent] = []
        self._max_history: int = 500

    async def subscribe(
        self,
        handler: PluginEventHandler,
        *,
        event_type: PluginEvent | str | None = None,
        filter_fn: PluginEventFilter | None = None,
    ) -> str:
        """Suscribe un handler a eventos de plugins."""
        import uuid
        token = str(uuid.uuid4())
        if hasattr(event_type, "value"):
            type_str = event_type.value  # type: ignore[union-attr]
        else:
            type_str = event_type
        self._handlers.append((type_str, handler, filter_fn, token))
        logger.debug(
            "Plugin event subscriber added for '%s' (token=%s)",
            type_str or "*", token,
        )
        return token

    async def unsubscribe(self, token: str) -> bool:
        """Elimina una suscripcion por token."""
        for i, (et, _h, _f, t) in enumerate(self._handlers):
            if t == token:
                self._handlers.pop(i)
                return True
        return False

    async def publish(
        self,
        event_type: PluginEvent | str,
        *,
        source: str = "PluginManager",
        plugin_id: str = "",
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> PluginSystemEvent:
        """Publica un evento de plugin."""
        self._sequence += 1
        event = PluginSystemEvent(
            event_type,
            source=source,
            plugin_id=plugin_id,
            payload=payload,
            correlation_id=correlation_id,
            sequence=self._sequence,
        )
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # Entregar a suscriptores.
        for et, handler, filter_fn, _token in self._handlers:
            if et is not None and et != event.type_str:
                continue
            if filter_fn is not None:
                try:
                    if not filter_fn(event):
                        continue
                except Exception:  # noqa: BLE001
                    logger.exception("Plugin event filter failed")
                    continue
            try:
                await handler(event)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Plugin event handler failed for %s", event.type_str,
                )
        return event

    def history(
        self,
        *,
        event_type: PluginEvent | str | None = None,
        limit: int = 100,
        plugin_id: str | None = None,
    ) -> list[PluginSystemEvent]:
        """Devuelve eventos del historial."""
        if hasattr(event_type, "value"):
            type_str = event_type.value  # type: ignore[union-attr]
        else:
            type_str = event_type
        result: list[PluginSystemEvent] = []
        for e in reversed(self._history):
            if type_str is not None and e.type_str != type_str:
                continue
            if plugin_id is not None and e.plugin_id != plugin_id:
                continue
            result.append(e)
            if len(result) >= limit:
                break
        result.reverse()
        return result

    def metrics(self) -> dict[str, Any]:
        """Devuelve metricas del bus."""
        return {
            "subscriber_count": len(self._handlers),
            "history_size": len(self._history),
            "sequence": self._sequence,
        }


__all__ = [
    "PluginEventBus",
    "PluginEventHandler",
    "PluginEventFilter",
    "PluginSystemEvent",
]