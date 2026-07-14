"""Tests para los agentes concretos (Sprint 2.1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.agents.coder import CoderAgent  # noqa: E402
from multiagent.agents.git import GitAgent  # noqa: E402
from multiagent.agents.memory import MemoryAgent  # noqa: E402
from multiagent.agents.planner import PlannerAgent  # noqa: E402
from multiagent.agents.research import ResearchAgent  # noqa: E402
from multiagent.agents.reviewer import ReviewerAgent  # noqa: E402
from multiagent.agents.terminal import TerminalAgent  # noqa: E402
from multiagent.enums import Permission  # noqa: E402


# ------------------------------------------------------------------
# Helper: arrancar un agente
# ------------------------------------------------------------------


async def _started(agent):
    await agent.start()
    return agent


# ------------------------------------------------------------------
# PlannerAgent
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_file_op_strategy() -> None:
    a = await _started(PlannerAgent())
    r = await a.execute({
        "id": "t1",
        "name": "plan",
        "payload": {"request": "lee el archivo config.json y resume su contenido"},
    })
    assert r["status"] == "ok"
    plan = r["result"]["plan"]
    assert plan["strategy"] == "file_op"
    assert len(plan["subtasks"]) == 3


@pytest.mark.asyncio
async def test_planner_terminal_strategy() -> None:
    a = await _started(PlannerAgent())
    r = await a.execute({
        "id": "t1",
        "name": "plan",
        "payload": {"request": "ejecuta el comando ls -la"},
    })
    plan = r["result"]["plan"]
    assert plan["strategy"] == "terminal"


@pytest.mark.asyncio
async def test_planner_fallback_single() -> None:
    a = await _started(PlannerAgent())
    r = await a.execute({
        "id": "t1",
        "name": "plan",
        "payload": {"request": "hello world"},
    })
    plan = r["result"]["plan"]
    assert plan["strategy"] == "single"
    assert len(plan["subtasks"]) == 1


# ------------------------------------------------------------------
# CoderAgent
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coder_refactor() -> None:
    a = await _started(CoderAgent())
    content = "def foo():\r\n    return 1   \r\n"
    r = await a.execute({
        "id": "t1",
        "name": "code",
        "payload": {"op": "refactor", "content": content, "language": "python"},
    })
    assert r["status"] == "ok"
    refactored = r["result"]["refactored"]
    assert "\r" not in refactored
    assert refactored.endswith("\n")


@pytest.mark.asyncio
async def test_coder_generate_tests() -> None:
    a = await _started(CoderAgent())
    content = "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n"
    r = await a.execute({
        "id": "t1",
        "name": "code",
        "payload": {"op": "test", "content": content, "path": "math_ops.py"},
    })
    assert r["status"] == "ok"
    tests = r["result"]["tests"]
    assert "test_add_basic" in tests
    assert "test_sub_basic" in tests


@pytest.mark.asyncio
async def test_coder_write_requires_permission() -> None:
    # Sin WRITE_FILES, debe lanzar PermissionError → execute falla con error.
    a = CoderAgent(permissions=frozenset({Permission.READ_FILES}))
    await a.start()
    r = await a.execute({
        "id": "t1",
        "name": "code",
        "payload": {"op": "write", "path": "/tmp/_liz_test.txt", "content": "hi"},
    })
    assert r["status"] == "error"
    assert "lacks permission" in r["error"]


@pytest.mark.asyncio
async def test_coder_fix_with_syntax_error() -> None:
    a = await _started(CoderAgent())
    r = await a.execute({
        "id": "t1",
        "name": "code",
        "payload": {"op": "fix", "content": "def foo(\n    pass\n", "error": "syntax error"},
    })
    assert r["status"] == "ok"
    assert "FIXME" in r["result"]["fixed"]


# ------------------------------------------------------------------
# ReviewerAgent
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_detects_security_issues() -> None:
    a = await _started(ReviewerAgent())
    content = "import os\nos.system('rm -rf /')\nresult = eval('1+1')\n"
    r = await a.execute({
        "id": "t1",
        "name": "review",
        "payload": {"content": content, "path": "bad.py"},
    })
    assert r["status"] == "ok"
    summary = r["result"]["summary"]
    assert summary["critical"] >= 2  # os.system + eval
    assert summary["by_category"]["security"] >= 2


@pytest.mark.asyncio
async def test_reviewer_detects_long_lines() -> None:
    a = await _started(ReviewerAgent())
    long_line = "x = " + "a" * 200
    r = await a.execute({
        "id": "t1",
        "name": "review",
        "payload": {"content": long_line, "path": "long.py"},
    })
    summary = r["result"]["summary"]
    assert summary["by_category"]["style"] >= 1


@pytest.mark.asyncio
async def test_reviewer_detects_missing_docstrings() -> None:
    a = await _started(ReviewerAgent())
    content = "def public_fn():\n    return 1\n"
    r = await a.execute({
        "id": "t1",
        "name": "review",
        "payload": {"content": content, "path": "no_docs.py"},
    })
    issues = r["result"]["issues"]
    docstring_issues = [i for i in issues if "docstring" in i["message"]]
    assert len(docstring_issues) >= 1


@pytest.mark.asyncio
async def test_reviewer_empty_content() -> None:
    a = await _started(ReviewerAgent())
    r = await a.execute({
        "id": "t1",
        "name": "review",
        "payload": {"content": "", "path": "empty.py"},
    })
    assert r["status"] == "ok"
    assert r["result"]["summary"]["total"] == 0


# ------------------------------------------------------------------
# ResearchAgent
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_research_builds_summary() -> None:
    a = await _started(ResearchAgent())
    r = await a.execute({
        "id": "t1",
        "name": "research",
        "payload": {"query": "investiga fastapi python framework", "topic": "fastapi"},
    })
    assert r["status"] == "ok"
    result = r["result"]
    assert result["topic"] == "fastapi"
    assert "overview" in result["summary"]
    assert len(result["summary"]["key_points"]) >= 1


@pytest.mark.asyncio
async def test_research_extract_topic() -> None:
    a = await _started(ResearchAgent())
    topic = a._extract_topic("investiga la librería fastapi para python")  # noqa: SLF001
    assert topic == "investiga" or topic == "librería" or topic == "fastapi" or topic == "python"


@pytest.mark.asyncio
async def test_research_fetch_invalid_url_returns_none() -> None:
    a = await _started(ResearchAgent())
    content = await a._fetch("not-a-url")  # noqa: SLF001
    assert content is None


# ------------------------------------------------------------------
# TerminalAgent
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_executes_allowed_command() -> None:
    a = await _started(TerminalAgent())
    r = await a.execute({
        "id": "t1",
        "name": "cmd",
        "payload": {"command": "echo hello"},
    })
    assert r["status"] == "ok"
    assert r["result"]["exit_code"] == 0
    assert "hello" in r["result"]["stdout"]


@pytest.mark.asyncio
async def test_terminal_rejects_dangerous_token() -> None:
    a = await _started(TerminalAgent())
    r = await a.execute({
        "id": "t1",
        "name": "cmd",
        "payload": {"command": "echo hi && rm -rf /"},
    })
    assert r["status"] == "ok"  # el agente respondió, pero con error
    assert r["result"]["exit_code"] < 0
    assert "forbidden token" in r["result"]["stderr"]


@pytest.mark.asyncio
async def test_terminal_rejects_non_allowlisted_command() -> None:
    a = await _started(TerminalAgent())
    r = await a.execute({
        "id": "t1",
        "name": "cmd",
        "payload": {"command": "rm -rf /"},
    })
    assert r["result"]["exit_code"] < 0
    assert "not in allowlist" in r["result"]["stderr"]


@pytest.mark.asyncio
async def test_terminal_requires_permission() -> None:
    a = TerminalAgent(permissions=frozenset({Permission.MEMORY_WRITE}))
    await a.start()
    r = await a.execute({
        "id": "t1",
        "name": "cmd",
        "payload": {"command": "echo hi"},
    })
    assert r["status"] == "error"
    assert "lacks permission" in r["error"]


# ------------------------------------------------------------------
# GitAgent
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_status(tmp_path: Path) -> None:
    # Crear un repo git temporal.
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    (tmp_path / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True,
    )

    a = GitAgent(repo_path=str(tmp_path))
    await a.start()
    r = await a.execute({
        "id": "t1",
        "name": "git",
        "payload": {"op": "log"},
    })
    assert r["status"] == "ok"
    assert r["result"]["exit_code"] == 0
    assert "initial" in r["result"]["stdout"]


@pytest.mark.asyncio
async def test_git_unknown_op() -> None:
    a = GitAgent(repo_path=".")
    await a.start()
    r = await a.execute({
        "id": "t1",
        "name": "git",
        "payload": {"op": "nonexistent_op"},
    })
    assert r["status"] == "ok"
    assert "unknown op" in r["result"]["error"]


@pytest.mark.asyncio
async def test_git_branch_op(tmp_path: Path) -> None:
    """Cubre branch/checkout/diff/pull/merge ops (al menos las ramas válidas)."""
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.txt").write_text("a")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=tmp_path, check=True, capture_output=True)

    a = GitAgent(repo_path=str(tmp_path))
    await a.start()
    # diff (no changes)
    r = await a.execute({"id": "t1", "name": "git", "payload": {"op": "diff"}})
    assert r["status"] == "ok"
    # branch + checkout
    r = await a.execute({"id": "t2", "name": "git", "payload": {"op": "branch", "name": "feature/x"}})
    assert r["status"] == "ok"
    r = await a.execute({"id": "t3", "name": "git", "payload": {"op": "checkout", "name": "feature/x"}})
    assert r["status"] == "ok"
    # merge back (should be no-op)
    r = await a.execute({"id": "t4", "name": "git", "payload": {"op": "checkout", "name": "master"}})
    assert r["status"] == "ok"
    r = await a.execute({"id": "t5", "name": "git", "payload": {"op": "merge", "name": "feature/x"}})
    assert r["status"] == "ok"
    # add + commit
    (tmp_path / "b.txt").write_text("b")
    r = await a.execute({"id": "t6", "name": "git", "payload": {"op": "add", "paths": ["."]}})
    assert r["status"] == "ok"
    r = await a.execute({"id": "t7", "name": "git", "payload": {"op": "commit", "message": "second"}})
    assert r["status"] == "ok"


@pytest.mark.asyncio
async def test_git_branch_requires_name() -> None:
    a = GitAgent(repo_path=".")
    await a.start()
    r = await a.execute({"id": "t1", "name": "git", "payload": {"op": "branch"}})
    assert r["status"] == "ok"
    assert "branch name required" in r["result"]["error"]


@pytest.mark.asyncio
async def test_git_push_force_requires_unrestricted(tmp_path: Path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "x.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "x"], cwd=tmp_path, check=True, capture_output=True)

    # Sin UNRESTRICTED, force push debe ser rechazado.
    a = GitAgent(repo_path=str(tmp_path))
    await a.start()
    r = await a.execute({
        "id": "t1",
        "name": "git",
        "payload": {"op": "push", "force": True, "remote": "origin"},
    })
    assert r["status"] == "ok"
    assert "force push requires UNRESTRICTED" in r["result"]["error"]


@pytest.mark.asyncio
async def test_git_requires_permission() -> None:
    a = GitAgent(
        repo_path=".",
        permissions=frozenset({Permission.MEMORY_WRITE}),
    )
    await a.start()
    r = await a.execute({
        "id": "t1",
        "name": "git",
        "payload": {"op": "status"},
    })
    assert r["status"] == "error"
    assert "lacks permission" in r["error"]


# ------------------------------------------------------------------
# MemoryAgent
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_store_retrieve() -> None:
    a = await _started(MemoryAgent())
    await a.execute({
        "id": "t1",
        "name": "mem",
        "payload": {"op": "store", "key": "k1", "value": {"answer": 42}},
    })
    r = await a.execute({
        "id": "t2",
        "name": "mem",
        "payload": {"op": "retrieve", "key": "k1"},
    })
    assert r["status"] == "ok"
    assert r["result"]["found"] is True
    assert r["result"]["entry"]["value"] == {"answer": 42}


@pytest.mark.asyncio
async def test_memory_forget() -> None:
    a = await _started(MemoryAgent())
    await a.execute({
        "id": "t1",
        "name": "mem",
        "payload": {"op": "store", "key": "k1", "value": "v1"},
    })
    r = await a.execute({
        "id": "t2",
        "name": "mem",
        "payload": {"op": "forget", "key": "k1"},
    })
    assert r["result"]["removed"] is True


@pytest.mark.asyncio
async def test_memory_search_embeddings() -> None:
    a = await _started(MemoryAgent())
    # Insertar embeddings.
    await a.execute({
        "id": "t1",
        "name": "mem",
        "payload": {"op": "add_embedding", "text": "hello world", "embedding": [1.0, 0.0, 0.0]},
    })
    await a.execute({
        "id": "t2",
        "name": "mem",
        "payload": {"op": "add_embedding", "text": "foo bar", "embedding": [0.0, 1.0, 0.0]},
    })
    # Buscar similar a [1, 0, 0].
    r = await a.execute({
        "id": "t3",
        "name": "mem",
        "payload": {"op": "search", "embedding": [1.0, 0.0, 0.0], "top_k": 5},
    })
    assert r["status"] == "ok"
    results = r["result"]["results"]
    assert len(results) >= 1
    assert results[0]["entry"]["value"] == "hello world"
    assert results[0]["score"] > 0.5


@pytest.mark.asyncio
async def test_memory_recent() -> None:
    a = await _started(MemoryAgent())
    await a.execute({
        "id": "t1",
        "name": "mem",
        "payload": {"op": "store", "key": "k1", "value": "v1"},
    })
    r = await a.execute({
        "id": "t2",
        "name": "mem",
        "payload": {"op": "recent", "n": 5},
    })
    assert r["status"] == "ok"
    assert len(r["result"]["entries"]) >= 1


@pytest.mark.asyncio
async def test_memory_unknown_op() -> None:
    a = await _started(MemoryAgent())
    r = await a.execute({
        "id": "t1",
        "name": "mem",
        "payload": {"op": "unknown_op"},
    })
    assert r["status"] == "ok"
    assert "unknown op" in r["result"]["error"]


# ------------------------------------------------------------------
# Interacción entre agentes vía MessageBus
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agents_communicate_via_bus() -> None:
    from multiagent.messages import MessageBus, Message
    from multiagent.enums import MessageType

    bus = MessageBus()
    planner = PlannerAgent(message_bus=bus)
    coder = CoderAgent(message_bus=bus)
    await planner.start()
    await coder.start()

    received: list[str] = []

    async def coder_handler(m: Message) -> None:
        received.append(m.payload.get("task", ""))

    # Suscribir manualmente un handler extra en coder.
    await bus.subscribe("coder", coder_handler)

    # planner envía un request a coder.
    msg = await planner.send_message(
        "coder",
        MessageType.REQUEST,
        {"task": "implement_feature_x"},
    )
    assert msg is not None
    # El handler debe haber recibido el mensaje.
    assert received == ["implement_feature_x"]

    await planner.stop()
    await coder.stop()
