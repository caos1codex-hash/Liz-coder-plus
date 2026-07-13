"""SQLAlchemy models for the SQLite memory backend.

Sprint 1 - Prompt 1 (Foundation): schema definition only.
Persistence logic lands in Sprint 2.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all memory models."""


class MemoryEntry(Base):
    """A single memory entry (key-value with namespace and timestamp)."""

    __tablename__ = "memory_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_memory_namespace_key", "namespace", "key", unique=True),
    )


class ConversationLog(Base):
    """Append-only log of conversation turns (short-term memory)."""

    __tablename__ = "conversation_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


# Note: LargeBinary imported for future use (e.g. embeddings storage).
_ = LargeBinary  # noqa: F841
