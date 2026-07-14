"""Tests for PluginManifest (Sprint 2.5)."""

import json
import pytest
import sys
import os

# Agregar src al path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages", "multiagent", "src"))

from multiagent.plugins.manifest import PluginManifest


class TestPluginManifestCreation:
    """Tests de creacion basica de PluginManifest."""

    def test_minimal_manifest(self):
        """Crear un manifest con solo los campos obligatorios."""
        m = PluginManifest(id="test", name="Test", version="1.0.0")
        assert m.id == "test"
        assert m.name == "Test"
        assert m.version == "1.0.0"
        assert m.author == ""
        assert m.description == ""
        assert m.api_version == "1.0.0"
        assert m.license == "MIT"
        assert m.capabilities == []
        assert m.dependencies == []
        assert m.entry_point == ""

    def test_full_manifest(self):
        """Crear un manifest con todos los campos."""
        m = PluginManifest(
            id="my-plugin",
            name="My Plugin",
            version="2.1.0",
            author="Test Author",
            description="A test plugin",
            api_version="1.0.0",
            license="Apache-2.0",
            capabilities=["code.generate", "git.commit"],
            dependencies=[{"id": "core", "minimum_version": "1.0.0"}],
            entry_point="plugins.my_plugin:MyPlugin",
            metadata={"category": "testing"},
        )
        assert m.id == "my-plugin"
        assert m.name == "My Plugin"
        assert m.version == "2.1.0"
        assert m.author == "Test Author"
        assert m.description == "A test plugin"
        assert m.api_version == "1.0.0"
        assert m.license == "Apache-2.0"
        assert m.capabilities == ["code.generate", "git.commit"]
        assert len(m.dependencies) == 1
        assert m.entry_point == "plugins.my_plugin:MyPlugin"
        assert m.metadata == {"category": "testing"}

    def test_frozen_immutable(self):
        """El manifest es frozen (inmutable)."""
        m = PluginManifest(id="test", name="Test", version="1.0.0")
        with pytest.raises(AttributeError):
            m.id = "other"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            m.name = "Other"  # type: ignore[misc]


class TestPluginManifestValidation:
    """Tests de validacion del PluginManifest."""

    def test_empty_id_raises(self):
        with pytest.raises(ValueError, match="id cannot be empty"):
            PluginManifest(id="", name="T", version="1.0.0")

    def test_invalid_id_uppercase_raises(self):
        with pytest.raises(ValueError, match="invalid"):
            PluginManifest(id="MyPlugin", name="T", version="1.0.0")

    def test_invalid_id_starts_with_number_raises(self):
        with pytest.raises(ValueError, match="invalid"):
            PluginManifest(id="1plugin", name="T", version="1.0.0")

    def test_valid_id_with_hyphens(self):
        m = PluginManifest(id="my-plugin-v2", name="T", version="1.0.0")
        assert m.id == "my-plugin-v2"

    def test_valid_id_with_underscores(self):
        m = PluginManifest(id="my_plugin_v2", name="T", version="1.0.0")
        assert m.id == "my_plugin_v2"

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="name cannot be empty"):
            PluginManifest(id="test", name="", version="1.0.0")

    def test_whitespace_name_raises(self):
        with pytest.raises(ValueError, match="name cannot be empty"):
            PluginManifest(id="test", name="   ", version="1.0.0")

    def test_empty_version_raises(self):
        with pytest.raises(ValueError, match="version cannot be empty"):
            PluginManifest(id="test", name="T", version="")

    def test_invalid_version_raises(self):
        with pytest.raises(ValueError, match="version.*invalid"):
            PluginManifest(id="test", name="T", version="abc")

    def test_valid_semver_version(self):
        m = PluginManifest(id="test", name="T", version="1.2.3")
        assert m.version == "1.2.3"

    def test_valid_semver_with_prerelease(self):
        m = PluginManifest(id="test", name="T", version="1.0.0-alpha.1")
        assert m.version == "1.0.0-alpha.1"

    def test_valid_semver_with_build(self):
        m = PluginManifest(id="test", name="T", version="1.0.0+build.123")
        assert m.version == "1.0.0+build.123"

    def test_empty_api_version_raises(self):
        with pytest.raises(ValueError, match="api_version cannot be empty"):
            PluginManifest(
                id="test", name="T", version="1.0.0", api_version="",
            )

    def test_invalid_api_version_raises(self):
        with pytest.raises(ValueError, match="api_version.*invalid"):
            PluginManifest(
                id="test", name="T", version="1.0.0", api_version="abc",
            )

    def test_invalid_entry_point_no_colon_accepted(self):
        # "just_a_module" is valid per regex (colon part is optional).
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            entry_point="just_a_module",
        )
        assert m.entry_point == "just_a_module"

    def test_invalid_entry_point_special_chars_raises(self):
        with pytest.raises(ValueError, match="entry_point.*invalid"):
            PluginManifest(
                id="test", name="T", version="1.0.0",
                entry_point="123invalid:Class",
            )

    def test_valid_entry_point(self):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            entry_point="plugins.my_plugin:MyPlugin",
        )
        assert m.entry_point == "plugins.my_plugin:MyPlugin"

    def test_empty_entry_point_is_valid(self):
        m = PluginManifest(id="test", name="T", version="1.0.0")
        assert m.entry_point == ""

    def test_duplicate_capabilities_raises(self):
        with pytest.raises(ValueError, match="Duplicate capability"):
            PluginManifest(
                id="test", name="T", version="1.0.0",
                capabilities=["code.generate", "code.generate"],
            )

    def test_empty_capability_raises(self):
        with pytest.raises(ValueError, match="Capability cannot be empty"):
            PluginManifest(
                id="test", name="T", version="1.0.0",
                capabilities=["code.generate", ""],
            )

    def test_non_string_capability_raises(self):
        with pytest.raises(TypeError, match="Capability must be a string"):
            PluginManifest(
                id="test", name="T", version="1.0.0",
                capabilities=[123],  # type: ignore[list-item]
            )

    def test_capabilities_string_converted_to_list(self):
        # A string gets list()'d into chars — not ideal but passes validation
        # since list("ab") = ["a", "b"] which are valid single-char caps.
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            capabilities="abc",  # type: ignore[arg-type]
        )
        assert m.capabilities == ["a", "b", "c"]

    def test_duplicate_dependency_raises(self):
        with pytest.raises(ValueError, match="Duplicate dependency"):
            PluginManifest(
                id="test", name="T", version="1.0.0",
                dependencies=[
                    {"id": "core", "minimum_version": "1.0.0"},
                    {"id": "core", "minimum_version": "1.0.0"},
                ],
            )

    def test_dependency_empty_id_raises(self):
        with pytest.raises(ValueError, match="non-empty 'id'"):
            PluginManifest(
                id="test", name="T", version="1.0.0",
                dependencies=[{"id": ""}],
            )


