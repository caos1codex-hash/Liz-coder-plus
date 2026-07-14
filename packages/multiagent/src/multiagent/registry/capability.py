"""Capability system (Sprint 2.2/2).

Una `Capability` es una habilidad declarativa que un agente provee,
requiere o prefiere. Las capabilities son strings jerárquicos con
notación de puntos:

    planning
    workflow
    code.generate
    code.review
    git.commit
    git.merge
    terminal.execute
    filesystem.read
    filesystem.write
    memory.retrieve
    memory.store
    web.search
    documentation.lookup

Jerarquía: `code.generate` es una sub-capability de `code`.
`code.*` matchea `code.generate` pero no al revés.

El `CapabilityRegistry` mantiene el catálogo de capabilities conocidas
(con descripción y metadatos). El `CapabilityResolver` resuelve un
objetivo (string o Capability) a un agente capaz, consultando el
`AgentRegistry`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# Capability
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Capability:
    """Una capability declarativa.

    Attributes:
        name:        Identificador jerárquico (e.g. "code.generate").
        description: Descripción humana.
        category:    Categoría agrupadora (e.g. "code", "git", "terminal").
        version:     Versión semántica de la capability.
        metadata:    Metadatos libres.
    """

    name: str
    description: str = ""
    category: str = ""
    version: str = "1.0.0"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validar formato: palabras separadas por puntos, cada palabra
        # alfanumérica + guiones bajos.
        if not self.name:
            raise ValueError("Capability name cannot be empty")
        parts = self.name.split(".")
        for part in parts:
            if not part.replace("_", "").isalnum():
                raise ValueError(
                    f"Invalid capability name '{self.name}': part '{part}' "
                    f"must be alphanumeric"
                )
        # Category automática si no se especifica.
        if not self.category:
            object.__setattr__(self, "category", parts[0])

    @property
    def parent(self) -> str | None:
        """Devuelve la capability padre (e.g. "code.generate" → "code")."""
        if "." not in self.name:
            return None
        return self.name.rsplit(".", 1)[0]

    @property
    def is_root(self) -> bool:
        """True si es una capability de primer nivel (sin padre)."""
        return "." not in self.name

    def matches(self, pattern: str) -> bool:
        """Devuelve True si `pattern` matchea esta capability.

        Soporta wildcards con `*`:
        - `code.*` matchea `code.generate`, `code.review`, etc.
        - `*` matchea cualquier capability.
        - `code.generate` matchea exactamente `code.generate`.
        """
        if pattern == "*":
            return True
        if pattern == self.name:
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return self.name == prefix or self.name.startswith(prefix + ".")
        return False

    def is_subcapability_of(self, other: str) -> bool:
        """True si esta capability es descendiente de `other`."""
        if self.name == other:
            return False
        return self.name.startswith(other + ".")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "version": self.version,
            "metadata": dict(self.metadata),
        }


# ----------------------------------------------------------------------
# CapabilityRegistry
# ----------------------------------------------------------------------


class CapabilityRegistry:
    """Catálogo de capabilities conocidas.

    Permite registrar capabilities con metadatos y consultarlas por
    nombre o patrón. No sabe qué agentes las proveen; eso lo hace el
    `AgentRegistry`.
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self._registration_count: int = 0
        self._lookup_count: int = 0
        self._hit_count: int = 0
        self._miss_count: int = 0

    # ------------------------------------------------------------------
    # Registro
    # ------------------------------------------------------------------

    def register(self, capability: Capability) -> Capability:
        """Registra una capability. Si ya existe, la reemplaza."""
        if not isinstance(capability, Capability):
            raise TypeError(f"Expected Capability, got {type(capability).__name__}")
        self._capabilities[capability.name] = capability
        self._registration_count += 1
        logger.debug("Registered capability '%s'", capability.name)
        return capability

    def register_by_name(
        self,
        name: str,
        *,
        description: str = "",
        category: str = "",
        version: str = "1.0.0",
        metadata: dict[str, Any] | None = None,
    ) -> Capability:
        """Conveniencia: registra una capability por nombre."""
        cap = Capability(
            name=name,
            description=description,
            category=category,
            version=version,
            metadata=metadata or {},
        )
        return self.register(cap)

    def unregister(self, name: str) -> bool:
        """Elimina una capability por nombre."""
        if name in self._capabilities:
            del self._capabilities[name]
            return True
        return False

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def get(self, name: str) -> Capability | None:
        """Devuelve una capability por nombre exacto."""
        self._lookup_count += 1
        cap = self._capabilities.get(name)
        if cap is not None:
            self._hit_count += 1
        else:
            self._miss_count += 1
        return cap

    def exists(self, name: str) -> bool:
        """True si la capability está registrada."""
        return name in self._capabilities

    def all(self) -> list[Capability]:
        """Devuelve todas las capabilities registradas."""
        return list(self._capabilities.values())

    def by_category(self, category: str) -> list[Capability]:
        """Devuelve todas las capabilities de una categoría."""
        return [c for c in self._capabilities.values() if c.category == category]

    def search(self, pattern: str) -> list[Capability]:
        """Busca capabilities que matcheen un patrón (con wildcards)."""
        return [c for c in self._capabilities.values() if c.matches(pattern)]

    def categories(self) -> list[str]:
        """Devuelve todas las categorías registradas."""
        return sorted({c.category for c in self._capabilities.values()})

    # ------------------------------------------------------------------
    # Métricas
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        return {
            "registered": len(self._capabilities),
            "total_registrations": self._registration_count,
            "lookups": self._lookup_count,
            "hits": self._hit_count,
            "misses": self._miss_count,
            "hit_rate": (
                round(self._hit_count / self._lookup_count, 4)
                if self._lookup_count > 0 else 0.0
            ),
            "categories": len(self.categories()),
        }

    def clear(self) -> None:
        """Elimina todas las capabilities."""
        self._capabilities.clear()


