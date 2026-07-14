"""Plugin tools support (Sprint 2.5).

Mixin y protocolo para plugins que exponen herramientas.

Las herramientas siguen un protocolo simple:

- `name`: str — nombre de la herramienta
- `description`: str — descripcion
- `category`: str — categoria
- `execute(params: dict) -> dict` — ejecucion

Los plugins pueden usar `ToolPluginMixin` para facilitar la
definicion y registro de herramientas.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# ToolDefinition
# ----------------------------------------------------------------------


@dataclass
class ToolDefinition:
    """Definicion declarativa de una herramienta de plugin.

    Attributes:
        name:        Nombre unico de la herramienta.
        description: Descripcion de que hace.
        category:    Categoria (filesystem, git, http, database, etc.).
        parameters:  Schema JSON de parametros (simplificado).
        metadata:    Metadatos libres.
    """

    name: str
    description: str = ""
    category: str = "general"
    parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "parameters": dict(self.parameters),
            "metadata": dict(self.metadata),
        }


# ----------------------------------------------------------------------
# ToolPlugin Protocol
# ----------------------------------------------------------------------


class ToolPlugin(ABC):
    """Protocolo para plugins que exponen herramientas.

    Un plugin que herede de ToolPlugin debe implementar
    `tool_definitions()` y `execute_tool()`.
    """

    @abstractmethod
    def tool_definitions(self) -> list[ToolDefinition]:
        """Devuelve las definiciones de las herramientas expuestas."""
        raise NotImplementedError

    @abstractmethod
    async def execute_tool(
        self, tool_name: str, params: dict[str, Any],
    ) -> dict[str, Any]:
        """Ejecuta una herramienta por nombre.

        Args:
            tool_name: Nombre de la herramienta.
            params:    Parametros de ejecucion.

        Returns:
            Dict con `status` (ok/error), `result` o `error`.
        """
        raise NotImplementedError


# ----------------------------------------------------------------------
# ToolPluginMixin
# ----------------------------------------------------------------------


class ToolPluginMixin:
    """Mixin para facilitar la definicion de herramientas en plugins.

    Uso::

        class MyPlugin(Plugin, ToolPluginMixin):
            def __init__(self):
                super().__init__()
                self._register_tool(ToolDefinition(
                    name="my_tool",
                    description="Does something useful",
                    category="custom",
                ))
    """

    def __init__(self) -> None:
        self._tool_defs: dict[str, ToolDefinition] = {}

    def _register_tool(self, definition: ToolDefinition) -> None:
        """Registra una definicion de herramienta."""
        if definition.name in self._tool_defs:
            raise ValueError(
                f"Tool '{definition.name}' already registered in plugin"
            )
        self._tool_defs[definition.name] = definition

    def _unregister_tool(self, name: str) -> bool:
        """Elimina una definicion de herramienta."""
        if name in self._tool_defs:
            del self._tool_defs[name]
            return True
        return False

    def tool_definitions(self) -> list[ToolDefinition]:
        """Devuelve todas las definiciones de herramientas."""
        return list(self._tool_defs.values())

    def get_tool_definition(self, name: str) -> ToolDefinition | None:
        """Devuelve una definicion de herramienta por nombre."""
        return self._tool_defs.get(name)

    def has_tool(self, name: str) -> bool:
        """True si el plugin tiene una herramienta con ese nombre."""
        return name in self._tool_defs


__all__ = [
    "ToolDefinition",
    "ToolPlugin",
    "ToolPluginMixin",
]