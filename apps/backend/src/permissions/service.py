"""Permission service.

Evaluates whether a given action is allowed to execute automatically or
requires explicit user confirmation, based on the active PermissionMode,
the configured whitelist/denylist of commands, and the permission level
of the tool being evaluated.

Permission levels:
  LOW:    Read-only operations, information queries.
          In Automatic mode with level-aware policy, allowed automatically.
  MEDIUM: Controlled modifications (write files, create resources).
          In Automatic mode, requires confirmation unless whitelisted.
  HIGH:   Execution of commands, critical system actions.
          Always requires confirmation unless explicitly whitelisted.

Sprint 1.6 — Added permission levels (LOW/MEDIUM/HIGH), tool metadata
evaluation, audit logging, and level-aware automatic mode.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from src.core.config import PermissionsConfig
from src.permissions.modes import PermissionMode

logger = logging.getLogger(__name__)

Decision = Literal["allow", "confirm", "deny"]

# Level order for comparison (higher = more dangerous).
_LEVEL_ORDER: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
}


@dataclass(frozen=True)
class PermissionDecision:
    """Outcome of a permission check."""

    decision: Decision
    reason: str
    mode: PermissionMode
    level: str = ""


@dataclass
class AuditEntry:
    """Record of a permission evaluation for audit purposes."""

    tool_name: str
    agent_name: str
    decision: str
    level: str
    mode: str
    reason: str
    timestamp: float = field(default_factory=time.monotonic)


class PermissionService:
    """Service that resolves whether an action may execute.

    Supports two evaluation modes:
      - ``evaluate(command)``: legacy string-based evaluation (backward
        compatible).
      - ``evaluate_tool(tool_name, *, level, agent_name)``: level-aware
        evaluation that considers the tool's permission level.

    Audit log:
      Every evaluation is recorded in ``self.audit_log`` for forensic
      analysis. Entries include tool name, agent name, decision, level,
      mode, reason, and timestamp.
    """

    def __init__(self, config: PermissionsConfig) -> None:
        self._mode = PermissionMode.from_string(config.mode)
        self._allowed = {cmd.lower() for cmd in config.allowed_commands}
        self._denied = {cmd.lower() for cmd in config.denied_commands}
        # Level thresholds for automatic mode:
        #   In automatic mode, tools at or below this level are allowed
        #   without confirmation (unless denied).
        self._auto_allow_up_to = _LEVEL_ORDER.get("low", 0)
        self._audit_log: list[AuditEntry] = []

    @property
    def mode(self) -> PermissionMode:
        """Return the active permission mode."""
        return self._mode

    @property
    def audit_log(self) -> list[AuditEntry]:
        """Return the audit log (read-only copy)."""
        return list(self._audit_log)

    @property
    def auto_allow_up_to(self) -> str:
        """Return the level threshold for automatic allowance."""
        for lvl_name, lvl_order in _LEVEL_ORDER.items():
            if lvl_order == self._auto_allow_up_to:
                return lvl_name
        return "low"

    def set_auto_allow_up_to(self, level: str) -> None:
        """Set the level threshold for automatic mode.

        In automatic mode, tools at or below this level are allowed
        without confirmation. For example, if set to "low", only LOW
        tools are auto-allowed. If set to "medium", LOW and MEDIUM
        tools are auto-allowed.

        Args:
            level: One of "low", "medium", "high".
        """
        order = _LEVEL_ORDER.get(level.lower())
        if order is None:
            raise ValueError(
                f"Invalid permission level: '{level}'. "
                f"Must be one of: {list(_LEVEL_ORDER.keys())}"
            )
        self._auto_allow_up_to = order
        logger.info(
            "Auto-allow threshold set to '%s' (order=%d)",
            level,
            order,
        )

    def evaluate(self, command: str) -> PermissionDecision:
        """Evaluate whether a command may run (legacy API).

        Security-first semantics:
          - Denied check uses **prefix match**: any command whose lowercased
            form starts with a denied entry is rejected.
          - Allowed check uses **exact match**.

        This method is backward-compatible with Sprint 1.5 tests.

        Args:
            command: The command name or path to evaluate.

        Returns:
            A PermissionDecision indicating the outcome.
        """
        key = command.lower().strip()

        # Deny list: prefix match for safety.
        for denied in self._denied:
            if key == denied or key.startswith(denied + " ") or key.startswith(denied):
                decision = PermissionDecision(
                    decision="deny",
                    reason=f"Command '{command}' matches denied pattern '{denied}'.",
                    mode=self._mode,
                )
                self._record_audit(command, "", decision)
                return decision

        # Allow list: exact match only.
        if self._mode is PermissionMode.AUTOMATIC and key in self._allowed:
            decision = PermissionDecision(
                decision="allow",
                reason="Automatic mode and command is whitelisted.",
                mode=self._mode,
            )
            self._record_audit(command, "", decision)
            return decision

        if self._mode is PermissionMode.AUTOMATIC:
            decision = PermissionDecision(
                decision="confirm",
                reason="Automatic mode but command not whitelisted.",
                mode=self._mode,
            )
            self._record_audit(command, "", decision)
            return decision

        decision = PermissionDecision(
            decision="confirm",
            reason="Confirmation mode requires user approval for all actions.",
            mode=self._mode,
        )
        self._record_audit(command, "", decision)
        return decision

    def evaluate_tool(
        self,
        tool_name: str,
        *,
        level: str = "low",
        agent_name: str = "",
    ) -> PermissionDecision:
        """Evaluate whether a tool may run, considering its permission level.

        This is the recommended API for Sprint 1.6+ when the tool's
        permission level is known.

        Decision logic:
          1. Check deny list (prefix match on tool_name).
          2. If in Confirmation mode → always confirm (unless denied).
          3. If in Automatic mode:
             a. If tool is whitelisted → allow.
             b. If tool level <= auto_allow_up_to → allow.
             c. Otherwise → confirm.

        Args:
            tool_name: Name of the tool to evaluate.
            level: Permission level of the tool (low/medium/high).
            agent_name: Name of the requesting agent (for audit).

        Returns:
            A PermissionDecision with the outcome and level info.
        """
        key = tool_name.lower().strip()
        level_key = level.lower()
        level_order = _LEVEL_ORDER.get(level_key, 0)

        # 1. Deny list: prefix match.
        for denied in self._denied:
            if key == denied or key.startswith(denied + " ") or key.startswith(denied):
                decision = PermissionDecision(
                    decision="deny",
                    reason=(
                        f"Tool '{tool_name}' matches denied pattern "
                        f"'{denied}'."
                    ),
                    mode=self._mode,
                    level=level_key,
                )
                self._record_audit(tool_name, agent_name, decision, level_key)
                return decision

        # 2. Confirmation mode → always confirm.
        if self._mode is PermissionMode.CONFIRMATION:
            if level_key == "low":
                decision = PermissionDecision(
                    decision="confirm",
                    reason=(
                        f"Confirmation mode: tool '{tool_name}' "
                        f"(level={level_key}) requires approval."
                    ),
                    mode=self._mode,
                    level=level_key,
                )
            elif level_key == "high":
                decision = PermissionDecision(
                    decision="confirm",
                    reason=(
                        f"Confirmation mode: HIGH risk tool "
                        f"'{tool_name}' requires explicit approval."
                    ),
                    mode=self._mode,
                    level=level_key,
                )
            else:
                decision = PermissionDecision(
                    decision="confirm",
                    reason=(
                        f"Confirmation mode: MEDIUM risk tool "
                        f"'{tool_name}' requires approval."
                    ),
                    mode=self._mode,
                    level=level_key,
                )
            self._record_audit(tool_name, agent_name, decision, level_key)
            return decision

        # 3. Automatic mode.
        # 3a. Whitelisted → allow.
        if key in self._allowed:
            decision = PermissionDecision(
                decision="allow",
                reason=(
                    f"Automatic mode: tool '{tool_name}' is whitelisted."
                ),
                mode=self._mode,
                level=level_key,
            )
            self._record_audit(tool_name, agent_name, decision, level_key)
            return decision

        # 3b. Level <= threshold → allow.
        if level_order <= self._auto_allow_up_to:
            decision = PermissionDecision(
                decision="allow",
                reason=(
                    f"Automatic mode: tool '{tool_name}' "
                    f"(level={level_key}) is at or below "
                    f"auto-allow threshold "
                    f"({self.auto_allow_up_to})."
                ),
                mode=self._mode,
                level=level_key,
            )
            self._record_audit(tool_name, agent_name, decision, level_key)
            return decision

        # 3c. Otherwise → confirm.
        decision = PermissionDecision(
            decision="confirm",
            reason=(
                f"Automatic mode: tool '{tool_name}' "
                f"(level={level_key}) exceeds auto-allow threshold "
                f"({self.auto_allow_up_to}). Requires confirmation."
            ),
            mode=self._mode,
            level=level_key,
        )
        self._record_audit(tool_name, agent_name, decision, level_key)
        return decision

    def clear_audit_log(self) -> int:
        """Clear the audit log and return the number of entries removed."""
        count = len(self._audit_log)
        self._audit_log.clear()
        return count

    def _record_audit(
        self,
        tool_name: str,
        agent_name: str,
        decision: PermissionDecision,
        level: str = "",
    ) -> None:
        """Record an audit entry for the evaluation."""
        entry = AuditEntry(
            tool_name=tool_name,
            agent_name=agent_name,
            decision=decision.decision,
            level=level or getattr(decision, "level", ""),
            mode=decision.mode.value,
            reason=decision.reason,
        )
        self._audit_log.append(entry)
        logger.debug(
            "Permission audit: tool=%s agent=%s decision=%s level=%s mode=%s",
            tool_name,
            agent_name,
            decision.decision,
            level or getattr(decision, "level", ""),
            decision.mode.value,
        )