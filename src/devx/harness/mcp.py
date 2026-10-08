from __future__ import annotations

import json
import os
import queue
import re
import shlex
import subprocess  # nosec B404 - MCP commands are explicit configuration and never use a shell.
import threading
import time
from pathlib import Path
from typing import Any

from devx.harness.tool_registry import ToolRegistry


class MCPError(RuntimeError):
    pass


class MCPClient:
    """Small stdio MCP client covering initialization, tool discovery, and calls."""

    def __init__(self, name: str, command: list[str], *, cwd: Path, env: dict[str, str] | None = None,
                 timeout: float = 30.0, max_output_bytes: int = 1_000_000):
        if not command:
            raise MCPError(f"MCP server {name!r} has an empty command")
        self.name, self.command, self.cwd = name, command, cwd
        self.env, self.timeout = env or {}, max(0.1, timeout)
        self.max_output_bytes = max(1, max_output_bytes)
        self.process: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._responses: queue.Queue[bytes] = queue.Queue()
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        if self.process is not None:
            return
        inherited_names = {
            "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
            "TMPDIR", "LANG", "LC_ALL", "PYTHONIOENCODING",
        }
        merged_env = {key: value for key, value in os.environ.items() if key in inherited_names}
        merged_env.update(self.env)
        try:
            self.process = subprocess.Popen(  # nosec B603 - shell=False and explicit configured argv.
                self.command,
                cwd=self.cwd,
                env=merged_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._reader = threading.Thread(target=self._read_stdout, daemon=True)
            self._reader.start()
            self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "devx", "version": "0.2"},
            })
            self._notify("notifications/initialized", {})
        except (OSError, MCPError):
            self.close()
            raise

    def list_tools(self) -> list[dict[str, Any]]:
        self.start()
        result = self._request("tools/list", {})
        tools = result.get("tools", [])
        if not isinstance(tools, list):
            raise MCPError(f"MCP server {self.name!r} returned an invalid tools list")
        return [item for item in tools if isinstance(item, dict) and item.get("name")]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.start()
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise MCPError(str(result))
        if result.get("structuredContent") is not None:
            return self._bound(json.dumps(result["structuredContent"], ensure_ascii=False, default=str))
        content = result.get("content", [])
        output: list[str] = []
        for item in content if isinstance(content, list) else []:
            if isinstance(item, dict) and item.get("type") == "text":
                output.append(str(item.get("text", "")))
            else:
                output.append(json.dumps(item, ensure_ascii=False, default=str))
        return self._bound("\n".join(output))

    def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                line = process.stdout.readline()
                if not line:
                    self._responses.put(b"")
                    return
                self._responses.put(line)
        except (OSError, ValueError):
            self._responses.put(b"")

    def _bound(self, value: str) -> str:
        return value.encode("utf-8")[:self.max_output_bytes].decode("utf-8", errors="replace")

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None:
            raise MCPError(f"MCP server {self.name!r} is not running")
        process.stdin.write((json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n").encode())
        process.stdin.flush()

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        process = self.process
        if process is None:
            # start() calls this during initialization after assigning process.
            raise MCPError(f"MCP server {self.name!r} is not running")
        if process.stdin is None or process.stdout is None:
            raise MCPError(f"MCP server {self.name!r} has no stdio pipes")
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            process.stdin.write((json.dumps({
                "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
            }) + "\n").encode())
            process.stdin.flush()
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.close()
                    raise MCPError(f"MCP server {self.name!r} timed out during {method}")
                try:
                    line = self._responses.get(timeout=remaining)
                except queue.Empty as exc:
                    self.close()
                    raise MCPError(f"MCP server {self.name!r} timed out during {method}") from exc
                if not line:
                    raise MCPError(f"MCP server {self.name!r} exited before responding")
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise MCPError(str(message["error"]))
                result = message.get("result", {})
                return result if isinstance(result, dict) else {"value": result}

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass


def _command(value: Any) -> list[str]:
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return list(value)
    if isinstance(value, str) and value.strip():
        return shlex.split(value, posix=os.name != "nt")
    raise MCPError("MCP server command must be a non-empty string or argument list")


def _schema_model(name: str, schema: Any) -> Any:
    if not isinstance(schema, dict):
        return None
    try:
        from pydantic import create_model, Field
    except ImportError:
        return None
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    if not isinstance(properties, dict):
        return None
    fields: dict[str, Any] = {}
    for field_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        default = ... if field_name in required else None
        if default is None:
            fields[str(field_name)] = (Any, Field(default=None))
        else:
            fields[str(field_name)] = (Any, default)
    return create_model(name, **fields)  # type: ignore[call-overload]


def register_mcp_servers(workspace: Path, configs: Any, registry: ToolRegistry, *,
                         max_output_bytes: int = 1_000_000) -> list[str]:
    """Discover tools from explicitly configured stdio MCP servers."""
    if isinstance(configs, dict):
        configs = [dict(value, name=name) if isinstance(value, dict) else value for name, value in configs.items()]
    if not isinstance(configs, (list, tuple)):
        raise MCPError("mcp_servers must be a list or object")
    registered: list[str] = []
    for index, raw in enumerate(configs):
        if not isinstance(raw, dict):
            raise MCPError("Each MCP server must be an object")
        name = str(raw.get("name") or f"server-{index + 1}").strip()
        cwd = Path(str(raw.get("cwd", "."))).expanduser()
        if not cwd.is_absolute():
            cwd = workspace / cwd
        cwd = cwd.resolve()
        try:
            cwd.relative_to(workspace.resolve())
        except ValueError as exc:
            raise MCPError(f"MCP server cwd must be inside the workspace: {cwd}") from exc
        client = MCPClient(name, _command(raw.get("command")), cwd=cwd,
                           env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
                           timeout=float(raw.get("timeout", 30)),
                           max_output_bytes=max_output_bytes)
        try:
            discovered = client.list_tools()
            registry.add_resource(client)
            for item in discovered:
                remote_name = str(item["name"])
                tool_name = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp_{name}_{remote_name}").replace("-", "_")

                def call_remote(*, _client=client, _name=remote_name, **kwargs: Any) -> str:
                    return _client.call_tool(_name, kwargs)

                registry.register(
                    tool_name,
                    call_remote,
                    description=str(item.get("description") or f"MCP tool {name}:{remote_name}"),
                    permission_action="mcp",
                    approval_target=f"{name}:{remote_name}",
                    args_schema=_schema_model(tool_name, item.get("inputSchema")),
                )
                registered.append(tool_name)
        except (OSError, MCPError, ValueError, TypeError):
            client.close()
            raise
    return registered
