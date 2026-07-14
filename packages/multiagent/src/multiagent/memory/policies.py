"""Memory policies — TTL, expiration, archival, compression, and cleanup.

Provides configurable policies for managing memory lifecycle:
- TTL-based expiration
- Maximum record limits per owner/type
- Priority-based eviction
- Automatic cleanup scheduling
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .models import MemoryRecord, MemoryType

logger = logging.getLogger(__name__)


class PolicyAction(str, Enum):
    """Actions that can be taken on records matching a policy."""

    EXPIRE = "expire"
    ARCHIVE = "archive"
    COMPRESS = "compress"
    EVICT = "evict"


@dataclass
class MemoryPolicy:
    """A rule governing memory record lifecycle.

    Attributes:
        name: Human-readable policy name.
        description: What this policy does.
        memory_types: Which memory types this policy applies to (empty = all).
        owners: Which owners this policy applies to (empty = all).
        ttl_seconds: Time-to-live in seconds. Records older than this are expired.
        max_records: Maximum records before eviction kicks in.
        max_total_records: Global maximum across all owners.
        priority_floor: Records with priority below this are evicted first.
        action: What to do with matching records.
        enabled: Whether this policy is active.
    """

    name: str = "default"
    description: str = ""
    memory_types: List[MemoryType] = field(default_factory=list)
    owners: List[str] = field(default_factory=list)
    ttl_seconds: Optional[int] = None
    max_records: Optional[int] = None
    max_total_records: Optional[int] = None
    priority_floor: int = 0
    action: PolicyAction = PolicyAction.EXPIRE
    enabled: bool = True

    def applies_to(self, record: MemoryRecord) -> bool:
        """Check if this policy applies to a given record."""
        if not self.enabled:
            return False
        if self.memory_types and record.memory_type not in self.memory_types:
            return False
        if self.owners and record.owner not in self.owners:
            return False
        return True

    def is_expired(self, record: MemoryRecord) -> bool:
        """Check if a record should be expired by this policy."""
        if not self.applies_to(record):
            return False
        if self.ttl_seconds is None:
            return False
        return record.is_expired() or self._custom_ttl_expired(record)

    def _custom_ttl_expired(self, record: MemoryRecord) -> bool:
        """Check custom TTL (may differ from record's own TTL)."""
        if self.ttl_seconds is None:
            return False
        try:
            from datetime import datetime, timezone

            created = datetime.fromisoformat(record.created_at)
            age = (datetime.now(timezone.utc) - created).total_seconds()
            return age > self.ttl_seconds
        except (ValueError, TypeError):
            return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "memory_types": [mt.value for mt in self.memory_types],
            "owners": self.owners,
            "ttl_seconds": self.ttl_seconds,
            "max_records": self.max_records,
            "max_total_records": self.max_total_records,
            "priority_floor": self.priority_floor,
            "action": self.action.value,
            "enabled": self.enabled,
        }


