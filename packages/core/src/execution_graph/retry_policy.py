"""Retry Policy for execution nodes.

Sprint 3.8.

Provides configurable retry strategies including exponential backoff
with jitter, retryable exception classification, and attempt tracking.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Default retryable exception types.
_DEFAULT_RETRYABLE: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
    RuntimeError,
)


@dataclass
class RetryPolicy:
    """Configurable retry policy for execution nodes.

    Attributes:
        max_retries:         Maximum number of retry attempts.
        base_delay:          Base delay in seconds for the first retry.
        max_delay:           Cap on delay in seconds.
        exponential_base:    Base for exponential backoff (2 = doubling).
        jitter:              Jitter factor (0.0 = none, 1.0 = full).
        retryable_exceptions: Exception types that are retryable.
    """

    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter: float = 0.1
    retryable_exceptions: tuple[type[BaseException], ...] = _DEFAULT_RETRYABLE

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def should_retry(
        self,
        error: BaseException | None = None,
        attempt: int = 0,
    ) -> bool:
        """Decide whether to retry after an error.

        Args:
            error:   The exception that caused the failure.
            attempt: The attempt number (0-based).

        Returns:
            True if a retry should be attempted.
        """
        if attempt >= self.max_retries:
            return False

        if error is None:
            return True  # Retry if no error info.

        # Check if the exception is retryable.
        for exc_type in self.retryable_exceptions:
            if isinstance(error, exc_type):
                return True

        return False

    # ------------------------------------------------------------------
    # Delay calculation
    # ------------------------------------------------------------------

    def next_delay(self, attempt: int) -> float:
        """Calculate the delay before the next retry.

        Uses exponential backoff with optional jitter::

            delay = min(base_delay * exponential_base ** attempt, max_delay)
            delay = delay + uniform(-jitter_range, +jitter_range)
            where jitter_range = delay * jitter

        Args:
            attempt: The attempt number (0-based).

        Returns:
            Delay in seconds.
        """
        # Exponential backoff.
        delay = self.base_delay * (self.exponential_base ** attempt)
        delay = min(delay, self.max_delay)

        # Apply jitter.
        if self.jitter > 0:
            jitter_range = delay * self.jitter
            jitter_offset = random.uniform(-jitter_range, jitter_range)
            delay = max(0.0, delay + jitter_offset)

        logger.debug(
            "RetryPolicy: attempt=%d, delay=%.3fs", attempt, delay,
        )
        return delay

    # ------------------------------------------------------------------
    # Attempt tracking
    # ------------------------------------------------------------------

    def record_attempt(
        self,
        node_name: str,
        attempt: int,
        error: BaseException | None = None,
        duration_ms: float = 0.0,
    ) -> dict[str, Any]:
        """Record a retry attempt and compute next delay.

        Args:
            node_name:    Name of the node.
            attempt:      Current attempt number.
            error:        Error that caused the retry.
            duration_ms:  Duration of the failed attempt.

        Returns:
            Dict with attempt info and next delay (if retrying).
        """
        should = self.should_retry(error, attempt)
        next_delay = self.next_delay(attempt) if should else 0.0

        record = {
            "node_name": node_name,
            "attempt": attempt,
            "max_retries": self.max_retries,
            "should_retry": should,
            "next_delay": next_delay,
            "error": str(error) if error else None,
            "error_type": type(error).__name__ if error else None,
            "duration_ms": duration_ms,
            "exhausted": attempt + 1 >= self.max_retries,
        }

        if should:
            logger.info(
                "RetryPolicy: '%s' retrying (attempt %d/%d) in %.3fs",
                node_name, attempt + 1, self.max_retries, next_delay,
            )
        else:
            logger.info(
                "RetryPolicy: '%s' retries exhausted (attempt %d/%d)",
                node_name, attempt + 1, self.max_retries,
            )

        return record

    async def wait_for_retry(self, attempt: int) -> None:
        """Wait the appropriate delay before retrying.

        Args:
            attempt: The attempt number (0-based).
        """
        delay = self.next_delay(attempt)
        if delay > 0:
            await asyncio_sleep(delay)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "base_delay": self.base_delay,
            "max_delay": self.max_delay,
            "exponential_base": self.exponential_base,
            "jitter": self.jitter,
            "retryable_exceptions": [
                t.__name__ for t in self.retryable_exceptions
            ],
        }


async def asyncio_sleep(delay: float) -> None:
    """Async sleep helper to avoid importing asyncio at module level."""
    import asyncio
    await asyncio.sleep(delay)


__all__ = ["RetryPolicy"]