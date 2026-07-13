"""Core package: orchestrator and event system for Liz Coder Plus."""

from src.agent_router import AgentRouter, EchoAgent  # noqa: F401
from src.event_bus import EventBus  # noqa: F401
from src.events import (  # noqa: F401
    AGENT_COMPLETED,
    AGENT_FAILED,
    AGENT_INVOKED,
    ASSISTANT_RESPONSE_SENT,
    MEMORY_RETRIEVED,
    MEMORY_STORED,
    PERMISSION_GRANTED,
    PERMISSION_PROMPTED,
    PERMISSION_REVOKED,
    SYSTEM_STARTED,
    SYSTEM_STOPPED,
    TOOL_APPROVED,
    TOOL_DENIED,
    TOOL_EXECUTED,
    TOOL_FAILED,
    TOOL_REQUESTED,
    USER_MESSAGE_RECEIVED,
)
from src.orchestrator import Orchestrator  # noqa: F401
from src.pipeline import (  # noqa: F401
    DEFAULT_STAGES,
    ExecutionPipeline,
    PipelineError,
    PipelineRequest,
)
from src.protocols import Agent, Memory, Tool  # noqa: F401
from src.session_manager import ChatTurn, SessionManager, SessionState  # noqa: F401
from src.tool_executor import ToolExecutionResult, ToolExecutor  # noqa: F401
from src.ws_connection import WebSocketConnection  # noqa: F401
from src.ws_manager import WebSocketManager  # noqa: F401

__version__ = "0.1.3"