# Extensibility and Session Safety

`devx` keeps extensions behind the same workspace and approval boundaries as
its built-in tools. Configure extensions in the project-local
`.devx/config.json`; secrets remain in `.env` or `~/.devx/config.env`.

See also:
- [Project Configuration](project-config.md) — Complete `.devx/config.json` schema
- [Plugin Development](plugin-development.md) — Building custom Python tools
- [MCP Server Development](mcp-development.md) — Integrating external MCP servers

## Permission Rules

Rules use three fields:

```json
{ "action": "edit", "resource": "src/**", "effect": "ask" }
```

Supported effects are `allow`, `ask`, and `deny`. Rules are evaluated in file
order and the last matching rule wins. File resources match workspace-relative
paths as well as absolute paths. Shell resources match the normalized command
text. `write` and `command` are accepted as aliases for `edit` and `shell`.

The default remains approval-required for edits and shell commands. A matching
`allow` rule may remove that prompt, while `ask` preserves it. Hard workspace
containment, destructive-command blocking, timeouts, output limits, and
atomic/journaled writes remain active regardless of configuration.

## Python Plugins

Plugins are explicitly configured and must be inside the workspace. Loading a
plugin is executable code: the configured plugin path is approved before any
plugin is imported. Plan-only runs do not load plugins or start MCP servers. A
plugin file or directory exports `register(registry)`:

```python
def register(registry):
    def count_lines(path: str) -> str:
        return str(len(open(path, encoding="utf-8").read().splitlines()))

    registry.register(
        "count_lines",
        count_lines,
        description="Count lines in a workspace file",
        permission_action="plugin",
        approval_target=lambda args: args.get("path", ""),
    )
```

Plugin imports are executable code, so they never load merely because a file
exists. Configure them under `plugins`, and keep them reviewed like any other
project code.

**Full guide:** [Plugin Development](plugin-development.md)

## MCP Servers

MCP servers use explicit stdio command configuration:

```json
{
  "mcp_servers": [
    {
      "name": "local-tools",
      "command": ["python", ".devx/mcp_server.py"],
      "cwd": ".",
      "timeout": 30
    }
  ]
}
```

`devx` approves each configured server before starting it, then initializes
the server, discovers `tools/list`, and exposes each tool through the
registry. Tool calls use `tools/call`. Server working directories must be
inside the workspace, commands run without a shell, and MCP calls use the
normal approval policy and tool-call budget. MCP processes receive a minimal
runtime environment plus explicitly configured variables; unrelated parent
process secrets are not inherited.

**Full guide:** [MCP Server Development](mcp-development.md)

## Automatic Compaction

Session messages are compacted locally when either the message count or
character budget is exceeded. Older role-labelled messages become a bounded
summary and the newest messages remain verbatim. Configure this under
`context_compaction` or with these environment variables:

```env
DEVX_CONTEXT_COMPACTION_ENABLED=true
DEVX_MAX_CONTEXT_MESSAGES=24
DEVX_MAX_CONTEXT_CHARS=24000
DEVX_COMPACTION_SUMMARY_CHARS=4000
```

Compaction is deterministic and does not make an extra model/API call.