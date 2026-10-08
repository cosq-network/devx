import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from devx.agents.runtime import AgentDependencyError, build_tool_registry
from devx.config import Settings
from devx.harness.mcp import MCPClient, MCPError, register_mcp_servers
from devx.harness.permissions import PermissionError, PermissionPolicy
from devx.harness.tool_registry import ToolRegistry, load_plugins
from devx.harness.tools import WorkspaceTools
from devx.token_optim.compaction import compact_messages


def test_permission_rules_are_ordered_and_preserve_hard_denials(tmp_path: Path):
    policy = PermissionPolicy(
        tmp_path,
        rules=[
            {"action": "edit", "resource": "**", "effect": "ask"},
            {"action": "edit", "resource": "tests/**", "effect": "allow"},
            {"action": "shell", "resource": "git diff *", "effect": "allow"},
        ],
    )
    tools = WorkspaceTools(policy, timeout=2)
    assert tools.write("tests/output.txt", "ok").ok
    assert not tools.run(["python", "-c", "print(1)"]).ok
    assert policy.requires_approval("plugin", "greet")
    assert policy.requires_approval("mcp", "local:echo")
    try:
        policy.command(["rm", "-rf", "."], approved=True)
    except PermissionError:
        pass
    else:
        raise AssertionError("destructive commands must remain blocked")


def test_project_config_loads_permissions_plugins_and_compaction(tmp_path: Path):
    config_dir = tmp_path / ".devx"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({
        "permissions": [{"action": "shell", "resource": "git *", "effect": "allow"}],
        "plugins": ["tools.py"],
        "mcp_servers": [],
        "context_compaction": {"max_messages": 6, "max_chars": 5000},
    }), encoding="utf-8")

    settings = Settings.load(workspace=tmp_path)

    assert settings.permission_rules[0]["action"] == "shell"
    assert settings.plugin_paths == ("tools.py",)
    assert settings.max_context_messages == 6
    assert settings.max_context_chars == 5000


def test_context_compaction_keeps_recent_messages_and_bounds_history():
    messages: list[dict[str, str]] = []
    for index in range(12):
        messages.append({"role": "assistant", "content": f"message-{index}-" + ("x" * 200)})

    result = compact_messages(messages, max_messages=5, max_chars=900, summary_chars=240)
    assert result.compacted
    assert result.messages[0]["role"] == "summary"
    assert "message-11" in result.messages[-1]["content"]
    assert len(result.messages) <= 5
    assert sum(len(item["content"]) for item in result.messages) <= 900


def test_workspace_plugin_registers_a_tool(tmp_path: Path):
    plugin = tmp_path / "tools.py"
    plugin.write_text(
        "def register(registry):\n"
        "    def greet(name: str) -> str:\n"
        "        return 'hello ' + name\n"
        "    registry.register('greet', greet, description='Greet a person')\n",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    load_plugins(tmp_path, ("tools.py",), registry)

    assert registry.get("greet").handler(name="Ada") == "hello Ada"
    registry.close()


def test_stdio_mcp_server_discovers_and_calls_tools(tmp_path: Path):
    server = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " m=json.loads(line)\n"
        " if m.get('method') == 'notifications/initialized': continue\n"
        " result={}\n"
        " if m.get('method') == 'initialize': result={'protocolVersion':'2024-11-05'}\n"
        " elif m.get('method') == 'tools/list': result={'tools':[{'name':'echo','description':'Echo','inputSchema':{'type':'object','properties':{'value':{'type':'string'}},'required':['value']}}]}\n"
        " elif m.get('method') == 'tools/call': result={'content':[{'type':'text','text':m['params']['arguments']['value']}]};\n"
        " print(json.dumps({'jsonrpc':'2.0','id':m.get('id'),'result':result}),flush=True)\n"
    )
    registry = ToolRegistry()
    register_mcp_servers(tmp_path, [{"name": "local", "command": [sys.executable, "-u", "-c", server]}], registry)

    spec = registry.get("mcp_local_echo")
    assert spec.handler is not None
    assert spec.handler(value="works") == "works"
    registry.close()


def test_project_plugins_require_approval_before_import(tmp_path: Path):
    marker = tmp_path / "imported.txt"
    plugin = tmp_path / "tools.py"
    plugin.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
        "def register(registry):\n"
        "    registry.register('safe_tool', lambda: 'ok')\n",
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        workspace=tmp_path,
        approval_required=True,
        permission_rules=(),
        plugin_paths=("tools.py",),
        mcp_servers=(),
        max_file_bytes=1000,
    )

    try:
        build_tool_registry(settings)
    except AgentDependencyError:
        pass
    else:
        raise AssertionError("project plugins must require approval before import")
    assert not marker.exists()

    registry = build_tool_registry(settings, lambda *_args: True)
    assert marker.read_text(encoding="utf-8") == "imported"
    assert "safe_tool" in registry.names()
    registry.close()


def test_mcp_timeout_is_bounded(tmp_path: Path):
    client = MCPClient(
        "slow",
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=tmp_path,
        timeout=0.1,
    )
    started = time.monotonic()
    try:
        client.list_tools()
    except MCPError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("unresponsive MCP server must time out")
    assert time.monotonic() - started < 2
    client.close()
