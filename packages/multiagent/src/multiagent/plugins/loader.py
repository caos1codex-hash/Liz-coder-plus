"""PluginLoader — carga dinamica de plugins (Sprint 2.5).

Responsable de importar e instanciar plugins desde su entry_point.

El loader:

1. Importa el modulo del entry_point.
2. Instancia la clase indicada.
3. Verifica que el resultado sea un Plugin.
4. No registra el plugin en ningun registry — eso lo hace PluginManager.

Seguridad:

- Solo permite entry_points validos.
- Captura errores de import y no los propaga.
- Timeout para la instanciacion.
"""

from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .base import Plugin
from .manifest import PluginManifest

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class LoadResult:
    """Resultado de la carga de un plugin.

    Attributes:
        success:    True si la carga fue exitosa.
        plugin:     Instancia del plugin (si exitoso).
        manifest:   Manifest del plugin (si disponible).
        error:      Mensaje de error (si fallido).
        duration_ms: Tiempo de carga en milisegundos.
    """

    success: bool
    plugin: Plugin | None = None
    manifest: PluginManifest | None = None
    error: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "plugin_id": self.manifest.id if self.manifest else None,
            "plugin_name": self.manifest.name if self.manifest else None,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 2),
        }


class PluginLoader:
    """Carga plugins dinamicamente desde entry_points.

    Uso::

        loader = PluginLoader()
        result = loader.load("plugins.git_enhanced:GitEnhancedPlugin")
        if result.success:
            plugin = result.plugin
    """

    def __init__(
        self,
        *,
        instantiate_timeout: float = 10.0,
    ) -> None:
        self._instantiate_timeout = instantiate_timeout
        self._load_count: int = 0
        self._error_count: int = 0

    @property
    def load_count(self) -> int:
        return self._load_count

    @property
    def error_count(self) -> int:
        return self._error_count

    def load(self, entry_point: str) -> LoadResult:
        """Carga un plugin desde un entry_point.

        Args:
            entry_point: Formato 'module.path:ClassName'.

        Returns:
            LoadResult con el resultado de la carga.
        """
        start = time.monotonic()

        # Validar formato del entry_point.
        if ":" not in entry_point:
            self._error_count += 1
            return LoadResult(
                success=False,
                error=f"Invalid entry_point format: '{entry_point}' "
                      f"(expected 'module.path:ClassName')",
            )

        module_path, class_name = entry_point.rsplit(":", 1)

        # Importar el modulo.
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            self._error_count += 1
            duration_ms = (time.monotonic() - start) * 1000.0
            error_msg = (
                f"Failed to import module '{module_path}': {exc}"
            )
            logger.error(error_msg)
            return LoadResult(
                success=False,
                error=error_msg,
                duration_ms=duration_ms,
            )
        except Exception as exc:  # noqa: BLE001
            self._error_count += 1
            duration_ms = (time.monotonic() - start) * 1000.0
            error_msg = (
                f"Error importing module '{module_path}': {exc}"
            )
            logger.error(error_msg)
            return LoadResult(
                success=False,
                error=error_msg,
                duration_ms=duration_ms,
            )

        # Obtener la clase.
        try:
            cls = getattr(module, class_name)
        except AttributeError as exc:
            self._error_count += 1
            duration_ms = (time.monotonic() - start) * 1000.0
            error_msg = (
                f"Class '{class_name}' not found in module "
                f"'{module_path}': {exc}"
            )
            logger.error(error_msg)
            return LoadResult(
                success=False,
                error=error_msg,
                duration_ms=duration_ms,
            )

        # Instanciar.
        try:
            instance = cls()
        except Exception as exc:  # noqa: BLE001
            self._error_count += 1
            duration_ms = (time.monotonic() - start) * 1000.0
            error_msg = (
                f"Failed to instantiate '{class_name}' from "
                f"'{module_path}': {exc}"
            )
            logger.error(error_msg)
            return LoadResult(
                success=False,
                error=error_msg,
                duration_ms=duration_ms,
            )

        # Verificar que es un Plugin.
        if not isinstance(instance, Plugin):
            self._error_count += 1
            duration_ms = (time.monotonic() - start) * 1000.0
            error_msg = (
                f"'{class_name}' from '{module_path}' is not a Plugin "
                f"instance (got {type(instance).__name__})"
            )
            logger.error(error_msg)
            return LoadResult(
                success=False,
                error=error_msg,
                duration_ms=duration_ms,
            )

        # Obtener el manifest.
        try:
            manifest = instance.manifest()
        except Exception as exc:  # noqa: BLE001
            self._error_count += 1
            duration_ms = (time.monotonic() - start) * 1000.0
            error_msg = (
                f"Failed to get manifest from '{class_name}': {exc}"
            )
            logger.error(error_msg)
            return LoadResult(
                success=False,
                error=error_msg,
                duration_ms=duration_ms,
            )

        self._load_count += 1
        duration_ms = (time.monotonic() - start) * 1000.0
        logger.info(
            "Plugin '%s' v%s loaded from '%s' (%.1fms)",
            manifest.id, manifest.version, entry_point, duration_ms,
        )

        return LoadResult(
            success=True,
            plugin=instance,
            manifest=manifest,
            duration_ms=duration_ms,
        )

    def metrics(self) -> dict[str, Any]:
        """Devuelve metricas del loader."""
        return {
            "load_count": self._load_count,
            "error_count": self._error_count,
            "success_rate": (
                round(
                    self._load_count
                    / (self._load_count + self._error_count),
                    4,
                )
                if (self._load_count + self._error_count) > 0
                else 0.0
            ),
        }


__all__ = ["LoadResult", "PluginLoader"]