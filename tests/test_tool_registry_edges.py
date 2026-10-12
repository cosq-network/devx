"""Edge-path and validation tests for the tool registry
(``devx.harness.tool_registry``)."""

from __future__ import annotations

import sys

import pytest

from devx.harness.tool_registry import (
    ToolRegistry,
    ToolRegistryError,
    ToolSpec,
    _plugin_files,
    load_plugins,
)


@pytest.fixture
def registry():
    return ToolRegistry()


def typed_echo(value: str = "") -> str:
    return value or "pong"


class TestRegister:
    def test_register_valid_name(self, registry):
        spec = registry.register("my_tool-1", lambda **kw: "ok", description="My tool")
        assert spec.name == "my_tool-1"
        assert spec.description == "My tool"
        assert spec.permission_action == "plugin"
        assert isinstance(spec, ToolSpec)

    def test_register_description_defaults_to_name(self, registry):
        spec = registry.register("bare", lambda **kw: None)
        assert spec.description == "bare"

    def test_register_rejects_invalid_names(self, registry):
        for bad in ("", "   ", "two words", "has spaces!"):
            with pytest.raises(ToolRegistryError):
                registry.register(bad, lambda **kw: None)

    def test_register_accepts_unicode_names(self, registry):
        spec = registry.register("ünïcode", lambda **kw: None)
        assert spec.name == "ünïcode"

    def test_register_duplicate_raises(self, registry):
        registry.register("dup", lambda **kw: None)
        with pytest.raises(ToolRegistryError, match="already registered"):
            registry.register("dup", lambda **kw: None)

    def test_register_replace_allows_override(self, registry):
        first = registry.register("dup", lambda **kw: "one", replace=True)
        second = registry.register("dup", lambda **kw: "two", replace=True)
        assert first is not second
        assert registry.get("dup") == second

    def test_register_without_replace_keeps_first(self, registry):
        registry.register("dup", lambda **kw: "one")
        with pytest.raises(ToolRegistryError, match="already registered"):
            registry.register("dup", lambda **kw: "two")

    def test_register_object_duplicate(self, registry):
        registry.register_object("obj", object())
        with pytest.raises(ToolRegistryError, match="already registered"):
            registry.register_object("obj", object())


class TestAccess:
    def test_get_unknown_raises(self, registry):
        with pytest.raises(ToolRegistryError, match="Unknown tool"):
            registry.get("missing")

    def test_names_and_specs(self, registry):
        registry.register("a", lambda **kw: None)
        registry.register("b", lambda **kw: None)
        assert registry.names() == ("a", "b")
        assert len(registry.specs()) == 2
        assert all(isinstance(spec, ToolSpec) for spec in registry.specs())


class TestLangChainTools:
    def test_requires_langchain(self, registry, monkeypatch):
        monkeypatch.setitem(sys.modules, "langchain_core.tools", None)
        with pytest.raises(ToolRegistryError, match="LangChain is required"):
            registry.langchain_tools()

    def test_returns_object_tool_unmodified(self, registry):
        registry.register_object("obj", "already-a-tool", permission_action=None, description="d")
        assert registry.langchain_tools() == ["already-a-tool"]

    def test_object_tool_without_before_call_is_unmodified(self, registry):
        registry.register_object("obj", "already-a-tool", permission_action="plugin")
        assert registry.langchain_tools() == ["already-a-tool"]

    def test_handler_based_tools_become_structured_tools(self, registry):
        registry.register("echo", typed_echo, description="echo a value")
        tools = registry.langchain_tools()
        assert len(tools) == 1
        assert tools[0].name == "echo"
        assert tools[0].description == "echo a value"

    def test_before_call_can_deny_handler_invocations(self, registry):
        registry.register("ping", lambda **kw: "pong", description="ping")
        tools = registry.langchain_tools(before_call=lambda spec, args: "DENIED")
        assert tools[0].invoke({}) == "DENIED"

    def test_before_call_without_denial_routes_to_handler(self, registry):
        registry.register("ping", lambda **kw: "pong", description="ping")
        tools = registry.langchain_tools(before_call=lambda spec, args: None)
        assert tools[0].invoke({}) == "pong"

    def test_object_tools_wrapped_when_before_call_and_action(self, registry):
        class FakeLCTool:
            description = "fake tool"

            def invoke(self, payload, **kw):
                return f"invoked {payload!r}"

            def bind_tools(self, *args, **kwargs):
                return self

        registry.register_object("fake", FakeLCTool(), permission_action="http")
        tools = registry.langchain_tools(before_call=lambda spec, args: None)
        assert len(tools) == 1
        assert tools[0].name == "fake"

    def test_object_tool_denied_by_before_call(self, registry):
        class FakeLCTool:
            description = "fake tool"

            def invoke(self, payload, **kw):
                return f"invoked {payload!r}"

        registry.register_object("fake", FakeLCTool(), permission_action="http")
        tools = registry.langchain_tools(before_call=lambda spec, args: "TOOL DENIED")
        # The wrapped tool's schema (from the bound invoke signature) requires
        # ``payload``; the denial has precedence over dispatch.
        assert tools[0].invoke({"payload": "x"}) == "TOOL DENIED"

    def test_object_tool_routes_payload_when_allowed(self, registry):
        class FakeLCTool:
            description = "fake tool"

            def invoke(self, payload, **kw):
                return f"invoked {payload!r}"

        registry.register_object("fake", FakeLCTool(), permission_action="http")
        tools = registry.langchain_tools(before_call=lambda spec, args: None)
        assert tools[0].invoke({"payload": "x"}) == "invoked {'payload': 'x', 'kw': None}"


