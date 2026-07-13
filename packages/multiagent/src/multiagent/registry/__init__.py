"""Registry subpackage (Sprint 2.2/2).

Public API:

- `Capability`, `CapabilityRegistry`, `register_standard_capabilities`
- `AgentRecord`, `AgentRegistry`
- `CapabilityResolver`, `ResolutionResult`, `ResolverWeights`
- `BackwardCompatibilityAdapter`
"""

from __future__ import annotations

from .adapter import (
    ACTION_TO_CAPABILITY,
    AGENT_NAME_TO_CAPABILITIES,
    BackwardCompatibilityAdapter,
)
from .agent_registry import AgentRecord, AgentRegistry
from .capability import (
    Capability,
    CapabilityRegistry,
    register_standard_capabilities,
)
from .resolver import CapabilityResolver, ResolutionResult, ResolverWeights

__all__ = [
    "ACTION_TO_CAPABILITY",
    "AGENT_NAME_TO_CAPABILITIES",
    "AgentRecord",
    "AgentRegistry",
    "BackwardCompatibilityAdapter",
    "Capability",
    "CapabilityRegistry",
    "CapabilityResolver",
    "ResolutionResult",
    "ResolverWeights",
    "register_standard_capabilities",
]