class MemoryPolicyManager:
    """Manages and enforces memory policies.

    Policies are evaluated in order. The first matching policy's action
    is applied to the record.
    """

    def __init__(self) -> None:
        self._policies: List[MemoryPolicy] = []
        self._expired_count = 0
        self._archived_count = 0
        self._evicted_count = 0
        self._cleanup_count = 0

    @property
    def policies(self) -> List[MemoryPolicy]:
        return list(self._policies)

    # -- policy management -------------------------------------------------

    def add_policy(self, policy: MemoryPolicy) -> None:
        """Register a new policy."""
        if not policy.name:
            raise ValueError("policy must have a name")
        # Check for duplicate names
        existing = [p for p in self._policies if p.name == policy.name]
        if existing:
            raise ValueError(f"duplicate policy name: {policy.name}")
        self._policies.append(policy)
        logger.info("added policy: %s (action=%s)", policy.name, policy.action.value)

    def remove_policy(self, name: str) -> bool:
        """Remove a policy by name. Returns True if removed."""
        for i, p in enumerate(self._policies):
            if p.name == name:
                self._policies.pop(i)
                logger.info("removed policy: %s", name)
                return True
        return False

    def get_policy(self, name: str) -> Optional[MemoryPolicy]:
        """Get a policy by name."""
        for p in self._policies:
            if p.name == name:
                return p
        return None

    def enable_policy(self, name: str) -> bool:
        """Enable a policy by name."""
        p = self.get_policy(name)
        if p:
            p.enabled = True
            return True
        return False

    def disable_policy(self, name: str) -> bool:
        """Disable a policy by name."""
        p = self.get_policy(name)
        if p:
            p.enabled = False
            return True
        return False

    # -- evaluation --------------------------------------------------------

    def evaluate(self, record: MemoryRecord) -> List[MemoryPolicy]:
        """Return all policies that apply to a record."""
        return [p for p in self._policies if p.applies_to(record)]

    def should_expire(self, record: MemoryRecord) -> bool:
        """Check if any policy says this record should be expired."""
        return any(p.is_expired(record) for p in self._policies)

    def get_action(self, record: MemoryRecord) -> Optional[PolicyAction]:
        """Get the first applicable policy's action for a record."""
        for p in self._policies:
            if p.is_expired(record):
                return p.action
        return None

    # -- cleanup -----------------------------------------------------------

    async def cleanup(self, engine: "MemoryEngine") -> Dict[str, int]:
        """Run cleanup: find expired/evictable records and apply policies.

        Requires a MemoryEngine to fetch and delete records.

        Returns dict with counts: expired, archived, evicted, total_cleaned.
        """
        from .engine import MemoryEngine

        results = {"expired": 0, "archived": 0, "evicted": 0, "total_cleaned": 0}
        records = await engine.list_records(limit=100_000)

        for record in records:
            action = self.get_action(record)
            if action is None:
                continue

            if action == PolicyAction.EXPIRE:
                await engine.remove(record.id)
                results["expired"] += 1
                self._expired_count += 1
            elif action == PolicyAction.EVICT:
                await engine.remove(record.id)
                results["evicted"] += 1
                self._evicted_count += 1
            elif action == PolicyAction.ARCHIVE:
                # Archive: mark as archived in metadata before removing
                record.metadata["archived"] = True
                try:
                    await engine.update(record)
                except (KeyError, ValueError):
                    pass
                results["archived"] += 1
                self._archived_count += 1

        results["total_cleaned"] = (
            results["expired"] + results["archived"] + results["evicted"]
        )
        self._cleanup_count += 1
        logger.info(
            "policy cleanup: %s", results
        )
        return results

    async def enforce_limits(self, engine: "MemoryEngine") -> Dict[str, int]:
        """Enforce max_records limits per owner/type."""
        from .engine import MemoryEngine

        results: Dict[str, int] = {}
        for policy in self._policies:
            if not policy.enabled:
                continue

            # Per-owner limits
            if policy.max_records is not None:
                for owner in policy.owners or [None]:
                    records = await engine.list_records(
                        owner=owner,
                        memory_type=policy.memory_types[0] if policy.memory_types else None,
                        limit=100_000,
                    )
                    if len(records) > policy.max_records:
                        # Sort by priority asc, remove lowest priority
                        records.sort(key=lambda r: r.priority)
                        to_remove = len(records) - policy.max_records
                        for rec in records[:to_remove]:
                            if rec.priority < policy.priority_floor:
                                await engine.remove(rec.id)
                                results[f"evicted_{owner or 'all'}"] = (
                                    results.get(f"evicted_{owner or 'all'}", 0) + 1
                                )

            # Global limit
            if policy.max_total_records is not None:
                total = await engine.count()
                if total > policy.max_total_records:
                    all_records = await engine.list_records(
                        limit=100_000
                    )
                    all_records.sort(key=lambda r: (r.priority, r.updated_at))
                    to_remove = total - policy.max_total_records
                    for rec in all_records[:to_remove]:
                        await engine.remove(rec.id)
                    results["global_evicted"] = to_remove

        return results

    def stats(self) -> Dict[str, Any]:
        """Return policy manager statistics."""
        return {
            "total_policies": len(self._policies),
            "enabled_policies": sum(1 for p in self._policies if p.enabled),
            "expired_total": self._expired_count,
            "archived_total": self._archived_count,
            "evicted_total": self._evicted_count,
            "cleanup_runs": self._cleanup_count,
        }