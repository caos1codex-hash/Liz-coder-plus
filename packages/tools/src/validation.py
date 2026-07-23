"""Tool Execution Validator.

Sprint 4.3 — Tool Execution Pipeline.

Provides :class:`ToolExecutionValidator` which validates all aspects
of a tool execution **before** it is submitted to the executor:

1. **Tool existence & availability** — the tool must be present and
   in the ``AVAILABLE`` state.
2. **Permission level** — the tool's required permission must not
   exceed the maximum allowed by the execution context.
3. **Argument schema** — required fields, type constraints, and
   any additional schema rules defined by the tool.
4. **Security restrictions** — optional configurable blocklist for
   argument values and patterns.

The validator does **not** execute the tool — it only inspects
inputs and raises :class:`~src.exceptions.ToolExecutionError`
subclasses on failure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.base import BaseTool, PermissionLevel, ToolError, ToolState
from src.exceptions import (
    ExecutionValidationError,
    InvalidArgumentsError,
    PermissionDeniedError,
    ToolExecutionError,
)
from src.results import ExecutionContext, ToolArguments

logger = logging.getLogger(__name__)

# Permission level ordering (higher number = more privilege).
_LEVEL_ORDER: dict[PermissionLevel, int] = {
    PermissionLevel.LOW: 0,
    PermissionLevel.MEDIUM: 1,
    PermissionLevel.HIGH: 2,
}

# Type-map used for schema-based type checking.
_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "str": str,
    "int": int,
    "float": (int, float),
    "bool": bool,
    "list": list,
    "dict": dict,
}

# Keys whose values are considered sensitive and should not be logged.
_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "token",
        "secret",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "private_key",
        "credential",
    }
)


@dataclass
class SecurityRestrictions:
    """Optional security restrictions applied during validation.

    Attributes:
        blocked_patterns: Regex patterns that are not allowed in any
            argument value (matched as substrings).
        blocked_values: Exact string values that are not allowed in
            any argument value.
        max_argument_count: Maximum number of arguments allowed.
            ``0`` or negative means no limit.
        max_total_size_bytes: Approximate size limit for the total
            serialised argument payload. ``0`` means no limit.
    """

    blocked_patterns: list[str] = field(default_factory=list)
    blocked_values: list[str] = field(default_factory=list)
    max_argument_count: int = 0
    max_total_size_bytes: int = 0


class ToolExecutionValidator:
    """Validates all pre-execution conditions for a tool.

    Args:
        max_permission_level: Highest permission level allowed.
        security: Optional security restrictions.

    Usage::

        validator = ToolExecutionValidator(
            max_permission_level=PermissionLevel.MEDIUM,
        )
        validator.validate(tool, args, exec_ctx)
    """

    def __init__(
        self,
        *,
        max_permission_level: PermissionLevel = PermissionLevel.HIGH,
        security: SecurityRestrictions | None = None,
    ) -> None:
        self._max_level = max_permission_level
        self._security = security or SecurityRestrictions()

    @property
    def max_permission_level(self) -> PermissionLevel:
        """The maximum allowed permission level."""
        return self._max_level

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def validate(
        self,
        tool: BaseTool,
        arguments: ToolArguments,
        exec_ctx: ExecutionContext,
    ) -> None:
        """Run all validation checks and raise on the first failure.

        The order of checks is intentional — cheap checks run first:

        1. Tool existence & availability
        2. Permission level
        3. Argument schema (types, required fields)
        4. Security restrictions

        Args:
            tool: The tool to validate.
            arguments: The arguments that will be passed.
            exec_ctx: The execution context.

        Raises:
            ExecutionValidationError: If the tool is not available.
            PermissionDeniedError: If permission level is too high.
            InvalidArgumentsError: If arguments fail schema checks.
            ToolExecutionError: If security restrictions are violated.
        """
        self._validate_availability(tool)
        self._validate_permission(tool)
        self._validate_arguments(tool, arguments)
        self._validate_security(arguments)

        logger.debug(
            "Validation passed for tool '%s' (args keys=%s)",
            tool.name,
            list(arguments.args.keys()),
        )

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _validate_availability(self, tool: BaseTool) -> None:
        """Check that the tool exists and is available."""
        if tool.state != ToolState.AVAILABLE:
            raise ExecutionValidationError(
                f"Tool '{tool.name}' is not available "
                f"(current state: {tool.state.value})",
                validation_type="availability",
                details={"tool_name": tool.name, "state": tool.state.value},
            )

    def _validate_permission(self, tool: BaseTool) -> None:
        """Check that the tool's permission level is allowed."""
        tool_ord = _LEVEL_ORDER[tool.permission_level]
        max_ord = _LEVEL_ORDER[self._max_level]

        if tool_ord > max_ord:
            raise PermissionDeniedError(
                f"Tool '{tool.name}' requires permission level "
                f"'{tool.permission_level.value}' but the maximum "
                f"allowed is '{self._max_level.value}'",
                tool_name=tool.name,
                required_level=tool.permission_level.value,
                max_level=self._max_level.value,
            )

    def _validate_arguments(
        self,
        tool: BaseTool,
        arguments: ToolArguments,
    ) -> None:
        """Validate arguments against the tool's parameter schema.

        This delegates to the tool's own ``validate_input()`` for
        schema-level checks, then performs additional type validation
        for fields that declare a ``type`` in the schema properties.
        """
        params = arguments.args

        # Let the tool validate its own schema first.
        try:
            tool.validate_input(params)
        except ToolError as exc:
            raise InvalidArgumentsError(
                str(exc),
                field=getattr(exc, "field", None),
            ) from exc

        # Additional type validation from schema properties.
        schema = tool.parameters_schema
        if not schema:
            return

        properties = schema.get("properties", {})
        for field_name, value in params.items():
            if field_name not in properties or value is None:
                continue
            field_spec = properties[field_name]
            expected_type_name = field_spec.get("type")
            if not expected_type_name:
                continue
            expected_type = _TYPE_MAP.get(expected_type_name)
            if expected_type is None:
                continue
            if not isinstance(value, expected_type):
                raise InvalidArgumentsError(
                    f"Argument '{field_name}' must be of type "
                    f"'{expected_type_name}', got "
                    f"'{type(value).__name__}'",
                    field=field_name,
                    expected=expected_type_name,
                    actual=type(value).__name__,
                )

    def _validate_security(self, arguments: ToolArguments) -> None:
        """Check argument values against security restrictions."""
        sec = self._security
        params = arguments.args

        # Argument count limit.
        if sec.max_argument_count > 0 and len(params) > sec.max_argument_count:
            raise ToolExecutionError(
                f"Too many arguments: {len(params)} exceeds limit of "
                f"{sec.max_argument_count}",
                details={"validation_type": "security"},
            )

        # Total size limit (approximate, based on str representation).
        if sec.max_total_size_bytes > 0:
            total_size = len(str(params))
            if total_size > sec.max_total_size_bytes:
                raise ToolExecutionError(
                    f"Total argument size ({total_size} bytes) exceeds "
                    f"limit of {sec.max_total_size_bytes} bytes",
                    details={"validation_type": "security"},
                )

        # Blocked exact values.
        for key, value in params.items():
            value_str = str(value)
            for blocked in sec.blocked_values:
                if blocked in value_str:
                    raise ToolExecutionError(
                        f"Argument '{key}' contains a blocked value",
                        details={
                            "validation_type": "security",
                            "argument": key,
                        },
                    )

        # Blocked regex patterns.
        for key, value in params.items():
            value_str = str(value)
            for pattern in sec.blocked_patterns:
                if re.search(pattern, value_str):
                    raise ToolExecutionError(
                        f"Argument '{key}' matches blocked pattern " f"'{pattern}'",
                        details={
                            "validation_type": "security",
                            "argument": key,
                            "pattern": pattern,
                        },
                    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def sanitize_args_for_logging(
        arguments: ToolArguments,
    ) -> dict[str, Any]:
        """Return a copy of the arguments with sensitive values masked.

        Any key containing a substring in ``_SENSITIVE_KEYS`` will
        have its value replaced with ``"***"``.
        """
        sanitized: dict[str, Any] = {}
        for key, value in arguments.args.items():
            key_lower = key.lower()
            if any(s in key_lower for s in _SENSITIVE_KEYS):
                sanitized[key] = "***"
            else:
                sanitized[key] = value
        return sanitized


__all__ = [
    "SecurityRestrictions",
    "ToolExecutionValidator",
]
