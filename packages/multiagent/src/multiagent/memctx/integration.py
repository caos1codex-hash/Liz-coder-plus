"""Integration adapters for connecting the Memory Engine with existing systems.

Provides adapters for:
- WorkflowEngine: automatic context save/restore for workflows
- AgentRegistry: memory capability registration
- PluginManager: memory event bridge to plugin event bus

These adapters are optional and do not modify the original components.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .engine import MemoryEngine
from .events import MemoryEventBus, MemoryEventType
from .models import MemoryRecord, MemoryType

if TYPE_CHECKING:
    from ..workflow.engine import WorkflowEngine
    from ..registry.agent_registry import AgentRegistry
    from ..plugins.manager import PluginManager

logger = logging.getLogger(__name__)


class WorkflowMemoryAdapter:
    """Bridges MemoryEngine with WorkflowEngine.

    Automatically:
    - Saves workflow context before execution
    - Restores context on workflow start
    - Records workflow results after execution
    - Emits memory events for workflow operations
    """

    def __init__(
        self,
        memory_engine: MemoryEngine,
        workflow_engine: Optional["WorkflowEngine"] = None,
    ) -> None:
        self._memory = memory_engine
        self._workflow = workflow_engine
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def save_workflow_context(
        self,
        workflow_id: str,
        context: Dict[str, Any],
        owner: str = "WorkflowMemoryAdapter",
    ) -> MemoryRecord:
        """Save the current workflow context to memory."""
        if not self._enabled:
            raise RuntimeError("adapter is disabled")

        record = MemoryRecord(
            memory_type=MemoryType.WORKFLOW,
            content={"context": context},
            owner=owner,
            tags=["workflow", workflow_id, "context"],
            metadata={"workflow_id": workflow_id, "kind": "context"},
        )
        return await self._memory.store(record)

    async def load_workflow_context(
        self, workflow_id: str
    ) -> List[MemoryRecord]:
        """Load previous context for a workflow."""
        return await self._memory.search(
            tags=["workflow", workflow_id, "context"],
            memory_type=MemoryType.WORKFLOW,
            limit=50,
        )

    async def record_step_result(
        self,
        workflow_id: str,
        step_id: str,
        result: Any,
        status: str = "completed",
        agent_name: str = "",
    ) -> MemoryRecord:
        """Record a workflow step's execution result."""
        return await self._memory.store_workflow(
            workflow_id=workflow_id,
            step_id=step_id,
            result=result,
            status=status,
            owner=agent_name or "WorkflowMemoryAdapter",
            tags=["workflow", workflow_id, step_id, "result"],
        )

    async def record_workflow_result(
        self,
        workflow_id: str,
        result: Any,
        status: str = "completed",
    ) -> MemoryRecord:
        """Record a workflow's final result."""
        record = MemoryRecord(
            memory_type=MemoryType.WORKFLOW,
            content={"result": _safe(result), "status": status},
            owner="WorkflowMemoryAdapter",
            tags=["workflow", workflow_id, "final"],
            metadata={"workflow_id": workflow_id, "kind": "final_result"},
            priority=10,
        )
        return await self._memory.store(record)

    async def get_workflow_history(
        self, workflow_id: str
    ) -> List[MemoryRecord]:
        """Get all memory records for a workflow."""
        return await self._memory.search(
            tags=["workflow", workflow_id],
            limit=200,
        )


class RegistryMemoryAdapter:
    """Bridges MemoryEngine with AgentRegistry.

    Enables agents to query and store memory through the registry
    capability system.
    """

    def __init__(
        self,
        memory_engine: MemoryEngine,
        registry: Optional["AgentRegistry"] = None,
    ) -> None:
        self._memory = memory_engine
        self._registry = registry
        self._agent_memory_map: Dict[str, str] = {}  # agent_name -> default owner

    def register_agent_memory(
        self, agent_name: str, owner: Optional[str] = None
    ) -> None:
        """Register an agent for memory access. The agent's memory is
        associated with the given owner (defaults to agent_name)."""
        self._agent_memory_map[agent_name] = owner or agent_name
        logger.info("registered memory access for agent: %s", agent_name)

    def unregister_agent_memory(self, agent_name: str) -> None:
        """Unregister an agent from memory access."""
        self._agent_memory_map.pop(agent_name, None)

    async def get_agent_memory(
        self, agent_name: str, limit: int = 50
    ) -> List[MemoryRecord]:
        """Get memory records for a specific agent."""
        owner = self._agent_memory_map.get(agent_name, agent_name)
        return await self._memory.list_records(owner=owner, limit=limit)

    async def store_agent_memory(
        self,
        agent_name: str,
        content: Any,
        tags: Optional[List[str]] = None,
        memory_type: MemoryType = MemoryType.KNOWLEDGE,
    ) -> MemoryRecord:
        """Store a memory record on behalf of an agent."""
        owner = self._agent_memory_map.get(agent_name, agent_name)
        record = MemoryRecord(
            memory_type=memory_type,
            content=content,
            owner=owner,
            tags=tags or ["agent", agent_name],
            metadata={"agent_name": agent_name},
        )
        return await self._memory.store(record)

    async def get_agent_context(
        self, agent_name: str, task: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get a simple context dict for an agent (for injection into task payloads)."""
        from .context import ContextEngine, ContextRequest

        ctx_engine = ContextEngine(self._memory)
        snapshot = await ctx_engine.build_agent_context(
            agent_name=agent_name, task=task
        )
        return snapshot.to_dict()


class PluginMemoryBridge:
    """Bridges memory events to the PluginEventBus.

    Plugins can subscribe to memory-related events through this bridge.
    """

    def __init__(
        self,
        memory_bus: MemoryEventBus,
        plugin_bus: Optional[Any] = None,
    ) -> None:
        self._memory_bus = memory_bus
        self._plugin_bus = plugin_bus
        self._bridge_tokens: List[str] = []

    async def connect(self) -> None:
        """Subscribe to all memory events and forward to plugin bus."""
        if self._plugin_bus is None:
            return

        async def _bridge_handler(event) -> None:
            try:
                event_type_val = (
                    event.type.value
                    if hasattr(event.type, "value")
                    else str(event.type)
                )
                await self._plugin_bus.emit(
                    event_type=f"memory.{event_type_val}",
                    source=event.source,
                    payload=event.payload,
                )
            except Exception:
                logger.exception("error bridging memory event to plugin bus")

        token = await self._memory_bus.subscribe_all(_bridge_handler)
        self._bridge_tokens.append(token)
        logger.info("connected memory events to plugin event bus")

    async def disconnect(self) -> None:
        """Unsubscribe all bridge handlers."""
        for token in self._bridge_tokens:
            await self._memory_bus.unsubscribe(token)
        self._bridge_tokens.clear()
        logger.info("disconnected memory event bridge")


def _safe(value: Any) -> Any:
    """Make a value JSON-safe."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return str(value)