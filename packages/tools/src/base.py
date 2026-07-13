"""Base class for all tools.

Every tool inherits from BaseTool. Subclasses must:
  - Set the ``name`` attribute.
  - Define ``parameters_schema`` (optional but recommended).
  - Implement ``_execute(params, context) -> dict``.

All tool executions are mediated by the ToolExecutor which consults
the PermissionService to decide whether the user must confirm the
action first.

The class provides identity (name + id), lifecycle states, permission
level, parameter validation, output validation, and structured metadata
for the orchestrator and UI to inspect before execution.

Sprint 1.6 — Added lifecycle states, permission levels, input/output
validation, parameter schema, unique id, and execution context support.
"""

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ToolCategory(str, Enum):
    """Categories of tools, used for permission grouping."""

    SHELL = "shell"
    FILE = "file"
    WEB = "web"
    APP = "app"
    SYSTEM = "system"
    CUSTOM = "custom"


class ToolState(str, Enum):
    """Lifecycle states for a tool.

    Transitions:
        AVAILABLE -> EXECUTING -> COMPLETED
                             -> ERROR
        Any non-terminal -> DISABLED
        DISABLED -> AVAILABLE (re-enable)
    """

    AVAILABLE = "available"
    EXECUTING = "executing"
    COMPLETED = "completed"
    ERROR = "error"
    DISABLED = "disabled"


class PermissionLevel(str, Enum):
    """Risk level associated with a tool, used by the permission system.

    LOW:    Read-only operations, information queries.
    MEDIUM: Controlled modifications (write files, create resources).
    HIGH:   Execution of commands, critical system actions.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolError(Exception):
    """Raised when a tool validation fails."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        self.field = field
        super().__init__(message)


