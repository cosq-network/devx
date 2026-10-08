from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from devx.harness.mcp import register_mcp_servers
from devx.harness.permissions import PermissionPolicy
from devx.harness.runtime import RunCancelled
from devx.harness.tool_registry import ToolRegistry, ToolSpec, load_plugins
from devx.harness.tools import WorkspaceTools
from devx.llm import create_llm

if TYPE_CHECKING:
    from devx.config import Settings


class AgentDependencyError(RuntimeError):
    pass


class AgentToolLimitExceeded(RuntimeError):
    """Raised when a model attempts more tool calls than the configured limit."""


def build_tool_registry(
    settings: Settings,
    approve: Callable[[str, str, str], bool] | None = None,
    *,
    load_extensions: bool = True,
) -> ToolRegistry:
    """Load explicitly configured plugins and MCP tools for one agent run.

    Project extensions are executable code, so approval happens before any
    plugin is imported or MCP process is spawned. ``load_extensions=False``
    is used by plan-only runs, which do not need external tools.
    """
    registry = ToolRegistry()
    if not load_extensions:
        return registry

    policy = PermissionPolicy(
        settings.workspace,
        getattr(settings, "approval_required", True),
        getattr(settings, "permission_rules", ()),
    )

    def authorize(action: str, target: str, reason: str) -> None:
        effect = policy.effect(action, target)
        if effect == "deny":
            raise AgentDependencyError(f"Permission denied for {action}: {target}")
        if policy.requires_approval(action, target) and (approve is None or not approve(action, target, reason)):
            raise AgentDependencyError(f"{action.capitalize()} extension denied: {target}")

    plugin_paths = tuple(getattr(settings, "plugin_paths", ()))
    for raw_path in plugin_paths:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = settings.workspace / candidate
        authorize("plugin", str(candidate.resolve(strict=False)),
                  "The project requested loading executable Python plugin code.")

    raw_servers = getattr(settings, "mcp_servers", ())
    if isinstance(raw_servers, dict):
        server_configs = tuple(
            dict(value, name=name) if isinstance(value, dict) else value
            for name, value in raw_servers.items()
        )
    else:
        server_configs = tuple(raw_servers)
    for index, raw_server in enumerate(server_configs):
        if not isinstance(raw_server, dict):
            continue
        name = str(raw_server.get("name") or f"server-{index + 1}").strip()
        authorize("mcp", name, "The project requested starting an MCP server process.")

    # Do not execute any approved extension until every configured extension
    # has passed the approval boundary.
    load_plugins(settings.workspace, plugin_paths, registry)
    register_mcp_servers(
        settings.workspace,
        server_configs,
        registry,
        max_output_bytes=max(1, int(getattr(settings, "max_file_bytes", 1_000_000))),
    )
    return registry


