"""Security and consistency validation for the semantic memory layer.

Validates:
- Vector dimensions
- Vector integrity (NaN, Inf, zero vectors)
- Index consistency (records in vector store vs. memory engine)
- Duplicate embeddings
- Empty queries
- Provider errors

The validator is defensive — it never corrupts the index.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class SemanticValidationError(Exception):
    """Raised when a semantic memory validation check fails."""

    def __init__(self, message: str, code: str = "validation_error") -> None:
        super().__init__(message)
        self.code = code


class SemanticValidator:
    """Validates data integrity for the semantic memory system.

    All validation methods are pure functions that return results
    without modifying any state. They are safe to call at any time.
    """

    def __init__(self, expected_dims: Optional[int] = None) -> None:
        self._expected_dims = expected_dims

    # -- vector validation -------------------------------------------------

    def validate_vector(
        self, vector: List[float], context: str = ""
    ) -> List[str]:
        """Validate a single vector.

        Returns a list of error messages. Empty list means valid.
        """
        errors: List[str] = []

        if not vector:
            errors.append(f"{context}: vector is empty")
            return errors

        # Check dimensions
        if self._expected_dims is not None and len(vector) != self._expected_dims:
            errors.append(
                f"{context}: expected {self._expected_dims} dimensions, got {len(vector)}"
            )

        # Check for NaN
        nan_indices = [i for i, v in enumerate(vector) if math.isnan(v)]
        if nan_indices:
            errors.append(
                f"{context}: NaN values at indices {nan_indices[:5]}"
                + ("..." if len(nan_indices) > 5 else "")
            )

        # Check for Inf
        inf_indices = [i for i, v in enumerate(vector) if math.isinf(v)]
        if inf_indices:
            errors.append(
                f"{context}: Inf values at indices {inf_indices[:5]}"
                + ("..." if len(inf_indices) > 5 else "")
            )

        # Check for zero vector
        if not any(v != 0 for v in vector):
            errors.append(f"{context}: zero vector (all components are 0)")

        return errors

    def validate_vectors(
        self, vectors: List[List[float]], context: str = ""
    ) -> Dict[str, List[str]]:
        """Validate multiple vectors.

        Returns a dict mapping index -> list of errors.
        Only includes indices with errors.
        """
        results: Dict[str, List[str]] = {}
        for i, vec in enumerate(vectors):
            errs = self.validate_vector(vec, f"{context}[{i}]")
            if errs:
                results[str(i)] = errs
        return results

    def validate_dimension_match(
        self, a: List[float], b: List[float], context: str = ""
    ) -> List[str]:
        """Validate that two vectors have the same dimensions."""
        if len(a) != len(b):
            return [f"{context}: dimension mismatch: {len(a)} vs {len(b)}"]
        return []

    # -- query validation --------------------------------------------------

    def validate_query(self, query: str) -> List[str]:
        """Validate a search query."""
        errors: List[str] = []
        if not query or not query.strip():
            errors.append("query is empty or whitespace only")
        if query and len(query) > 10_000:
            errors.append(f"query too long: {len(query)} characters (max 10000)")
        return errors

    # -- index consistency -------------------------------------------------

    async def validate_index_consistency(
        self,
        memory_ids: Set[str],
        vector_ids: Set[str],
    ) -> Dict[str, Any]:
        """Check consistency between memory engine and vector store.

        Args:
            memory_ids: Set of IDs from the memory engine.
            vector_ids: Set of IDs from the vector store.

        Returns:
            Dict with 'in_memory_not_vector', 'in_vector_not_memory', 'consistent'.
        """
        in_memory_not_vector = memory_ids - vector_ids
        in_vector_not_memory = vector_ids - memory_ids
        common = memory_ids & vector_ids

        return {
            "in_memory_not_vector": sorted(in_memory_not_vector),
            "in_vector_not_memory": sorted(in_vector_not_memory),
            "common_count": len(common),
            "memory_count": len(memory_ids),
            "vector_count": len(vector_ids),
            "consistent": len(in_memory_not_vector) == 0 and len(in_vector_not_memory) == 0,
        }

    # -- embedding validation ----------------------------------------------

    def validate_embedding_result(
        self,
        vector: List[float],
        text_hash: str,
        provider_name: str,
    ) -> List[str]:
        """Validate a complete embedding result."""
        errors = self.validate_vector(vector, f"embedding({provider_name})")
        if not text_hash:
            errors.append("embedding: empty text_hash")
        if not provider_name:
            errors.append("embedding: empty provider_name")
        return errors

    # -- duplicate detection -----------------------------------------------

    def find_duplicate_vectors(
        self,
        vectors: Dict[str, List[float]],
        threshold: float = 0.999,
    ) -> List[Tuple[str, str, float]]:
        """Find pairs of near-duplicate vectors.

        Args:
            vectors: Dict of id -> vector.
            threshold: Cosine similarity threshold for considering duplicates.

        Returns:
            List of (id_a, id_b, similarity) tuples for duplicate pairs.
        """
        duplicates: List[Tuple[str, str, float]] = []
        ids = list(vectors.keys())

        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a = vectors[ids[i]]
                b = vectors[ids[j]]
                if len(a) != len(b):
                    continue
                sim = self._cosine_similarity(a, b)
                if sim >= threshold:
                    duplicates.append((ids[i], ids[j], sim))

        return duplicates

    # -- sanitization ------------------------------------------------------

    def sanitize_vector(self, vector: List[float]) -> List[float]:
        """Return a sanitized copy of the vector.

        Replaces NaN with 0.0 and Inf with large finite values.
        """
        result = []
        for v in vector:
            if math.isnan(v):
                result.append(0.0)
            elif math.isinf(v):
                result.append(1e6 if v > 0 else -1e6)
            else:
                result.append(v)
        return result

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)