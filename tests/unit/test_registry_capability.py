"""Tests para Capability y CapabilityRegistry (Sprint 2.2/2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.registry.capability import (  # noqa: E402
    Capability,
    CapabilityRegistry,
    register_standard_capabilities,
)


# --- Capability ---


def test_capability_creation() -> None:
    cap = Capability(name="code.generate", description="Generate code")
    assert cap.name == "code.generate"
    assert cap.description == "Generate code"
    assert cap.category == "code"  # auto-derivada
    assert cap.version == "1.0.0"


def test_capability_parent() -> None:
    cap = Capability(name="code.generate")
    assert cap.parent == "code"
    assert cap.is_root is False

    root = Capability(name="code")
    assert root.parent is None
    assert root.is_root is True


def test_capability_is_subcapability_of() -> None:
    cap = Capability(name="code.generate")
    assert cap.is_subcapability_of("code") is True
    assert cap.is_subcapability_of("code.generate") is False
    assert cap.is_subcapability_of("git") is False


def test_capability_matches_exact() -> None:
    cap = Capability(name="code.generate")
    assert cap.matches("code.generate") is True
    assert cap.matches("code.review") is False


def test_capability_matches_wildcard() -> None:
    cap = Capability(name="code.generate")
    assert cap.matches("code.*") is True
    assert cap.matches("*") is True
    assert cap.matches("git.*") is False


def test_capability_invalid_name_raises() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        Capability(name="")
    with pytest.raises(ValueError, match="must be alphanumeric"):
        Capability(name="code.gen-erate!")  # símbolos no permitidos


def test_capability_frozen() -> None:
    cap = Capability(name="code.generate")
    with pytest.raises(Exception):
        cap.name = "other"  # type: ignore[misc]


def test_capability_serialization() -> None:
    cap = Capability(
        name="code.generate",
        description="Generate code",
        category="code",
        version="2.0.0",
        metadata={"cost": 0.5},
    )
    d = cap.to_dict()
    assert d["name"] == "code.generate"
    assert d["description"] == "Generate code"
    assert d["category"] == "code"
    assert d["version"] == "2.0.0"
    assert d["metadata"] == {"cost": 0.5}


# --- CapabilityRegistry ---


def test_registry_register_and_get() -> None:
    reg = CapabilityRegistry()
    reg.register(Capability(name="code.generate", description="gen"))
    assert reg.exists("code.generate")
    fetched = reg.get("code.generate")
    assert fetched is not None
    assert fetched.description == "gen"


def test_registry_register_by_name() -> None:
    reg = CapabilityRegistry()
    cap = reg.register_by_name("code.review", description="review code")
    assert cap.name == "code.review"
    assert cap.description == "review code"
    assert reg.exists("code.review")


def test_registry_unregister() -> None:
    reg = CapabilityRegistry()
    reg.register_by_name("code.generate")
    assert reg.unregister("code.generate") is True
    assert reg.exists("code.generate") is False
    assert reg.unregister("nonexistent") is False


def test_registry_all() -> None:
    reg = CapabilityRegistry()
    reg.register_by_name("code.generate")
    reg.register_by_name("code.review")
    reg.register_by_name("git.commit")
    all_caps = reg.all()
    assert len(all_caps) == 3


def test_registry_by_category() -> None:
    reg = CapabilityRegistry()
    reg.register_by_name("code.generate")
    reg.register_by_name("code.review")
    reg.register_by_name("git.commit")
    code_caps = reg.by_category("code")
    assert len(code_caps) == 2
    git_caps = reg.by_category("git")
    assert len(git_caps) == 1


def test_registry_search_wildcard() -> None:
    reg = CapabilityRegistry()
    reg.register_by_name("code.generate")
    reg.register_by_name("code.review")
    reg.register_by_name("git.commit")
    results = reg.search("code.*")
    assert len(results) == 2
    results = reg.search("*")
    assert len(results) == 3


def test_registry_categories() -> None:
    reg = CapabilityRegistry()
    reg.register_by_name("code.generate")
    reg.register_by_name("git.commit")
    cats = reg.categories()
    assert "code" in cats
    assert "git" in cats


def test_registry_metrics() -> None:
    reg = CapabilityRegistry()
    reg.register_by_name("code.generate")
    reg.get("code.generate")  # hit
    reg.get("nonexistent")    # miss
    m = reg.metrics()
    assert m["registered"] == 1
    assert m["lookups"] == 2
    assert m["hits"] == 1
    assert m["misses"] == 1
    assert m["hit_rate"] == 0.5


def test_registry_clear() -> None:
    reg = CapabilityRegistry()
    reg.register_by_name("code.generate")
    reg.clear()
    assert reg.all() == []


# --- register_standard_capabilities ---


def test_register_standard_capabilities() -> None:
    reg = CapabilityRegistry()
    register_standard_capabilities(reg)
    all_caps = reg.all()
    assert len(all_caps) >= 30
    # Verificar algunas clave.
    assert reg.exists("planning")
    assert reg.exists("code.generate")
    assert reg.exists("code.review")
    assert reg.exists("git.commit")
    assert reg.exists("terminal.execute")
    assert reg.exists("filesystem.read")
    assert reg.exists("memory.retrieve")
    assert reg.exists("web.search")
    assert reg.exists("documentation.lookup")


def test_standard_capabilities_have_categories() -> None:
    reg = CapabilityRegistry()
    register_standard_capabilities(reg)
    cats = reg.categories()
    expected = {"planning", "workflow", "code", "git", "terminal",
                "filesystem", "memory", "web", "documentation", "research"}
    assert expected.issubset(set(cats))


def test_register_standard_capabilities_idempotent() -> None:
    reg = CapabilityRegistry()
    register_standard_capabilities(reg)
    count1 = len(reg.all())
    register_standard_capabilities(reg)  # sobreescribe, no duplica
    count2 = len(reg.all())
    assert count1 == count2
