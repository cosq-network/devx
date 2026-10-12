# `devx` User Guide

Welcome to **`devx`**, your local-first, approval-gated AI coding partner.

This guide is designed for developers who work in the terminal, VS Code, and Git every day. It focuses on the **vibe coding workflow** — expressing your high-level intent, letting the agent plan and inspect the details, reviewing atomic diffs, and keeping 100% control over every change.

For reference documentation on architecture, internals, and provider schemas, refer to the [README](../README.md) and [Architecture Guide](architecture.md).

---

## Table of Contents

1. [What is devx?](#1-what-is-devx)
2. [5-Minute Setup](#2-5-minute-setup)
3. [Core Mental Model](#3-core-mental-model)
4. [Your First Vibe Session](#4-your-first-vibe-session)
5. [CLI Cheat Sheet](#5-cli-cheat-sheet)
6. [TUI Workflow Guide](#6-tui-workflow-guide)
7. [Common Workflows](#7-common-workflows)
8. [Configuration That Just Works](#8-configuration-that-just-works)
9. [Troubleshooting and Recovery](#9-troubleshooting-and-recovery)

---

## 1. What is devx?

`devx` pairs you with an autonomous coding agent that respects your code and your authority.

### The "Vibe Coding" Mental Model

Traditional AI coding assistants either force you to copy-paste code snippets from a chat window or aggressively edit files behind your back. `devx` adopts a balanced, approval-gated workflow:

```
    Describe Intent ──► Agent Plans ──► You Review Plan ──► Agent Previews Diff ──► You Approve ──► Tests Run
```

- **You drive intent**: "Refactor the authentication middleware to use JWT bearer tokens."
- **Agent plans first**: It reads files, designs a concrete plan, and presents it to you.
- **You stay in control**: Every file modification and shell command requires explicit confirmation (unless explicitly run with `--approve-all` in sandboxed environments).
- **Zero accidental damage**: Atomic writes, before/after checksums, automatic backups in SQLite, and one-command rollback (`/undo`).
- **No auto-commits**: `devx` never commits to Git automatically. You inspect diffs and commit when satisfied.

### Quick Comparison

| Capability | `devx` | GitHub Copilot / Codex | Claude Code |
|---|---|---|---|
| **Primary UX** | Interactive Textual TUI & Typer CLI | IDE extension | Terminal CLI |
| **Model choice** | Any provider (OpenAI, Anthropic, Google, Azure, Ollama, OpenRouter) | OpenAI / Microsoft models | Anthropic Claude |
| **Approval model** | Explicit approval gate for all writes & commands | Inline ghost text / prompt chat | Terminal prompts |
| **Rollback & undo** | Journaled file backups (`/undo` restores exact state) | Standard editor undo | Manual git revert |
| **Local token optimization** | Client-side ONNX embeddings + local history compaction | Cloud-managed | Cloud-managed |
| **Extensibility** | Workspace Python plugins + stdio MCP tools | Extensions API | MCP support |

---

## 2. 5-Minute Setup

### Step 1: Install `devx`

`devx` is packaged using standard **PEP 517 / PEP 621** conventions via [`pyproject.toml`](../pyproject.toml). Rather than maintaining a separate `requirements.txt`, all core dependencies, optional feature extras, and command-line entry points are declared directly in `pyproject.toml`.

#### Install in Editable Mode (Recommended for Development)

In your Python 3.11+ environment, install with your desired extras:

```bash
# Recommended for full TUI + CLI + Agent + SSH features
pip install -e ".[agent,tui,cli,ssh,dev]"

# Or install all optional dependencies:
pip install -e ".[all]"
```

> **Why editable (`-e`)?**  
> An editable install links the generated `devx` launcher directly to your working copy. Any updates to code in `src/devx` are instantly active without needing to reinstall.

#### Making `devx` Accessible from Any Directory

Installing `devx` puts the executable in your Python environment's `Scripts` (Windows) or `bin` (macOS/Linux) folder.

- **Windows PowerShell**: Verify the command resolves globally:
  ```powershell
  where devx
  ```
  If `where devx` fails, append your Python Scripts directory to your User `PATH`:
  ```powershell
  $scripts = "$env:LOCALAPPDATA\Programs\Python\Python313\Scripts"
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  if (($userPath -split ";") -notcontains $scripts) {
      [Environment]::SetEnvironmentVariable("Path", "$userPath;$scripts", "User")
  }
  ```
- **macOS / Linux**: Ensure `~/.local/bin` or your venv's `bin/` directory is in `$PATH`:
  ```bash
  export PATH="$HOME/.local/bin:$PATH"
  which devx
  ```

#### Building a Distributable Wheel (Optional)

To package `devx` as a wheel:
```bash
python -m pip install build
python -m build
pip install "dist/devx_coding_agent-0.2.0.dev0-py3-none-any.whl[all]"
```

### Step 2: Initialize Your Project

Run the initialization wizard in your project root:

```bash
devx init
```

The interactive wizard will:
1. Detect your project structure and suggest appropriate test commands (e.g. `pytest`, `npm test`, `cargo test`).
2. Help you select your LLM provider (OpenAI, Anthropic, Google Gemini, Azure, Ollama, OpenRouter).
3. Let you pick a curated model with tool-calling capabilities.
4. Save configuration cleanly into `.devx/config.json` (project settings) and `.env` (credentials).

*Tip: For automated setups in CI or Docker scripts, use non-interactive mode:*

```bash
# With OpenAI:
devx init --provider openai --model gpt-4o --api-key "$OPENAI_API_KEY" --non-interactive

# Or with OpenRouter's 200k-context free router model:
devx init --provider openrouter --model openrouter/free --api-key "$OPENROUTER_API_KEY" --non-interactive
```

### Step 3: Run Diagnostic Checks

Verify your setup with `devx doctor` and `devx health`:

```bash
devx doctor
devx health
```

You should see all checks marked healthy (`✓`) for configuration, session database, and optional packages.

### Step 4: Your First Task

Run a quick inspection:

```bash
# Ask a question referencing a project file
devx ask "explain @README.md"

# Or launch the interactive terminal UI
devx tui
```

---

## 3. Core Mental Model

Understanding `devx` comes down to four concepts:

### 1. The Supervisor State Machine

Under the hood, `devx` executes a LangGraph supervisor graph:

```
       ┌──────────┐
       │  START   │
       └────┬─────┘
            ▼
       ┌──────────┐      Plan-only
       │ Planner  ├────────────────────► FINISH
       └────┬─────┘
            ▼ Approved
    ┌───────┴───────────────┐
    │                       │
    ▼ (single file)         ▼ (multi-file / batch)
┌───────┐             ┌────────────────┐
│ Coder │             │ Parallel Coder │
└───┬───┘             └───────┬────────┘
    │                         │
    └───────────┬─────────────┘
                ▼
          ┌──────────┐
          │  Tester  │◄────────────┐
          └─────┬────┘             │
                │ Tests fail       │ Repair loop
                ├──────────────────┘ (max 2 attempts)
                ▼ Tests pass / done
           ┌────────┐
           │ FINISH │
           └────────┘
```

- **Planner**: Gathers relevant context, queries the model, and creates a step-by-step strategy.
- **Coder**: Implements minimal changes. For tasks touching multiple files or using keywords like "multiple", "several", or "batch", `devx` automatically routes to **Parallel Coder**, which batch-reads files and generates coordinated diffs.
- **Tester**: Executes test and lint commands in parallel to verify the implementation. If tests fail, it enters a bounded repair loop (up to 2 repair attempts with revised code from the coder).

### 2. Approval Gates

Whenever the agent wants to touch your filesystem or execute a shell command, execution pauses. You see:
- Action type (`write`, `replace`, or `shell`)
- The target file or command
- A unified diff preview of the proposed changes

You decide whether to proceed (`y`) or decline (`n`). By default, all file writes and shell commands require approval (`DEVX_APPROVAL_REQUIRED=true` by default). You can use `--approve-all` only in trusted, isolated workspaces.

### 3. Persistent SQLite Sessions

Every session is identified by an ID and saved in `~/.devx/sessions.db`.
- Resume any session with `devx ask --session <id>` or `/session use <id>` in the TUI.
- View events with `devx session events <id>`.
- Export/import sessions to share debugging runs with teammates.

### 4. `@` File Mentions

Use `@path/to/file` in prompts to explicitly pull files into the agent's context:

```bash
devx ask "add a retry decorator to @src/api/client.py based on @src/utils/retry.py"
```

In the TUI, typing `@` opens fuzzy completion for paths within the workspace.

---

## 4. Your First Vibe Session

### Scenario A: Inspect and Understand

You just joined a repository and want a high-level walkthrough:

```bash
devx ask "give me a guided tour of the architecture starting with @src/devx/cli/app.py"
```

The agent reads the mentioned file, traces dependencies, and returns a concise, structured overview.

### Scenario B: Fix a Bug (Plan-First)

By default, CLI commands use **plan-first** mode:

```bash
devx ask "fix the off-by-one error in @src/parser.py when reading trailing newlines"
```

1. **Step 1 (Plan Preview)**: `devx` displays the generated plan and asks:
   ```text
   Proceed with this plan? [y/N]: y
   ```
2. **Step 2 (Diff Review)**: The coder generates a patch. You see:
   ```diff
   --- src/parser.py
   +++ src/parser.py
   @@ -42,3 +42,3 @@
   -    for i in range(len(lines) - 1):
   +    for i in range(len(lines)):
   ```
3. **Step 3 (Approval & Testing)**: You approve the diff; `devx` writes the file and runs tests to verify the fix.

### Scenario C: Refactor with Undo Safety

Need to restructure code but worried about breaking things?

```bash
# In the TUI:
> refactor @src/auth.py to use dependency injection for token verification
```

Review the plan, approve the edits, and check tests. If you dislike the result:

```text
> /undo
Restored src/auth.py from backup
```

The file returns immediately to its exact pre-run hash.

---

## 5. CLI Cheat Sheet

### `devx ask`

```bash
# Standard one-shot task (plan-first by default)
devx ask "refactor database connection pool in @src/db.py"

# Preview plan without writing any code or running commands
devx ask --plan-only "architect a migration to PostgreSQL"

# Skip the initial plan confirmation prompt
devx ask --no-plan-first "quick typo fix in @README.md"

# Auto-approve all operations (use only in isolated/trusted containers)
devx ask --approve-all "run lint and format all files"

# Continue a named session
devx ask --session feature-auth "now add rate limiting to login"

# Run against a remote server directory over SSH (direct URI)
devx ask "fix healthcheck in @app.py" --remote ssh://ubuntu@192.168.1.100/srv/app --ssh-key ~/.ssh/id_ed25519

# Run using ~/.ssh/config host alias (supports ProxyJump / bastion jump-hosts)
devx ask "run test suite and fix failing tests" --remote dev-box:/srv/app

# Preview remote changes in plan-only mode
devx ask --plan-only "architect payment webhook" --remote dev-box:/srv/app

# Structured JSON output for CI or scripting
devx ask --json "list all public API functions in @src/app.py"
```

### `devx init`

```bash
# Interactive setup
devx init

# Scripted non-interactive setup
devx init -p openai -m gpt-4o -k "$API_KEY" -t "pytest tests/" -l "ruff check" -y

# Zero-cost setup with OpenRouter free models router
devx init -p openrouter -m openrouter/free -k "$OPENROUTER_API_KEY" -y
```

### `devx session`

```bash
# List all saved sessions
devx session list

# View structured event log
devx session events <SESSION_ID>

# Export session to JSON file
devx session export <SESSION_ID> backup.json

# Import session from JSON file
devx session import backup.json

# Manage the Dead Letter Queue (DLQ) for failed runs
devx session dlq                  # View failed runs in DLQ
devx session dlq-retry <ENTRY_ID> # Retry a failed run
devx session dlq-remove <ENTRY_ID># Remove a DLQ entry
```

### `devx index`

Build local semantic search embeddings for your workspace:

```bash
# One-time indexing
devx index

# Continuous file-watcher mode (re-indexes on save)
devx index --watch

# Force full rebuild of the index
devx index --force
```

### `devx health` & `devx metrics`

```bash
# Component health checks
devx health
devx health --json

# Observability metrics export (Prometheus or JSON)
devx metrics --format prometheus
devx metrics --format json
```

---

## 6. TUI Workflow Guide

Start the interactive terminal UI with:

```bash
devx tui
# Or resume an existing session:
devx tui --session feature-auth
```

### Interface Layout

- **Top Bar**: Active provider, model, workspace path, and session status.
- **Main Transcript**: Streaming agent output, user queries, diffs, and test logs.
- **Bottom Bar**: Context status, progress indicators, and keyboard shortcuts.
- **Input Area**: Prompt input with `@` mention and `/` command autocompletion.

### Slash Commands

| Command | Description |
|---|---|
| `/setup` | Open provider, model, base URL, and API key configuration dialog |
| `/provider` | List or select active LLM provider (`/provider use anthropic`) |
| `/model` | Browse curated coding models or live provider models (`/model live`) |
| `/diff` | Display the current bounded Git diff |
| `/undo` | Roll back approved changes from the previous run |
| `/plan <task>` | Create a plan without modifying files or running commands |
| `/cancel` | Cancel an active agent run immediately |
| `/session` | List or switch active sessions (`/session use <id>`) |
| `/remote` | Connect or disconnect remote SSH workspaces (`/remote connect dev-box:/srv/app`) |
| `/copy` | Copy transcript text to clipboard |
| `/clear` | Clear the transcript screen (`Ctrl+L`) |
| `/config show` | Display active configuration with masked API keys |
| `/help` | Show command help or topic details (`/help shortcuts`) |

### Keyboard Shortcuts

- `F1` — Open Help panel
- `Ctrl+C` — Copy active selection or cancel a running task
- `Ctrl+L` — Clear transcript
- `F2` or `Ctrl+Insert` — Copy transcript to system clipboard
- `Right Arrow` — Accept autocomplete suggestion for `@` mentions or `/` commands

---

## 7. Common Workflows

### 1. Bug Hunting & Test-Driven Fixing

```text
> write a failing test for invalid email addresses in @tests/test_auth.py, then update @src/auth.py until all tests pass
```

1. Agent creates `test_invalid_email` in `tests/test_auth.py`.
2. Agent runs `pytest` and verifies it fails.
3. Agent patches `src/auth.py`.
4. Agent runs `pytest` again and confirms green.

### 2. Multi-File Refactoring

When tasks touch more than 3 files or mention "multiple/batch", `devx` uses **Parallel Coder**:

```text
> rename the UserProfile model to AccountProfile across @src/models.py, @src/views.py, and @tests/test_models.py
```

`devx` batch-reads all candidate files, plans the coordinated rename, generates unified diffs for each file, and runs the test suite.

### 3. Safe Exploration

```text
> /plan how should we implement webhook signature verification?
```

The agent inspects existing webhooks, suggests the cryptographic algorithm and error handling, and formats the plan without modifying any code.

### 4. Remote Server Development (SSH)

Work directly on remote cloud instances, staging servers, or development containers without leaving your local environment:

```bash
# 1. Direct connection over SSH
devx ask "profile slow queries in @api/handlers.py and run pytest" --remote ssh://deploy@staging-box:22/var/www/app --ssh-key ~/.ssh/id_rsa

# 2. Or using ~/.ssh/config aliases (supports ProxyJump, custom ports, bastion jump-hosts)
devx ask "patch celery retry policy in @tasks.py" --remote bastion-worker:/home/ubuntu/service

# 3. In the interactive TUI:
> /remote connect dev-server:/srv/app
Switched to remote SSH workspace: dev-server:/srv/app
> run pytest and fix failing tests in @tests/test_api.py
```

- **Client-Side Safety**: All diffs, approvals, and destructive command blockers continue to run locally before touching the remote network.
- **Remote Testing**: Runs remote test suites directly on the remote server with automatic `.venv` activation.
- **Rollback Safety**: Full `/undo` support to revert remote edits back to their exact pre-run state.

---

## 8. Configuration That Just Works

Configuration is resolved in order of precedence:
1. Environment variables (`DEVX_*` or legacy `DEV_*`)
2. Workspace `.env`
3. User global config (`~/.devx/config.env`)
4. Non-secret project config (`.devx/config.json`)
5. Built-in defaults

### Key Environment Variables

```env
# Provider and Model
DEVX_PROVIDER=openrouter          # openrouter, openai, anthropic, google, azure, ollama
DEVX_MODEL=openrouter/free        # 200k context window, free inference, tool calling
DEVX_API_KEY=sk-or-v1-...
DEVX_BASE_URL=https://openrouter.ai/api/v1

# Workflows & Bounds
DEVX_APPROVAL_REQUIRED=true       # Always keep true for safety
DEVX_TEST_COMMAND=pytest          # Auto-detected if omitted
DEVX_LINT_COMMAND=ruff check      # Optional linter run before/alongside tests
DEVX_MAX_ITERATIONS=8             # Maximum planner-coder-tester loops
DEVX_COMMAND_TIMEOUT=120          # Shell execution timeout in seconds

# Local Token Optimization
DEVX_EMBEDDINGS_ENABLED=true                # Enable local ONNX INT8 embeddings
DEVX_DIAGNOSTIC_DISTILLATION_ENABLED=true   # Distill test failures to cut repair-loop tokens (workload-dependent)
DEVX_DECOUPLED_CONTEXT_ENABLED=true         # Decouple planner/coder contexts to shrink initial prompts

# Remote SSH Workspace
DEVX_REMOTE_SSH=ssh://ubuntu@host/srv/app   # Remote workspace URI or ~/.ssh/config host alias
DEVX_SSH_KEY=~/.ssh/id_ed25519              # Optional private key path override
```

### Project Rules (`.devx/config.json`)

Define granular permissions for commands and file access:

```json
{
  "permissions": [
    { "action": "read", "resource": "**", "effect": "allow" },
    { "action": "edit", "resource": "tests/**", "effect": "allow" },
    { "action": "shell", "resource": "git status", "effect": "allow" },
    { "action": "shell", "resource": "rm *", "effect": "deny" }
  ],
  "context_compaction": {
    "enabled": true,
    "max_messages": 24,
    "max_chars": 24000,
    "summary_chars": 4000
  }
}
```

---

## 9. Troubleshooting and Recovery

### 1. Diagnostic Checks

If a provider or command fails:

```bash
devx doctor
devx health
```

`devx health` checks:
- Session database read/write integrity
- Workspace path permissions
- LLM provider connectivity and latency
- Local embedding model status

### 2. Dead Letter Queue (DLQ)

If an agent run fails unexpectedly (e.g. network interruption or API rate limit):
1. The failed state is saved in the **Dead Letter Queue**.
2. Inspect the failure:
   ```bash
   devx session dlq
   ```
3. Retry the execution once the issue is resolved:
   ```bash
   devx session dlq-retry <ENTRY_ID>
   ```

### 3. Rate Limits and Circuit Breakers

`devx` includes an automated **Circuit Breaker** and exponential backoff retry policy for all LLM calls. If a provider returns `429 Rate Limit`:
- `devx` retries with jitter up to the configured retry count.
- If repeated failures occur, the circuit breaker opens to prevent runaway token costs and cascading failures.
- Switch models or providers easily via `/setup` in the TUI or by updating `DEVX_MODEL`.

### 4. Viewing Detailed Logs

For deep troubleshooting, pass `--json-logs` to output machine-readable structured logs with correlation IDs:

```bash
devx ask --json-logs "debug my task"
```
