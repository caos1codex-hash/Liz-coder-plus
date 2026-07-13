"""Common enumerations shared across the project."""

from __future__ import annotations

from enum import Enum


class PermissionMode(str, Enum):
    """Permission execution modes.

    - CONFIRMATION: asks the user before running actions.
    - AUTOMATIC: runs whitelisted actions without asking.
    """

    CONFIRMATION = "Confirmation"
    AUTOMATIC = "Automatic"


class AgentRole(str, Enum):
    """Roles an agent can play in a conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessageStatus(str, Enum):
    """Status of a message in the pipeline."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Environment(str, Enum):
    """Runtime environments."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"
