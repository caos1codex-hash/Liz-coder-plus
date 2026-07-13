"""Permission modes for Liz Coder Plus.

Two execution modes are supported:

- CONFIRMATION: the assistant asks the user before running any action
  that could affect the system (commands, file writes, network calls).
- AUTOMATIC: actions listed in the configuration's `allowed_commands`
  whitelist run without prompting; everything else still requires
  confirmation.

The mode is read from the active configuration file in `config/`.
"""

from __future__ import annotations

from enum import Enum


class PermissionMode(str, Enum):
    """Enumeration of permission modes.

    Inherits from `str` so values serialize cleanly to JSON in API
    responses and configuration files.
    """

    CONFIRMATION = "confirmation"
    AUTOMATIC = "automatic"

    @property
    def requires_confirmation(self) -> bool:
        """Return True if this mode asks the user before running actions."""
        return self is PermissionMode.CONFIRMATION

    @classmethod
    def from_string(cls, value: str | None) -> "PermissionMode":
        """Parse a permission mode from a string.

        Args:
            value: The string representation (case-insensitive).

        Returns:
            The matching PermissionMode. Defaults to CONFIRMATION when
            the input is missing or unrecognised, for safety.
        """
        if not value:
            return cls.CONFIRMATION
        try:
            return cls(value.lower())
        except ValueError:
            return cls.CONFIRMATION