class BaseTool(ABC):
    """Abstract base class for tools.

    Subclasses must set ``name``, define ``parameters_schema``
    (recommended), and implement ``_execute(params, context) -> dict``.
    The returned dict should include at minimum a ``"success"`` boolean.

    The base class manages:
      - Unique ``tool_id`` (UUID) for auditing.
      - Lifecycle state (AVAILABLE → EXECUTING → COMPLETED/ERROR).
      - Permission level (LOW/MEDIUM/HIGH) for policy decisions.
      - Parameter schema declaration for validation.
      - Input validation via ``validate_input()`` (override for custom logic).
      - Output validation via ``validate_output()`` (override for custom logic).
      - Execution context (session_id, agent_name) passed via ``execute()``.
      - Execution metrics (executions, failures, last error, total time).
    """

    name: str = "base"
    description: str = ""
    version: str = "0.1.0"
    category: ToolCategory = ToolCategory.CUSTOM
    permission_level: PermissionLevel = PermissionLevel.LOW
    parameters_schema: dict[str, Any] = {}

    def __init__(self, *, name: str | None = None) -> None:
        if name is not None:
            self.name = name
        self._id: str = str(uuid.uuid4())[:12]
        self._state = ToolState.AVAILABLE
        self._executions: int = 0
        self._failures: int = 0
        self._last_error: str | None = None
        self._total_time_ms: int = 0
        self._created_at: float = time.monotonic()
        logger.info(
            "Tool '%s' (id=%s) instantiated [%s, %s]",
            self.name,
            self._id,
            self.category.value,
            self.permission_level.value,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def tool_id(self) -> str:
        """Unique short identifier for this tool instance."""
        return self._id

    @property
    def state(self) -> ToolState:
        """Current lifecycle state."""
        return self._state

    @property
    def executions(self) -> int:
        """Total number of successful executions."""
        return self._executions

    @property
    def failures(self) -> int:
        """Total number of failed executions."""
        return self._failures

    @property
    def last_error(self) -> str | None:
        """Last error message, if any."""
        return self._last_error

    @property
    def is_available(self) -> bool:
        """Whether the tool is available for execution."""
        return self._state == ToolState.AVAILABLE

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def enable(self) -> None:
        """Mark the tool as available.

        Transitions: DISABLED -> AVAILABLE.
        Raises:
            RuntimeError: If the tool is currently executing.
        """
        if self._state == ToolState.EXECUTING:
            raise RuntimeError(
                f"Tool '{self.name}' cannot be enabled while executing"
            )
        prev = self._state
        self._state = ToolState.AVAILABLE
        if prev != ToolState.AVAILABLE:
            logger.info(
                "Tool '%s' (id=%s) enabled (was %s)",
                self.name,
                self._id,
                prev.value,
            )

    def disable(self) -> None:
        """Disable the tool so it cannot be executed.

        Transitions: Any non-EXECUTING -> DISABLED.
        Raises:
            RuntimeError: If the tool is currently executing.
        """
        if self._state == ToolState.EXECUTING:
            raise RuntimeError(
                f"Tool '{self.name}' cannot be disabled while executing"
            )
        prev = self._state
        self._state = ToolState.DISABLED
        if prev != ToolState.DISABLED:
            logger.info(
                "Tool '%s' (id=%s) disabled (was %s)",
                self.name,
                self._id,
                prev.value,
            )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_input(self, params: dict[str, Any]) -> None:
        """Validate input parameters before execution.

        Default implementation checks required fields defined in
        ``parameters_schema``. Subclasses can override for custom
        validation logic. Raise ``ToolError`` on validation failure.

        The schema format supports:
          - ``required`` (list[str]): field names that must be present.
          - ``properties`` (dict): per-field constraints:
              - ``type`` (str): expected type name (str, int, float, bool, list, dict).
              - ``description`` (str): human-readable description.
              - ``default`` (Any): default value if missing and not required.

        Args:
            params: The parameters to validate.

        Raises:
            ToolError: If validation fails.
        """
        if not self.parameters_schema:
            return

        required = self.parameters_schema.get("required", [])
        properties = self.parameters_schema.get("properties", {})

        # Check required fields.
        for field_name in required:
            if field_name not in params:
                raise ToolError(
                    f"Missing required parameter: '{field_name}'",
                    field=field_name,
                )

        # Check type constraints for provided fields.
        for field_name, value in params.items():
            if field_name in properties:
                field_spec = properties[field_name]
                expected_type = field_spec.get("type")
                if expected_type and value is not None:
                    type_map = {
                        "str": str,
                        "int": int,
                        "float": (int, float),
                        "bool": bool,
                        "list": list,
                        "dict": dict,
                    }
                    expected = type_map.get(expected_type)
                    if expected and not isinstance(value, expected):
                        raise ToolError(
                            f"Parameter '{field_name}' must be of type "
                            f"'{expected_type}', got '{type(value).__name__}'",
                            field=field_name,
                        )

    def validate_output(self, result: dict[str, Any]) -> None:
        """Validate output after execution.

        Default implementation checks that the result contains a
        ``"success"`` boolean. Subclasses can override for stricter
        validation.

        Args:
            result: The execution result dict.

        Raises:
            ToolError: If the output is invalid.
        """
        if "success" not in result:
            raise ToolError(
                "Tool result must include a 'success' boolean key"
            )
        if not isinstance(result["success"], bool):
            raise ToolError(
                "Tool result 'success' must be a boolean"
            )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        params: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the tool with validation and lifecycle tracking.

        This is the main entry point called by the ToolExecutor. It
        manages state transitions, validates input/output, and records
        metrics. Subclasses should NOT override this method; instead
        override ``_execute()``.

        Args:
            params: Tool-specific parameters.
            context: Execution context (session_id, agent_name, etc.).

        Returns:
            A dictionary with at minimum a ``"success"`` boolean.
            Additional fields: ``"output"``, ``"error"``, ``"tool_id"``,
            ``"duration_ms"``.

        Raises:
            ToolError: If input or output validation fails.
            RuntimeError: If the tool is not in an executable state.
        """
        if self._state == ToolState.DISABLED:
            raise RuntimeError(
                f"Tool '{self.name}' is disabled and cannot be executed"
            )
        if self._state == ToolState.EXECUTING:
            raise RuntimeError(
                f"Tool '{self.name}' is already executing"
            )

        start = time.monotonic()

        try:
            # Validate input.
            self.validate_input(params)

            # Transition to EXECUTING.
            self._state = ToolState.EXECUTING
            ctx = context or {}
            result = await self._execute(params, ctx)
            duration_ms = int((time.monotonic() - start) * 1000)

            # Validate output.
            self.validate_output(result)

            # Transition to COMPLETED.
            self._state = ToolState.COMPLETED
            self._executions += 1
            self._last_error = None
            self._total_time_ms += duration_ms

            result["tool_id"] = self._id
            result["duration_ms"] = duration_ms
            logger.info(
                "Tool '%s' (id=%s) completed in %dms",
                self.name,
                self._id,
                duration_ms,
            )
            return result

        except ToolError:
            self._state = ToolState.ERROR
            self._failures += 1
            raise

        except Exception as exc:
            self._state = ToolState.ERROR
            self._failures += 1
            self._last_error = str(exc)
            self._total_time_ms += int((time.monotonic() - start) * 1000)
            logger.exception(
                "Tool '%s' (id=%s) failed: %s", self.name, self._id, exc
            )
            raise

    @abstractmethod
    async def _execute(
        self,
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Actual execution logic. Subclasses must implement this.

        Args:
            params: Validated parameters.
            context: Execution context with keys like
                ``session_id``, ``agent_name``, etc.

        Returns:
            A dict with at minimum ``"success": True/False``.
            May include ``"output"`` and ``"error"``.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def metadata(self) -> dict[str, Any]:
        """Serializable metadata about this tool."""
        return {
            "name": self.name,
            "tool_id": self._id,
            "description": self.description,
            "version": self.version,
            "category": self.category.value,
            "permission_level": self.permission_level.value,
            "state": self._state.value,
            "type": self.__class__.__name__,
            "parameters_schema": self.parameters_schema,
            "executions": self._executions,
            "failures": self._failures,
            "last_error": self._last_error,
            "total_time_ms": self._total_time_ms,
        }

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} name={self.name!r} "
            f"id={self._id} category={self.category.value} "
            f"level={self.permission_level.value} "
            f"state={self._state.value}>"
        )