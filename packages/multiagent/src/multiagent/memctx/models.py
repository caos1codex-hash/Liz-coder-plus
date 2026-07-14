"""Memory models for Sprint 2.6.

Defines the core data structures for the memory system:
- MemoryRecord: base record with id, timestamps, owner, tags, metadata, version
- ConversationMemory: dialogue turns and summaries
- ProjectMemory: project-scoped decisions, files, architecture
- WorkflowMemory: workflow execution context and results
- KnowledgeMemory: learned facts, patterns, and insights
- ContextSnapshot: assembled context ready for agent consumption
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class MemoryType(str, Enum):
    """Types of memory records."""

    CONVERSATION = "conversation"
    PROJECT = "project"
    WORKFLOW = "workflow"
    KNOWLEDGE = "knowledge"
    CONTEXT = "context"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
_TAG_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")


def _validate_id(memory_id: str) -> None:
    if not memory_id or not isinstance(memory_id, str):
        raise ValueError("memory id must be a non-empty string")
    if len(memory_id) > 128:
        raise ValueError("memory id must be <= 128 characters")
    if not _ID_PATTERN.match(memory_id):
        raise ValueError(
            f"memory id contains invalid characters: {memory_id!r}"
        )


def _validate_tags(tags: List[str]) -> None:
    if not isinstance(tags, list):
        raise ValueError("tags must be a list of strings")
    for tag in tags:
        if not isinstance(tag, str) or not tag:
            raise ValueError("each tag must be a non-empty string")
        if len(tag) > 64:
            raise ValueError(f"tag too long: {tag!r}")
        if not _TAG_PATTERN.match(tag):
            raise ValueError(f"tag contains invalid characters: {tag!r}")


def _validate_metadata(metadata: Dict[str, Any]) -> None:
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict")
    for k, v in metadata.items():
        if not isinstance(k, str) or not k:
            raise ValueError("metadata keys must be non-empty strings")
        if len(k) > 128:
            raise ValueError(f"metadata key too long: {k!r}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_value(value: Any) -> Any:
    """Recursively ensure value is JSON-serializable."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _serialize_value(v) for k, v in value.items()}
    raise TypeError(f"unserializable type: {type(value).__name__}")


