"""PluginManifest — contrato comun para plugins (Sprint 2.5).

Un PluginManifest es la identidad declarativa de un plugin: lo que es,
que provee, que requiere y como cargarlo. Es inmutable y serializable,
pensado para ser descubierto y validado sin instanciar el plugin.

Ejemplo::

    manifest = PluginManifest(
        id="git-enhanced",
        name="Git Enhanced Plugin",
        version="1.2.0",
        author="Liz Team",
        description="Advanced git operations plugin",
        api_version="1.0.0",
        capabilities=["git.advanced", "git.rebase"],
        entry_point="plugins.git_enhanced:GitEnhancedPlugin",
        dependencies=[{"id": "core-git", "minimum_version": "1.0.0"}],
    )

Diseño:

- Frozen dataclass (inmutable, como AgentManifest).
- Validaciones en __post_init__.
- Serializacion a/from dict y JSON.
- Compatibilidad de API version via semver.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ----------------------------------------------------------------------
# Validadores
# ----------------------------------------------------------------------


_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_\-]*$")
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+([\-+].*)?$")
_ENTRY_POINT_PATTERN = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_.]*(?::[a-zA-Z_][a-zA-Z0-9_]*)?$"
)


def _validate_plugin_id(plugin_id: str) -> None:
    """Valida el formato del ID del plugin."""
    if not plugin_id:
        raise ValueError("PluginManifest.id cannot be empty")
    if not _ID_PATTERN.match(plugin_id):
        raise ValueError(
            f"PluginManifest.id '{plugin_id}' is invalid: must start with a "
            f"lowercase letter and contain only lowercase letters, digits, "
            f"underscores and hyphens"
        )


def _validate_plugin_version(version: str) -> None:
    """Valida el formato de la version (semver-like)."""
    if not version:
        raise ValueError("PluginManifest.version cannot be empty")
    if not _VERSION_PATTERN.match(version):
        raise ValueError(
            f"PluginManifest.version '{version}' is invalid: must follow "
            f"semver format MAJOR.MINOR.PATCH (e.g. '1.0.0')"
        )


def _validate_name(name: str) -> None:
    """Valida el nombre."""
    if not name or not name.strip():
        raise ValueError("PluginManifest.name cannot be empty")


def _validate_api_version(api_version: str) -> None:
    """Valida la version de API."""
    if not api_version:
        raise ValueError("PluginManifest.api_version cannot be empty")
    if not _VERSION_PATTERN.match(api_version):
        raise ValueError(
            f"PluginManifest.api_version '{api_version}' is invalid: "
            f"must follow semver format (e.g. '1.0.0')"
        )


def _validate_entry_point(entry_point: str) -> None:
    """Valida el formato del entry point (module:ClassName)."""
    if not entry_point:
        raise ValueError("PluginManifest.entry_point cannot be empty")
    if not _ENTRY_POINT_PATTERN.match(entry_point):
        raise ValueError(
            f"PluginManifest.entry_point '{entry_point}' is invalid: "
            f"must be in format 'module.path:ClassName'"
        )


def _validate_capabilities(capabilities: list[str]) -> None:
    """Valida la lista de capabilities del plugin."""
    if not isinstance(capabilities, list):
        raise TypeError("capabilities must be a list of strings")
    seen: set[str] = set()
    for cap in capabilities:
        if not isinstance(cap, str):
            raise TypeError(f"Capability must be a string, got {type(cap).__name__}")
        if not cap:
            raise ValueError("Capability cannot be empty string")
        if cap in seen:
            raise ValueError(f"Duplicate capability '{cap}' in manifest")
        seen.add(cap)


def _validate_dependencies(deps: list[dict[str, Any]]) -> None:
    """Valida las dependencias del plugin."""
    if not isinstance(deps, list):
        raise TypeError("dependencies must be a list of dicts")
    seen_ids: set[str] = set()
    for dep in deps:
        if not isinstance(dep, dict):
            raise TypeError(f"Each dependency must be a dict, got {type(dep).__name__}")
        dep_id = dep.get("id")
        if not dep_id or not isinstance(dep_id, str):
            raise ValueError("Each dependency must have a non-empty 'id' string")
        if dep_id in seen_ids:
            raise ValueError(f"Duplicate dependency '{dep_id}' in manifest")
        seen_ids.add(dep_id)


def _validate_licenses(licenses: list[str]) -> None:
    """Valida las licencias."""
    if not isinstance(licenses, list):
        raise TypeError("licenses must be a list of strings")
    for lic in licenses:
        if not isinstance(lic, str) or not lic.strip():
            raise ValueError("Each license must be a non-empty string")


# ----------------------------------------------------------------------
# PluginManifest
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class PluginManifest:
    """Contrato declarativo de un plugin.

    Attributes:
        id:           Identificador unico (lowercase, sin espacios).
        name:         Nombre humano para mostrar.
        version:      Version semver del plugin.
        author:       Autor del plugin.
        description:  Descripcion humana de que hace el plugin.
        api_version:  Version de la API del sistema que este plugin requiere.
        license:      Licencia del plugin (SPDX identifier).
        capabilities: Lista de capabilities que el plugin provee.
        dependencies: Lista de dependencias (otros plugins requeridos).
        entry_point:  Ruta de importacion (module.path:ClassName).
        metadata:     Metadatos libres (tags, category, etc.).
    """

    id: str
    name: str
    version: str
    author: str = ""
    description: str = ""
    api_version: str = "1.0.0"
    license: str = "MIT"
    capabilities: list[str] = field(default_factory=list)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    entry_point: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_plugin_id(self.id)
        _validate_name(self.name)
        _validate_plugin_version(self.version)
        _validate_api_version(self.api_version)
        if self.entry_point:
            _validate_entry_point(self.entry_point)
        # Hacer copias defensivas para evitar mutacion externa.
        caps = list(self.capabilities) if self.capabilities else []
        _validate_capabilities(caps)
        deps = list(self.dependencies) if self.dependencies else []
        _validate_dependencies(deps)
        # Re-asignar via object.__setattr__ porque el dataclass es frozen.
        object.__setattr__(self, "capabilities", caps)
        object.__setattr__(self, "dependencies", deps)
        object.__setattr__(self, "metadata", dict(self.metadata))

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def has_capability(self, capability: str) -> bool:
        """True si el plugin provee la capability (con wildcard y jerarquico)."""
        for cap in self.capabilities:
            if cap == capability:
                return True
            if cap.endswith(".*"):
                prefix = cap[:-2]
                if capability == prefix or capability.startswith(prefix + "."):
                    return True
            if capability.startswith(cap + "."):
                return True
        return False

    def depends_on(self, plugin_id: str) -> bool:
        """True si el plugin depende de otro plugin con el ID dado."""
        return any(
            dep.get("id") == plugin_id for dep in self.dependencies
        )

    def get_dependency_version_constraint(
        self, plugin_id: str,
    ) -> dict[str, str] | None:
        """Devuelve la constraint de version para una dependencia, o None."""
        for dep in self.dependencies:
            if dep.get("id") == plugin_id:
                return {
                    k: v for k, v in dep.items()
                    if k in ("minimum_version", "maximum_version", "version")
                }
        return None

    # ------------------------------------------------------------------
    # Serializacion
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serializa a dict plano (JSON-compatible)."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "api_version": self.api_version,
            "license": self.license,
            "capabilities": list(self.capabilities),
            "dependencies": list(self.dependencies),
            "entry_point": self.entry_point,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginManifest:
        """Deserializa desde un dict plano."""
        return cls(
            id=data["id"],
            name=data["name"],
            version=data["version"],
            author=data.get("author", ""),
            description=data.get("description", ""),
            api_version=data.get("api_version", "1.0.0"),
            license=data.get("license", "MIT"),
            capabilities=list(data.get("capabilities", [])),
            dependencies=list(data.get("dependencies", [])),
            entry_point=data.get("entry_point", ""),
            metadata=dict(data.get("metadata", {})),
        )

    def to_json(self) -> str:
        """Serializa a JSON string."""
        import json
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> PluginManifest:
        """Deserializa desde un JSON string."""
        import json
        return cls.from_dict(json.loads(json_str))


__all__ = ["PluginManifest"]