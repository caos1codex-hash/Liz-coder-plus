"""Core package: orchestrator and event system for Liz Coder Plus."""

from src.agent_router import AgentRouter, EchoAgent  # noqa: F401
from src.event_bus import EventBus  # noqa: F401
from src.events import (  # noqa: F401
    AGENT_COMPLETED,
    AGENT_FAILED,
    AGENT_INVOKED,
    ASSISTANT_RESPONSE_SENT,
    CHECKPOINT_RESTORED,
    CHECKPOINT_SAVED,
    MEMORY_RETRIEVED,
    MEMORY_STORED,
    METRIC_RECORDED,
    PERMISSION_GRANTED,
    PERMISSION_PROMPTED,
    PERMISSION_REVOKED,
    PLANNER_DECISION,
    PLANNER_PLAN_CREATED,
    RECOVERY_COMPLETED,
    RECOVERY_STARTED,
    RECOVERY_TASK_RESUMED,
    RECOVERY_WORKFLOW_RESUMED,
    SCHEDULER_JOB_CANCELLED,
    SCHEDULER_JOB_COMPLETED,
    SCHEDULER_JOB_FAILED,
    SCHEDULER_JOB_RETRIED,
    SCHEDULER_JOB_SCHEDULED,
    SCHEDULER_JOB_STARTED,
    SCHEDULER_STARTED,
    SCHEDULER_STOPPED,
    SYSTEM_STARTED,
    SYSTEM_STOPPED,
    TASK_CANCELLED,
    TASK_COMPLETED,
    TASK_CREATED,
    TASK_FAILED,
    TASK_PAUSED,
    TASK_RESUMED,
    TASK_STARTED,
    TOOL_APPROVED,
    TOOL_DENIED,
    TOOL_EXECUTED,
    TOOL_FAILED,
    TOOL_REQUESTED,
    USER_MESSAGE_RECEIVED,
    WORKFLOW_CANCELLED,
    WORKFLOW_COMPLETED,
    WORKFLOW_CREATED,
    WORKFLOW_FAILED,
    WORKFLOW_STARTED,
)
from src.orchestrator import Orchestrator  # noqa: F401
from src.observability import (  # noqa: F401
    AuditRecorder,
    Counter,
    Histogram,
    MetricsCollector,
    StructuredLogger,
)
from src.pipeline import (  # noqa: F401
    DEFAULT_STAGES,
    ExecutionPipeline,
    PipelineError,
    PipelineRequest,
)
from src.planner import Plan, Planner, SubTask  # noqa: F401
from src.scheduler import JobState, Scheduler, SchedulerJob, SchedulerRepository  # noqa: F401
from src.protocols import Agent, Memory, Tool  # noqa: F401
from src.session_manager import ChatTurn, SessionManager, SessionState  # noqa: F401
from src.task import Task, TaskManager, TaskPriority, TaskState  # noqa: F401
from src.tool_executor import ToolExecutionResult, ToolExecutor  # noqa: F401
from src.workflow import (  # noqa: F401
    Workflow,
    WorkflowBuilder,
    WorkflowCancelled,
    WorkflowFailed,
    WorkflowManager,
    WorkflowNode,
    WorkflowState,
)
from src.ws_connection import WebSocketConnection  # noqa: F401
from src.ws_manager import WebSocketManager  # noqa: F401

__version__ = "0.1.4"