from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
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

    policy = PermissionPolicy.from_settings(settings)

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
    def read_files(paths: list[str]) -> str:
        """Read multiple UTF-8 text files in parallel. Returns concatenated results with file headers."""
        consume_tool_call()
        if not paths:
            return "ERROR: no paths provided"
        
        results = []
        max_workers = min(len(paths), 8)  # Cap parallelism
        
        def read_one(path: str) -> tuple[str, str]:
            result = tools.read(path)
            return path, result.output if result.ok else f"ERROR: {result.output}"
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {executor.submit(read_one, path): path for path in paths}
            for future in as_completed(future_to_path):
                path, output = future.result()
                results.append(f"\n--- {path} ---\n{output}")
        
        return "".join(results)

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
        requires_approval, _ = tools.check_approval("edit", path)
        if requires_approval and not allowed("write", path, "Proposed diff:\n" + preview):
            tools.cancellation.raise_if_cancelled()
            return "WRITE DENIED: request user approval before changing this file."
        return tools.write(path, content, approved=True).output

    @tool
    def replace_in_file(path: str, old_text: str, new_text: str, expected_hash: str = "") -> str:
        """Apply one exact replacement, refusing ambiguous or concurrently changed files."""
        consume_tool_call()
        preview = tools.preview_replace(path, old_text, new_text)
        requires_approval, _ = tools.check_approval("edit", path)
        if requires_approval and not allowed("write", path, "Proposed diff:\n" + preview):
            tools.cancellation.raise_if_cancelled()
            return "PATCH DENIED: request user approval before changing this file."
        return tools.replace(path, old_text, new_text, expected_hash=expected_hash or None, approved=True).output

    @tool
    def run_command(command: str) -> str:
        """Run a project command after explicit user approval."""
        consume_tool_call()
        requires_approval, _ = tools.check_approval("shell", command)
        if requires_approval and not allowed("command", command, "The agent requested this project command."):
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
            requires_approval, _ = tools.check_approval("edit", path)
            if requires_approval and not allowed("write", path, "Proposed diff:\n" + preview):
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
        requires_approval, _ = tools.check_approval("shell", f"git checkout -b {name}")
        if requires_approval and not allowed("command", f"git checkout -b {name}", "Create new git branch"):
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
            requires_approval, _ = tools.check_approval("shell", cmd_str)
            if requires_approval and not allowed("command", cmd_str, "Git commit"):
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
        requires_approval, _ = tools.check_approval("shell", cmd_str)
        if requires_approval and not allowed("command", cmd_str, "Git push"):
            tools.cancellation.raise_if_cancelled()
            return "GIT PUSH DENIED: request user approval before pushing."
        return tools.run(cmd, approved=True).output

    @tool
    def gh_pr_create(title: str, body: str = "", base: str = "main") -> str:
        """Create a GitHub pull request using gh CLI."""
        consume_tool_call()
        cmd = ["gh", "pr", "create", "--title", title, "--body", body, "--base", base]
        cmd_str = " ".join(cmd)
        requires_approval, _ = tools.check_approval("shell", cmd_str)
        if requires_approval and not allowed("command", cmd_str, "Create GitHub PR"):
            tools.cancellation.raise_if_cancelled()
            return "GH PR CREATE DENIED: request user approval before creating PR."
        return tools.run(cmd, approved=True).output

    registry = registry or build_tool_registry(settings, approve)
    builtins = {
        "read_file": read_file,
        "read_files": read_files,
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


async def run_tools_parallel(
    tool_calls: list[tuple[str, dict]],
    tools: WorkspaceTools,
    approve: Callable[[str, str, str], bool] | None = None,
    max_parallel: int = 4,
) -> list[dict]:
    """Run multiple tool calls in parallel.
    
    Args:
        tool_calls: List of (tool_name, arguments) tuples
        tools: WorkspaceTools instance
        approve: Approval callback
        max_parallel: Maximum parallel executions
    
    Returns:
        List of results with keys: tool, arguments, result, error
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    
    async def run_one(tool_name: str, args: dict) -> dict:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                if tool_name == "read_file":
                    path = args.get("path", "")
                    result = await loop.run_in_executor(executor, lambda: tools.read(path))
                    return {"tool": tool_name, "arguments": args, "result": result.output if result.ok else None, "error": None if result.ok else result.output}
                elif tool_name == "search_files":
                    query = args.get("query", "")
                    path = args.get("path", ".")
                    result = await loop.run_in_executor(executor, lambda: tools.search(query, path))
                    return {"tool": tool_name, "arguments": args, "result": result.output if result.ok else None, "error": None if result.ok else result.output}
                elif tool_name == "write_file":
                    path = args.get("path", "")
                    content = args.get("content", "")
                    if not path:
                        return {"tool": tool_name, "arguments": args, "result": None, "error": "missing path"}
                    preview = tools.preview_write(path, content)
                    if tools.requires_approval("edit", path) and approve and not approve("write", path, "Proposed diff:\n" + preview):
                        return {"tool": tool_name, "arguments": args, "result": None, "error": "WRITE DENIED"}
                    result = await loop.run_in_executor(executor, lambda: tools.write(path, content, approved=True))
                    return {"tool": tool_name, "arguments": args, "result": result.output if result.ok else None, "error": None if result.ok else result.output}
                elif tool_name == "run_command":
                    command = args.get("command", "")
                    if not command:
                        return {"tool": tool_name, "arguments": args, "result": None, "error": "missing command"}
                    if tools.requires_approval("shell", command) and approve and not approve("command", command, "The agent requested this project command."):
                        return {"tool": tool_name, "arguments": args, "result": None, "error": "COMMAND DENIED"}
                    result = await loop.run_in_executor(executor, lambda: tools.run(command, approved=True))
                    return {"tool": tool_name, "arguments": args, "result": result.output if result.ok else None, "error": None if result.ok else result.output}
                else:
                    return {"tool": tool_name, "arguments": args, "result": None, "error": f"Unknown tool: {tool_name}"}
            except Exception as exc:
                return {"tool": tool_name, "arguments": args, "result": None, "error": str(exc)}
    
    semaphore = asyncio.Semaphore(max_parallel)
    
    async def run_with_semaphore(tool_name: str, args: dict) -> dict:
        async with semaphore:
            return await run_one(tool_name, args)
    
    tasks = [run_with_semaphore(name, args) for name, args in tool_calls]
    return await asyncio.gather(*tasks)


def run_tools_parallel_sync(
    tool_calls: list[tuple[str, dict]],
    tools: WorkspaceTools,
    approve: Callable[[str, str, str], bool] | None = None,
    max_parallel: int = 4,
) -> list[dict]:
    """Synchronous version of run_tools_parallel."""
    import asyncio
    return asyncio.run(run_tools_parallel(tool_calls, tools, approve, max_parallel))