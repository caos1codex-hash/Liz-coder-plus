# Developer Guide — Liz Coder Plus

**Sprint 2.10 — Final Architecture Audit**  
**Status**: Canonical — read this before starting any new sprint.  
**Last updated**: 2026-07-14

This guide is for developers starting work on Liz Coder Plus after
Sprint 2.10 (Phase 2 closure). It covers the project structure,
conventions, testing strategy, and preparation for Phase 3.

---

## 1. Quick Start

### 1.1 Clone & branch

```bash
git clone https://github.com/caos1codex-hash/Liz-coder-plus.git
cd Liz-coder-plus
git checkout -b sprint-X.Y-feature-name
```

### 1.2 Install dependencies

```bash
pip install -r apps/backend/requirements.txt
pip install pytest pytest-asyncio pytest-cov ruff black mypy
```

### 1.3 Run tests

```bash
# All tests (run from repo root).
python -m pytest tests/ -q

# Communication tests only.
python -m pytest tests/communication/ -q

# Planner tests only.
python -m pytest tests/planner/ -q

# Multiagent + core tests only.
python -m pytest tests/unit/test_workflow_dag.py tests/unit/test_multiagent_orchestrator.py tests/agents/ -q
```

**Note**: some test files share names (e.g. `test_integration.py` exists
in both `tests/communication/` and `tests/planner/`). Run them separately
to avoid pytest collection errors.

### 1.4 Run quality checks

```bash
ruff check packages/ tests/
black --check --line-length=100 packages/ tests/
mypy packages/communication/src/communication/ --ignore-missing-imports
```

---

## 2. Project Structure

```
Liz-coder-plus/
├── packages/                ← Python packages (monorepo)
│   ├── core/                ← Backend orchestrator
│   ├── multiagent/          ← Multi-agent runtime
│   ├── communication/       ← Agent messaging (Sprint 2.9)
│   ├── planner/             ← Task planner (Sprint 2.8)
│   ├── memory/              ← SQLite persistence
│   ├── llm/                 ← LLM provider abstraction
│   ├── tools/               ← Tool system
│   ├── agents/              ← Lightweight Agent Protocol
│   └── shared/              ← Shared types
├── tests/                   ← Test suite
│   ├── unit/                ← Unit tests (core, multiagent)
│   ├── integration/         ← Integration tests
│   ├── agents/              ← Agent tests
│   ├── planner/             ← Planner tests (Sprint 2.8)
│   └── communication/       ← Communication tests (Sprint 2.9)
├── apps/                    ← Applications
│   ├── backend/             ← FastAPI + WebSocket backend
│   └── desktop/             ← WPF desktop client (C#)
├── docs/                    ← Documentation
├── config/                  ← Configuration files
├── scripts/                 ← Utility scripts
├── CHANGELOG.md             ← Versioned changelog
└── VERSION                  ← Current version (0.10.0)
```

### 2.1 The `src/` namespace collision (known debt)

Every package uses `src/` as its source directory. The `__init__.py`
inside makes `src` itself the top-level package. This means only one
package's `src` can be on `sys.path` at a time.

**Workarounds**:
- `packages/core/liz_core.py` — a shim that saves/restores `sys.modules["src"]`.
- Each test module manipulates `sys.path` individually (see any
  `tests/*/test_*.py` file for the pattern).
- `apps/backend` uses the `liz_core.py` shim + `sys.path` hacks.

**Future fix** (not in Sprint 2.10): rename each package's source dir to
match its published name (e.g. `packages/core/src/` → `packages/core/liz_core/`).

---

## 3. Conventions

### 3.1 Code style

- **Line length**: 100 chars (enforced by black + ruff).
- **Python version**: 3.11+ (target 3.12).
- **Typing**: strong typing everywhere. Use `mypy --strict` for new code.
- **Imports**: use `from __future__ import annotations` at the top of
  every module.
- **Docstrings**: every public class and function must have a docstring.
  Use Google-style or reStructuredText.

### 3.2 Package structure

Each package follows this layout:

```
packages/<name>/
├── pyproject.toml          ← Package metadata + tool config
├── README.md               ← Package overview
└── src/
    ├── __init__.py          ← Makes "src" a package (known debt)
    └── <name>/              ← Actual package code
        ├── __init__.py      ← Public API exports
        ├── module1.py
        └── ...
```

### 3.3 Cross-package imports

- **Never** import from another package at module load time (top-level).
- **Always** use lazy imports for cross-package dependencies:
  - `TYPE_CHECKING` for type hints.
  - Function-local imports for runtime use.
  - `try/except ImportError` for optional dependencies.
