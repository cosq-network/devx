# Extensibility, Plugins & MCP in `devx`

`devx` is designed to adapt to your unique engineering environment. You can extend the agent's capabilities with custom tools via two primary mechanisms:
1. **Workspace Python Plugins**: Local Python scripts placed in `.devx/plugins/`.
2. **Model Context Protocol (MCP) Servers**: External tools exposed over stdio via the open MCP standard.

Every custom tool — whether originating from a Python plugin or an MCP server — is registered in the unified `ToolRegistry` and bound to the same **approval gates and permission rules** as built-in tools.

```mermaid
graph TD
    AGENT[Supervisor Agent Graph] --> REGISTRY[ToolRegistry]
    
    REGISTRY --> BUILTIN[Built-in Tools: read, write, shell, test]
    REGISTRY --> PLUGINS[Workspace Python Plugins (.devx/plugins/)]
    REGISTRY --> MCP[MCP Client (Stdio)]
    
    MCP --> MCP_SRV1[Postgres MCP Server]
    MCP --> MCP_SRV2[GitHub MCP Server]
    
    REGISTRY --> GATE{Approval Gate & Permissions}
    GATE -->|Approved| EXEC[Execute Tool Handler]
    GATE -->|Denied| REJECT[Return Tool Denial Error]
```

---

## 1. Workspace Python Plugins

Workspace plugins are standalone Python files located inside your project's `.devx/plugins/` directory. They allow you to define custom actions — such as running database migrations, checking internal APIs, or validating schema contracts.

### Creating a Plugin

1. Create the plugin directory if it doesn't already exist:
   ```bash
   mkdir -p .devx/plugins
   ```
2. Create a new Python file, for example `.devx/plugins/database.py`:

```python
"""Custom database inspection plugin for devx."""

import subprocess
from pydantic import BaseModel, Field


class MigrateArgs(BaseModel):
    environment: str = Field(default="development", description="Target environment (development, staging)")


def run_migrations(environment: str = "development") -> str:
    """Run pending Alembic database migrations."""
    cmd = ["alembic", "upgrade", "head"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return f"Migration failed: {result.stderr}"
    return f"Migrations applied successfully: {result.stdout}"


def register_tools(registry):
    """Entry point called by devx during initialization."""
    registry.register(
        name="run_migrations",
        handler=run_migrations,
        description="Apply pending database migrations using Alembic",
        args_schema=MigrateArgs,
        permission_action="database",  # Binds to permission rules
    )
```

### Plugin Contract & Lifecycle
- **Discovery**: On startup, `devx` scans `.devx/plugins/*.py` and imports each module dynamically using `importlib`.
- **`register_tools(registry)`**: If defined, `devx` passes the global `ToolRegistry` instance to this function.
- **Permission Binding**: You can assign a custom `permission_action` (e.g. `database`). You can then govern this action in `.devx/config.json`:
  ```json
  {
    "permissions": [
      { "action": "database", "resource": "*", "effect": "deny" }
    ]
  }
  ```

---

## 2. Model Context Protocol (MCP) Integration

The **Model Context Protocol (MCP)** is an open industry standard that enables language models to securely discover and invoke tools provided by external processes.

`devx` includes an integrated stdio **MCP Client** (`MCPClient`) that manages external server lifecycles, parses JSON-RPC messages, and dynamically registers exposed tools into the agent runtime.

### Configuring MCP Servers in `.devx/config.json`

Define your external MCP servers inside `.devx/config.json` under the `mcp_servers` dictionary:

```json
{
  "mcp_servers": {
    "sqlite-db": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sqlite", "--file", "app.db"],
      "env": {
        "DEBUG": "mcp:*"
      }
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_your_token_here"
      }
    }
  }
}
```

### Execution Flow:
1. **Server Initialization**: When `devx` launches, it spawns each configured MCP command as a child process over stdio (`subprocess.Popen`).
2. **Capability Handshake**: Sends the standard `initialize` JSON-RPC request and checks protocol version compatibility.
3. **Tool Discovery**: Invokes `tools/list` on the MCP server to retrieve tool names, descriptions, and JSON schema parameters.
4. **Agent Exposure**: Wraps each discovered tool as a LangChain `StructuredTool` and exposes it directly to the Planner and Coder agents.
5. **Approval Enforcement**: When the model invokes an MCP tool, `devx` prompts for approval just as it does for built-in shell commands.

---

## 3. Custom Tool Safety Guarantees

All custom tools (plugins and MCP) run with hard safety boundaries:
- **Timeouts**: Long-running plugin executions or hung MCP servers are killed after the configured timeout (default: 30 seconds).
- **Output Bounds**: Tool output captured from stdout/stderr is capped (default: 1 MB) to prevent out-of-memory crashes.
- **Process Cleanup**: When `devx` exits, `ToolRegistry.close()` gracefully shuts down all child MCP server processes.

---

## Next Steps

- Explore Prometheus metrics, pricing calculation, and DLQ recovery in [Observability & Operations](09-observability-and-ops.md).
- Review [Governance and Safety](06-governance-and-safety.md) for permission syntax.
- Return to the [User Guide Index](../user-guide.md).