class TestResources:
    def test_close_closes_resources_in_reverse_and_clears(self):
        closed: list[str] = []

        class Resource:
            def __init__(self, name):
                self.name = name

            def close(self):
                closed.append(self.name)

        registry = ToolRegistry()
        registry.add_resource(Resource("first"))
        registry.add_resource(Resource("second"))
        registry.close()
        assert closed == ["second", "first"]
        assert registry._resources == []

    def test_close_ignores_resource_exceptions(self):
        class LoudResource:
            def close(self):
                raise RuntimeError("nope")

        registry = ToolRegistry()
        registry.add_resource(LoudResource())
        registry.close()  # must not raise

    def test_close_tolerates_missing_close(self):
        registry = ToolRegistry()
        registry.add_resource(object())
        registry.close()


class TestPluginFiles:
    def test_single_file(self, tmp_path):
        plugin = tmp_path / "plugin.py"
        plugin.write_text("def register(r): pass\n", encoding="utf-8")
        assert _plugin_files(tmp_path, ("plugin.py",)) == [plugin.resolve()]

    def test_directory_glob_skips_underscore_prefixed(self, tmp_path):
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "a.py").write_text("x", encoding="utf-8")
        (tmp_path / "tools" / "_private.py").write_text("x", encoding="utf-8")
        files = _plugin_files(tmp_path, ("tools",))
        assert files == [(tmp_path / "tools" / "a.py").resolve()]

    def test_requires_inside_workspace(self, tmp_path):
        outside = tmp_path.parent / "outside.py"
        outside.write_text("def register(r): pass\n", encoding="utf-8")
        with pytest.raises(ToolRegistryError, match="inside the workspace"):
            _plugin_files(tmp_path, (str(outside),))

    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(ToolRegistryError, match="does not exist"):
            _plugin_files(tmp_path, ("nope.py",))

    def test_deduplicates_repeated_paths(self, tmp_path):
        plugin = tmp_path / "plugin.py"
        plugin.write_text("x", encoding="utf-8")
        files = _plugin_files(tmp_path, ("plugin.py", "plugin.py"))
        assert len(files) == 1


class TestLoadPlugins:
    def test_loads_valid_plugin_with_register(self, tmp_path):
        plugin = tmp_path / "plugin.py"
        plugin.write_text(
            "def register(registry):\n"
            "    registry.register('from_plugin', lambda **kw: 'ok', permission_action=None)\n",
            encoding="utf-8",
        )
        registry = ToolRegistry()
        loaded = load_plugins(tmp_path, ("plugin.py",), registry)
        assert loaded == [str(plugin.resolve())]
        assert "from_plugin" in registry.names()

    def test_loads_plugin_with_setup_hook(self, tmp_path):
        plugin = tmp_path / "plugin.py"
        plugin.write_text(
            "def setup(registry):\n"
            "    registry.register('via_setup', lambda **kw: 'ok', permission_action=None)\n",
            encoding="utf-8",
        )
        registry = ToolRegistry()
        load_plugins(tmp_path, ("plugin.py",), registry)
        assert "via_setup" in registry.names()

    def test_missing_hook_raises(self, tmp_path):
        plugin = tmp_path / "plugin.py"
        plugin.write_text("import os\n", encoding="utf-8")
        with pytest.raises(ToolRegistryError, match="must define register"):
            load_plugins(tmp_path, ("plugin.py",), ToolRegistry())

    def test_hook_exception_is_wrapped_with_path(self, tmp_path):
        plugin = tmp_path / "plugin.py"
        plugin.write_text(
            "def register(registry):\n    raise ValueError('boom')\n", encoding="utf-8"
        )
        with pytest.raises(ToolRegistryError, match=r"Plugin failed to load .*boom"):
            load_plugins(tmp_path, ("plugin.py",), ToolRegistry())

    def test_syntax_error_is_wrapped(self, tmp_path):
        plugin = tmp_path / "plugin.py"
        plugin.write_text("def register(registry):\n   nope!!!\n", encoding="utf-8")
        with pytest.raises(ToolRegistryError, match="Plugin failed to load"):
            load_plugins(tmp_path, ("plugin.py",), ToolRegistry())

    def test_plugin_registration_error_propagates_raw(self, tmp_path):
        plugin = tmp_path / "plugin.py"
        plugin.write_text(
            "from devx.harness.tool_registry import ToolRegistryError\n"
            "def register(registry):\n"
            "    raise ToolRegistryError('already registered')\n",
            encoding="utf-8",
        )
        with pytest.raises(ToolRegistryError, match="already registered"):
            load_plugins(tmp_path, ("plugin.py",), ToolRegistry())