- **Example** (from `communication/bus.py`):
  ```python
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      from multiagent.messages import MessageBus
  ```

### 3.4 Exception hierarchy

Every package defines its own exception hierarchy rooted at a single
base class:
- `communication.exceptions.CommunicationError`
- `planner.exceptions.PlannerError`
- `multiagent` uses stdlib exceptions (ValueError, TypeError, RuntimeError).

### 3.5 Serialization

All dataclasses that cross package boundaries must implement:
- `serialize() -> dict[str, Any]` — JSON-compatible.
- `deserialize(data: dict) -> Self` — classmethod, round-trip safe.

### 3.6 Async conventions

- All I/O operations must be `async`.
- Use `asyncio.Lock` for shared mutable state (not `threading.Lock`).
- Never call `asyncio.run()` inside an async context.
- Test async code with `@pytest.mark.asyncio`.

---

## 4. Testing Strategy

### 4.1 Test organization

```
tests/
├── unit/           ← Unit tests (fast, no I/O)
├── integration/    ← Integration tests (may use SQLite, may be slower)
├── agents/         ← Agent lifecycle tests
├── planner/        ← Planner package tests
├── communication/  ← Communication package tests
├── e2e/            ← End-to-end tests (full stack)
├── conftest.py     ← Shared fixtures
└── pytest.ini      ← pytest config (asyncio_mode=auto)
```

### 4.2 Test file convention

Each test module is responsible for its own `sys.path` setup:

```python
"""Unit tests for <module>."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SRC = ROOT / "packages" / "<name>" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

# Drop cached modules to avoid namespace clashes.
for _k in [k for k in list(sys.modules) if k == "<name>" or k.startswith("<name>.")]:
    sys.modules.pop(_k, None)

from <name> import <symbols>  # noqa: E402
```

### 4.3 Coverage target

- **Minimum**: 90% for new packages.
- **Target**: 95%+.
- Sprint 2.8 (planner): 95%.
- Sprint 2.9 (communication): 79% (lower due to wire-mode paths).
- Sprint 2.10 (audit): no new code; coverage unchanged.

### 4.4 Test categories

1. **Unit tests** — test a single class/function in isolation.
2. **Integration tests** — test multiple modules together (e.g.
   `test_integration.py`).
3. **Flow tests** — test a complete message flow (request → response).
4. **Failure handling tests** — test timeouts, errors, missing agents.
5. **Concurrent tests** — test `asyncio.gather()` with multiple
   publishers/subscribers.
6. **Regression tests** — test that previously-fixed bugs don't recur.

### 4.5 Running tests

```bash
# All tests.
python -m pytest tests/ -q

# With coverage.
python -m pytest tests/communication/ --cov=communication --cov-report=term-missing

# Verbose, stop on first failure.
python -m pytest tests/planner/ -v --tb=short -x

# Specific test.
python -m pytest tests/communication/test_collaboration.py::test_delegate_with_response -v
```

---

## 5. Adding a New Agent

```python
# packages/multiagent/src/multiagent/agents/my_agent.py

from __future__ import annotations

from typing import Any

from ..base import BaseAgent
from ..enums import Permission


class MyAgent(BaseAgent):
    """Does something useful."""

    def __init__(self, name: str = "my_agent") -> None:
        super().__init__(
            name=name,
            description="My custom agent",
            permissions=frozenset({Permission.READ_FILES}),
            objectives=["my_objective"],
            tools=["file_tool"],
        )

    async def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        # Your logic here.
        return {"result": "done"}
```

Register it:

```python
# packages/multiagent/src/multiagent/registry/manifest.py

def standard_manifests() -> list[AgentManifest]:
    return [
        # ... existing manifests ...
        AgentManifest(
            id="my_agent",
            name="My Agent",
            version="1.0.0",
            description="Does something useful",
            capabilities=["my.capability"],
            priority=5,
            metadata={"cost": 0.3},
        ),
    ]
```

Test it:

```python
# tests/agents/test_my_agent.py
import pytest
from multiagent.agents.my_agent import MyAgent

@pytest.mark.asyncio
async def test_my_agent_executes():
    agent = MyAgent()
    await agent.start()
    result = await agent.execute({"action": "do_something"})
    assert result["result"] == "done"
    await agent.stop()
```

---

## 6. Adding a New Communication Pattern

1. **Add a message type** (if needed) in `communication/protocol.py`:
   ```python
   class MessageType(str, Enum):
       # ... existing types ...
       MY_NEW_TYPE = "my_new_type"
   ```

