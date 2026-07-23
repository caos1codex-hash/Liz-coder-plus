"""Adapters package — bridges between subsystems."""

from .multiagent_adapter import MultiagentAdapter, create_multiagent_adapters

__all__ = ["MultiagentAdapter", "create_multiagent_adapters"]
