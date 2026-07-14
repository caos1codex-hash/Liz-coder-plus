"""PluginCompatibilityChecker — validacion de versiones (Sprint 2.5).

Verifica que un plugin sea compatible con el sistema antes de cargarlo.

Reglas:

1. api_version del plugin debe ser compatible con la version del sistema.
2. Las dependencias del plugin deben estar satisfechas (presentes y en
   el rango de version correcto).
3. Se soporta semver simplificado: MAJOR.MINOR.PATCH.

Estrategia de compatibilidad:

- Major igual: compatible (minor/patch pueden diferir).
- Major diferente: incompatible.
- Se puede especificar minimum_version y maximum_version en dependencias.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .manifest import PluginManifest

logger = logging.getLogger(__name__)

# Version de la API del sistema.
SYSTEM_API_VERSION = "1.0.0"


@dataclass(frozen=True)
class CompatibilityResult:
    """Resultado de la verificacion de compatibilidad.

    Attributes:
        compatible:     True si el plugin es compatible.
        plugin_id:      ID del plugin verificado.
        errors:         Lista de errores encontrados.
        warnings:       Lista de advertencias.
    """

    compatible: bool
    plugin_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class PluginCompatibilityChecker:
    """Verifica la compatibilidad de un plugin con el sistema.

    Uso::

        checker = PluginCompatibilityChecker(system_api_version="1.0.0")
        result = checker.check(manifest)
        if not result.compatible:
            for err in result.errors:
                print(err)
    """

    def __init__(
        self,
        *,
        system_api_version: str = SYSTEM_API_VERSION,
    ) -> None:
        self._system_api_version = system_api_version

    @property
    def system_api_version(self) -> str:
        return self._system_api_version

    def check(
        self,
        manifest: PluginManifest,
        *,
        available_plugins: dict[str, str] | None = None,
    ) -> CompatibilityResult:
        """Verifica si un plugin es compatible con el sistema.

        Args:
            manifest:          PluginManifest del plugin a verificar.
            available_plugins: Mapa {plugin_id: version} de plugins ya
                               cargados en el sistema.

        Returns:
            CompatibilityResult con el resultado.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # 1. Verificar API version.
        api_compat = self._check_api_version(manifest.api_version)
        if not api_compat:
            errors.append(
                f"Incompatible API version: plugin requires "
                f"'{manifest.api_version}' but system provides "
                f"'{self._system_api_version}'"
            )

        # 2. Verificar dependencias.
        if available_plugins is not None:
            dep_errors = self._check_dependencies(
                manifest, available_plugins,
            )
            errors.extend(dep_errors)

        return CompatibilityResult(
            compatible=len(errors) == 0,
            plugin_id=manifest.id,
            errors=errors,
            warnings=warnings,
        )

    def _check_api_version(self, plugin_api_version: str) -> bool:
        """Verifica compatibilidad de API version (major must match)."""
        try:
            plugin_major = self._parse_major(plugin_api_version)
            system_major = self._parse_major(self._system_api_version)
            return plugin_major == system_major
        except (ValueError, IndexError):
            logger.warning(
                "Cannot parse API version for comparison: "
                "plugin='%s', system='%s'",
                plugin_api_version,
                self._system_api_version,
            )
            return False

    def _check_dependencies(
        self,
        manifest: PluginManifest,
        available_plugins: dict[str, str],
    ) -> list[str]:
        """Verifica que las dependencias del plugin esten satisfechas."""
        errors: list[str] = []
        for dep in manifest.dependencies:
            dep_id = dep.get("id", "")
            if not dep_id:
                continue
            if dep_id not in available_plugins:
                errors.append(
                    f"Missing dependency: '{dep_id}' required by "
                    f"plugin '{manifest.id}'"
                )
                continue
            # Verificar constraint de version.
            available_version = available_plugins[dep_id]
            min_ver = dep.get("minimum_version")
            max_ver = dep.get("maximum_version")
            if min_ver and not self._version_gte(available_version, min_ver):
                errors.append(
                    f"Dependency '{dep_id}' version '{available_version}' "
                    f"does not satisfy minimum_version '{min_ver}' "
                    f"(required by plugin '{manifest.id}')"
                )
            if max_ver and not self._version_lte(available_version, max_ver):
                errors.append(
                    f"Dependency '{dep_id}' version '{available_version}' "
                    f"exceeds maximum_version '{max_ver}' "
                    f"(required by plugin '{manifest.id}')"
                )
        return errors

    @staticmethod
    def _parse_major(version: str) -> int:
        """Extrae el major version number."""
        return int(version.split(".")[0])

    @staticmethod
    def _parse_version_tuple(version: str) -> tuple[int, ...]:
        """Parsea una version semver a tuple de enteros."""
        parts = version.split(".")
        result: list[int] = []
        for part in parts:
            # Strip pre-release/build metadata for comparison.
            clean = part.split("-")[0].split("+")[0]
            if clean.isdigit():
                result.append(int(clean))
            else:
                break
        return tuple(result)

    def _version_gte(self, a: str, b: str) -> bool:
        """True si version a >= version b."""
        ta = self._parse_version_tuple(a)
        tb = self._parse_version_tuple(b)
        return ta >= tb

    def _version_lte(self, a: str, b: str) -> bool:
        """True si version a <= version b."""
        ta = self._parse_version_tuple(a)
        tb = self._parse_version_tuple(b)
        return ta <= tb


__all__ = ["CompatibilityResult", "PluginCompatibilityChecker"]