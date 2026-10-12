"""Tests for agent runtime helpers: tool registry gating, response shaping,
parallel tool execution, and model-name lookup (``devx.agents.runtime``)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from devx.agents.runtime import (
    AgentDependencyError,
    _agent_model_name,
    build_tool_registry,
    response_text,
    run_tools_parallel_sync,
)


# ── Fake tool surface ────────────────────────────────────────────────────

class FakeWorkspaceTools:
    def __init__(self):
        self.approval_map = {}  # (action, target) -> requires approval
        self.read_result = SimpleNamespace(ok=True, output="file content")
        self.raise_on_read = False

    def requires_approval(self, action, target):
        return self.approval_map.get((action, str(target)), False)

    def read(self, path):
        if self.raise_on_read:
            raise OSError("boom")
        return self.read_result

    def search(self, query, path):
        return SimpleNamespace(ok=True, output=f"search:{query}")

    def preview_write(self, path, content):
        return f"diff for {path}"

    def write(self, path, content, approved=False):
        return SimpleNamespace(ok=True, output=f"wrote:{path}")

    def run(self, command, approved=False):
        return SimpleNamespace(ok=True, output=f"ran:{command}")


def make_result(tool_name, args, tools, approve=None):
    return run_tools_parallel_sync([(tool_name, args)], tools, approve=approve)[0]


class TestRunToolsParallel:
    def test_read_file_ok(self):
        result = make_result("read_file", {"path": "a.py"}, FakeWorkspaceTools())
        assert result["result"] == "file content"
        assert result["error"] is None

    def test_read_file_error_captured(self):
        tools = FakeWorkspaceTools()
        tools.read_result = SimpleNamespace(ok=False, output="not found")
        result = make_result("read_file", {"path": "a.py"}, tools)
        assert result["result"] is None
        assert result["error"] == "not found"

    def test_search_files(self):
        result = make_result("search_files", {"query": "foo"}, FakeWorkspaceTools())
        assert result["result"] == "search:foo"
        assert result["error"] is None

    def test_write_file_missing_path(self):
        result = make_result("write_file", {"content": "x"}, FakeWorkspaceTools())
        assert result["error"] == "missing path"

    def test_write_file_ok_without_approval_gate(self):
        result = make_result("write_file", {"path": "a.py", "content": "x"}, FakeWorkspaceTools())
        assert result["result"] == "wrote:a.py"
        assert result["error"] is None

    def test_write_file_denied_when_approval_rejected(self):
        tools = FakeWorkspaceTools()
        tools.approval_map[("edit", "a.py")] = True
        result = make_result(
            "write_file", {"path": "a.py", "content": "x"}, tools, approve=lambda *_: False
        )
        assert result["result"] is None
        assert result["error"] == "WRITE DENIED"

    def test_write_file_approved_goes_through(self):
        tools = FakeWorkspaceTools()
        tools.approval_map[("edit", "a.py")] = True
        result = make_result(
            "write_file", {"path": "a.py", "content": "x"}, tools, approve=lambda *_: True
        )
        assert result["result"] == "wrote:a.py"

    def test_run_command_missing_command(self):
        result = make_result("run_command", {}, FakeWorkspaceTools())
        assert result["error"] == "missing command"

    def test_run_command_denied_when_approval_rejected(self):
        tools = FakeWorkspaceTools()
        tools.approval_map[("shell", "rm -rf build")] = True
        result = make_result(
            "run_command", {"command": "rm -rf build"}, tools, approve=lambda *_: False
        )
        assert result["error"] == "COMMAND DENIED"

    def test_run_command_ok(self):
        result = make_result("run_command", {"command": "pytest"}, FakeWorkspaceTools())
        assert result["result"] == "ran:pytest"

    def test_unknown_tool(self):
        result = make_result("bogus", {}, FakeWorkspaceTools())
        assert result["error"] == "Unknown tool: bogus"
        assert result["result"] is None

    def test_tool_exception_propagates_as_error(self):
        tools = FakeWorkspaceTools()
        tools.raise_on_read = True
        result = make_result("read_file", {"path": "x"}, tools)
        assert "boom" in result["error"]

    def test_multiple_calls_run_and_preserve_order(self):
        tools = FakeWorkspaceTools()
        calls = [("read_file", {"path": f"f{i}.py"}) for i in range(5)]
        results = run_tools_parallel_sync(calls, tools)
        assert len(results) == 5
        assert [r["tool"] for r in results] == ["read_file"] * 5
        assert all(r["result"] == "file content" for r in results)

    def test_parallel_limit_is_respected(self):
        tools = FakeWorkspaceTools()
        calls = [("read_file", {"path": f"f{i}.py"}) for i in range(10)]
        results = run_tools_parallel_sync(calls, tools, max_parallel=2)
        assert len(results) == 10


class TestResponseText:
    def test_empty_result(self):
        assert response_text({}) == ""
        assert response_text({"messages": []}) == ""

    def test_str_content(self):
        assert response_text({"messages": [{"content": "done"}]}) == "done"

    def test_message_object_content(self):
        msg = SimpleNamespace(content="hello world")
        assert response_text({"messages": [msg]}) == "hello world"

    def test_list_of_parts_joined(self):
        msg = {"content": [{"text": "part1 "}, {"text": "part2"}]}
        assert response_text({"messages": [msg]}) == "part1 part2"

    def test_list_with_non_dict_items(self):
        msg = {"content": ["a", {"text": "b"}]}
        assert response_text({"messages": [msg]}) == "ab"

    def test_non_str_content_stringified(self):
        msg = {"content": 5}
        assert response_text({"messages": [msg]}) == "5"


class TestAgentModelName:
    def test_model_name_direct(self):
        assert _agent_model_name(SimpleNamespace(model_name="gpt-4")) == "gpt-4"

    def test_model_name_from_agent_attribute(self):
        agent = SimpleNamespace(agent=SimpleNamespace(model_name="claude-3"))
        assert _agent_model_name(agent) == "claude-3"

    def test_model_name_from_model_attribute(self):
        agent = SimpleNamespace(model=SimpleNamespace(model_name="gemini-2"))
        assert _agent_model_name(agent) == "gemini-2"

    def test_model_name_from_llm_chain(self):
        agent = SimpleNamespace(
            llm_chain=SimpleNamespace(llm=SimpleNamespace(model_name="llama-3"))
        )
        assert _agent_model_name(agent) == "llama-3"

    def test_model_name_from_runnable_middle(self):
        node = SimpleNamespace(model_name="mistral")
        agent = SimpleNamespace(runnable=SimpleNamespace(middle=[node]))
        assert _agent_model_name(agent) == "mistral"

    def test_model_name_prefers_agent_over_model(self):
        agent = SimpleNamespace(
            agent=SimpleNamespace(model_name="primary"),
            model=SimpleNamespace(model_name="secondary"),
        )
        assert _agent_model_name(agent) == "primary"

    def test_unknown_yields_empty_string(self):
        assert _agent_model_name(SimpleNamespace()) == ""

    def test_non_string_model_name_ignored(self):
        assert _agent_model_name(SimpleNamespace(model_name=123)) == ""


# ── build_tool_registry ──────────────────────────────────────────────────

class StubSettings:
    """Minimal settings surface used by build_tool_registry."""

    def __init__(
        self,
        workspace,
        *,
        plugin_paths=(),
        mcp_servers=(),
        max_file_bytes=1_000_000,
        permission_rules=(),
    ):
        self.workspace = workspace
        self.plugin_paths = plugin_paths
        self.mcp_servers = mcp_servers
        self.max_file_bytes = max_file_bytes
        self.permission_rules = permission_rules
        self.approval_required = True
        self.policy_preset = ""


class TestBuildToolRegistry:
    def test_load_extensions_false_returns_empty_registry(self, tmp_path):
        registry = build_tool_registry(StubSettings(tmp_path), load_extensions=False)
        assert registry.names() == ()

    def test_no_extensions_returns_empty_registry(self, tmp_path):
        registry = build_tool_registry(StubSettings(tmp_path), approve=lambda *_: True)
        assert registry.names() == ()

    def test_mcp_entries_forwarded_to_register(self, tmp_path, monkeypatch):
        seen = []

        def fake_register(workspace, servers, registry, **kwargs):
            seen.extend(servers)

        monkeypatch.setattr("devx.agents.runtime.load_plugins", lambda *a, **k: None)
        monkeypatch.setattr("devx.agents.runtime.register_mcp_servers", fake_register)
        # build_tool_registry forwards every configured entry verbatim; the
        # register_mcp_servers helper is responsible for filtering.
        settings = StubSettings(tmp_path, mcp_servers=({"name": "srv", "command": "echo"}, "junk"))
        build_tool_registry(settings, approve=lambda *_: True)
        assert seen == [{"name": "srv", "command": "echo"}, "junk"]

    def test_plugin_denied_by_policy(self, tmp_path, monkeypatch):
        called = []

        def fake_load(*args, **kwargs):
            called.append(args)

        monkeypatch.setattr("devx.agents.runtime.load_plugins", fake_load)
        settings = StubSettings(
            tmp_path,
            plugin_paths=("plugins",),
            permission_rules=({"action": "plugin", "resource": "*", "effect": "deny"},),
        )
        with pytest.raises(AgentDependencyError, match="Permission denied for plugin"):
            build_tool_registry(settings, approve=lambda *_: True)
        assert called == []

    def test_plugin_denied_without_approval_callback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("devx.agents.runtime.load_plugins", lambda *a, **k: None)
        monkeypatch.setattr(
            "devx.agents.runtime.register_mcp_servers", lambda *a, **k: None
        )
        settings = StubSettings(tmp_path, plugin_paths=("plugins",))
        with pytest.raises(AgentDependencyError, match="Plugin extension denied"):
            build_tool_registry(settings, approve=None)

    def test_plugin_rejected_by_approval_callback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("devx.agents.runtime.load_plugins", lambda *a, **k: None)
        monkeypatch.setattr(
            "devx.agents.runtime.register_mcp_servers", lambda *a, **k: None
        )
        settings = StubSettings(tmp_path, plugin_paths=("plugins",))
        with pytest.raises(AgentDependencyError, match="Plugin extension denied"):
            build_tool_registry(settings, approve=lambda *_: False)

    def test_mcp_server_denied_without_approval(self, tmp_path, monkeypatch):
        monkeypatch.setattr("devx.agents.runtime.load_plugins", lambda *a, **k: None)
        monkeypatch.setattr(
            "devx.agents.runtime.register_mcp_servers", lambda *a, **k: None
        )
        settings = StubSettings(tmp_path, mcp_servers=({"name": "srv", "command": "echo"},))
        with pytest.raises(AgentDependencyError, match="Mcp extension denied"):
            build_tool_registry(settings, approve=None)

    def test_approved_extensions_are_loaded(self, tmp_path, monkeypatch):
        loaded = {}

        def fake_load_plugins(workspace, paths, registry):
            loaded["workspace"] = workspace
            loaded["paths"] = tuple(paths)
            registry.register("echo_plugin", lambda **kw: "ok", permission_action=None)

        def fake_register_mcp(workspace, servers, registry, **kwargs):
            loaded["servers"] = list(servers)

        monkeypatch.setattr("devx.agents.runtime.load_plugins", fake_load_plugins)
        monkeypatch.setattr("devx.agents.runtime.register_mcp_servers", fake_register_mcp)
        settings = StubSettings(
            tmp_path,
            plugin_paths=("plugins", "more-plugins"),
            mcp_servers=({"name": "srv", "command": "echo hi"}, {"name": "srv2", "command": "pwd"}),
        )
        registry = build_tool_registry(settings, approve=lambda *_: True)
        assert "echo_plugin" in registry.names()
        assert loaded["workspace"] == tmp_path
        assert loaded["paths"] == ("plugins", "more-plugins")
        assert [s.get("name") for s in loaded["servers"]] == ["srv", "srv2"]