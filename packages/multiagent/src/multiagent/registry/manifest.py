"""AgentManifest — contrato común para agentes (Sprint 2.3).

Un `AgentManifest` es la **identidad declarativa** de un agente: lo que
es, lo que sabe hacer (capabilities) y su prioridad. Es inmutable y
serializable, pensado para ser publicado/descubierto sin necesidad de
instanciar el agente.

Ejemplo::

    manifest = AgentManifest(
        id="coder",
        name="Coder Agent",
        version="1.0.0",
        description="Writes, refactors and tests code",
        capabilities=["code.generate", "code.refactor", "code.fix", "code.test",
                      "filesystem.read", "filesystem.write"],
        priority=10,
    )

Todos los agentes del sistema deben poder exponer su manifest vía el
mixin `ManifestMixin` (ver `manifest_mixin.py`), que añade el método
`manifest()` a `BaseAgent`.

El manifest es la base para:

- **Discovery**: el registry puede indexar por capabilities sin instanciar.
- **Routing**: el orchestrator pide "un agente con capability X" y el
  registry devuelve los manifests que matchean.
- **Documentación**: un endpoint REST puede listar todos los manifests.
- **Validación**: al registrar un agente, su manifest debe ser consistente.
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


def _validate_id(agent_id: str) -> None:
    """Valida el formato del ID."""
    if not agent_id:
        raise ValueError("AgentManifest.id cannot be empty")
    if not _ID_PATTERN.match(agent_id):
        raise ValueError(
            f"AgentManifest.id '{agent_id}' is invalid: must start with a "
            f"lowercase letter and contain only lowercase letters, digits, "
            f"underscores and hyphens"
        )


def _validate_version(version: str) -> None:
    """Valida el formato de la versión (semver-like)."""
    if not version:
        raise ValueError("AgentManifest.version cannot be empty")
    if not _VERSION_PATTERN.match(version):
        raise ValueError(
            f"AgentManifest.version '{version}' is invalid: must follow "
            f"semver format MAJOR.MINOR.PATCH (e.g. '1.0.0')"
        )


def _validate_name(name: str) -> None:
    """Valida el nombre."""
    if not name or not name.strip():
        raise ValueError("AgentManifest.name cannot be empty")


def _validate_capabilities(capabilities: list[str]) -> None:
    """Valida la lista de capabilities."""
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


def _validate_priority(priority: int) -> None:
    """Valida la prioridad (entero >= 0)."""
    if not isinstance(priority, int):
        raise TypeError(f"priority must be int, got {type(priority).__name__}")
    if priority < 0:
        raise ValueError(f"priority must be >= 0, got {priority}")


# ----------------------------------------------------------------------
# AgentManifest
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class AgentManifest:
    """Contrato declarativo de un agente.

    Attributes:
        id:            Identificador único (lowercase, sin espacios).
                       Ej: "coder", "planner", "git".
        name:          Nombre humano para mostrar. Ej: "Coder Agent".
        version:       Versión semver. Ej: "1.0.0".
        description:   Descripción humana de qué hace el agente.
        capabilities:  Lista de capabilities que provee (strings jerárquicos
                       con dots, ej: "code.generate").
        priority:      Prioridad de selección (mayor = más prioritario).
        metadata:      Metadatos libres (cost, latency_hint, tags, etc.).
    """

    id: str
    name: str
    version: str
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_id(self.id)
        _validate_name(self.name)
        _validate_version(self.version)
        # Hacer copia defensive de capabilities para evitar mutación externa.
        caps = list(self.capabilities) if self.capabilities else []
        _validate_capabilities(caps)
        _validate_priority(self.priority)
        # Re-asignar via object.__setattr__ porque el dataclass es frozen.
        object.__setattr__(self, "capabilities", caps)
        object.__setattr__(self, "metadata", dict(self.metadata))

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def has_capability(self, capability: str) -> bool:
        """True si el agente provee la capability (con wildcard y jerárquico).

        - Match exacto: "code.generate" == "code.generate"
        - Wildcard: "code.*" matchea "code.generate", "code.review", etc.
        - Jerárquico: "code" matchea "code.generate" (padre cubre hijos).
        """
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

    def has_any_capability(self, capabilities: list[str]) -> bool:
        """True si provee al menos una de las capabilities dadas."""
        return any(self.has_capability(c) for c in capabilities)

    def has_all_capabilities(self, capabilities: list[str]) -> bool:
        """True si provee todas las capabilities dadas."""
        return all(self.has_capability(c) for c in capabilities)

    def matches(self, pattern: str) -> bool:
        """True si alguna capability del manifest matchea el patrón.

        Soporta wildcards: "code.*", "*", "git.*".
        """
        if pattern == "*":
            return len(self.capabilities) > 0
        for cap in self.capabilities:
            if _pattern_matches(pattern, cap):
                return True
        return False

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serializa a dict plano (JSON-compatible)."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "priority": self.priority,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentManifest":
        """Deserializa desde un dict plano."""
        return cls(
            id=data["id"],
            name=data["name"],
            version=data["version"],
            description=data.get("description", ""),
            capabilities=list(data.get("capabilities", [])),
            priority=int(data.get("priority", 0)),
            metadata=dict(data.get("metadata", {})),
        )

    def to_json(self) -> str:
        """Serializa a JSON string."""
        import json
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _pattern_matches(pattern: str, capability: str) -> bool:
    """True si `pattern` (con wildcards) matchea `capability`."""
    if pattern == "*":
        return True
    if pattern == capability:
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return capability == prefix or capability.startswith(prefix + ".")
    # Match jerárquico: pattern="code" matches capability="code.generate".
    if capability.startswith(pattern + "."):
        return True
    return False


# ----------------------------------------------------------------------
# Manifests estándar para los agentes concretos de Sprint 2.1
# ----------------------------------------------------------------------


def standard_manifests() -> list[AgentManifest]:
    """Devuelve manifests para los 7 agentes concretos de Sprint 2.1.

    Útil para registrar agentes automáticamente al iniciar el sistema.
    """
    return [
        AgentManifest(
            id="planner",
            name="Planner Agent",
            version="1.0.0",
            description="Divides tasks into subtasks, builds workflows with DAG",
            capabilities=["planning", "workflow.create", "plan", "decompose"],
            priority=5,
            metadata={"cost": 0.3},
        ),
        AgentManifest(
            id="coder",
            name="Coder Agent",
            version="1.0.0",
            description="Writes, refactors and tests code",
            capabilities=[
                "code.generate", "code.refactor", "code.fix", "code.test",
                "filesystem.read", "filesystem.write",
                "python", "typescript",  # capabilities de lenguaje legacy
            ],
            priority=10,
            metadata={"cost": 0.5},
        ),
        AgentManifest(
            id="reviewer",
            name="Reviewer Agent",
            version="1.0.0",
            description="Reviews code for quality, security and performance",
            capabilities=["code.review", "audit"],
            priority=8,
            metadata={"cost": 0.4},
        ),
        AgentManifest(
            id="research",
            name="Research Agent",
            version="1.0.0",
            description="Investigates docs, APIs and libraries; produces summaries",
            capabilities=["research", "web.search", "documentation.lookup"],
            priority=6,
            metadata={"cost": 0.6},
        ),
        AgentManifest(
            id="terminal",
            name="Terminal Agent",
            version="1.0.0",
            description="Executes shell commands with permission gating",
            capabilities=["terminal.execute", "execute", "shell"],
            priority=7,
            metadata={"cost": 0.8},
        ),
        AgentManifest(
            id="git",
            name="Git Agent",
            version="1.0.0",
            description="Manages git operations: commits, branches, merges, push",
            capabilities=[
                "git.commit", "git.branch", "git.merge", "git.push",
                "git.pull", "git.status",
            ],
            priority=9,
            metadata={"cost": 0.5},
        ),
        AgentManifest(
            id="memory",
            name="Memory Agent",
            version="1.0.0",
            description="Manages short/long-term memory and embeddings",
            capabilities=["memory.retrieve", "memory.store", "memory.search"],
            priority=4,
            metadata={"cost": 0.2},
        ),
    ]


__all__ = [
    "AgentManifest",
    "standard_manifests",
]
