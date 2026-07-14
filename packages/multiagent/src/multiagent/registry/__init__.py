"""Registry subpackage (Sprint 2.2/2 + 2.3).

Public API:

- `Capability`, `CapabilityRegistry`, `register_standard_capabilities`
- `AgentRecord`, `AgentRegistry`
- `CapabilityResolver`, `ResolutionResult`, `ResolverWeights`
- `BackwardCompatibilityAdapter`
- `AgentManifest`, `standard_manifests` (Sprint 2.3)
- `LifecycleManager`, `LifecycleState`, `LifecycleHealth` (Sprint 2.3)
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
from .lifecycle import (
    LifecycleHealth,
    LifecycleManager,
    LifecycleState,
    can_transition_lifecycle,
)
from .manifest import AgentManifest, standard_manifests
from .orchestrator import ExecutionResult, RegistryOrchestrator
from .resolver import CapabilityResolver, ResolutionResult, ResolverWeights

__all__ = [
    "ACTION_TO_CAPABILITY",
    "AGENT_NAME_TO_CAPABILITIES",
    "AgentManifest",
    "AgentRecord",
    "AgentRegistry",
    "BackwardCompatibilityAdapter",
    "Capability",
    "CapabilityRegistry",
    "CapabilityResolver",
    "ExecutionResult",
    "LifecycleHealth",
    "LifecycleManager",
    "LifecycleState",
    "RegistryOrchestrator",
    "ResolutionResult",
    "ResolverWeights",
    "can_transition_lifecycle",
    "register_standard_capabilities",
    "standard_manifests",
]
