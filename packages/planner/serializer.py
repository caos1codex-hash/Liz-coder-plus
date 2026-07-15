"""Plan serializer.

Sprint 3.1 — Intelligent Task Planning Engine.

Supports three serialization backends:

* ``dict``  — native Python dict (fastest, default)
* ``json``  — JSON string via the standard library
* ``yaml``  — YAML string via PyYAML (optional dependency)

If ``yaml`` is not installed the YAML backend raises
:class:`SerializationError` with a clear message.
"""

from __future__ import annotations

import json
from typing import Any

from .exceptions import SerializationError
from .interfaces import ISerializer
from .models import TaskPlan


class PlanSerializer(ISerializer):
    """Concrete implementation of :class:`ISerializer`.

    Usage::

        serializer = PlanSerializer()
        blob = serializer.serialize(plan, format="json")
        plan = serializer.deserialize(blob, format="json")
    """

    # -- ISerializer ---------------------------------------------------------

    def serialize(self, plan: TaskPlan, format: str = "dict") -> Any:  # noqa: A002
        """Serialize *plan* to the requested format.

        Args:
            plan: The plan to serialize.
            format: ``"dict"``, ``"json"``, or ``"yaml"``.

        Returns:
            A dict, JSON string, or YAML string depending on *format*.

        Raises:
            SerializationError: On unknown format or missing YAML dependency.
        """
        fmt = format.lower()
        data = plan.to_dict()

        if fmt == "dict":
            return data
        if fmt == "json":
            return json.dumps(data, indent=2, ensure_ascii=False)
        if fmt == "yaml":
            return self._to_yaml(data)
        raise SerializationError(f"Unsupported serialization format: '{format}'")

    def deserialize(self, data: Any, format: str = "dict") -> TaskPlan:  # noqa: A002
        """Deserialize *data* back into a :class:`TaskPlan`.

        Args:
            data: A dict, JSON string, or YAML string.
            format: ``"dict"``, ``"json"``, or ``"yaml"``.

        Returns:
            A fully-constructed :class:`TaskPlan`.

        Raises:
            SerializationError: On parse failures or unknown format.
        """
        fmt = format.lower()

        if fmt == "dict":
            if isinstance(data, str):
                # Try to parse as JSON first
                try:
                    data = json.loads(data)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise SerializationError(f"Failed to parse dict data: {exc}") from exc
            if not isinstance(data, dict):
                raise SerializationError(
                    f"Expected dict or JSON string for 'dict' format, got {type(data).__name__}"
                )
            return TaskPlan.from_dict(data)

        if fmt == "json":
            if isinstance(data, dict):
                return TaskPlan.from_dict(data)
            if isinstance(data, str):
                try:
                    parsed = json.loads(data)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise SerializationError(f"Invalid JSON: {exc}") from exc
                return TaskPlan.from_dict(parsed)
            raise SerializationError(
                f"Expected str or dict for 'json' format, got {type(data).__name__}"
            )

        if fmt == "yaml":
            parsed = self._from_yaml(data)
            return TaskPlan.from_dict(parsed)

        raise SerializationError(f"Unsupported deserialization format: '{format}'")

    # -- YAML helpers --------------------------------------------------------

    @staticmethod
    def _to_yaml(data: dict[str, Any]) -> str:
        """Convert a dict to a YAML string.

        Raises:
            SerializationError: If PyYAML is not installed.
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise SerializationError(
                "PyYAML is required for YAML serialization. Install it with: pip install pyyaml"
            ) from exc
        return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)

    @staticmethod
    def _from_yaml(data: Any) -> dict[str, Any]:
        """Parse YAML data into a dict.

        Args:
            data: A YAML string or a file-like object.

        Raises:
            SerializationError: On parse errors or missing dependency.
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise SerializationError(
                "PyYAML is required for YAML deserialization. Install it with: pip install pyyaml"
            ) from exc
        try:
            result = yaml.safe_load(data)
        except yaml.YAMLError as exc:
            raise SerializationError(f"Invalid YAML: {exc}") from exc
        if not isinstance(result, dict):
            raise SerializationError(f"YAML content must be a mapping, got {type(result).__name__}")
        return result