def build_coding_agent(
    settings: Settings,
    tools: WorkspaceTools,
    approve: Callable[[str, str, str], bool] | None = None,
    tool_call_budget: Callable[[], None] | None = None,
    registry: ToolRegistry | None = None,
):
    """Build a LangChain tool-calling agent with safe mutation gates."""
    try:
        from langchain.agents import create_agent
        from langchain.tools import tool
    except ImportError as exc:
        raise AgentDependencyError("Install the agent extra: pip install 'devx-coding-agent[agent]'") from exc

    max_tool_calls = max(1, int(getattr(settings, "max_tool_calls", 40)))
    call_count = 0

    def consume_local_tool_call() -> None:
        tools.cancellation.raise_if_cancelled()
        nonlocal call_count
        call_count += 1
        if call_count > max_tool_calls:
            raise AgentToolLimitExceeded(f"Tool-call limit exceeded ({max_tool_calls})")

    consume_tool_call = tool_call_budget or consume_local_tool_call

    def allowed(action: str, target: str, reason: str = "") -> bool:
        return bool(approve and approve(action, target, reason))

    @tool
    def read_file(path: str) -> str:
        """Read a UTF-8 text file inside the workspace."""
        consume_tool_call()
        return tools.read(path).output

    @tool
    def search_files(query: str, path: str = ".") -> str:
        """Search text files inside the workspace."""
        consume_tool_call()
        return tools.search(query, path).output

    @tool
    def write_file(path: str, content: str) -> str:
        """Write a file after the user approves the exact path."""
        consume_tool_call()
        preview = tools.preview_write(path, content)
        if tools.requires_approval("edit", path) and not allowed("write", path, "Proposed diff:\n" + preview):
            tools.cancellation.raise_if_cancelled()
            return "WRITE DENIED: request user approval before changing this file."
        return tools.write(path, content, approved=True).output

    @tool
    def replace_in_file(path: str, old_text: str, new_text: str, expected_hash: str = "") -> str:
        """Apply one exact replacement, refusing ambiguous or concurrently changed files."""
        consume_tool_call()
        preview = tools.preview_replace(path, old_text, new_text)
        if tools.requires_approval("edit", path) and not allowed("write", path, "Proposed diff:\n" + preview):
            tools.cancellation.raise_if_cancelled()
            return "PATCH DENIED: request user approval before changing this file."
        return tools.replace(path, old_text, new_text, expected_hash=expected_hash or None, approved=True).output

    @tool
    def run_command(command: str) -> str:
        """Run a project command after explicit user approval."""
        consume_tool_call()
        if tools.requires_approval("shell", command) and not allowed("command", command, "The agent requested this project command."):
            tools.cancellation.raise_if_cancelled()
            return "COMMAND DENIED: request user approval before executing this command."
        return tools.run(command, approved=True).output

    @tool
    def write_files(operations: list[dict]) -> str:
        """Write multiple files in a batch. Each operation: {'path': str, 'content': str}."""
        consume_tool_call()
        results = []
        for op in operations:
            path = op.get("path")
            content = op.get("content", "")
            if not path:
                results.append("ERROR: missing path in operation")
                continue
            preview = tools.preview_write(path, content)
            if tools.requires_approval("edit", path) and not allowed("write", path, "Proposed diff:\n" + preview):
                tools.cancellation.raise_if_cancelled()
                results.append(f"WRITE DENIED: {path}")
                continue
            result = tools.write(path, content, approved=True)
            results.append(result.output if result.ok else f"ERROR: {result.output}")
        return "\n".join(results)

    @tool
    def git_branch(name: str) -> str:
        """Create and switch to a new git branch."""
        consume_tool_call()
        if tools.requires_approval("shell", f"git checkout -b {name}") and not allowed("command", f"git checkout -b {name}", "Create new git branch"):
            tools.cancellation.raise_if_cancelled()
            return "GIT BRANCH DENIED: request user approval before creating branch."
        return tools.run(["git", "checkout", "-b", name], approved=True).output

    @tool
    def git_commit(message: str, add_all: bool = True) -> str:
        """Stage changes and create a git commit."""
        consume_tool_call()
        cmds = []
        if add_all:
            cmds.append(["git", "add", "-A"])
        cmds.append(["git", "commit", "-m", message])
        results = []
        for cmd in cmds:
            cmd_str = " ".join(cmd)
            if tools.requires_approval("shell", cmd_str) and not allowed("command", cmd_str, "Git commit"):
                tools.cancellation.raise_if_cancelled()
                return "GIT COMMIT DENIED: request user approval before committing."
            result = tools.run(cmd, approved=True)
            results.append(result.output if result.ok else f"ERROR: {result.output}")
        return "\n".join(results)

    @tool
    def git_push(remote: str = "origin", branch: str = "") -> str:
        """Push current branch to remote."""
        consume_tool_call()
        cmd = ["git", "push", remote]
        if branch:
            cmd.append(branch)
        cmd_str = " ".join(cmd)
        if tools.requires_approval("shell", cmd_str) and not allowed("command", cmd_str, "Git push"):
            tools.cancellation.raise_if_cancelled()
            return "GIT PUSH DENIED: request user approval before pushing."
        return tools.run(cmd, approved=True).output

    @tool
    def gh_pr_create(title: str, body: str = "", base: str = "main") -> str:
        """Create a GitHub pull request using gh CLI."""
        consume_tool_call()
        cmd = ["gh", "pr", "create", "--title", title, "--body", body, "--base", base]
        cmd_str = " ".join(cmd)
        if tools.requires_approval("shell", cmd_str) and not allowed("command", cmd_str, "Create GitHub PR"):
            tools.cancellation.raise_if_cancelled()
            return "GH PR CREATE DENIED: request user approval before creating PR."
        return tools.run(cmd, approved=True).output

    registry = registry or build_tool_registry(settings, approve)
    builtins = {
        "read_file": read_file,
        "search_files": search_files,
        "write_file": write_file,
        "replace_in_file": replace_in_file,
        "run_command": run_command,
        "write_files": write_files,
        "git_branch": git_branch,
        "git_commit": git_commit,
        "git_push": git_push,
        "gh_pr_create": gh_pr_create,
    }
    for name, tool_object in builtins.items():
        if name not in registry.names():
            # Built-ins perform their own path/command approval checks and
            # consume the tool budget inside their handlers.
            registry.register_object(name, tool_object, permission_action=None)

    def before_extension_call(spec: ToolSpec, arguments: dict[str, Any]) -> str | None:
        consume_tool_call()
        action = spec.permission_action
        if not action:
            return None
        target = spec.approval_target
        if callable(target):
            target = target(arguments)
        target = str(target or spec.name)
        if tools.requires_approval(action, target) and not allowed(
            action, target, f"The tool {spec.name!r} requested an external action."
        ):
            tools.cancellation.raise_if_cancelled()
            return f"{spec.name.upper()} DENIED: request user approval before using this tool."
        return None

    return create_agent(
        model=create_llm(settings),
        tools=registry.langchain_tools(before_extension_call),
        system_prompt=(
            "You are a careful coding agent. Inspect before editing, make minimal changes, "
            "explain intended mutations, and run focused tests after approved edits. "
            "Never claim a denied action succeeded."
        ),
    )


async def stream_agent_events(
    agent: Any,
    messages: list[dict[str, str]],
    callback: Callable[[dict[str, Any]], None] | None = None,
):
    """Yield LangChain v2 event records and optionally forward them to a UI."""
    async for event in agent.astream_events({"messages": messages}, version="v2"):
        if callback:
            callback(event)
        yield event


def stream_agent_text(
    agent: Any,
    messages: list[dict[str, str]],
    callback: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """Synchronously stream AI message chunks for supervisor worker threads."""
    chunks: list[str] = []
    try:
        updates = agent.stream({"messages": messages}, stream_mode="messages")
        for item in updates:
            message = item[0] if isinstance(item, tuple) else item
            if getattr(message, "type", "ai") not in {"ai", "AIMessageChunk"}:
                continue
            content = getattr(message, "content", "")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            if content:
                text = str(content)
                chunks.append(text)
                if callback:
                    callback({"type": "token", "text": text})
    except RunCancelled:
        raise
    except (AttributeError, TypeError, RuntimeError):
        return response_text(agent.invoke({"messages": messages}))
    return "".join(chunks)


def response_text(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        return ""
    content = getattr(messages[-1], "content", messages[-1].get("content", "") if isinstance(messages[-1], dict) else "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(item.get("text", str(item)) if isinstance(item, dict) else str(item) for item in content)
    return str(content)