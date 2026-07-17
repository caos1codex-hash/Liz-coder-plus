"""Shared fixtures for the tool_intelligence test suite.

Sprint 4.1 — Tool Intelligence Foundation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add the packages/ directory to sys.path so we can import
# tool_intelligence as a regular package.
ROOT = Path(__file__).resolve().parents[3]
PACKAGES = ROOT / "packages"
if str(PACKAGES) not in sys.path:
    sys.path.insert(0, str(PACKAGES))

# Clean any cached src modules from previous test runs (the project
# uses a shared `src` namespace across multiple packages).
for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from tool_intelligence import (  # noqa: E402
    CapabilityIndex,
    RiskLevel,
    SelectionContext,
    ToolCategory,
    ToolHistoryStats,
    ToolMetadata,
    UserIntent,
)


@pytest.fixture
def file_tool_meta() -> ToolMetadata:
    """A file-reading tool."""
    return ToolMetadata(
        id="file_read",
        name="file_read",
        description="Read files from disk and return their contents.",
        category=ToolCategory.FILE,
        capabilities=["filesystem.read"],
        risk_level=RiskLevel.LOW,
        estimated_cost=0.05,
        estimated_latency=20.0,
        aliases=["read_file", "cat"],
        tags=["io", "disk"],
        keywords=["read", "file", "disk"],
        permissions=["fs:read"],
        reliability=0.99,
    )


@pytest.fixture
def file_write_tool_meta() -> ToolMetadata:
    """A file-writing tool."""
    return ToolMetadata(
        id="file_write",
        name="file_write",
        description="Write content to a file on disk.",
        category=ToolCategory.FILE,
        capabilities=["filesystem.write"],
        risk_level=RiskLevel.MEDIUM,
        estimated_cost=0.1,
        estimated_latency=30.0,
        aliases=["write_file"],
        tags=["io", "disk"],
        keywords=["write", "file", "disk"],
        permissions=["fs:write"],
        requires_confirmation=True,
        reliability=0.97,
    )


@pytest.fixture
def shell_tool_meta() -> ToolMetadata:
    """A shell-execution tool."""
    return ToolMetadata(
        id="shell_exec",
        name="shell_exec",
        description="Execute shell commands safely with an allow-list.",
        category=ToolCategory.SHELL,
        capabilities=["terminal.execute"],
        risk_level=RiskLevel.HIGH,
        estimated_cost=0.3,
        estimated_latency=100.0,
        aliases=["bash", "cmd"],
        tags=["process", "exec"],
        keywords=["execute", "command", "shell", "bash"],
        permissions=["shell:exec"],
        dependencies=["bash"],
        requires_confirmation=True,
        reliability=0.92,
    )


@pytest.fixture
def web_search_tool_meta() -> ToolMetadata:
    """A web-search tool."""
    return ToolMetadata(
        id="web_search",
        name="web_search",
        description="Search the web for up-to-date information.",
        category=ToolCategory.WEB,
        capabilities=["web.search"],
        risk_level=RiskLevel.LOW,
        estimated_cost=0.2,
        estimated_latency=500.0,
        aliases=["search"],
        tags=["internet", "research"],
        keywords=["search", "web", "internet", "research"],
        permissions=["net:access"],
        reliability=0.95,
    )


@pytest.fixture
def llm_tool_meta() -> ToolMetadata:
    """A tool that requires a specific LLM model."""
    return ToolMetadata(
        id="llm_summarize",
        name="llm_summarize",
        description="Summarize text using a large language model.",
        category=ToolCategory.LLM,
        capabilities=["text.summarize"],
        risk_level=RiskLevel.LOW,
        estimated_cost=0.5,
        estimated_latency=2000.0,
        required_model="gpt-4",
        permissions=["llm:call"],
        keywords=["summarize", "text", "llm"],
        reliability=0.9,
    )


@pytest.fixture
def plugin_tool_meta() -> ToolMetadata:
    """A tool that requires a plugin."""
    return ToolMetadata(
        id="db_query",
        name="db_query",
        description="Run a SQL query against the project database.",
        category=ToolCategory.DATABASE,
        capabilities=["database.query"],
        risk_level=RiskLevel.MEDIUM,
        estimated_cost=0.15,
        estimated_latency=80.0,
        plugins=["postgres"],
        permissions=["db:read"],
        keywords=["database", "sql", "query"],
        reliability=0.93,
    )


@pytest.fixture
def populated_index(
    file_tool_meta: ToolMetadata,
    file_write_tool_meta: ToolMetadata,
    shell_tool_meta: ToolMetadata,
    web_search_tool_meta: ToolMetadata,
    llm_tool_meta: ToolMetadata,
    plugin_tool_meta: ToolMetadata,
) -> CapabilityIndex:
    """A CapabilityIndex pre-populated with six diverse tools."""
    index = CapabilityIndex()
    for meta in (
        file_tool_meta,
        file_write_tool_meta,
        shell_tool_meta,
        web_search_tool_meta,
        llm_tool_meta,
        plugin_tool_meta,
    ):
        index.add(meta)
    return index


@pytest.fixture
def default_context() -> SelectionContext:
    """Default selection context with bash available and broad permissions."""
    return SelectionContext(
        platform="linux",
        available_models=["gpt-4", "gpt-3.5"],
        granted_permissions=[
            "fs:read",
            "fs:write",
            "shell:exec",
            "net:access",
            "llm:call",
            "db:read",
            "category:file",
            "category:shell",
            "category:web",
            "category:llm",
            "category:database",
            "category:custom",
        ],
        loaded_plugins=["postgres"],
        max_cost=1.0,
        max_latency=10000.0,
    )


@pytest.fixture
def read_intent() -> UserIntent:
    """User intent asking to read a file."""
    return UserIntent(
        message="read the contents of a file",
        capabilities=["filesystem.read"],
        keywords=["read", "file"],
    )


@pytest.fixture
def history_with_stats() -> dict[str, ToolHistoryStats]:
    """Sample history stats for two tools."""
    return {
        "file_read": ToolHistoryStats(
            executions=100,
            successes=98,
            failures=2,
            avg_latency_ms=15.0,
            avg_cost=0.04,
        ),
        "shell_exec": ToolHistoryStats(
            executions=50,
            successes=40,
            failures=10,
            avg_latency_ms=90.0,
            avg_cost=0.28,
        ),
    }