# ----------------------------------------------------------------------
# Capabilities estándar (catálogo inicial)
# ----------------------------------------------------------------------


def register_standard_capabilities(registry: CapabilityRegistry) -> None:
    """Registra el catálogo estándar de capabilities.

    Llamar al iniciar el sistema para tener un vocabulario común.
    """
    standards = [
        Capability("planning", "Task decomposition and workflow planning", "planning"),
        Capability("workflow", "Workflow execution", "workflow"),
        Capability("workflow.create", "Create new workflows", "workflow"),
        Capability("workflow.execute", "Execute workflows", "workflow"),
        Capability("workflow.cancel", "Cancel workflows", "workflow"),
        Capability("code", "Code-related capabilities", "code"),
        Capability("code.generate", "Generate code", "code"),
        Capability("code.refactor", "Refactor code", "code"),
        Capability("code.fix", "Fix code bugs", "code"),
        Capability("code.test", "Generate tests", "code"),
        Capability("code.review", "Review code for quality/security", "code"),
        Capability("git", "Git operations", "git"),
        Capability("git.commit", "Create commits", "git"),
        Capability("git.branch", "Manage branches", "git"),
        Capability("git.merge", "Merge branches", "git"),
        Capability("git.push", "Push to remote", "git"),
        Capability("git.pull", "Pull from remote", "git"),
        Capability("git.status", "Query git status", "git"),
        Capability("terminal", "Terminal operations", "terminal"),
        Capability("terminal.execute", "Execute shell commands", "terminal"),
        Capability("filesystem", "Filesystem operations", "filesystem"),
        Capability("filesystem.read", "Read files", "filesystem"),
        Capability("filesystem.write", "Write files", "filesystem"),
        Capability("filesystem.list", "List directory contents", "filesystem"),
        Capability("memory", "Memory operations", "memory"),
        Capability("memory.retrieve", "Retrieve from memory", "memory"),
        Capability("memory.store", "Store to memory", "memory"),
        Capability("memory.search", "Search memory (semantic)", "memory"),
        Capability("web", "Web operations", "web"),
        Capability("web.search", "Search the web", "web"),
        Capability("documentation", "Documentation operations", "documentation"),
        Capability("documentation.lookup", "Look up documentation", "documentation"),
        Capability("research", "Research and summarize", "research"),
        Capability("research.summarize", "Summarize findings", "research"),
    ]
    for cap in standards:
        registry.register(cap)


__all__ = [
    "Capability",
    "CapabilityRegistry",
    "register_standard_capabilities",
]