# ---------------------------------------------------------------------------
# MemoryRecord
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MemoryRecord:
    """Base memory record with identity, ownership, and versioning.

    Attributes:
        id: Unique identifier. Auto-generated UUID if not provided.
        memory_type: Category of memory.
        content: The actual data payload (must be JSON-serializable).
        owner: Entity that created this record (agent name, system, etc.).
        tags: Searchable labels for categorization.
        metadata: Free-form key-value metadata.
        version: Optimistic concurrency version (incremented on update).
        created_at: UTC timestamp of creation.
        updated_at: UTC timestamp of last modification.
        ttl_seconds: Optional time-to-live in seconds. ``None`` means no expiry.
        priority: Numeric priority (higher = more important). Default 0.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    memory_type: MemoryType = MemoryType.KNOWLEDGE
    content: Any = field(default=None)
    owner: str = field(default="system")
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    version: int = field(default=1)
    created_at: str = field(default_factory=lambda: _utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: _utcnow().isoformat())
    ttl_seconds: Optional[int] = field(default=None)
    priority: int = field(default=0)

    # -- validation --------------------------------------------------------

    def __post_init__(self) -> None:
        _validate_id(self.id)
        _validate_tags(self.tags)
        _validate_metadata(self.metadata)
        if not isinstance(self.priority, int) or self.priority < 0:
            raise ValueError("priority must be a non-negative integer")
        if self.ttl_seconds is not None and (
            not isinstance(self.ttl_seconds, int) or self.ttl_seconds <= 0
        ):
            raise ValueError("ttl_seconds must be a positive integer or None")
        if not isinstance(self.memory_type, MemoryType):
            raise ValueError(
                f"memory_type must be a MemoryType, got {type(self.memory_type)}"
            )
        # Ensure content is serializable
        _serialize_value(self.content)

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "memory_type": self.memory_type.value,
            "content": _serialize_value(self.content),
            "owner": self.owner,
            "tags": list(self.tags),
            "metadata": {k: _serialize_value(v) for k, v in self.metadata.items()},
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ttl_seconds": self.ttl_seconds,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MemoryRecord:
        """Deserialize from a dictionary produced by ``to_dict``."""
        mt = data.get("memory_type", "knowledge")
        if isinstance(mt, str):
            mt = MemoryType(mt)

        return cls(
            id=str(data["id"]),
            memory_type=mt,
            content=data.get("content"),
            owner=str(data.get("owner", "system")),
            tags=list(data.get("tags", [])),
            metadata=dict(data.get("metadata", {})),
            version=int(data.get("version", 1)),
            created_at=str(data.get("created_at", _utcnow().isoformat())),
            updated_at=str(data.get("updated_at", _utcnow().isoformat())),
            ttl_seconds=data.get("ttl_seconds"),
            priority=int(data.get("priority", 0)),
        )

    # -- convenience -------------------------------------------------------

    def bump_version(self) -> None:
        """Increment version and update ``updated_at``."""
        self.version += 1
        self.updated_at = _utcnow().isoformat()

    def is_expired(self) -> bool:
        """Check if the record has exceeded its TTL."""
        if self.ttl_seconds is None:
            return False
        try:
            created = datetime.fromisoformat(self.created_at)
            age = (_utcnow() - created).total_seconds()
            return age > self.ttl_seconds
        except (ValueError, TypeError):
            return False

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MemoryRecord):
            return NotImplemented
        return self.id == other.id and self.version == other.version

    def __hash__(self) -> int:
        return hash((self.id, self.version))


# ---------------------------------------------------------------------------
# Specialized memory types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ConversationMemory(MemoryRecord):
    """Memory specific to conversation/dialogue turns.

    Extends MemoryRecord with conversation-specific fields.
    """

    session_id: str = ""
    role: str = "user"
    summary: Optional[str] = None

    def __post_init__(self) -> None:
        self.memory_type = MemoryType.CONVERSATION
        if self.content is None:
            self.content = {"text": ""}
        # Inline parent validation (no super() with slots)
        _validate_id(self.id)
        _validate_tags(self.tags)
        _validate_metadata(self.metadata)
        _serialize_value(self.content)


@dataclass(slots=True)
class ProjectMemory(MemoryRecord):
    """Memory scoped to a project.

    Stores decisions, architecture notes, file associations.
    """

    project_id: str = ""
    category: str = "general"  # decision, architecture, note, file

    def __post_init__(self) -> None:
        self.memory_type = MemoryType.PROJECT
        if self.content is None:
            self.content = {"description": ""}
        _validate_id(self.id)
        _validate_tags(self.tags)
        _validate_metadata(self.metadata)
        _serialize_value(self.content)


@dataclass(slots=True)
class WorkflowMemory(MemoryRecord):
    """Memory for workflow execution context and results.

    Links to workflow_id, step_id, and stores execution outcomes.
    """

    workflow_id: str = ""
    step_id: str = ""
    status: str = "pending"  # pending, running, completed, failed

    def __post_init__(self) -> None:
        self.memory_type = MemoryType.WORKFLOW
        if self.content is None:
            self.content = {"result": None}
        _validate_id(self.id)
        _validate_tags(self.tags)
        _validate_metadata(self.metadata)
        _serialize_value(self.content)


@dataclass(slots=True)
class KnowledgeMemory(MemoryRecord):
    """Memory for learned facts, patterns, and insights.

    Supports confidence scoring and source tracking.
    """

    confidence: float = 1.0
    source: str = ""
    knowledge_type: str = "fact"  # fact, pattern, insight, rule

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        self.memory_type = MemoryType.KNOWLEDGE
        if self.content is None:
            self.content = {"text": ""}
        _validate_id(self.id)
        _validate_tags(self.tags)
        _validate_metadata(self.metadata)
        _serialize_value(self.content)


# ---------------------------------------------------------------------------
# ContextSnapshot
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ContextSnapshot:
    """An assembled context ready for agent/workflow consumption.

    Combines relevant memory records into a single payload with metadata
    about sources, priority, and trimming information.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    target: str = ""  # agent name, workflow id, etc.
    records: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    total_tokens_estimate: int = 0
    max_tokens: int = 4096
    was_trimmed: bool = False
    trimmed_count: int = 0
    sources: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: _utcnow().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target,
            "records": self.records,
            "summary": self.summary,
            "total_tokens_estimate": self.total_tokens_estimate,
            "max_tokens": self.max_tokens,
            "was_trimmed": was_trimmed_set(self),
            "trimmed_count": self.trimmed_count,
            "sources": self.sources,
            "created_at": self.created_at,
            "metadata": _serialize_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ContextSnapshot:
        return cls(
            id=str(data["id"]),
            target=str(data.get("target", "")),
            records=list(data.get("records", [])),
            summary=str(data.get("summary", "")),
            total_tokens_estimate=int(data.get("total_tokens_estimate", 0)),
            max_tokens=int(data.get("max_tokens", 4096)),
            was_trimmed=bool(data.get("was_trimmed", False)),
            trimmed_count=int(data.get("trimmed_count", 0)),
            sources=list(data.get("sources", [])),
            created_at=str(data.get("created_at", _utcnow().isoformat())),
            metadata=dict(data.get("metadata", {})),
        )


def was_trimmed_set(snapshot: ContextSnapshot) -> bool:
    """Helper to avoid dataclass slot access issues."""
    return snapshot.was_trimmed