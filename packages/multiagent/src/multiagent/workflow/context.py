"""ContextManager para workflows colaborativos (Sprint 2.2).

Mantiene un contexto compartido entre los agentes que participan en
un workflow. Soporta:

- **objetivos**: lista mutable de objetivos del workflow.
- **archivos**: registry {path: FileSnapshot} con contenido + hash.
- **fragmentos de código**: registry {snippet_id: CodeSnippet}.
- **variables**: dict {key: value} tipado (str/int/float/dict/list).
- **mensajes**: cola cronológica de mensajes intercambiados.
- **memoria**: referencias a entradas de AgentMemory por agente.

El contexto es **mutable** y **asyncio-safe**. Cada modificación emite
un evento `CONTEXT_SHARED` (si hay EventBus adjunto) para que los
agentes puedan reaccionar.

El contexto se puede **transferir** de un agente a otro
(`transfer_context`), copiando sólo las entradas marcadas como
`shareable=True`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Snapshot types
# ----------------------------------------------------------------------


@dataclass
class FileSnapshot:
    """Snapshot de un archivo en un momento dado.

    Attributes:
        path:        Ruta relativa o absoluta.
        content:     Contenido textual (None si binario).
        content_hash: SHA-256 del contenido.
        size:        Tamaño en bytes.
        modified_at: Epoch segundos.
        modified_by: Agente que modificó el archivo.
    """

    path: str
    content: str | None = None
    content_hash: str = ""
    size: int = 0
    modified_at: float = field(default_factory=time.time)
    modified_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "content": self.content,
            "content_hash": self.content_hash,
            "size": self.size,
            "modified_at": self.modified_at,
            "modified_by": self.modified_by,
        }


@dataclass
class CodeSnippet:
    """Fragmento de código compartido.

    Attributes:
        id:          UUID4.
        name:        Nombre descriptivo.
        language:    Lenguaje (python, typescript, etc.).
        content:     Contenido del snippet.
        created_by:  Agente que lo creó.
        created_at:  Epoch segundos.
        tags:        Etiquetas libres.
        shareable:   Si puede transferirse a otro agente.
    """

    name: str
    content: str
    language: str = "python"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_by: str = ""
    created_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    shareable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "language": self.language,
            "content": self.content,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "tags": list(self.tags),
            "shareable": self.shareable,
        }


@dataclass
class SharedMessage:
    """Mensaje compartido en el contexto.

    Attributes:
        id:        UUID4.
        sender:    Agente emisor.
        receiver:  Agente destinatario ("*" = broadcast).
        content:   Contenido del mensaje.
        timestamp: Epoch segundos.
        shareable: Si puede transferirse.
    """

    sender: str
    receiver: str
    content: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    shareable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sender": self.sender,
            "receiver": self.receiver,
            "content": self.content,
            "timestamp": self.timestamp,
            "shareable": self.shareable,
        }


# ----------------------------------------------------------------------
# ContextManager
# ----------------------------------------------------------------------


class ContextManager:
    """Contexto compartido entre agentes de un workflow.

    Args:
        workflow_id:   ID del workflow al que pertenece.
        event_bus:     EventBus opcional para emitir eventos.
    """

    def __init__(
        self,
        workflow_id: str,
        *,
        event_bus: Any | None = None,
    ) -> None:
        self._workflow_id = workflow_id
        self._bus = event_bus
        self._lock = asyncio.Lock()

        self._objectives: list[str] = []
        self._files: dict[str, FileSnapshot] = {}
        self._snippets: dict[str, CodeSnippet] = {}
        self._variables: dict[str, Any] = {}
        self._messages: list[SharedMessage] = []
        self._memory_refs: dict[str, dict[str, Any]] = {}
        # memory_refs[agent_name] = {key: value}

    @property
    def workflow_id(self) -> str:
        return self._workflow_id

    # ------------------------------------------------------------------
    # Objetivos
    # ------------------------------------------------------------------

    async def add_objective(self, objective: str) -> None:
        """Añade un objetivo al contexto."""
        async with self._lock:
            self._objectives.append(objective)
        await self._emit_context_event(
            "objective.added", {"objective": objective}
        )

    async def remove_objective(self, index: int) -> str | None:
        """Elimina un objetivo por índice. Devuelve el objetivo eliminado."""
        async with self._lock:
            if 0 <= index < len(self._objectives):
                removed = self._objectives.pop(index)
            else:
                return None
        await self._emit_context_event(
            "objective.removed", {"objective": removed}
        )
        return removed

    def objectives(self) -> list[str]:
        return list(self._objectives)

    # ------------------------------------------------------------------
    # Archivos
    # ------------------------------------------------------------------

    async def register_file(
        self,
        path: str,
        content: str | None,
        *,
        modified_by: str = "",
    ) -> FileSnapshot:
        """Registra o actualiza un archivo en el contexto."""
        if content is None:
            size = 0
            content_hash = ""
        else:
            size = len(content.encode("utf-8"))
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        snapshot = FileSnapshot(
            path=path,
            content=content,
            content_hash=content_hash,
            size=size,
            modified_by=modified_by,
        )
        async with self._lock:
            self._files[path] = snapshot
        await self._emit_context_event(
            "file.updated",
            {"path": path, "size": size, "hash": content_hash[:8], "by": modified_by},
        )
        return snapshot

    def get_file(self, path: str) -> FileSnapshot | None:
        return self._files.get(path)

    def files(self) -> list[FileSnapshot]:
        return list(self._files.values())

    # ------------------------------------------------------------------
    # Snippets
    # ------------------------------------------------------------------

    async def add_snippet(
        self,
        name: str,
        content: str,
        *,
        language: str = "python",
        created_by: str = "",
        tags: list[str] | None = None,
        shareable: bool = True,
    ) -> CodeSnippet:
        """Añade un fragmento de código al contexto."""
        snippet = CodeSnippet(
            name=name,
            content=content,
            language=language,
            created_by=created_by,
            tags=list(tags or []),
            shareable=shareable,
        )
        async with self._lock:
            self._snippets[snippet.id] = snippet
        await self._emit_context_event(
            "snippet.added", {"snippet_id": snippet.id, "name": name, "by": created_by}
        )
        return snippet

    def get_snippet(self, snippet_id: str) -> CodeSnippet | None:
        return self._snippets.get(snippet_id)

    def snippets(self) -> list[CodeSnippet]:
        return list(self._snippets.values())

    # ------------------------------------------------------------------
    # Variables
    # ------------------------------------------------------------------

    async def set_variable(self, key: str, value: Any) -> None:
        """Establece una variable compartida."""
        async with self._lock:
            self._variables[key] = value
        await self._emit_context_event(
            "variable.set", {"key": key, "value_type": type(value).__name__}
        )

    def get_variable(self, key: str, default: Any = None) -> Any:
        return self._variables.get(key, default)

    def variables(self) -> dict[str, Any]:
        return dict(self._variables)

    async def remove_variable(self, key: str) -> bool:
        async with self._lock:
            if key in self._variables:
                del self._variables[key]
                removed = True
            else:
                removed = False
        if removed:
            await self._emit_context_event("variable.removed", {"key": key})
        return removed

    # ------------------------------------------------------------------
    # Mensajes
    # ------------------------------------------------------------------

    async def add_message(
        self,
        sender: str,
        receiver: str,
        content: str,
        *,
        shareable: bool = True,
    ) -> SharedMessage:
        """Añade un mensaje intercambiado entre agentes."""
        msg = SharedMessage(
            sender=sender,
            receiver=receiver,
            content=content,
            shareable=shareable,
        )
        async with self._lock:
            self._messages.append(msg)
        await self._emit_context_event(
            "message.added",
            {"sender": sender, "receiver": receiver, "preview": content[:80]},
        )
        return msg

    def messages(self, limit: int = 50) -> list[SharedMessage]:
        return list(self._messages[-limit:])

    # ------------------------------------------------------------------
    # Memoria (referencias)
    # ------------------------------------------------------------------

    async def share_memory(
        self,
        agent: str,
        key: str,
        value: Any,
    ) -> None:
        """Comparte una entrada de memoria de un agente con el contexto."""
        async with self._lock:
            self._memory_refs.setdefault(agent, {})[key] = value
        await self._emit_context_event(
            "memory.shared", {"agent": agent, "key": key}
        )

    def get_shared_memory(self, agent: str) -> dict[str, Any]:
        return dict(self._memory_refs.get(agent, {}))

    def all_shared_memory(self) -> dict[str, dict[str, Any]]:
        return {a: dict(v) for a, v in self._memory_refs.items()}

    # ------------------------------------------------------------------
    # Transferencia de contexto
    # ------------------------------------------------------------------

    async def transfer_context(
        self,
        from_agent: str,
        to_agent: str,
        *,
        include_files: bool = True,
        include_snippets: bool = True,
        include_variables: bool = True,
        include_messages: bool = False,
        include_memory: bool = True,
    ) -> dict[str, Any]:
        """Transfiere el contexto compartido de un agente a otro.

        Sólo se transfieren los elementos marcados como `shareable=True`.
        """
        transferred: dict[str, Any] = {
            "from": from_agent,
            "to": to_agent,
            "files": [],
            "snippets": [],
            "variables": {},
            "messages": [],
            "memory": {},
        }
        async with self._lock:
            if include_files:
                transferred["files"] = [
                    s.to_dict() for s in self._files.values()
                ]
            if include_snippets:
                transferred["snippets"] = [
                    s.to_dict() for s in self._snippets.values() if s.shareable
                ]
            if include_variables:
                transferred["variables"] = dict(self._variables)
            if include_messages:
                transferred["messages"] = [
                    m.to_dict() for m in self._messages if m.shareable
                ]
            if include_memory:
                transferred["memory"] = dict(self._memory_refs.get(from_agent, {}))
                # Copiar al destino.
                self._memory_refs.setdefault(to_agent, {}).update(
                    transferred["memory"]
                )

        if self._bus is not None:
            try:
                # Import diferido para evitar ciclo.
                from .enums import WorkflowEvent
                await self._bus.publish(
                    WorkflowEvent.CONTEXT_TRANSFERRED,
                    source=from_agent,
                    payload=transferred,
                    correlation_id=self._workflow_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to emit CONTEXT_TRANSFERRED")
        return transferred

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Devuelve un snapshot completo del contexto."""
        return {
            "workflow_id": self._workflow_id,
            "objectives": list(self._objectives),
            "files": [f.to_dict() for f in self._files.values()],
            "snippets": [s.to_dict() for s in self._snippets.values()],
            "variables": dict(self._variables),
            "messages": [m.to_dict() for m in self._messages],
            "memory_refs": {a: dict(v) for a, v in self._memory_refs.items()},
        }

    async def clear(self) -> None:
        async with self._lock:
            self._objectives.clear()
            self._files.clear()
            self._snippets.clear()
            self._variables.clear()
            self._messages.clear()
            self._memory_refs.clear()

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    async def _emit_context_event(
        self, action: str, payload: dict[str, Any]
    ) -> None:
        if self._bus is None:
            return
        try:
            from .enums import WorkflowEvent
            await self._bus.publish(
                WorkflowEvent.CONTEXT_SHARED,
                source="context_manager",
                payload={"action": action, **payload},
                correlation_id=self._workflow_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit CONTEXT_SHARED")


__all__ = [
    "CodeSnippet",
    "ContextManager",
    "FileSnapshot",
    "SharedMessage",
]
