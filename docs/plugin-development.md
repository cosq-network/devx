# Plugin Development Guide

`devx` supports workspace plugins — Python modules that register custom tools with the agent. Plugins are loaded at runtime and their tools participate in the same approval and budget system as built-in tools.

## Plugin Structure

A plugin is a Python file (or package) that exports a `register(registry)` function:

```
.devx/
└── plugins/
    ├── __init__.py
    └── my_plugin.py
```

### Minimal Plugin

```python
# .devx/plugins/my_plugin.py
from devx.harness.tool_registry import ToolRegistry, ToolSpec


def register(registry: ToolRegistry) -> None:
    @registry.tool(
        name="my_tool",
        description="Does something useful",
        permission_action="read",  # or "edit", "shell", "plugin", "mcp"
        approval_target=lambda args: args.get("path", "unknown"),
    )
    def my_tool(path: str, option: bool = False) -> str:
        """Tool docstring becomes the LLM-visible description."""
        # Your implementation here
        return f"Processed {path} with option={option}"
```

## Tool Registration API

### `registry.tool` decorator

Registers a Python function as a tool available to the agent.

**Parameters:**
- `name` (str): Tool name (must be unique across all tools)
- `description` (str): Human-readable description for the LLM
- `permission_action` (str): One of `read`, `edit`, `shell`, `plugin`, `mcp` — determines which permission rules apply
- `approval_target` (str | Callable[[dict], str]): Static string or function that returns the target for approval checks

### `registry.register_object`

Registers a pre-built LangChain tool or any object with a `__call__` method.

```python
from langchain.tools import Tool

def my_function(query: str) -> str:
    return f"Result: {query}"

registry.register_object(
    name="my_function",
    tool_object=Tool.from_function(my_function),
    permission_action="read",
)
```

### `registry.register_spec`

Registers a `ToolSpec` for advanced use cases.

```python
from devx.harness.tool_registry import ToolSpec

spec = ToolSpec(
    name="advanced_tool",
    description="Advanced tool with custom schema",
    permission_action="edit",
    approval_target="advanced_tool",
    schema={"type": "object", "properties": {"input": {"type": "string"}}},
    function=my_async_function,  # Can be async
)

registry.register_spec(spec)
```

## Permission Model

All plugin tools go through the approval system:

1. **Permission rules** from `.devx/config.json` are evaluated
2. If `effect == "deny"`, tool is blocked
3. If `effect == "allow"`, tool runs without prompting
4. If `effect == "ask"` or no rule matches and `approval_required=true`, user is prompted
5. Tool calls count against the per-run budget (`DEVX_MAX_TOOL_CALLS`)

## Configuration

Enable plugins in `.devx/config.json`:

```json
{
  "plugins": [".devx/plugins"],
  "permissions": [
    { "action": "plugin", "resource": ".devx/plugins/**", "effect": "allow" }
  ]
}
```

## Example: File Hash Plugin

```python
# .devx/plugins/file_hash.py
import hashlib
from pathlib import Path
from devx.harness.tool_registry import ToolRegistry


def register(registry: ToolRegistry) -> None:
    @registry.tool(
        name="file_hash",
        description="Compute SHA-256 hash of a file",
        permission_action="read",
        approval_target=lambda args: args["path"],
    )
    def file_hash(path: str, algorithm: str = "sha256") -> str:
        """Compute cryptographic hash of a file."""
        file_path = Path(path)
        if not file_path.is_file():
            return f"Error: {path} is not a file"

        hasher = hashlib.new(algorithm)
        with file_path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
```

## Example: HTTP Request Plugin

```python
# .devx/plugins/http.py
import urllib.request
import urllib.error
from devx.harness.tool_registry import ToolRegistry


def register(registry: ToolRegistry) -> None:
    @registry.tool(
        name="http_get",
        description="Make an HTTP GET request",
        permission_action="shell",  # Network access treated as shell
        approval_target=lambda args: args["url"],
    )
    def http_get(url: str, timeout: int = 10) -> str:
        """Fetch content from a URL."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "devx-agent/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")[:10000]
        except urllib.error.HTTPError as e:
            return f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            return f"Error: {e}"
```

## Best Practices

1. **Keep tools focused** — One tool, one purpose
2. **Validate inputs** — Check paths, URLs, and parameters
3. **Handle errors gracefully** — Return error strings, don't raise exceptions
4. **Limit output size** — Truncate large responses to stay within token budgets
5. **Use appropriate permissions** — `read` for read-only, `edit` for mutations, `shell` for external calls
6. **Document with docstrings** — The docstring becomes the LLM's tool description

## Testing Plugins

```python
# test_my_plugin.py
from devx.harness.tool_registry import ToolRegistry
from devx.harness.permissions import PermissionPolicy
from devx.config import Settings
from my_plugin import register


def test_plugin():
    policy = PermissionPolicy(Path.cwd(), approval_required=False)
    settings = Settings(workspace=Path.cwd(), approval_required=False)
    registry = ToolRegistry()
    register(registry)

    tools = registry.langchain_tools()
    assert "my_tool" in [t.name for t in tools]
```

## Security Considerations

- Plugins run with full user permissions — only load trusted plugins
- Use `permission_action` and `approval_target` to integrate with the approval system
- Never execute untrusted code from plugin outputs
- Plugin files are loaded via `importlib` — they can execute arbitrary Python on import