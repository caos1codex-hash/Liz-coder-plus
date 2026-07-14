"""Unit tests for planner.task_decomposer (Sprint 2.8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLANNER_SRC = ROOT / "packages" / "planner" / "src"
sys.path.insert(0, str(PLANNER_SRC))

for _k in [k for k in list(sys.modules) if k == "planner" or k.startswith("planner.")]:
    sys.modules.pop(_k, None)

from planner.models import TaskPriority  # noqa: E402
from planner.task_decomposer import (  # noqa: E402
    DecomposerConfig,
    DecompositionTemplate,
    TaskDecomposer,
)

# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def test_default_construction():
    d = TaskDecomposer()
    # Built-in templates loaded.
    assert len(d.templates) >= 5


def test_custom_config_no_builtins():
    cfg = DecomposerConfig(use_builtin_templates=False)
    d = TaskDecomposer(config=cfg)
    assert len(d.templates) == 0


def test_register_custom_template():
    d = TaskDecomposer()
    tpl = DecompositionTemplate(
        id="custom",
        name="Custom",
        match_keywords=["custom_kw"],
        subtasks=[{"title": "Step"}],
    )
    initial = len(d.templates)
    d.register_template(tpl)
    assert len(d.templates) == initial + 1
    # Custom template is prepended.
    assert d.templates[0].id == "custom"


# ----------------------------------------------------------------------
# Software project decomposition
# ----------------------------------------------------------------------


def test_decompose_software_project():
    d = TaskDecomposer()
    tasks = d.decompose("Create an AI code editor")
    # Should produce 8 subtasks (Requirements, Architecture, Backend,
    # Frontend, AI Integration, Testing, Packaging, Documentation).
    assert len(tasks) == 8
    titles = [t.title for t in tasks]
    assert "Requirements" in titles
    assert "Architecture" in titles
    assert "Backend" in titles
    assert "Frontend" in titles
    assert "AI Integration" in titles
    assert "Testing" in titles
    assert "Packaging" in titles
    assert "Documentation" in titles


def test_decompose_software_project_order():
    d = TaskDecomposer()
    tasks = d.decompose("Build a web application")
    # Tasks should be sorted by phase.
    phases = [t.metadata.get("phase") for t in tasks]
    assert phases == sorted(phases)


def test_decompose_includes_objective_in_metadata():
    d = TaskDecomposer()
    tasks = d.decompose("Build something")
    for t in tasks:
        assert t.metadata["source_objective"] == "Build something"


def test_decompose_includes_template_id():
    d = TaskDecomposer()
    tasks = d.decompose("Create an AI code editor")
    for t in tasks:
        assert t.metadata["template_id"] == "software_project"


# ----------------------------------------------------------------------
# Other templates
# ----------------------------------------------------------------------


def test_decompose_research():
    d = TaskDecomposer()
    tasks = d.decompose("Investigate the latest ML frameworks")
    assert len(tasks) >= 3
    titles = [t.title for t in tasks]
    assert "Gather" in titles


def test_decompose_refactor():
    d = TaskDecomposer()
    tasks = d.decompose("Refactor the authentication module")
    assert len(tasks) >= 3
    titles = [t.title for t in tasks]
    assert "Read" in titles


def test_decompose_bugfix():
    d = TaskDecomposer()
    tasks = d.decompose("Fix the login crash bug")
    assert len(tasks) >= 3
    titles = [t.title for t in tasks]
    assert "Reproduce" in titles


def test_decompose_documentation():
    d = TaskDecomposer()
    tasks = d.decompose("Document the API endpoints")
    assert len(tasks) >= 2
    titles = [t.title for t in tasks]
    assert "Outline" in titles


# ----------------------------------------------------------------------
# Keyword-based decomposition (no template match)
# ----------------------------------------------------------------------


def test_decompose_keyword_fallback():
    d = TaskDecomposer()
    # An objective that doesn't match any template but has known keywords.
    # Avoid words like "create/build/implement" which trigger software_project.
    tasks = d.decompose("plan then test a thing")
    assert len(tasks) >= 2
    titles = [t.title for t in tasks]
    # Should include at least plan and test.
    assert "Plan" in titles
    assert "Test" in titles


def test_decompose_keyword_template_id():
    d = TaskDecomposer()
    # Use an objective that triggers keyword decomposition (no template match).
    tasks = d.decompose("plan then test a thing")
    for t in tasks:
        assert t.metadata["template_id"] == "keyword"


# ----------------------------------------------------------------------
# Fallback (single task)
# ----------------------------------------------------------------------


def test_decompose_fallback_single_task():
    d = TaskDecomposer()
    # An objective with no keywords and no template match.
    tasks = d.decompose("xyz random unknown gibberish")
    assert len(tasks) == 1
    assert tasks[0].metadata["template_id"] == "fallback"


def test_decompose_fallback_has_default_capability():
    d = TaskDecomposer()
    tasks = d.decompose("xyz random")
    assert tasks[0].required_capabilities == ["*"]


# ----------------------------------------------------------------------
# Context overrides
# ----------------------------------------------------------------------


def test_decompose_with_tags():
    d = TaskDecomposer()
    tasks = d.decompose("Create an app", context={"tags": ["sprint-2.8", "planner"]})
    for t in tasks:
        assert "sprint-2.8" in t.metadata["tags"]
        assert "planner" in t.metadata["tags"]


def test_decompose_with_string_tag():
    d = TaskDecomposer()
    tasks = d.decompose("Create an app", context={"tags": "single_tag"})
    for t in tasks:
        assert "single_tag" in t.metadata["tags"]


def test_decompose_force_template():
    d = TaskDecomposer()
    # Force the research template even though the objective matches
    # software_project keywords.
    tasks = d.decompose(
        "Create an app",
        context={"template": "research"},
    )
    titles = [t.title for t in tasks]
    assert "Gather" in titles
    assert "Create" not in titles  # not the software_project template


def test_decompose_force_unknown_template_falls_back():
    d = TaskDecomposer()
    tasks = d.decompose(
        "Create an app",
        context={"template": "nonexistent_template"},
    )
    # Should fall back to keyword detection or single-task fallback.
    assert len(tasks) >= 1


# ----------------------------------------------------------------------
# Empty / invalid objective
# ----------------------------------------------------------------------


def test_decompose_empty_raises():
    d = TaskDecomposer()
    with pytest.raises(ValueError):
        d.decompose("")


def test_decompose_whitespace_raises():
    d = TaskDecomposer()
    with pytest.raises(ValueError):
        d.decompose("   ")


# ----------------------------------------------------------------------
# Max subtasks cap
# ----------------------------------------------------------------------


def test_max_subtasks_cap():
    cfg = DecomposerConfig(max_subtasks=3)
    d = TaskDecomposer(config=cfg)
    tasks = d.decompose("Create an AI code editor")
    assert len(tasks) <= 3


# ----------------------------------------------------------------------
# Tasks have capabilities and priority
# ----------------------------------------------------------------------


def test_tasks_have_required_capabilities():
    d = TaskDecomposer()
    tasks = d.decompose("Create an AI code editor")
    for t in tasks:
        assert len(t.required_capabilities) > 0


def test_tasks_have_priority():
    d = TaskDecomposer()
    tasks = d.decompose("Create an AI code editor")
    for t in tasks:
        assert isinstance(t.priority, TaskPriority)


def test_tasks_have_unique_ids():
    d = TaskDecomposer()
    tasks = d.decompose("Create an AI code editor")
    ids = [t.id for t in tasks]
    assert len(ids) == len(set(ids))


# ----------------------------------------------------------------------
# Custom template
# ----------------------------------------------------------------------


def test_custom_template_used_when_keyword_matches():
    custom = DecompositionTemplate(
        id="my_tpl",
        name="My Template",
        match_keywords=["special_keyword"],
        subtasks=[
            {"title": "A", "required_capabilities": ["x"], "phase": 0},
            {"title": "B", "required_capabilities": ["y"], "phase": 1},
        ],
    )
    d = TaskDecomposer(config=DecomposerConfig(use_builtin_templates=False))
    d.register_template(custom)
    tasks = d.decompose("Do something with special_keyword please")
    assert len(tasks) == 2
    titles = [t.title for t in tasks]
    assert "A" in titles
    assert "B" in titles
    assert all(t.metadata["template_id"] == "my_tpl" for t in tasks)
