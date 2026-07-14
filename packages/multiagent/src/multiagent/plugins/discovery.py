"""PluginDiscovery — descubrimiento automatico de plugins (Sprint 2.5).

Escanea directorios buscando plugins con manifests (JSON o Python).

Estrategia de descubrimiento:

1. Busca archivos `manifest.json` en el directorio de plugins.
2. Busca archivos `plugin.py` que exporten una clase Plugin.
3. Busca directorios con `__init__.py` que contengan un Plugin.

El descubrimiento NO carga los plugins — solo los identifica.
La carga la hace PluginLoader via PluginManager.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .manifest import PluginManifest

logger = logging.getLogger(__name__)

# Nombre del archivo de manifest.
MANIFEST_FILENAME = "manifest.json"


@dataclass
class DiscoveredPlugin:
    """Un plugin descubierto pero no cargado.

    Attributes:
        manifest:       PluginManifest del plugin.
        entry_point:    Entry point para cargar el plugin.
        source_path:    Ruta al directorio del plugin.
        discovery_type: Como fue descubierto (manifest_json, module, etc.).
    """

    manifest: PluginManifest
    entry_point: str = ""
    source_path: str = ""
    discovery_type: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.to_dict(),
            "entry_point": self.entry_point,
            "source_path": self.source_path,
            "discovery_type": self.discovery_type,
        }


@dataclass
class DiscoveryResult:
    """Resultado del escaneo de descubrimiento.

    Attributes:
        discovered:  Lista de plugins descubiertos.
        errors:      Errores encontrados durante el escaneo.
        scanned_paths: Directorios escaneados.
        duration_ms:  Tiempo de escaneo en ms.
    """

    discovered: list[DiscoveredPlugin] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scanned_paths: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


class PluginDiscovery:
    """Descubre plugins automaticamente en directorios.

    Uso::

        discovery = PluginDiscovery(plugin_dirs=["plugins"])
        result = await discovery.scan()
        for dp in result.discovered:
            print(f"Found: {dp.manifest.id} v{dp.manifest.version}")
    """

    def __init__(
        self,
        *,
        plugin_dirs: list[str] | None = None,
    ) -> None:
        self._plugin_dirs = plugin_dirs or ["plugins"]
        self._discovery_count: int = 0

    @property
    def plugin_dirs(self) -> list[str]:
        return list(self._plugin_dirs)

    def add_plugin_dir(self, path: str) -> None:
        """Agrega un directorio de busqueda."""
        if path not in self._plugin_dirs:
            self._plugin_dirs.append(path)

    async def scan(self) -> DiscoveryResult:
        """Escanea los directorios en busca de plugins.

        Returns:
            DiscoveryResult con los plugins encontrados.
        """
        import time
        start = time.monotonic()
        discovered: list[DiscoveredPlugin] = []
        errors: list[str] = []
        scanned: list[str] = []

        for plugin_dir in self._plugin_dirs:
            path = Path(plugin_dir)
            if not path.exists():
                logger.debug("Plugin directory does not exist: %s", plugin_dir)
                continue
            if not path.is_dir():
                errors.append(
                    f"Plugin path is not a directory: {plugin_dir}"
                )
                continue

            scanned.append(str(path.resolve()))
            dir_results = await self._scan_directory(path)
            discovered.extend(dir_results.discovered)
            errors.extend(dir_results.errors)

        duration_ms = (time.monotonic() - start) * 1000.0
        self._discovery_count += len(discovered)

        logger.info(
            "Discovery scan complete: %d plugins found in %d dirs (%.1fms)",
            len(discovered), len(scanned), duration_ms,
        )

        return DiscoveryResult(
            discovered=discovered,
            errors=errors,
            scanned_paths=scanned,
            duration_ms=duration_ms,
        )

    async def _scan_directory(self, path: Path) -> DiscoveryResult:
        """Escanea un solo directorio."""
        discovered: list[DiscoveredPlugin] = []
        errors: list[str] = []

        # Estrategia 1: Buscar manifest.json en subdirectorios.
        try:
            entries = list(path.iterdir())
        except PermissionError as exc:
            errors.append(f"Cannot read directory {path}: {exc}")
            return DiscoveryResult(errors=errors)

        for entry in sorted(entries):
            if not entry.is_dir():
                continue

            manifest_path = entry / MANIFEST_FILENAME
            if manifest_path.exists():
                dp, error = self._load_manifest_json(entry, manifest_path)
                if dp is not None:
                    discovered.append(dp)
                elif error is not None:
                    errors.append(error)

        # Estrategia 2: Buscar plugin.py en el directorio raiz.
        plugin_py = path / "plugin.py"
        if plugin_py.exists():
            try:
                dp = self._discover_standalone_plugin(path, plugin_py)
                if dp is not None:
                    discovered.append(dp)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"Error discovering standalone plugin at {path}: {exc}"
                )

        return DiscoveryResult(discovered=discovered, errors=errors)

    def _load_manifest_json(
        self,
        plugin_dir: Path,
        manifest_path: Path,
    ) -> tuple[DiscoveredPlugin | None, str | None]:
        """Carga un manifest.json y construye un DiscoveredPlugin.

        Returns:
            Tuple of (DiscoveredPlugin or None, error string or None).
        """
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            error = f"Invalid JSON in {manifest_path}: {exc}"
            logger.error(error)
            return None, error

        try:
            manifest = PluginManifest.from_dict(data)
        except (ValueError, TypeError, KeyError) as exc:
            error = f"Invalid manifest in {manifest_path}: {exc}"
            logger.error(error)
            return None, error

        # Entry point: usar el del manifest o inferir.
        entry_point = manifest.entry_point
        if not entry_point:
            # Inferir: plugin_dir_name:ClassName
            dir_name = plugin_dir.name
            # Convertir a snake_case y capitalizar para ClassName.
            class_name = "".join(
                word.capitalize() for word in dir_name.split("_")
            )
            entry_point = f"{dir_name}.plugin:{class_name}"

        return DiscoveredPlugin(
            manifest=manifest,
            entry_point=entry_point,
            source_path=str(plugin_dir.resolve()),
            discovery_type="manifest_json",
        ), None

    def _discover_standalone_plugin(
        self,
        plugin_dir: Path,
        plugin_py: Path,
    ) -> DiscoveredPlugin | None:
        """Descubre un plugin standalone (plugin.py con manifest)."""
        # Intentar importar para obtener el manifest.
        module_name = f"discovery_{plugin_dir.name}"
        spec = None
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                module_name, str(plugin_py),
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Buscar una funcion get_manifest() o variable MANIFEST.
            manifest = None
            if hasattr(module, "get_manifest"):
                manifest = module.get_manifest()
            elif hasattr(module, "MANIFEST"):
                manifest = module.MANIFEST

            if not isinstance(manifest, PluginManifest):
                return None

            # Inferir entry point.
            entry_point = manifest.entry_point
            if not entry_point:
                class_candidates = [
                    name for name in dir(module)
                    if name[0].isupper() and not name.startswith("_")
                ]
                if class_candidates:
                    entry_point = (
                        f"{plugin_dir.name}.plugin:{class_candidates[0]}"
                    )

            return DiscoveredPlugin(
                manifest=manifest,
                entry_point=entry_point,
                source_path=str(plugin_dir.resolve()),
                discovery_type="standalone_module",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to discover standalone plugin at %s: %s",
                plugin_dir, exc,
            )
            return None
        finally:
            # Limpiar el modulo temporal del sys.modules.
            if module_name in __import__("sys").modules:
                del __import__("sys").modules[module_name]

    def metrics(self) -> dict[str, Any]:
        """Devuelve metricas del discovery."""
        return {
            "discovery_count": self._discovery_count,
            "plugin_dirs": self._plugin_dirs,
        }


__all__ = ["DiscoveredPlugin", "DiscoveryResult", "PluginDiscovery"]