"""Unified execution pipeline.

Sprint 1.7 — Phase 2.

The pipeline formalises the orchestrator flow into discrete stages,
each with a single responsibility:

    Request
      ↓
    1. ValidateRequest
    2. BuildContext
    3. RetrieveMemory
    4. PlanExecution       (optional, when a Planner is attached)
    5. SelectAgent
    6. CheckPermissions
    7. ExecuteAgent
    8. UpdateMemory
    9. PublishEvents
   10. Respond

Each stage is an async callable ``(request) -> request`` that mutates
the shared ``PipelineRequest`` and returns it. Errors are funnelled
through a single ``PipelineError`` so the orchestrator can produce
a consistent error envelope.

The pipeline is intentionally pure-Python (no external deps) and
remains optional — the legacy ``Orchestrator.handle_message`` API
continues to work, but now delegates to this pipeline internally.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from src.events import (
    AGENT_COMPLETED,
    AGENT_FAILED,
    AGENT_INVOKED,
    USER_MESSAGE_RECEIVED,
)

if TYPE_CHECKING:
    from src.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Pipeline data structures
# ----------------------------------------------------------------------


class PipelineError(Exception):
    """Raised when a pipeline stage fails in a recoverable way.

    Attributes:
        stage:     Name of the failing stage.
        error_type: Short error category (validation, timeout,
                    unexpected, ...).
        message:   Human-readable error message.
    """

    def __init__(
        self,
        stage: str,
        error_type: str,
        message: str,
    ) -> None:
        super().__init__(f"[{stage}] {error_type}: {message}")
        self.stage = stage
        self.error_type = error_type
        self.message = message


@dataclass
class PipelineRequest:
    """Mutable state shared by every stage of the pipeline.

    Attributes:
        message:      Raw user input.
        session_id:   Conversation session identifier.
        mode:         Permission mode requested by the client.
        context:      Built agent context (history, language, mode).
        agent:        Selected agent instance (filled by SelectAgent).
        agent_name:   Name of the selected agent.
        response:     Agent response text (filled by ExecuteAgent).
        metadata:     Bag for cross-stage data (planner decisions,
                      tool calls, etc.).
        started_at:   Monotonic timestamp when the pipeline started.
        duration_ms:  Total pipeline duration in milliseconds.
        stage_timings: Per-stage duration in milliseconds.
    """

    message: str
    session_id: str
    mode: str = "confirmation"
    context: dict[str, Any] = field(default_factory=dict)
    agent: Any | None = None
    agent_name: str = ""
    response: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)
    duration_ms: int = 0
    stage_timings: dict[str, int] = field(default_factory=dict)


# A pipeline stage is an async callable that takes the orchestrator
# and the current request, and returns the (mutated) request.
StageFn = Callable[
    ["Orchestrator", PipelineRequest],
    Awaitable[PipelineRequest],
]


# ----------------------------------------------------------------------
# Built-in stages
# ----------------------------------------------------------------------


async def stage_validate_request(
    orch: "Orchestrator", req: PipelineRequest
) -> PipelineRequest:
    """Validate the incoming request envelope.

    Checks:
        - message is a non-empty string
        - session_id is a non-empty string
        - mode is a known value
    """
    if not isinstance(req.message, str) or not req.message.strip():
        raise PipelineError(
            "stage_validate_request", "validation",
            "Message must be a non-empty string.",
        )
    if not isinstance(req.session_id, str) or not req.session_id.strip():
        raise PipelineError(
            "stage_validate_request", "validation",
            "session_id must be a non-empty string.",
        )
    valid_modes = {"confirmation", "automatic"}
    if req.mode not in valid_modes:
        logger.warning(
            "Unknown permission mode '%s'; defaulting to 'confirmation'",
            req.mode,
        )
        req.mode = "confirmation"
    return req


async def stage_publish_user_message(
    orch: "Orchestrator", req: PipelineRequest
) -> PipelineRequest:
    """Emit ``user.message.received`` event.

    Extracted as its own stage so observability can hook here.
    """
    await orch._emit(USER_MESSAGE_RECEIVED, {
        "session_id": req.session_id,
        "message_length": len(req.message),
        "mode": req.mode,
    })
    return req


async def stage_persist_user_turn(
    orch: "Orchestrator", req: PipelineRequest
) -> PipelineRequest:
    """Persist the user turn in the session manager (RAM + SQLite)."""
    orch.session_manager.set_mode(req.session_id, req.mode)
    await orch.session_manager.append_turn(
        req.session_id,
        role="user",
        content=req.message,
        metadata={"mode": req.mode},
    )
    return req


async def stage_build_context(
    orch: "Orchestrator", req: PipelineRequest
) -> PipelineRequest:
    """Build the agent context from session state."""
    req.context = orch._build_context(req.session_id)
    return req


async def stage_retrieve_memory(
    orch: "Orchestrator", req: PipelineRequest
) -> PipelineRequest:
    """Augment context with memory retrieval (no-op if memory is absent).

    Today this is a thin layer that exposes the persistent history
    through the context. Sprint 2 will add semantic retrieval here.
    """
    if orch._memory is not None and orch._memory.is_initialized:
        try:
            history = await orch._memory.get_context(req.session_id)
            req.context["persistent_history"] = history
        except Exception:  # noqa: BLE001
            logger.exception(
                "Memory retrieval failed for session %s", req.session_id
            )
    return req


async def stage_plan_execution(
    orch: "Orchestrator", req: PipelineRequest
) -> PipelineRequest:
    """Invoke the planner if one is attached.

    Sprint 3.6: If a PlanExecutor is attached AND the planner
    produces more than one sub-task, the full plan execution flow
    is delegated to the PlanExecutor. The plan result is stored
    in ``req.metadata['executed_plan']`` and the pipeline short-
    circuits (no further stages needed).

    Otherwise, falls back to the Sprint 1.7 behaviour: the plan
    is recorded in ``req.metadata['plan']`` for informational use.
    """
    planner = getattr(orch, "_planner", None)
    if planner is None:
        return req
    try:
        plan_result = await planner.plan(req.message, req.context)
        req.metadata["plan"] = plan_result

        # Sprint 3.6: full plan execution via PlanExecutor.
        plan_executor = getattr(orch, "_plan_executor", None)
        if (
            plan_executor is not None
            and isinstance(plan_result, list)
            and len(plan_result) > 1
        ):
            executed_plan = await plan_executor.execute_plan(
                plan_result,
                original_message=req.message,
                context=req.context,
            )
            req.metadata["executed_plan"] = executed_plan

            # Build a composite response from the plan's task results.
            responses: list[str] = []
            for t in executed_plan.tasks:
                if t.status.value == "completed" and t.result:
                    responses.append(str(t.result))
                elif t.status.value == "skipped":
                    responses.append(f"[Omitido: {t.name}]")
                elif t.error:
                    responses.append(f"[Error en {t.name}: {t.error}]")

            if responses:
                req.response = "\n\n".join(responses)
                req.metadata["plan_execution_completed"] = True
                req.metadata["plan_metrics"] = executed_plan.metrics()
            else:
                req.metadata["plan_execution_completed"] = False

        logger.debug(
            "Planner produced %d item(s) for session %s",
            len(plan_result) if isinstance(plan_result, (list, tuple)) else 1,
            req.session_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Planner failed; continuing without plan")
    return req


async def stage_select_agent(
    orch: "Orchestrator", req: PipelineRequest
) -> PipelineRequest:
    """Select the agent that will handle the message."""
    agent = await orch.router.route(req.message, req.context)
    req.agent = agent
    req.agent_name = agent.name
    await orch._emit(AGENT_INVOKED, {
        "agent_name": req.agent_name,
        "session_id": req.session_id,
        "message_length": len(req.message),
    })
    return req


async def stage_check_permissions(
    orch: "Orchestrator", req: PipelineRequest
) -> PipelineRequest:
    """Permission gate.

    **Intentionally deferred to Sprint 2.** This stage is a no-op because
    real agents with declared tool capabilities do not exist yet. When
    Sprint 2 introduces LLM-backed agents that declare which tools they
    intend to use, this stage will:

      1. Inspect the selected agent's declared tool set.
      2. Cross-reference with the ``PermissionService`` under the
         current ``req.mode`` (confirmation / automatic).
      3. Raise ``PipelineError`` if the agent is not authorised to
         invoke any of its declared tools.

    Currently agents are trusted and all tool-level permission checks
    happen inside ``ToolExecutor.execute()`` when a ``permission_service``
    is provided.
    """
    # Deferred: no agents with tool declarations exist yet (Sprint 2).
    return req


async def stage_execute_agent(
    orch: "Orchestrator", req: PipelineRequest
) -> PipelineRequest:
    """Execute the selected agent with timeout."""
    assert req.agent is not None, "agent must be selected before execute"

    start = time.monotonic()
    try:
        response_text = await asyncio.wait_for(
            req.agent.handle(req.message, req.context),
            timeout=orch.agent_timeout,
        )
    except asyncio.TimeoutError as exc:
        req.metadata["agent_duration_ms"] = int(
            (time.monotonic() - start) * 1000
        )
        raise PipelineError(
            "stage_execute_agent", "timeout",
            f"Agent exceeded {orch.agent_timeout}s",
        ) from exc
    except (ValueError, TypeError) as exc:
        req.metadata["agent_duration_ms"] = int(
            (time.monotonic() - start) * 1000
        )
        raise PipelineError(
            "stage_execute_agent", "validation", str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        req.metadata["agent_duration_ms"] = int(
            (time.monotonic() - start) * 1000
        )
        raise PipelineError(
            "stage_execute_agent", "unexpected", str(exc),
        ) from exc

    req.response = response_text
    req.metadata["agent_duration_ms"] = int(
        (time.monotonic() - start) * 1000
    )
    return req


async def stage_publish_agent_result(
    orch: "Orchestrator", req: PipelineRequest
) -> PipelineRequest:
    """Emit ``agent.completed`` after successful execution."""
    await orch._emit(AGENT_COMPLETED, {
        "agent_name": req.agent_name,
        "session_id": req.session_id,
        "duration_ms": req.metadata.get("agent_duration_ms", 0),
        "response_length": len(req.response),
    })
    return req


async def stage_update_memory(
    orch: "Orchestrator", req: PipelineRequest
) -> PipelineRequest:
    """Persist the assistant turn."""
    await orch.session_manager.append_turn(
        req.session_id,
        role="assistant",
        content=req.response,
        metadata={"agent": req.agent_name},
    )
    return req


# ----------------------------------------------------------------------
# Default pipeline definition
# ----------------------------------------------------------------------


DEFAULT_STAGES: list[StageFn] = [
    stage_validate_request,
    stage_publish_user_message,
    stage_persist_user_turn,
    stage_build_context,
    stage_retrieve_memory,
    stage_plan_execution,
    stage_select_agent,
    stage_check_permissions,
    stage_execute_agent,
    stage_publish_agent_result,
    stage_update_memory,
]


# ----------------------------------------------------------------------
# Pipeline executor
# ----------------------------------------------------------------------


class ExecutionPipeline:
    """Runs a sequence of stages over a ``PipelineRequest``.

    The pipeline is the single internal entry point used by the
    Orchestrator. It centralises error handling and per-stage timing.

    Args:
        orchestrator: The owning Orchestrator instance.
        stages:       Ordered list of stage callables. If None, the
                      ``DEFAULT_STAGES`` are used.
    """

    def __init__(
        self,
        orchestrator: "Orchestrator",
        stages: list[StageFn] | None = None,
    ) -> None:
        self._orch = orchestrator
        self._stages = stages if stages is not None else list(DEFAULT_STAGES)

    @property
    def stages(self) -> list[StageFn]:
        """Return the (immutable) list of stages."""
        return list(self._stages)

    async def run(self, message: str, session_id: str, *, mode: str) -> PipelineRequest:
        """Execute the pipeline end-to-end.

        Returns:
            The final ``PipelineRequest`` with response and timing.

        Raises:
            PipelineError: If any stage fails. The caller is expected
                           to translate this into an error envelope.
        """
        req = PipelineRequest(
            message=message,
            session_id=session_id,
            mode=mode,
        )

        for stage in self._stages:
            stage_name = getattr(stage, "__name__", stage.__class__.__name__)
            stage_start = time.monotonic()
            try:
                req = await stage(self._orch, req)
            except PipelineError:
                # Re-raise pipeline errors with stage context.
                req.stage_timings[stage_name] = int(
                    (time.monotonic() - stage_start) * 1000
                )
                raise
            except Exception as exc:  # noqa: BLE001
                # Wrap unexpected errors in PipelineError.
                req.stage_timings[stage_name] = int(
                    (time.monotonic() - stage_start) * 1000
                )
                raise PipelineError(
                    stage_name, "unexpected", str(exc),
                ) from exc

            req.stage_timings[stage_name] = int(
                (time.monotonic() - stage_start) * 1000
            )

        req.duration_ms = int((time.monotonic() - req.started_at) * 1000)
        return req

    async def run_safe(
        self, message: str, session_id: str, *, mode: str
    ) -> dict[str, Any]:
        """Like ``run`` but always returns a dict envelope.

        On failure, emits the appropriate ``agent.failed`` event and
        returns an error envelope identical to the legacy
        ``Orchestrator.handle_message`` shape, so callers can treat
        both paths uniformly.
        """
        pipeline_start = time.monotonic()
        try:
            req = await self.run(message, session_id, mode=mode)
        except PipelineError as exc:
            duration_ms = int((time.monotonic() - pipeline_start) * 1000)
            await self._orch._emit(AGENT_FAILED, {
                "agent_name": exc.stage,
                "session_id": session_id,
                "error_type": exc.error_type,
                "error": exc.message,
                "stage": exc.stage,
                "duration_ms": duration_ms,
            })

            # Persist the failure as a system turn (best-effort).
            try:
                await self._orch.session_manager.append_turn(
                    session_id,
                    role="system",
                    content=f"{exc.error_type}: {exc.message}",
                    metadata={
                        "stage": exc.stage,
                        "error_type": exc.error_type,
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to persist pipeline error turn")

            user_msg = self._format_error(exc)
            error_key = self._error_key(exc)
            return {
                "type": "error",
                "content": user_msg,
                "status": "failed",
                "session_id": session_id,
                "error": error_key,
                "stage": exc.stage,
                "duration_ms": 0,
            }

        return {
            "type": "message",
            "content": req.response,
            "status": "completed",
            "session_id": session_id,
            "message_id": f"{session_id}:{len(self._orch.session_manager.history(session_id))}",
            "agent_name": req.agent_name,
            "duration_ms": req.duration_ms,
            "stage_timings": req.stage_timings,
        }

    @staticmethod
    def _error_key(exc: PipelineError) -> str:
        """Map error_type to the legacy ``error`` envelope key.

        Preserves backwards compatibility with the original
        ``Orchestrator.handle_message`` shape:
            - timeout → agent_timeout
            - validation → str(exc) (legacy behaviour)
            - unexpected → str(exc) (legacy behaviour)
        """
        if exc.error_type == "timeout":
            return "agent_timeout"
        return exc.message

    @staticmethod
    def _format_error(exc: PipelineError) -> str:
        """Translate pipeline errors to user-facing Spanish messages."""
        if exc.error_type == "timeout":
            return (
                f"Lo siento, el agente tardó demasiado en responder "
                f"(límite: etapa {exc.stage})."
            )
        if exc.error_type == "validation":
            return "El mensaje no pudo ser procesado correctamente."
        return "Lo siento, ocurrió un error al procesar tu mensaje."


__all__ = [
    "DEFAULT_STAGES",
    "ExecutionPipeline",
    "PipelineError",
    "PipelineRequest",
    "StageFn",
    "stage_build_context",
    "stage_check_permissions",
    "stage_execute_agent",
    "stage_persist_user_turn",
    "stage_plan_execution",
    "stage_publish_agent_result",
    "stage_publish_user_message",
    "stage_retrieve_memory",
    "stage_select_agent",
    "stage_validate_request",
]
