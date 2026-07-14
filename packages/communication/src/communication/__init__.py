"""Liz Coder Plus — Agent Communication & Collaboration Layer (Sprint 2.9).

This package provides a formal communication layer between agents,
built on top of the existing Sprint 2.1-2.8 infrastructure. It wraps
the existing ``MessageBus`` / ``Message`` / ``WorkflowOrchestrator``
APIs with a higher-level facade that adds persistence, tracing,
collaboration tracking, and timeouts.

Public API::

    from communication import (
        # Protocol
        AgentMessage, MessageType, MessagePriority,
        # Bus
        AgentMessageBus, BusConfig,
        # Collaboration
        CollaborationManager, Collaboration, CollaborationStatus,
        # Persistence
        MessageRepository, InMemoryMessageRepository, IMessageRepository,
        # Integration
        RegistryAdapter, OrchestratorAdapter,
        build_integrated_bus, build_integrated_manager,
        # Exceptions
        CommunicationError, MessageDeliveryError, MessageExpiredError,
        AgentNotFoundError, CollaborationError, CollaborationTimeoutError,
        PersistenceError,
    )
"""

from __future__ import annotations

__version__ = "0.1.0"

# Exceptions
# Bus
from .bus import (
    AgentMessageBus,
    BusConfig,
    MessageHandler,
    assert_agent_known,
)

# Collaboration
from .collaboration import (
    Collaboration,
    CollaborationManager,
    CollaborationStatus,
)
from .exceptions import (
    AgentNotFoundError,
    CollaborationError,
    CollaborationTimeoutError,
    CommunicationError,
    MessageDeliveryError,
    MessageExpiredError,
    PersistenceError,
)

# Integration
from .integration import (
    OrchestratorAdapter,
    RegistryAdapter,
    build_integrated_bus,
    build_integrated_manager,
)

# Protocol
from .protocol import (
    AgentMessage,
    MessagePriority,
    MessageType,
    priority_weight,
)

# Repository
from .repository import (
    IMessageRepository,
    InMemoryMessageRepository,
    MessageRepository,
)

__all__ = [
    # Version
    "__version__",
    # Exceptions
    "AgentNotFoundError",
    "CollaborationError",
    "CollaborationTimeoutError",
    "CommunicationError",
    "MessageDeliveryError",
    "MessageExpiredError",
    "PersistenceError",
    # Protocol
    "AgentMessage",
    "MessageHandler",
    "MessagePriority",
    "MessageType",
    "priority_weight",
    # Repository
    "IMessageRepository",
    "InMemoryMessageRepository",
    "MessageRepository",
    # Bus
    "AgentMessageBus",
    "BusConfig",
    "assert_agent_known",
    # Collaboration
    "Collaboration",
    "CollaborationManager",
    "CollaborationStatus",
    # Integration
    "OrchestratorAdapter",
    "RegistryAdapter",
    "build_integrated_bus",
    "build_integrated_manager",
]
