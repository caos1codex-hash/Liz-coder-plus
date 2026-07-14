"""Tests para AgentManifest (Sprint 2.3)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.registry.manifest import (  # noqa: E402
    AgentManifest,
    standard_manifests,
)


# --- Creación y validación ---


def test_manifest_creation_basic() -> None:
    m = AgentManifest(
        id="coder",
        name="Coder Agent",
        version="1.0.0",
        description="Writes code",
        capabilities=["python", "typescript", "debugging"],
        priority=10,
    )
    assert m.id == "coder"
    assert m.name == "Coder Agent"
    assert m.version == "1.0.0"
    assert m.capabilities == ["python", "typescript", "debugging"]
    assert m.priority == 10


def test_manifest_defaults() -> None:
    m = AgentManifest(id="x", name="X", version="0.1.0")
    assert m.description == ""
    assert m.capabilities == []
    assert m.priority == 0
    assert m.metadata == {}


def test_manifest_invalid_id_empty() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        AgentManifest(id="", name="X", version="1.0.0")


def test_manifest_invalid_id_uppercase() -> None:
    with pytest.raises(ValueError, match="must start with a lowercase"):
        AgentManifest(id="Coder", name="X", version="1.0.0")


def test_manifest_invalid_id_spaces() -> None:
    with pytest.raises(ValueError, match="lowercase letters, digits"):
        AgentManifest(id="code agent", name="X", version="1.0.0")


def test_manifest_invalid_id_special_chars() -> None:
    with pytest.raises(ValueError, match="lowercase letters, digits"):
        AgentManifest(id="code@agent", name="X", version="1.0.0")


def test_manifest_invalid_version_empty() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        AgentManifest(id="x", name="X", version="")


def test_manifest_invalid_version_format() -> None:
    with pytest.raises(ValueError, match="semver"):
        AgentManifest(id="x", name="X", version="1.0")  # falta patch


def test_manifest_invalid_name_empty() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        AgentManifest(id="x", name="", version="1.0.0")


def test_manifest_invalid_name_whitespace() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        AgentManifest(id="x", name="   ", version="1.0.0")


def test_manifest_duplicate_capabilities() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        AgentManifest(
            id="x", name="X", version="1.0.0",
            capabilities=["python", "python"],
        )


def test_manifest_empty_capability_string() -> None:
    with pytest.raises(ValueError, match="cannot be empty string"):
        AgentManifest(
            id="x", name="X", version="1.0.0",
            capabilities=["python", ""],
        )


def test_manifest_non_string_capability() -> None:
    with pytest.raises(TypeError, match="must be a string"):
        AgentManifest(
            id="x", name="X", version="1.0.0",
            capabilities=["python", 42],  # type: ignore[list-item]
        )


def test_manifest_negative_priority() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        AgentManifest(id="x", name="X", version="1.0.0", priority=-1)


def test_manifest_non_int_priority() -> None:
    with pytest.raises(TypeError, match="must be int"):
        AgentManifest(
            id="x", name="X", version="1.0.0", priority="high",  # type: ignore[arg-type]
        )


# --- has_capability ---


def test_has_capability_exact() -> None:
    m = AgentManifest(
        id="x", name="X", version="1.0.0",
        capabilities=["code.generate"],
    )
    assert m.has_capability("code.generate") is True
    assert m.has_capability("code.review") is False


def test_has_capability_wildcard() -> None:
    m = AgentManifest(
        id="x", name="X", version="1.0.0",
        capabilities=["code.*"],
    )
    assert m.has_capability("code.generate") is True
    assert m.has_capability("code.review") is True
    assert m.has_capability("git.commit") is False


def test_has_capability_hierarchical() -> None:
    """Un agente que provee 'code' puede manejar 'code.generate'."""
    m = AgentManifest(
        id="x", name="X", version="1.0.0",
        capabilities=["code"],
    )
    assert m.has_capability("code.generate") is True
    assert m.has_capability("code.review") is True
    assert m.has_capability("git.commit") is False


def test_has_any_capability() -> None:
    m = AgentManifest(
        id="x", name="X", version="1.0.0",
        capabilities=["python", "typescript"],
    )
    assert m.has_any_capability(["python", "rust"]) is True
    assert m.has_any_capability(["rust", "go"]) is False


def test_has_all_capabilities() -> None:
    m = AgentManifest(
        id="x", name="X", version="1.0.0",
        capabilities=["python", "typescript", "debugging"],
    )
    assert m.has_all_capabilities(["python", "typescript"]) is True
    assert m.has_all_capabilities(["python", "rust"]) is False


# --- matches (patrones) ---


def test_matches_star() -> None:
    m = AgentManifest(
        id="x", name="X", version="1.0.0",
        capabilities=["python"],
    )
    assert m.matches("*") is True


def test_matches_star_empty_caps() -> None:
    m = AgentManifest(id="x", name="X", version="1.0.0", capabilities=[])
    assert m.matches("*") is False


def test_matches_wildcard_pattern() -> None:
    m = AgentManifest(
        id="x", name="X", version="1.0.0",
        capabilities=["code.generate", "code.review", "git.commit"],
    )
    assert m.matches("code.*") is True
    assert m.matches("git.*") is True
    assert m.matches("terminal.*") is False


def test_matches_exact() -> None:
    m = AgentManifest(
        id="x", name="X", version="1.0.0",
        capabilities=["code.generate"],
    )
    assert m.matches("code.generate") is True
    assert m.matches("code.review") is False


# --- Serialización ---


def test_to_dict() -> None:
    m = AgentManifest(
        id="coder", name="Coder", version="1.0.0",
        description="writes code",
        capabilities=["python", "typescript"],
        priority=10,
        metadata={"cost": 0.5},
    )
    d = m.to_dict()
    assert d["id"] == "coder"
    assert d["name"] == "Coder"
    assert d["version"] == "1.0.0"
    assert d["capabilities"] == ["python", "typescript"]
    assert d["priority"] == 10
    assert d["metadata"] == {"cost": 0.5}


def test_from_dict_roundtrip() -> None:
    m1 = AgentManifest(
        id="coder", name="Coder", version="1.0.0",
        description="writes code",
        capabilities=["python", "typescript"],
        priority=10,
        metadata={"cost": 0.5},
    )
    d = m1.to_dict()
    m2 = AgentManifest.from_dict(d)
    assert m2.id == m1.id
    assert m2.name == m1.name
    assert m2.version == m1.version
    assert m2.capabilities == m1.capabilities
    assert m2.priority == m1.priority
    assert m2.metadata == m1.metadata


def test_to_json() -> None:
    m = AgentManifest(
        id="x", name="X", version="1.0.0",
        capabilities=["python"],
    )
    s = m.to_json()
    d = json.loads(s)
    assert d["id"] == "x"
    assert d["capabilities"] == ["python"]


# --- Inmutabilidad ---


def test_manifest_is_frozen() -> None:
    m = AgentManifest(id="x", name="X", version="1.0.0")
    with pytest.raises(Exception):
        m.id = "other"  # type: ignore[misc]
    with pytest.raises(Exception):
        m.priority = 5  # type: ignore[misc]


def test_manifest_capabilities_copied() -> None:
    """La lista de capabilities se copia defensive."""
    caps = ["python", "typescript"]
    m = AgentManifest(id="x", name="X", version="1.0.0", capabilities=caps)
    caps.append("rust")  # mutar la lista original
    assert m.capabilities == ["python", "typescript"]  # no afecta al manifest


# --- standard_manifests ---


def test_standard_manifests_count() -> None:
    manifests = standard_manifests()
    assert len(manifests) == 7


def test_standard_manifests_ids() -> None:
    manifests = standard_manifests()
    ids = {m.id for m in manifests}
    expected = {"planner", "coder", "reviewer", "research", "terminal", "git", "memory"}
    assert ids == expected


def test_standard_manifests_have_capabilities() -> None:
    for m in standard_manifests():
        assert len(m.capabilities) > 0, f"Manifest {m.id} has no capabilities"


def test_standard_manifests_priority_range() -> None:
    for m in standard_manifests():
        assert m.priority >= 0
        assert m.priority <= 100


def test_coder_manifest_has_python() -> None:
    manifests = {m.id: m for m in standard_manifests()}
    assert "python" in manifests["coder"].capabilities


def test_coder_manifest_has_code_generate() -> None:
    manifests = {m.id: m for m in standard_manifests()}
    assert manifests["coder"].has_capability("code.generate")


def test_git_manifest_has_git_commit() -> None:
    manifests = {m.id: m for m in standard_manifests()}
    assert manifests["git"].has_capability("git.commit")
