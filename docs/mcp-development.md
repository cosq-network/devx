# MCP Server Development Guide

`devx` supports stdio-based MCP (Model Context Protocol) servers. MCP servers are external processes that expose tools to the agent via JSON-RPC over stdin/stdout.

## Configuration

Configure MCP servers in `.devx/config.json`:

```json
{
  "mcp_servers": [
    {
      "name": "my-server",
      "command": ["python", ".devx/mcp_server.py"],
      "timeout": 30
    }
  ],
  "permissions": [
    { "action": "mcp", "resource": "my-server", "effect": "allow" }
  ]
}
```

## MCP Server Protocol

The agent communicates with MCP servers using the standard MCP protocol over stdio:

1. **Initialization**: Agent sends `initialize` request
2. **Tool Discovery**: Agent calls `tools/list` to discover available tools
3. **Tool Execution**: Agent calls `tools/call` to invoke tools

## Implementing an MCP Server (Python)

### Using the MCP SDK

```python
# .devx/mcp_server.py
import asyncio
import json
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


app = Server("my-mcp-server")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="echo",
            description="Echo back the input",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to echo"}
                },
                "required": ["message"],
            },
        ),
        Tool(
            name="get_time",
            description="Get current timestamp",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "echo":
        message = arguments.get("message", "")
        return [TextContent(type="text", text=f"Echo: {message}")]
    elif name == "get_time":
        from datetime import datetime
        return [TextContent(type="text", text=datetime.now().isoformat())]
    else:
        raise ValueError(f"Unknown tool: {name}")


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
```

### Without SDK (Raw JSON-RPC)

```python
# .devx/mcp_server_raw.py
import asyncio
import json
import sys
from datetime import datetime
from typing import Any


TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the input",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"}
            },
            "required": ["message"],
        },
    },
    {
        "name": "get_time",
        "description": "Get current timestamp",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


async def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    method = request.get("method")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "raw-mcp-server", "version": "1.0.0"},
            },
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})

        if name == "echo":
            result = f"Echo: {args.get('message', '')}"
        elif name == "get_time":
            result = datetime.now().isoformat()
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {name}"},
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": result}],
                "isError": False,
            },
        }

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


async def main():
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)
    writer = asyncio.StreamWriter(sys.stdout, {}, None, asyncio.get_event_loop())

    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            request = json.loads(line.decode())
            response = await handle_request(request)
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
        except json.JSONDecodeError:
            continue


if __name__ == "__main__":
    asyncio.run(main())
```

## Tool Schema Requirements

MCP tools must follow JSON Schema for their `inputSchema`:

```json
{
  "name": "tool_name",
  "description": "Human-readable description",
  "inputSchema": {
    "type": "object",
    "properties": {
      "param1": {"type": "string", "description": "Description"},
      "param2": {"type": "integer", "default": 10}
    },
    "required": ["param1"]
  }
}
```

Supported types: `string`, `number`, `integer`, `boolean`, `array`, `object`, `null`

## Permission Integration

MCP tools participate in the approval system:

1. When `tools/list` returns tools, the agent registers them with the tool registry
2. Each tool gets a `permission_action` based on its name/description heuristics:
   - File operations → `edit` or `read`
   - Shell/command tools → `shell`
   - Network/API tools → `shell`
   - Read-only queries → `read`
3. You can override by setting `permission_action` in tool metadata (if supported)

## Timeout and Output Limits

- **Startup timeout**: Configured in `mcp_servers[].timeout` (default 30s)
- **Output limit**: Bounded by `DEVX_MAX_FILE_BYTES` (default 1MB)
- **Tool call budget**: Counts against `DEVX_MAX_TOOL_CALLS` (default 40)

## Best Practices

1. **Keep servers stateless** — Each run spawns a new process
2. **Handle initialization** — Always respond to `initialize` and `tools/list`
3. **Validate inputs** — Check arguments against schema
4. **Return structured errors** — Use JSON-RPC error format
5. **Limit output** — Truncate large responses
6. **Use stderr for logging** — stdout is reserved for JSON-RPC

## Debugging MCP Servers

Test your server manually:

```bash
# Start server and send initialize request
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python .devx/mcp_server.py

# List tools
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python .devx/mcp_server.py

# Call tool
echo '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"message":"hello"}}}' | python .devx/mcp_server.py
```

## Example: Filesystem MCP Server

```python
# .devx/mcp_filesystem.py
import asyncio
import os
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


app = Server("filesystem")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="read_file",
            description="Read a file",
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        Tool(
            name="list_dir",
            description="List directory contents",
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    base = Path.cwd()

    if name == "read_file":
        path = base / arguments["path"]
        if not path.is_file():
            return [TextContent(type="text", text=f"Not found: {path}")]
        return [TextContent(type="text", text=path.read_text())]

    elif name == "list_dir":
        path = base / arguments.get("path", ".")
        if not path.is_dir():
            return [TextContent(type="text", text=f"Not a directory: {path}")]
        items = [f"{'DIR' if p.is_dir() else 'FILE'} {p.name}" for p in path.iterdir()]
        return [TextContent(type="text", text="\n".join(items))]

    raise ValueError(f"Unknown tool: {name}")


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
```