2. **Add a factory constructor** to `AgentMessage`:
   ```python
   @classmethod
   def my_new_type(
       cls, sender: str, receiver: str, *, payload=None, **kwargs
   ) -> "AgentMessage":
       return cls(
           sender_agent=sender,
           receiver_agent=receiver,
           message_type=MessageType.MY_NEW_TYPE,
           payload=dict(payload or {}),
           **kwargs,
       )
   ```

3. **Add a convenience sender** to `AgentMessageBus`:
   ```python
   async def send_my_new_type(self, sender, receiver, **kwargs):
       msg = AgentMessage.my_new_type(sender, receiver, **kwargs)
       await self.publish(msg)
       return msg
   ```

4. **Handle the new type** in `Collaboration.route_message` (if it
   affects collaboration state).

5. **Add tests** in `tests/communication/`.

---

## 7. Debugging Tips

### 7.1 Message routing issues

- Check `bus.history()` to see all messages that passed through the bus.
- Check `bus.metrics()` for `dropped_no_subscriber` count.
- Check `mgr.history()` for collaboration state transitions.
- Enable debug logging: `import logging; logging.basicConfig(level=logging.DEBUG)`.

### 7.2 Test pollution

- If a test passes in isolation but fails in the suite, check for
  `sys.path` pollution from a previous test module.
- Each test module should clean up `sys.modules` at the top (see §4.2).
- Run tests with `--forked` if pytest-asyncio event loops leak.

### 7.3 Import errors

- If you get `ModuleNotFoundError: No module named 'src.X'`, check that
  the correct package's `src/` is on `sys.path`.
- Use `python -c "import sys; print(sys.path)"` to debug.
- The `src` namespace collision (§2.1) is the usual culprit.

### 7.4 Async issues

- If `wait_for_response` times out unexpectedly, check that the
  responder is actually subscribed to the bus.
- Use `asyncio.get_event_loop().set_debug(True)` to detect slow callbacks.
- Check for `await` missing before coroutines.

---

## 8. Phase 3 Preparation

Sprint 2.10 closes Phase 2. Phase 3 will focus on:

- **Autonomous agents** — agents that can spawn sub-agents, self-heal,
  and make decisions without orchestrator intervention.
- **Advanced tools** — code execution sandbox, browser automation,
  git operations, file system watchers.
- **Complex planning** — multi-step plans with conditional branches,
  loops, and human-in-the-loop checkpoints.
- **Claude Code/Codex-style execution** — long-running tasks with
  streaming output, interruption, and rollback.

### 8.1 Architecture readiness

The Phase 2 architecture is **ready** for Phase 3:

- ✅ Multi-agent runtime is stable (789+ tests, 0 regressions).
- ✅ Communication layer supports complex collaboration patterns.
- ✅ Planner decomposes objectives into executable DAGs.
- ✅ Memory persists conversations, tasks, workflows, and messages.
- ✅ Plugin system allows dynamic agent loading.
- ✅ Semantic memory enables retrieval-augmented planning.

### 8.2 Known debt to address in Phase 3

1. **`src` namespace collision** — blocks `pip install`. High priority.
2. **Three `Task` classes** — merge into a shared base. Medium priority.
3. **Three `Scheduler` classes** — rename for clarity. Low priority.
4. **Four memory subsystems** — consolidate. Medium priority.
5. **`core.Planner` vs `planner.TaskPlanner`** — deprecate the former.
   Low priority.
6. **`agents` → `core` eager import** — define `Agent` Protocol locally.
   Low priority.
7. **50ms polling in `wait_for`** — replace with `asyncio.Event`.
   Low priority (latency optimization).

### 8.3 Recommended Phase 3 sprint sequence

1. **Sprint 3.1**: Fix the `src` namespace collision (packaging).
2. **Sprint 3.2**: Autonomous agent framework (self-spawning, self-healing).
3. **Sprint 3.3**: Advanced tools (sandbox, browser, git).
4. **Sprint 3.4**: Conditional planning (branches, loops, HITL).
5. **Sprint 3.5**: Streaming execution (Claude Code-style).
6. **Sprint 3.6**: Consolidate memory subsystems.

---

## 9. References

- [Agent Architecture Contract](agent-architecture-contract.md)
- [Module Responsibility Map](module-responsibility-map.md)
- [Sprint 2.10 Final Audit Report](sprint-2.10-final-audit-report.md)
- [CHANGELOG.md](../CHANGELOG.md)
- [Architecture diagrams](architecture.md)
- [Roadmap](roadmap.md)
