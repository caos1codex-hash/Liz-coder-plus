"""Tests for Tool Plugins (Sprint 2.5)."""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "packages", "multiagent", "src"))

from multiagent.plugins.base import Plugin
from multiagent.plugins.manifest import PluginManifest
from multiagent.plugins.tools import (
    ToolDefinition,
    ToolPlugin,
    ToolPluginMixin,
)


# ----------------------------------------------------------------------
# Tests de ToolDefinition
# ----------------------------------------------------------------------


class TestToolDefinition:
    """Tests de la definicion de herramientas."""

    def test_create_minimal(self):
        td = ToolDefinition(name="test_tool")
        assert td.name == "test_tool"
        assert td.description == ""
        assert td.category == "general"
        assert td.parameters == {}
        assert td.metadata == {}

    def test_create_full(self):
        td = ToolDefinition(
            name="git_commit",
            description="Creates a git commit",
            category="git",
            parameters={"message": {"type": "string"}},
            metadata={"async": True},
        )
        assert td.name == "git_commit"
        assert td.description == "Creates a git commit"
        assert td.category == "git"
        assert td.parameters == {"message": {"type": "string"}}
        assert td.metadata == {"async": True}

    def test_to_dict(self):
        td = ToolDefinition(
            name="test", description="desc", category="cat",
        )
        d = td.to_dict()
        assert d["name"] == "test"
        assert d["description"] == "desc"
        assert d["category"] == "cat"
        assert isinstance(d["parameters"], dict)


# ----------------------------------------------------------------------
# Tests de ToolPluginMixin
# ----------------------------------------------------------------------


class TestToolPluginMixin:
    """Tests del mixin de herramientas."""

    def test_register_tool(self):
        class MyMixin(ToolPluginMixin):
            def __init__(self):
                super().__init__()
                self._register_tool(ToolDefinition(
                    name="tool1", description="Tool 1",
                ))

        mixin = MyMixin()
        tools = mixin.tool_definitions()
        assert len(tools) == 1
        assert tools[0].name == "tool1"

    def test_register_multiple_tools(self):
        class MyMixin(ToolPluginMixin):
            def __init__(self):
                super().__init__()
                self._register_tool(ToolDefinition(name="t1"))
                self._register_tool(ToolDefinition(name="t2"))
                self._register_tool(ToolDefinition(name="t3"))

        mixin = MyMixin()
        assert len(mixin.tool_definitions()) == 3

    def test_register_duplicate_tool_raises(self):
        class MyMixin(ToolPluginMixin):
            def __init__(self):
                super().__init__()
                self._register_tool(ToolDefinition(name="dup"))

        mixin = MyMixin()
        with pytest.raises(ValueError, match="already registered"):
            mixin._register_tool(ToolDefinition(name="dup"))

    def test_unregister_tool(self):
        class MyMixin(ToolPluginMixin):
            def __init__(self):
                super().__init__()
                self._register_tool(ToolDefinition(name="removable"))

        mixin = MyMixin()
        assert mixin.has_tool("removable")
        result = mixin._unregister_tool("removable")
        assert result is True
        assert not mixin.has_tool("removable")

    def test_unregister_nonexistent_tool(self):
        class MyMixin(ToolPluginMixin):
            pass

        mixin = MyMixin()
        result = mixin._unregister_tool("nonexistent")
        assert result is False

    def test_get_tool_definition(self):
        class MyMixin(ToolPluginMixin):
            def __init__(self):
                super().__init__()
                self._register_tool(ToolDefinition(
                    name="specific", description="A specific tool",
                ))

        mixin = MyMixin()
        td = mixin.get_tool_definition("specific")
        assert td is not None
        assert td.description == "A specific tool"

    def test_get_nonexistent_tool_definition(self):
        class MyMixin(ToolPluginMixin):
            pass

        mixin = MyMixin()
        assert mixin.get_tool_definition("nonexistent") is None

    def test_has_tool(self):
        class MyMixin(ToolPluginMixin):
            def __init__(self):
                super().__init__()
                self._register_tool(ToolDefinition(name="exists"))

        mixin = MyMixin()
        assert mixin.has_tool("exists") is True
        assert mixin.has_tool("nope") is False

    def test_empty_tool_definitions(self):
        class MyMixin(ToolPluginMixin):
            pass

        mixin = MyMixin()
        assert mixin.tool_definitions() == []


# ----------------------------------------------------------------------
# Tests de ToolPlugin con Plugin
# ----------------------------------------------------------------------


class TestToolPluginIntegration:
    """Tests de integracion de ToolPlugin con Plugin."""

    def test_plugin_with_mixin(self):
        """Un plugin puede usar ToolPluginMixin."""

        class MyPlugin(Plugin, ToolPluginMixin):
            def __init__(self):
                Plugin.__init__(self)
                ToolPluginMixin.__init__(self)
                self._register_tool(ToolDefinition(
                    name="plugin_tool", category="custom",
                ))

            def manifest(self) -> PluginManifest:
                return PluginManifest(
                    id="tool-mixin-plugin",
                    name="Tool Mixin Plugin",
                    version="1.0.0",
                    capabilities=["tool.custom"],
                )

        plugin = MyPlugin()
        tools = plugin.tool_definitions()
        assert len(tools) == 1
        assert tools[0].name == "plugin_tool"
        assert tools[0].category == "custom"

    def test_plugin_get_tools_from_mixin(self):
        """get_tools() puede delegar a tool_definitions()."""

        class MyPlugin(Plugin, ToolPluginMixin):
            def __init__(self):
                Plugin.__init__(self)
                ToolPluginMixin.__init__(self)
                self._register_tool(ToolDefinition(name="t1"))

            def manifest(self) -> PluginManifest:
                return PluginManifest(
                    id="t", name="T", version="1.0.0",
                )

            def get_tools(self) -> list:
                return self.tool_definitions()

        plugin = MyPlugin()
        tools = plugin.get_tools()
        assert len(tools) == 1