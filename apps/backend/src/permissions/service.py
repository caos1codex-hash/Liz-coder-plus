"""Permission service.

Evaluates whether a given action is allowed to execute automatically or
requires explicit user confirmation, based on the active PermissionMode
and the configured whitelist/denylist of commands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from src.core.config import PermissionsConfig
from src.permissions.modes import PermissionMode

logger = logging.getLogger(__name__)

Decision = Literal["allow", "confirm", "deny"]


@dataclass(frozen=True)
class PermissionDecision:
    """Outcome of a permission check."""

    decision: Decision
    reason: str
    mode: PermissionMode


class PermissionService:
    """Service that resolves whether an action may execute."""

    def __init__(self, config: PermissionsConfig) -> None:
        self._mode = PermissionMode.from_string(config.mode)
        self._allowed = {cmd.lower() for cmd in config.allowed_commands}
        self._denied = {cmd.lower() for cmd in config.denied_commands}

    @property
    def mode(self) -> PermissionMode:
        """Return the active permission mode."""
        return self._mode

    def evaluate(self, command: str) -> PermissionDecision:
        """Evaluate whether a command may run.

        Security-first semantics:
          - Denied check uses **prefix match**: any command whose lowercased
            form starts with a denied entry is rejected. This catches
            variants like `rm -rf /tmp` when `rm -rf` is denied.
          - Allowed check uses **exact match** to avoid granting more
            than the operator intended.

        Args:
            command: The command name or path to evaluate.

        Returns:
            A PermissionDecision indicating the outcome.
        """
        key = command.lower().strip()

        # Deny list: prefix match for safety.
        for denied in self._denied:
            if key == denied or key.startswith(denied + " ") or key.startswith(denied):
                return PermissionDecision(
                    decision="deny",
                    reason=f"Command '{command}' matches denied pattern '{denied}'.",
                    mode=self._mode,
                )

        # Allow list: exact match only.
        if self._mode is PermissionMode.AUTOMATIC and key in self._allowed:
            return PermissionDecision(
                decision="allow",
                reason="Automatic mode and command is whitelisted.",
                mode=self._mode,
            )

        if self._mode is PermissionMode.AUTOMATIC:
            return PermissionDecision(
                decision="confirm",
                reason="Automatic mode but command not whitelisted.",
                mode=self._mode,
            )

        return PermissionDecision(
            decision="confirm",
            reason="Confirmation mode requires user approval for all actions.",
            mode=self._mode,
        )
