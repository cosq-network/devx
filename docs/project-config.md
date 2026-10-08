# Project Configuration: `.devx/config.json`

The `.devx/config.json` file provides project-scoped, non-secret configuration for the `devx` agent. It is loaded automatically when the agent runs inside a workspace.

## File Location

```
<workspace-root>/.devx/config.json
```

## Schema

```json
{
  "permissions": [
    {
      "action": "read|edit|shell|plugin|mcp",
      "resource": "glob-pattern",
      "effect": "allow|ask|deny"
    }
  ],
  "plugins": ["relative/path/to/plugins"],
  "mcp_servers": [
    {
      "name": "server-name",
      "command": ["executable", "arg1", "arg2"],
      "timeout": 30
    }
  ],
  "context_compaction": {
    "enabled": true,
    "max_messages": 24,
    "max_chars": 24000,
    "summary_chars": 4000
  }
}
```

## Field Reference

### `permissions` (array of objects)

Ordered permission rules that control what the agent can do without explicit approval. Rules are evaluated in order; the **last matching rule wins**. The `effect` can be:

- `allow` — Action is permitted without prompting
- `ask` — Action requires user approval (default behavior)
- `deny` — Action is blocked entirely

**Actions:**
- `read` — Reading files
- `edit` — Writing/modifying files
- `shell` — Running shell commands
- `plugin` — Loading workspace plugins
- `mcp` — Starting MCP servers
- `*` — All actions

**Resource Patterns:**
- File paths use glob patterns relative to workspace: `src/**`, `tests/*.py`, `**/*.md`
- Shell commands match against the full command string: `git diff *`, `pytest *`, `rm *`

**Example:**
```json
{
  "permissions": [
    { "action": "read", "resource": "**", "effect": "allow" },
    { "action": "edit", "resource": "tests/**", "effect": "allow" },
    { "action": "shell", "resource": "git diff *", "effect": "allow" },
    { "action": "shell", "resource": "rm *", "effect": "deny" }
  ]
}
```

### `plugins` (array of strings)

Paths to workspace plugin directories. Each path is relative to the workspace root. Plugins are Python modules that export a `register(registry)` function.

```json
{
  "plugins": [".devx/plugins", "tools/custom"]
}
```

### `mcp_servers` (array of objects)

Configuration for stdio-based MCP (Model Context Protocol) servers. Each server is spawned as a child process and communicates via stdin/stdout.

**Fields:**
- `name` (string, required): Unique identifier for the server
- `command` (array of strings, required): Command and arguments to start the server
- `timeout` (integer, optional): Startup timeout in seconds (default: 30)

```json
{
  "mcp_servers": [
    {
      "name": "local-tools",
      "command": ["python", ".devx/mcp_server.py"],
      "timeout": 30
    },
    {
      "name": "filesystem",
      "command": ["npx", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"],
      "timeout": 10
    }
  ]
}
```

### `context_compaction` (object)

Controls automatic conversation history compaction to stay within token budgets.

**Fields:**
- `enabled` (boolean, default: true): Enable automatic compaction
- `max_messages` (integer, default: 24): Maximum messages before compaction triggers
- `max_chars` (integer, default: 24000): Maximum total characters before compaction triggers
- `summary_chars` (integer, default: 4000): Target size of compaction summary

```json
{
  "context_compaction": {
    "enabled": true,
    "max_messages": 30,
    "max_chars": 30000,
    "summary_chars": 5000
  }
}
```

## Precedence

Configuration is loaded with the following precedence (highest to lowest):
1. Environment variables (`DEVX_*`)
2. Workspace `.env` file
3. User config (`~/.devx/config.env`)
4. Project config (`.devx/config.json`)
5. Built-in defaults

Note: `.devx/config.json` only contains non-secret settings. Secrets (API keys) must be provided via environment variables or user config.