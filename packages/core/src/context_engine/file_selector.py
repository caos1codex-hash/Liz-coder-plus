"""File Selector — relevance-based file selection and scoring.

Sprint 3.9.

Selects only files relevant to a given task from a project file list.
Uses keyword matching, path heuristics, dependency proximity and
recency/frequency signals to score each candidate file.

Example::

    selector = FileSelector()
    files = selector.select(
        task="Fix login bug",
        available_files=["src/auth/login.py", "src/ui/dashboard.py", ...],
    )
    # files = [FileScore(path="src/auth/login.py", score=0.92, ...), ...]
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .models import ContextFile, RelevanceLevel

logger = logging.getLogger(__name__)


@dataclass
class FileScore:
    """Scored file candidate.

    Attributes:
        path:         File path.
        score:        Relevance score (0.0 - 1.0).
        reasons:      Human-readable list of scoring reasons.
        tokens:       Estimated token count for the file content.
        content:      File content (filled by the engine if available).
        language:     Detected language.
    """

    path: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    tokens: int = 0
    content: str = ""
    language: str = ""


# Common keyword-to-path-fragment mappings used by heuristic scoring.
_TOPIC_PATTERNS: dict[str, list[str]] = {
    "auth": [
        "auth",
        "login",
        "logout",
        "session",
        "token",
        "jwt",
        "oauth",
        "password",
        "credential",
    ],
    "database": [
        "db",
        "database",
        "model",
        "migration",
        "schema",
        "query",
        "repository",
        "repo",
    ],
    "api": [
        "api",
        "route",
        "endpoint",
        "controller",
        "handler",
        "resource",
        "rest",
        "graphql",
    ],
    "ui": [
        "ui",
        "component",
        "view",
        "template",
        "style",
        "css",
        "html",
        "layout",
        "page",
    ],
    "test": ["test", "spec", "mock", "fixture", "conftest"],
    "config": ["config", "settings", "env", "conf", "setup", "pyproject", "toml"],
    "deploy": ["deploy", "docker", "ci", "cd", "pipeline", "compose", "kubernetes"],
}


def _estimate_tokens(text: str) -> int:
    """Rough estimate: ~4 characters per token for mixed content."""
    return max(1, len(text) // 4) if text else 0


def _detect_language(path: str) -> str:
    """Detect programming language from file extension."""
    ext = PurePosixPath(path).suffix.lstrip(".").lower()
    lang_map: dict[str, str] = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "tsx": "tsx",
        "jsx": "jsx",
        "rs": "rust",
        "go": "go",
        "java": "java",
        "rb": "ruby",
        "php": "php",
        "cs": "csharp",
        "md": "markdown",
        "yaml": "yaml",
        "yml": "yaml",
        "toml": "toml",
        "json": "json",
        "sql": "sql",
        "html": "html",
        "css": "css",
        "scss": "scss",
        "sh": "shell",
        "bash": "shell",
        "txt": "text",
    }
    return lang_map.get(ext, ext or "unknown")


def _classify_relevance(score: float) -> RelevanceLevel:
    """Map a numeric score to a RelevanceLevel."""
    if score >= 0.8:
        return RelevanceLevel.CRITICAL
    if score >= 0.6:
        return RelevanceLevel.HIGH
    if score >= 0.4:
        return RelevanceLevel.MEDIUM
    if score >= 0.2:
        return RelevanceLevel.LOW
    return RelevanceLevel.NONE


class FileSelector:
    """Selects and scores files relevant to a task.

    The selector uses multiple signals:

    1. **Keyword match** — task terms found in file path or content.
    2. **Path heuristics** — known directory/file naming conventions.
    3. **Test proximity** — prefer test files matching source files.
    4. **Recency/frequency** — recently modified or frequently referenced.

    Args:
        min_score:    Minimum score to include a file.
        max_files:    Maximum number of files to return (0 = unlimited).
        weights:      Signal weights: keyword, path, test, recency, frequency.
    """

    def __init__(
        self,
        *,
        min_score: float = 0.1,
        max_files: int = 20,
        weights: dict[str, float] | None = None,
    ) -> None:
        self._min_score = min_score
        self._max_files = max_files
        self._weights = {
            "keyword": 0.40,
            "path": 0.25,
            "test": 0.15,
            "recency": 0.10,
            "frequency": 0.10,
            **(weights or {}),
        }
        self._file_access_count: dict[str, int] = {}
        self._file_last_access: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select(
        self,
        task: str,
        available_files: list[str],
        *,
        file_contents: dict[str, str] | None = None,
    ) -> list[FileScore]:
        """Score and rank files by relevance to the task.

        Args:
            task:              The task description or user message.
            available_files:   List of candidate file paths.
            file_contents:     Optional mapping of path -> content for
                               content-based scoring.

        Returns:
            List of ``FileScore`` sorted by score (descending).
        """
        task_lower = task.lower()
        task_terms = self._extract_terms(task_lower)
        contents = file_contents or {}

        scored: list[FileScore] = []
        for path in available_files:
            score, reasons = self._score_file(
                path=path,
                task_terms=task_terms,
                task_lower=task_lower,
                file_content=contents.get(path, ""),
            )
            if score >= self._min_score:
                lang = _detect_language(path)
                content = contents.get(path, "")
                scored.append(
                    FileScore(
                        path=path,
                        score=round(score, 4),
                        reasons=reasons,
                        tokens=_estimate_tokens(content),
                        content=content,
                        language=lang,
                    )
                )

        scored.sort(key=lambda f: f.score, reverse=True)

        if self._max_files > 0:
            scored = scored[: self._max_files]

        # Update access tracking for recency/frequency.
        now = time.monotonic()
        for fs in scored:
            self._file_access_count[fs.path] = (
                self._file_access_count.get(fs.path, 0) + 1
            )
            self._file_last_access[fs.path] = now

        logger.debug(
            "FileSelector: %d/%d files selected for task (top=%s)",
            len(scored),
            len(available_files),
            scored[0].path if scored else "none",
        )
        return scored

    def record_access(self, path: str) -> None:
        """Manually record that a file was accessed (for recency/frequency)."""
        self._file_access_count[path] = self._file_access_count.get(path, 0) + 1
        self._file_last_access[path] = time.monotonic()

    def to_context_files(self, scores: list[FileScore]) -> list[ContextFile]:
        """Convert FileScore list to ContextFile list."""
        result: list[ContextFile] = []
        for fs in scores:
            result.append(
                ContextFile(
                    path=fs.path,
                    content=fs.content,
                    language=fs.language,
                    score=fs.score,
                    relevance=_classify_relevance(fs.score),
                    tokens=fs.tokens,
                    metadata={"reasons": fs.reasons},
                )
            )
        return result

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _extract_terms(self, text: str) -> set[str]:
        """Extract meaningful terms from text."""
        # Split on non-alphanumeric characters and filter short words.
        terms: set[str] = set()
        for token in re.split(r"[^a-z0-9]+", text):
            if len(token) >= 2:
                terms.add(token)
        return terms

    def _score_file(
        self,
        path: str,
        task_terms: set[str],
        task_lower: str,
        file_content: str,
    ) -> tuple[float, list[str]]:
        """Compute a composite score for a single file."""
        path_lower = path.lower().replace("\\", "/")
        path_parts = set(PurePosixPath(path_lower).parts)
        stem = PurePosixPath(path_lower).stem.lower()

        score = 0.0
        reasons: list[str] = []

        # 1. Keyword match in path.
        keyword_hits = task_terms & path_parts
        if stem in task_terms:
            keyword_hits.add(stem)
        if keyword_hits:
            kw_score = min(1.0, len(keyword_hits) / max(1, len(task_terms)))
            weighted = kw_score * self._weights["keyword"]
            score += weighted
            reasons.append(f"keyword_match({', '.join(sorted(keyword_hits)[:3])})")

        # Keyword match in content.
        if file_content:
            content_lower = file_content.lower()
            content_hits = sum(1 for term in task_terms if term in content_lower)
            if content_hits > 0:
                content_score = min(1.0, content_hits / max(1, len(task_terms)))
                weighted = content_score * self._weights["keyword"] * 0.5
                score += weighted
                reasons.append(f"content_keywords({content_hits})")

        # 2. Path heuristics — topic pattern matching.
        path_score, path_reasons = self._path_heuristic_score(path_lower, task_lower)
        if path_score > 0:
            score += path_score * self._weights["path"]
            reasons.extend(path_reasons)

        # 3. Test proximity.
        test_score, test_reason = self._test_proximity_score(path_lower, task_terms)
        if test_score > 0:
            score += test_score * self._weights["test"]
            reasons.append(test_reason)

        # 4. Recency signal.
        recency_score = self._recency_score(path)
        if recency_score > 0:
            score += recency_score * self._weights["recency"]

        # 5. Frequency signal.
        freq_score = self._frequency_score(path)
        if freq_score > 0:
            score += freq_score * self._weights["frequency"]

        return min(score, 1.0), reasons

    def _path_heuristic_score(
        self, path_lower: str, task_lower: str
    ) -> tuple[float, list[str]]:
        """Score based on known topic-to-path patterns."""
        score = 0.0
        reasons: list[str] = []

        for topic, fragments in _TOPIC_PATTERNS.items():
            for frag in fragments:
                if frag in task_lower:
                    # Task mentions this topic.
                    for frag2 in fragments:
                        if frag2 in path_lower:
                            score += 0.3
                            reasons.append(f"topic_{topic}({frag2})")
                            break
                    break

        return min(score, 1.0), reasons

    def _test_proximity_score(
        self, path_lower: str, task_terms: set[str]
    ) -> tuple[float, str]:
        """Bonus if a test file matches a source file referenced by task."""
        if "test" not in path_lower:
            return 0.0, ""
        for term in task_terms:
            if term in path_lower:
                return 0.7, f"test_match({term})"
        return 0.0, ""

    def _recency_score(self, path: str) -> float:
        """Score based on how recently the file was accessed."""
        last = self._file_last_access.get(path)
        if last is None:
            return 0.0
        age = time.monotonic() - last
        # Decay: 1.0 at 0s, ~0.5 at 60s, ~0.1 at 300s.
        return max(0.0, math.exp(-age / 120.0))

    def _frequency_score(self, path: str) -> float:
        """Score based on how often the file was accessed."""
        count = self._file_access_count.get(path, 0)
        if count == 0:
            return 0.0
        # Saturates at 5 accesses.
        return min(1.0, count / 5.0)


__all__ = [
    "FileScore",
    "FileSelector",
]