class TestPluginManifestQueries:
    """Tests de consultas del manifest."""

    def test_has_capability_exact(self):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            capabilities=["code.generate", "git.commit"],
        )
        assert m.has_capability("code.generate") is True
        assert m.has_capability("git.commit") is True
        assert m.has_capability("code.review") is False

    def test_has_capability_wildcard(self):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            capabilities=["code.*"],
        )
        assert m.has_capability("code.generate") is True
        assert m.has_capability("code.review") is True
        assert m.has_capability("git.commit") is False

    def test_has_capability_hierarchical(self):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            capabilities=["code"],
        )
        assert m.has_capability("code.generate") is True
        assert m.has_capability("code.review") is True
        assert m.has_capability("git.commit") is False

    def test_depends_on(self):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            dependencies=[
                {"id": "core", "minimum_version": "1.0.0"},
                {"id": "utils"},
            ],
        )
        assert m.depends_on("core") is True
        assert m.depends_on("utils") is True
        assert m.depends_on("nonexistent") is False

    def test_get_dependency_version_constraint(self):
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            dependencies=[
                {"id": "core", "minimum_version": "1.0.0", "maximum_version": "2.0.0"},
            ],
        )
        constraint = m.get_dependency_version_constraint("core")
        assert constraint is not None
        assert constraint["minimum_version"] == "1.0.0"
        assert constraint["maximum_version"] == "2.0.0"

    def test_get_dependency_version_constraint_not_found(self):
        m = PluginManifest(id="test", name="T", version="1.0.0")
        assert m.get_dependency_version_constraint("core") is None


class TestPluginManifestSerialization:
    """Tests de serializacion del manifest."""

    def test_to_dict(self):
        m = PluginManifest(
            id="test", name="Test", version="1.0.0",
            capabilities=["code.generate"],
        )
        d = m.to_dict()
        assert d["id"] == "test"
        assert d["name"] == "Test"
        assert d["version"] == "1.0.0"
        assert d["capabilities"] == ["code.generate"]
        assert isinstance(d["metadata"], dict)

    def test_from_dict(self):
        data = {
            "id": "test",
            "name": "Test",
            "version": "1.0.0",
            "capabilities": ["code.generate"],
        }
        m = PluginManifest.from_dict(data)
        assert m.id == "test"
        assert m.capabilities == ["code.generate"]

    def test_to_json(self):
        m = PluginManifest(id="test", name="T", version="1.0.0")
        j = m.to_json()
        parsed = json.loads(j)
        assert parsed["id"] == "test"
        assert parsed["version"] == "1.0.0"

    def test_from_json(self):
        m = PluginManifest(id="test", name="T", version="1.0.0")
        j = m.to_json()
        m2 = PluginManifest.from_json(j)
        assert m2.id == m.id
        assert m2.version == m.version

    def test_round_trip_dict(self):
        m = PluginManifest(
            id="test", name="Test Plugin", version="2.1.0",
            author="Author",
            capabilities=["a", "b"],
            dependencies=[{"id": "x"}],
            metadata={"k": "v"},
        )
        m2 = PluginManifest.from_dict(m.to_dict())
        assert m2.id == m.id
        assert m2.name == m.name
        assert m2.version == m.version
        assert m2.capabilities == m.capabilities
        assert m2.dependencies == m.dependencies
        assert m2.metadata == m.metadata

    def test_defensive_copy_capabilities(self):
        """Capabilities no se comparten entre instancias."""
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            capabilities=["a"],
        )
        # Intentar mutar la lista original no afecta al manifest.
        original_caps = ["a"]
        m2 = PluginManifest(
            id="test2", name="T2", version="1.0.0",
            capabilities=original_caps,
        )
        original_caps.append("b")
        assert "b" not in m2.capabilities

    def test_defensive_copy_metadata(self):
        """Metadata no se comparte entre instancias."""
        meta = {"k": "v"}
        m = PluginManifest(
            id="test", name="T", version="1.0.0",
            metadata=meta,
        )
        meta["k2"] = "v2"
        assert "k2" not in m.metadata