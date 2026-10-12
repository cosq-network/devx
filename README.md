# devx-coding-agent

`devx` is a **local-first, approval-gated AI coding agent** distributed as a Python package. It ships with two user interfaces:

- A **Textual TUI** for interactive coding sessions
- A **Typer CLI** for one-shot commands and session management

Under the hood, `devx` uses a **LangGraph supervisor workflow** (planner → coder → tester), approval-gated workspace tools, SQLite-backed session persistence, and supports any OpenAI-compatible or native LLM provider (OpenAI, Anthropic, Google, Azure, LiteLLM, Ollama, and more).

For the positioning statement, target users, and the core/team feature split, see [docs/positioning.md](docs/positioning.md). Quantitative claims are bound to the [benchmark methodology](docs/benchmark-methodology.md).

---

## Table of Contents

1. [Features](#features)
2. [User Guide](docs/user-guide.md)
3. [Architecture](#architecture)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Quick Start](#quick-start)
7. [CLI Usage](#cli-usage)
8. [TUI Usage](#tui-usage)
9. [Slash Commands](#slash-commands)
10. [Development](#development)
11. [Testing](#testing)
12. [Security](#security)
13. [Roadmap](#roadmap)
14. [Contributing](#contributing)

---

## Features

| Feature | Description |
|---|---|
| **LangGraph supervisor** | Explicit planner → coder → tester state machine with conditional branching, loops, and interrupt/resume |
| **Parallel execution** | Automatic parallel coder for multi-file tasks (batch reads) and concurrent lint/test execution |
| **Remote SSH workspace** | Work transparently on remote servers via SSH/SFTP with jump-host bastion support, auto venv activation, and TCP keepalives |
| **Containers too** | Run against a `container://` / `docker://` target; file edits and commands execute inside the container with no bind mount required |
| **Governed remote execution** | The same approvals, hash-guarded undo, and audit trail as local runs — plus policy presets (`remote_only`, `deny_local_writes`, `strict`) and `devx doctor --remote` preflight (see [docs/governed-remote.md](docs/governed-remote.md)) |
| **Approval-gated tools** | All file writes and shell commands require explicit user approval by default |
| **Safe review and recovery** | Approved writes show bounded diffs, are atomic and journaled, and can be undone with `/undo` |
| **File-by-file review** | `devx changes list` / `accept` / `reject` reviews each recorded change and reverts one at a time, hash-guarded |
| **Cost visibility** | Per-model pricing turns recorded token usage into dollars with `devx usage`, including per-run receipts (`devx usage --run <id> --markdown`) |
| **Portable audit trail** | `devx audit export` emits sessions, events, changes, DLQ entries, and usage as one JSON/JSONL stream |
| **Drift verification** | `devx verify` checks the workspace against the change journal — every recorded change is reported `intact`, `reverted`, `conflict`, or `missing` by hash |
| **Secret guard** | Reads are scanned for API keys, tokens, and private keys with `warn` / `redact` / `block` modes, plus a CI-friendly `devx scan-secrets` command |
| **PR-ready reports** | `devx report <session-or-run>` renders a Markdown/JSON summary with timeline, file hashes, lint/test results, cost, and a suggested PR body |
| **Disposable remote lab** | `devx lab up` boots a throwaway SSH container and prints ready-to-paste `--remote` / `--container` URIs for demos and onboarding |
| **Resilience & circuit breaker** | Circuit breaker protection, rate limit detection, and exponential backoff retry for LLM calls |
| **Dead Letter Queue (DLQ)** | Failed runs are captured in SQLite for debugging, inspection, and one-command retries |
| **SQLite sessions** | Persistent session state, event history, and checkpoint-compatible run IDs |
| **@ file mentions** | Reference files/directories with `@` syntax; fuzzy completion in TUI |
| **Plan-first & plan-only** | Preview generated plans and approve before coding, or run `--plan-only` without edits |
| **Bounded execution** | File reads, command output, context, iterations, and tool calls have configurable limits |
| **Configurable permissions** | Ordered project rules can allow, ask, or deny reads, edits, shell commands, plugins, and MCP tools |
| **Automatic compaction** | Long session histories are summarized locally while recent work remains available |
| **Extensible tools** | Workspace plugins and stdio MCP servers register tools through the same approval and budget controls |
| **Local workspace indexing** | Background or on-demand semantic indexing with `devx index` and file-watcher `--watch` |
| **Observability suite** | Structured JSON logs, correlation IDs, health checks (`devx health`), and Prometheus metrics (`devx metrics`) |
| **Project initialization** | Fast interactive or non-interactive onboarding wizard with `devx init` |
| **Knowledge graph memory** | Optional SQLite node/edge store that persists approval decisions and task history for cross-session context |
| **Team layer (prototype)** | Local-first `devx team` manifest, membership registry, and shared-session usage counters (see [docs/team-layer.md](docs/team-layer.md)) |
| **Cancellation** | Cancel active TUI runs and terminate active project commands safely |
| **Multi-provider support** | OpenAI, Anthropic, Google, Azure, OpenRouter, LiteLLM, Ollama, or any OpenAI-compatible endpoint |
| **Local token optimization** | Optional ONNX INT8 embeddings for semantic cache and context compression (zero API calls) |
| **LangSmith tracing** | Optional tracing integration for debugging agent runs |
| **Cross-platform** | Windows, macOS, Linux support with CI/CD |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      User Interface Layer                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  Textual TUI │  │  Typer CLI   │  │Session Mgr   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
├─────────────────────────────────────────────────────────────┤
│                 Agentic Harness (Multi-Agent)                │
│  ┌──────────────────────────────────────────────────────┐  │
│  │        LangGraph State Machine / Supervisor            │  │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐    │  │
│  │  │ Planner │ │Coder/Par.│ │ Tester  │ │  Docs   │    │  │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘    │  │
│  └──────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│              Token Optimization Layer (Client-Side)          │
│  ┌────────────────────┐  ┌─────────────────────────────┐  │
│  │ Local Embeddings   │  │ Semantic Cache + Context    │  │
│  │ (ONNX INT8)        │  │ Selection (@ file mentions) │  │
│  └────────────────────┘  └─────────────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                    Model Abstraction Layer                   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  LangChain ChatOpenAI (configurable base_url)         │  │
│  │  → Any OpenAI-compatible endpoint                     │  │
│  │    (LiteLLM / vLLM / Ollama / OpenAI / etc.)          │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

- **`src/devx/agents/graph.py`** — LangGraph state graph builder; orchestrates planner, coder, and tester stages with conditional branching
- **`src/devx/harness/tools.py`** — `WorkspaceTools`: read, write, replace, search, `git_diff`, and `run` (shell commands)
- **`src/devx/harness/backend.py`** & **`ssh_backend.py`** — Pluggable `LocalWorkspaceBackend` and `SshWorkspaceBackend` for local and remote SSH/SFTP environments
- **`src/devx/harness/container_backend.py`** — `ContainerWorkspaceBackend` running file ops and commands via `docker exec` (no bind mount)
- **`src/devx/pricing.py`** — per-model pricing table, cost formatting, and Markdown usage receipts for `devx usage`
- **`src/devx/audit.py`** — portable audit-trail collection and JSON/JSONL rendering
- **`src/devx/secrets.py`** — lightweight secret scanner powering read-time `warn`/`redact`/`block` modes and `devx scan-secrets`
- **`src/devx/report.py`** — PR-ready run/session reports (`devx report`)
- **`src/devx/harness/lab.py`** — disposable remote workspaces (`devx lab`) built on the e2e SSH container image
- **`src/devx/harness/tool_registry.py`** — built-in, workspace plugin, and MCP tool registration
- **`src/devx/harness/mcp.py`** — bounded stdio MCP initialization, discovery, and tool calls
- **`src/devx/harness/approval.py`** — `ApprovalManager` gates all mutations (writes, command execution)
- **`src/devx/harness/permissions.py`** — workspace containment and ordered allow/ask/deny rules
- **`src/devx/harness/session.py`** — `SessionStore` (SQLite CRUD, event log, and Dead Letter Queue)
- **`src/devx/observability/`** — structured logging, correlation IDs, health probes, and metrics collector
- **`src/devx/harness/changes.py`** — `ChangeJournal` (atomic writes, backups, rollback support)
- **`src/devx/token_optim/context.py`** — bounded context assembly and deterministic relevance selection
- **`src/devx/token_optim/compaction.py`** — bounded local session-history compaction
- **`src/devx/token_optim/cache.py`** — optional semantic cache isolated by provider/model/embedder namespace
- **`src/devx/tui/app.py`** — `DevxTUI` (Textual app)
- **`src/devx/cli/app.py`** — Typer CLI entrypoint

---

## Requirements

- **Python** >= 3.11
- **pip** (for installation)
- An LLM provider API key (or local Ollama/LiteLLM endpoint)

---

## Installation

### Dependency Architecture & `pyproject.toml`

`devx` follows modern Python packaging standards (**PEP 517** and **PEP 621** via Hatchling). All dependencies, optional extras, and console entry points are declared directly in [`pyproject.toml`](pyproject.toml) rather than a legacy `requirements.txt`.

- **Reproducible CI environments**: Pinned validated dependency versions are tracked in [`constraints.txt`](constraints.txt) (`pip install -c constraints.txt -e ".[all]"`).
- **Vulnerability scanning**: Audited runtime dependencies are listed in [`requirements-audit.txt`](requirements-audit.txt).

### From source (recommended for development)

Installing in editable mode (`-e`) links the command launcher directly to your local working directory, so changes to `src/devx` are immediately live across your system without reinstallation:

```bash
# Recommended for core development (Agent runtime + TUI + CLI + SSH + Dev tools)
pip install -e ".[agent,tui,cli,ssh,dev]"

# Or install all optional dependencies:
pip install -e ".[all]"
```

### Optional extras

| Extra | Packages | Purpose |
|---|---|---|
| `agent` | langchain, langgraph, langgraph-checkpoint-sqlite, langchain-openai | Core agent runtime |
| `tui` | textual | Textual TUI |
| `cli` | typer | Typer CLI |
| `ssh` | paramiko | Remote SSH/SFTP workspace support |
| `openai` | langchain-openai | OpenAI provider |
| `anthropic` | langchain-anthropic | Anthropic provider |
| `google` | langchain-google-genai | Google provider |
| `azure` | langchain-openai | Azure OpenAI provider |
| `embeddings` | numpy, onnxruntime, transformers, sentence-transformers | Local ONNX embeddings |
| `tracing` | langsmith | LangSmith tracing |
| `dev` | pytest, pytest-cov, ruff, pyright | Development tools |
| `all` | all of the above | Full installation |

### Make `devx` command available on any directory

Installing `devx` creates a console executable (`devx.exe` on Windows, `devx` on Linux/macOS) in your Python environment's `Scripts` or `bin` directory.

- **Windows PowerShell**: Verify the command resolves globally:
  ```powershell
  where devx
  ```
  If not found, ensure Python's Scripts folder is in your User `PATH`:
  ```powershell
  $scriptsPath = "$env:LOCALAPPDATA\Programs\Python\Python313\Scripts"
  $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
  if (($currentPath -split ";") -notcontains $scriptsPath) {
      [Environment]::SetEnvironmentVariable("Path", "$currentPath;$scriptsPath", "User")
  }
  ```
- **Linux & macOS**: Ensure `~/.local/bin` or your active venv's `bin/` directory is in `$PATH`:
  ```bash
  export PATH="$HOME/.local/bin:$PATH"
  which devx
  ```

### Building a standalone wheel

To produce a production wheel artifact:
```bash
python -m pip install build
python -m build
# Installs from dist/
pip install "dist/devx_coding_agent-0.2.0.dev0-py3-none-any.whl[all]"
```

---

## Configuration

`devx` loads configuration from environment variables, a workspace `.env`, and **`~/.devx/config.env`** (or legacy `~/.dev/config.env`). The precedence order is: environment variables > workspace `.env` > user config > built-in defaults.

### Environment Variables

Both `DEVX_*` and legacy `DEV_*` variable names are supported, with `DEVX_*` taking precedence.

| Variable | Default | Description |
|---|---|---|
| `DEVX_BASE_URL` | `http://localhost:4000/v1` | OpenAI-compatible API endpoint |
| `DEVX_API_KEY` | `sk-placeholder` | API key for the LLM provider |
| `DEVX_MODEL` | `gpt-4o` | Model name to use |
| `DEVX_PROVIDER` | `openai` | Provider: `openai`, `anthropic`, `google`, `azure` |
| `DEVX_WORKSPACE` | `cwd` (current working directory) | Workspace root directory |
| `DEVX_REMOTE_SSH` | `""` | Remote workspace SSH target (`user@host:/path` or `ssh://...`) |
| `DEVX_SSH_KEY` | `""` | Path to private SSH key for remote authentication |
| `DEVX_SSH_KNOWN_HOSTS` | `""` | Path to an SSH `known_hosts` file used for host-key verification |
| `DEVX_SSH_ALLOW_UNKNOWN_KEYS` | `false` | Auto-add a new SSH host key on first connect (throwaway/lab targets only) |
| `DEVX_CONTAINER` | `""` | Default container workspace URI (`container://name/path`) |
| `DEVX_SECRET_SCAN` | `warn` | Secret scan on agent reads and writes: `off`, `warn`, `redact`, or `block` |
| `DEVX_POLICY_PRESET` | `""` | Policy preset: `remote_only`, `deny_local_writes`, `protected`, or `strict` |
| `DEVX_SESSION_DB` | `~/.devx/sessions.db` | SQLite session database path |
| `DEVX_CACHE_DB` | `~/.devx/cache.db` | Semantic cache database path |
| `DEVX_APPROVAL_REQUIRED` | `true` | Require approval for file writes and commands |
| `DEVX_MAX_ITERATIONS` | `8` | Maximum agent loop iterations per run |
| `DEVX_MAX_TOOL_CALLS` | `40` | Maximum tool calls per run |
| `DEVX_COMMAND_TIMEOUT` | `120` | Shell command timeout in seconds |
| `DEVX_TEST_COMMAND` | `""` (auto-detect) | Custom test command for tester agent |
| `DEVX_MAX_FILE_BYTES` | `1000000` | Maximum file size to read (bytes) |
| `DEVX_MAX_CONTEXT_FILES` | `5` | Maximum files selected for each run; lexical selection is used when embeddings are disabled |
| `DEVX_LINT_COMMAND` | `""` | Optional lint command run before tests |
| `DEVX_EMBEDDINGS_ENABLED` | `false` | Enable local ONNX embeddings for token optimization |
| `DEVX_EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Local embedding model identifier |
| `DEVX_EMBEDDING_CACHE_DIR` | `~/.cache/devx/embeddings` | Local embedding model cache directory |
| `DEVX_EMBEDDINGS_OFFLINE` | `false` | Prevent embedding model downloads |
| `LANGSMITH_TRACING` | `false` | Enable LangSmith tracing |

### Example `~/.devx/config.env`

```env
DEVX_BASE_URL=https://api.openai.com/v1
DEVX_API_KEY=sk-...
DEVX_MODEL=gpt-4o
DEVX_PROVIDER=openai
DEVX_WORKSPACE=C:/Projects/my-project
DEVX_APPROVAL_REQUIRED=true
DEVX_TEST_COMMAND=pytest
DEVX_EMBEDDINGS_ENABLED=true
```

> **Note:** Quoted values are supported in `config.env`.

### Project permissions, plugins, MCP, and compaction

Optional non-secret project settings live in `.devx/config.json`. The file is
explicitly project-scoped; plugin imports and MCP server processes only start
when configured.

```json
{
  "permissions": [
    { "action": "read", "resource": "**", "effect": "allow" },
    { "action": "edit", "resource": "tests/**", "effect": "allow" },
    { "action": "shell", "resource": "git diff *", "effect": "allow" },
    { "action": "shell", "resource": "rm *", "effect": "deny" }
  ],
  "plugins": [".devx/plugins"],
  "mcp_servers": [
    {
      "name": "local-tools",
      "command": ["python", ".devx/mcp_server.py"],
      "timeout": 30
    }
  ],
  "context_compaction": {
    "enabled": true,
    "max_messages": 24,
    "max_chars": 24000,
    "summary_chars": 4000
  },
  "policy_preset": "strict"
}
```

Rules are evaluated in order and the last matching rule wins. `deny` always
wins over approval callbacks and the existing destructive-command checks remain
active. A Python plugin exports `register(registry)` and registers ordinary
typed Python functions. MCP servers use the stdio transport and expose tools
from `tools/list`; every plugin and MCP call still passes through the approval
policy and per-run tool-call budget.

---

## Quick Start

1. **Install:**
   ```bash
   pip install -e ".[agent,tui,cli]"
   ```

2. **Initialize your workspace:**
   ```bash
   devx init
   ```
   *Opens a full-screen TUI wizard to configure your provider, API key, and curated tool-calling model — or script it with `--non-interactive` for CI.*

3. **Run diagnostic checks:**
   ```bash
   devx doctor
   ```

4. **Start coding:**
   ```bash
   # Interactive TUI
   devx tui

   # One-shot CLI
   devx ask "inspect @src/app.py"
   ```

---

## CLI Usage

The `devx` CLI exposes the following commands:

### `devx init`

Interactive or scripted project initialization wizard. Run with no flags on a
terminal to open the full-screen TUI setup wizard; pass flags (or
`--non-interactive`) to skip the wizard and configure directly.

```bash
# Full-screen TUI wizard (provider, model, API key)
devx init

# Scripted non-interactive onboarding
devx init --provider openai --model gpt-4o --api-key "$API_KEY" --non-interactive
```

| Option | Description |
|---|---|
| `-p, --provider` | Provider: `openai`, `anthropic`, `google`, `azure`, `ollama`, `openrouter` |
| `-m, --model` | Target model identifier |
| `-k, --api-key` | Provider API key |
| `-u, --base-url` | Custom base URL endpoint |
| `-e, --embeddings` | Enable local ONNX embeddings for semantic search |
| `-t, --test-cmd` | Test command (auto-detected if omitted) |
| `-l, --lint-cmd` | Optional linter command |
| `-y, --non-interactive` | Skip prompts and use defaults |

### `devx ask`

Run a single agent task.

```bash
devx ask "explain @src/main.py"
devx ask --session my-project "fix the bug in @src/utils.py"
devx ask --remote "user@remote-host:/path/to/project" "run tests and fix failures"
devx ask --container "container://api-box/app" "run the suite"
devx ask --approve-all "run tests and fix failures"   # trusted workspaces only
devx ask --plan-only "design the authentication refactor"  # plan without edits
```

| Option | Description |
|---|---|
| `--session NAME` | Resume or create a named session |
| `-r, --remote URI` | Remote workspace SSH target (e.g. `user@host:/path` or `ssh://...`) |
| `--ssh-key PATH` | Path to private SSH key for remote authentication |
| `--container URI` | Container workspace target (`container://name/path` or `docker://name/path`) |
| `--plan-first / --no-plan-first` | Prompt for user confirmation on generated plan before coding (enabled by default) |
| `--plan-only` | Create the plan and stop before coding, testing, or mutations |
| `--approve-all` | Auto-approve all writes and commands (use only in trusted, isolated workspaces) |
| `--approval-cache` | Reuse approval decisions within a single run |
| `--approval-matching` | Auto-approve actions matching project permission rules |
| `--json` | Emit the complete run state as JSON for automation |
| `--json-logs` | Output structured JSON logs with correlation IDs |

### `devx tui`

Launch the interactive Textual TUI.

```bash
devx tui
```

### `devx doctor`

Run a diagnostic check to verify configuration, provider connectivity, and dependencies.

```bash
devx doctor
devx doctor --remote "deploy@bastion:/srv/app"   # preflight a remote workspace
devx doctor --remote "deploy@bastion:/srv/app" --json
```

With `--remote`, `devx` validates connection, authentication, the remote path,
writability, `git`, and the virtualenv before you start a run.

### `devx usage`

Show recorded token usage and estimated cost, priced per model.

```bash
devx usage
devx usage --session my-session --json
devx usage --run 4f2a1c --markdown      # per-run receipt
```

### `devx audit export`

Export a portable audit trail (sessions, events, changes, DLQ, usage).

```bash
devx audit export --format jsonl --out audit.jsonl
devx audit export --session my-session --format json
```

### `devx report`

Render a PR-ready summary of a session or run: the agent timeline, checked-in
file changes with before/after hashes, lint/test results, token cost, and a
suggested PR description.

```bash
devx report my-session                # Markdown to stdout
devx report 4f2a1c --out report.md    # a run id, written to a file
devx report my-session --json         # machine-readable
```

### `devx verify`

Compare the workspace against the recorded change journal. Each recorded
change is reported `intact`, `reverted`, `conflict`, or `missing` by comparing
current file hashes; exits `1` when anything is in conflict or missing.

```bash
devx verify
devx verify --run 4f2a1c
devx verify --remote "deploy@bastion:/srv/app"
devx verify --json
```

### `devx scan-secrets`

Scan files for potential secrets (private keys, AWS/GitHub/OpenAI tokens,
credential assignments). Exits `1` when anything is found, so it can gate CI.

```bash
devx scan-secrets .               # whole tree (skips .git, node_modules, ...)
devx scan-secrets .env            # a single file
devx scan-secrets . --json
```

By default the agent also scans every file it reads **and writes**. Set
`DEVX_SECRET_SCAN=warn|redact|block|off` (default `warn`) to control whether
secrets are warned about, masked out of the model context, refused, or
ignored. In `block` mode a write whose content contains a credential is refused
too — a secret can't be persisted to disk, not just kept out of the model
context.

### `devx lab`

Boot or tear down a disposable remote workspace container so you can try
governed remote execution in about a minute — no server setup needed.

```bash
devx lab up
devx lab down devx-lab-a1b2c3d4
```

`lab up` prints ready-to-paste `--remote` and `--container` URIs backed by the
same container image the SSH end-to-end test suite uses. Each lab container gets
a fresh host key, so the printed SSH one-liner includes `--accept-new-keys` to
auto-trust that one-time throwaway key (real servers should add the key to a
`known_hosts` file and set `DEVX_SSH_KNOWN_HOSTS` instead). For a declarative,
inspectable alternative, `docker-compose.yml` in the repo root boots the same
workspace (`docker compose up -d`).

### `devx changes`

Review recorded file changes and accept or reject them one at a time.

```bash
devx changes list
devx changes list --run 4f2a1c --json
devx changes accept src/parser.py --run 4f2a1c
devx changes reject src/legacy.py --run 4f2a1c --remote "deploy@bastion:/srv/app"
```

`reject` is hash-guarded: it reverts a file only if it still matches the run's
output, otherwise it exits non-zero and leaves the file untouched.

### `devx health`

Run comprehensive component health probes.

```bash
devx health
devx health --json
```

### `devx index`

Build or update the local semantic search index for workspace files.

```bash
devx index             # Index workspace
devx index --watch     # Watch filesystem and re-index on save
devx index --force     # Force complete re-index
```

### `devx metrics`

Export agent, tool, and runtime metrics in Prometheus or JSON format.

```bash
devx metrics --format prometheus
devx metrics --format json
```

### `devx sessions`

List all sessions.

```bash
devx sessions
```

### `devx session list`

Alias for `devx sessions`.

### `devx session events <ID>`

Show event history for a specific session.

```bash
devx session events abc123
```

### `devx session delete <ID>`

Delete a session and its events.

```bash
devx session delete abc123
```

### `devx session dlq`

Inspect and manage failed tasks in the Dead Letter Queue (DLQ).

```bash
devx session dlq                   # List all DLQ entries
devx session dlq-retry <ENTRY_ID>  # Retry a failed task
devx session dlq-remove <ENTRY_ID> # Remove a DLQ entry
```

### `devx team`

Manage the optional, local-first team workspace (prototype). Team state is a
portable `.devx/team.json` manifest plus a shared SQLite session database. No
server component is required. See [Team layer](docs/team-layer.md).

```bash
devx team init <NAME>       # Create the team manifest
devx team invite <MEMBER>   # Register a member id
devx team status            # Team info + usage counters
devx team list-sessions     # List sessions in the shared database
```

---

## TUI Usage

Launch the TUI with `devx tui`.

### Features

- **Chat interface** — Conversational interaction with the coding agent
- **@ file mentions** — Type `@` to reference files or directories; fuzzy completion is provided
- **Slash commands** — Control sessions and agent behavior (see below)
- **Approval prompts** — Inline approval requests for writes and commands
- **Approval previews** — Bounded proposed diffs are shown before file mutations
- **Session management** — Resume, create, and delete sessions from the sidebar
- **Diff and recovery** — Inspect the current Git diff with `/diff` and undo the last run with `/undo`
- **Cancellation** — Stop an active run with `/cancel` or `Ctrl+C`; press `Ctrl+C` twice to quit the TUI

---

## Slash Commands

| Command | Description |
|---|---|
| `/diff` | Show the current bounded Git diff without changing files |
| `/undo` (or `/rollback`) | Undo the last run's approved file changes when files are unchanged afterward |
| `/plan <task>` | Create a plan without editing or running project commands |
| `/cancel` | Request cancellation of the active run |
| `/copy` | Copy the selected or full transcript |
| `/setup`, `/provider`, `/model` | Configure provider and model settings |
| `/session ...` | List, switch, rename, export, or import sessions |
| `/remote connect <uri>` | Connect the workspace to a remote host via SSH/SFTP |
| `/remote disconnect` | Disconnect remote SSH workspace and revert to local directory |
| `/clear` | Clear the current conversation |
| `/help` | Show help for available commands |

> **Note:** The command set is minimal by design. Additional commands may be added in future releases.

---

## Supported Providers

`devx` supports any OpenAI-compatible endpoint or native provider:

| Provider | Configuration |
|---|---|
| **OpenAI** | `DEVX_PROVIDER=openai`, `DEVX_BASE_URL=https://api.openai.com/v1` |
| **Anthropic** | `DEVX_PROVIDER=anthropic`, `DEVX_BASE_URL=https://api.anthropic.com` |
| **Google** | `DEVX_PROVIDER=google` |
| **Azure OpenAI** | `DEVX_PROVIDER=azure`, `DEVX_BASE_URL=https://<resource>.openai.azure.com` |
| **OpenRouter** | `DEVX_PROVIDER=openrouter`, `DEVX_BASE_URL=https://openrouter.ai/api/v1` (supports curated models & `openrouter/free`) |
| **LiteLLM** | `DEVX_PROVIDER=openai`, `DEVX_BASE_URL=http://localhost:4000/v1` |
| **Ollama** | `DEVX_PROVIDER=openai`, `DEVX_BASE_URL=http://localhost:11434/v1` |
| **vLLM / any compatible** | `DEVX_PROVIDER=openai`, `DEVX_BASE_URL=<your-endpoint>` |

Tool calling support is required for the agent harness to function correctly.

---

## Security

By default, `devx` is designed with a security-first approach:

- **Approval required** — All file writes and shell commands require explicit user approval (`DEVX_APPROVAL_REQUIRED=true` by default)
- **Workspace sandboxing** — All file operations are resolved relative to the configured workspace; paths outside the workspace are rejected
- **Atomic writes** — File writes are atomic with automatic backups and before/after hashes
- **No auto-commits** — The agent never commits Git changes automatically; inspect diffs and commit yourself
- **Bounded shell execution** — Commands run with `shell=False`, bounded output, configurable timeouts, and process cleanup
- **Extension approval** — Workspace plugins and MCP tools require approval by default and remain subject to tool-call budgets
- **Secret redaction** — API keys and tokens are redacted from logs and traces
- **No auto-approve** — `--approve-all` is explicitly opt-in and intended only for trusted, isolated workspaces
- **Governed remote execution** — Remote (SSH) and container runs use the same approvals, sandbox, and hash-guarded undo as local runs; remote commands run under `setsid` and are killed as a process group on cancel/timeout; dropped connections fail closed with a clear error
- **Policy presets** — `remote_only`, `deny_local_writes`, and `strict` enforce hard constraints that apply even to approved actions
- **Auditable by default** — Every change is journaled with before/after hashes and can be exported with `devx audit export`

For the full model, see [Threat model](docs/threat-model.md) and
[Governed remote execution](docs/governed-remote.md). For reporting, see
[SECURITY.md](SECURITY.md).

---

## Development

### Clone the repository

```bash
git clone https://github.com/your-org/devx-coding-agent.git
cd devx
```

### Install in development mode

```bash
pip install -e ".[agent,tui,cli,dev]"
```

### Run the TUI

```bash
devx tui
```

### Run the CLI

```bash
devx ask "hello world"
```

---

## Testing

Run the test suite with pytest:

```bash
python -m pytest -q
```

Run the provider-contract checks separately when validating tool-calling
compatibility:

```bash
python -m pytest -q tests/test_provider_contract.py
```

The offline benchmark suite is also part of the validation workflow. It measures
the bounded-output, command-safety, rollback, and context-budget primitives
without contacting a model. See [Benchmark methodology](docs/benchmark-methodology.md).

```bash
python scripts/benchmark.py
```

The test configuration is defined in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--strict-markers"
pythonpath = ["src", "."]
```

### Lint and format

```bash
ruff check src/
```

### Type check

```bash
pyright
```

### CI/CD

The project uses GitHub Actions for continuous integration and automated PyPI releases:

- `.github/workflows/ci.yml` — Runs tests, lint, compilation, benchmarks, and security checks on every push and pull request
- `.github/workflows/release.yml` — Automates PyPI releases on version tags: builds wheel/sdist, signs with Sigstore, publishes via OIDC trusted publishing, and emits an SBOM and changelog (see [docs/gitflow.md](docs/gitflow.md) for the cut-a-release walkthrough)
- `.github/workflows/version.yml` — GitFlow semantic versioning on `develop`, `release/*`, and `hotfix/*` branches: commits the version bump and creates the `vX.Y.Z` tag at the bump commit that triggers the release

---

## Roadmap

See [docs/roadmap.md](docs/roadmap.md) for the full roadmap.

Current priorities:

- Multi-file refactoring support
- Smarter project-wide context indexing and retrieval
- Extension system (custom tools, custom agents)
- Web-based dashboard for session history and diffs

---

## Contributing

Contributions are welcome. Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Ensure tests pass (`python -m pytest -q`) and linting passes (`python -m ruff check src tests scripts`)
5. Submit a pull request

---

## License

Apache License 2.0. See [LICENSE](LICENSE) for the full text.

---

## Acknowledgments

Built with [LangChain](https://python.langchain.com/), [LangGraph](https://langchain-ai.github.io/langgraph/), [Textual](https://textual.textualize.io/), and [Typer](https://typer.tiangolo.com/).
