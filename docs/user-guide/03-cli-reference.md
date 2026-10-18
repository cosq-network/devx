# CLI Reference for `devx`

`devx` provides a comprehensive command-line interface built with Typer. It supports both interactive developer workflows and headless automation for CI/CD runners.

---

## Command Overview

| Command | Summary |
|---|---|
| [`devx`](#1-devx-default-entrypoint) | Start the interactive TUI if no subcommand is given |
| [`devx ask`](#2-devx-ask) | Execute a coding or research task autonomously |
| [`devx init`](#3-devx-init) | Initialize project configuration and provider credentials |
| [`devx tui`](#4-devx-tui) | Explicitly launch the interactive Terminal User Interface |
| [`devx session`](#5-devx-session--devx-sessions) | Manage saved sessions, event logs, and the Dead Letter Queue |
| [`devx changes`](#6-devx-changes) | Inspect, accept, or reject journaled file changes |
| [`devx verify`](#7-devx-verify) | Verify workspace integrity and detect drift against journal hashes |
| [`devx scan-secrets`](#8-devx-scan-secrets) | Scan workspace or specific paths for leaked credentials |
| [`devx report`](#9-devx-report) | Generate PR-ready Markdown / JSON summary of a session or run |
| [`devx usage`](#10-devx-usage) | View itemized token consumption and estimated USD spend |
| [`devx audit`](#11-devx-audit) | Export audit trail containing events, changes, and approvals |
| [`devx doctor`](#12-devx-doctor) | Run local environment checks or remote SSH preflight diagnostics |
| [`devx health`](#13-devx-health) | Check component health, database integrity, and provider latencies |
| [`devx index`](#14-devx-index) | Build or watch local semantic search embeddings |
| [`devx metrics`](#15-devx-metrics) | Export Prometheus or JSON observability metrics |
| [`devx lab`](#16-devx-lab) | Spin up disposable remote workspace containers |
| [`devx team`](#17-devx-team) | Coordinate multi-agent team manifests and shared workspaces |
| [`devx review`](#18-devx-review) | Automated multi-perspective code review with local static analysis |
| [`devx design`](#19-devx-design) | Codebase topology, Mermaid diagrams, ADRs, and symbol research |
| [`devx test`](#20-devx-test) | Test-Impact Analysis (TIA), flakiness probing, and integration tests |

---

## 1. `devx` (Default Entrypoint)

Invoking `devx` without arguments in an interactive terminal automatically starts the full-screen Textual TUI.

```bash
devx
```

If stdin/stdout are redirected or piped in non-interactive environments, running bare `devx` displays the help banner.

---

## 2. `devx ask`

Execute an autonomous coding or analysis task.

```bash
devx ask [OPTIONS] PROMPT
```

### Arguments
- `PROMPT` *(required)*: The task description. May contain `@path/to/file` mentions.

### Options
- `--session <id|name>`: Continue an existing session or assign a custom session identifier.
- `-m, --mode <mode>`: Set operational mode: `code` (default), `architect`, `review`, `ask`.
- `--plan-only`: Run the Planner agent, display the implementation plan, and exit without modifying files or running commands.
- `--plan-first` / `--no-plan-first`: Prompt for plan confirmation before executing code changes. (Default: enabled for interactive terminals).
- `--approve-all`: Automatically approve all file writes and shell commands. **Use only in isolated/trusted containers.**
- `--remote <uri>`: Target a remote SSH workspace (e.g. `ssh://user@host/path` or `~/.ssh/config` alias).
- `--container <uri>`: Target an existing container workspace (e.g. `container://container_name/path`). Note: mutually exclusive with `--remote`.
- `--ssh-key <path>`: Path to custom private SSH key file.
- `--ssh-known-hosts <path>`: Custom `known_hosts` file path for strict host key verification.
- `--accept-new-keys`: Automatically accept and trust unseen host keys (useful for throwaway lab containers).
- `--json`: Output the final task result as machine-readable JSON.
- `--json-logs`: Output structured JSON logs with correlation IDs to stderr.

### Examples
```bash
# Standard refactoring task
devx ask "refactor database connection pool in @src/db.py"

# Preview architectural plan without modifying code
devx ask --plan-only "architect user notification microservice"

# Headless run in Docker container with auto-approvals
devx ask --approve-all --json "run pytest and format codebase"

# Remote execution on cloud server
devx ask "fix healthcheck in @app.py" --remote dev-box:/srv/app
```

---

## 3. `devx init`

Initialize configuration files in the current workspace.

```bash
devx init [OPTIONS]
```

### Options
- `-p, --provider <name>`: LLM provider (`openai`, `openrouter`, `anthropic`, `google`, `azure`, `ollama`).
- `-m, --model <id>`: Coding model identifier (e.g. `gpt-4o`, `claude-3-7-sonnet`, `openrouter/free`).
- `-k, --api-key <key>`: Provider API key.
- `-u, --base-url <url>`: Custom API base URL endpoint.
- `-e, --embeddings` / `--no-embeddings`: Enable local ONNX embeddings for semantic codebase indexing.
- `-y, --non-interactive`: Bypass the interactive TUI wizard and apply defaults or command flags directly.

### Examples
```bash
# Launch interactive TUI setup wizard
devx init

# Scripted non-interactive onboarding
devx init --provider openrouter --model openrouter/free --api-key "$OPENROUTER_API_KEY" -y
```

---

## 4. `devx tui`

Explicitly launch the interactive terminal user interface.

```bash
devx tui [--session <id>]
```

### Options
- `--session <id>`: Resume an existing session inside the TUI.

---

## 5. `devx session` / `devx sessions`

Manage sessions, event logs, and the Dead Letter Queue (DLQ).

```bash
# List all saved sessions
devx session list
devx sessions

# Fork current or target session into a new branch
devx session fork <SESSION_ID> [--name <BRANCH_NAME>]

# Visualize the conversation tree hierarchy
devx session tree

# Compact historical messages into a milestone summary to bound tokens
devx session compact <SESSION_ID>

# Rename an existing session
devx session rename <SESSION_ID> --name <NEW_NAME>

# Delete a session and its associated events
devx session delete <SESSION_ID>

# View structured timeline of events for a session
devx session events <SESSION_ID>

# Export session data to JSON file
devx session export <SESSION_ID> --path <BACKUP_FILE>

# Import session data from JSON file
devx session import --path <BACKUP_FILE> [--name <NAME>]

# Inspect failed runs in Dead Letter Queue
devx session dlq

# Retry a failed DLQ entry
devx session dlq-retry <ENTRY_ID>

# Discard a DLQ entry
devx session dlq-remove <ENTRY_ID>
```

---

## 6. `devx changes`

Inspect and govern file changes stored in the SQLite change journal.

```bash
devx changes [ACTION] [OPTIONS]
```

### Actions
- `list`: Show all files modified in the latest run (or specified `--run`).
- `diff [PATH]`: Show the unified diff for all changes or a specific file.
- `accept [PATH]`: Accept and confirm modified files.
- `reject [PATH]`: Revert a modified file back to its exact pre-run hash.
- `undo`: Revert all changes made during the latest run.
- `summary`: View a high-level table of modified files, lines added, and lines removed.

### Options
- `--run <id>`: Target a specific run ID instead of the most recent run.
- `--remote <uri>`: Target a remote SSH workspace.
- `--container <uri>`: Target a container workspace.

### Examples
```bash
# List what the agent touched in the last run
devx changes list

# Revert a single file without discarding other work
devx changes reject src/auth.py

# Roll back the entire run
devx changes undo
```

---

## 7. `devx verify`

Detect workspace drift by checking current file hashes against the change journal.

```bash
devx verify [OPTIONS]
```

### Options
- `--run <id>`: Restrict verification to a single run.
- `--remote <uri>`: Verify a remote SSH workspace.
- `--container <uri>`: Verify a container workspace.
- `--json`: Output machine-readable verification statuses.

### Exit Codes & Statuses
- `0`: All files are `intact` (matches post-run state) or `reverted` (cleanly rolled back).
- `1`: One or more files are in `conflict` (modified after the agent run) or `missing` (deleted). Ideal for CI gating.

---

## 8. `devx scan-secrets`

Scan files for leaked credentials, tokens, and private keys.

```bash
devx scan-secrets [PATH] [--json]
```

### Arguments & Options
- `PATH`: File or directory to scan (default: entire workspace).
- `--json`: Output findings as a JSON list.

### Behavior
Scans for OpenAI keys (`sk-...`), GitHub tokens (`ghp_...`), AWS access keys (`AKIA...`), Slack tokens, Anthropic keys, and PEM private keys. Exits `1` if any secrets are discovered.

---

## 9. `devx report`

Generate a consolidated PR-ready Markdown or JSON summary of a session or run.

```bash
devx report IDENTIFIER [OPTIONS]
```

### Arguments & Options
- `IDENTIFIER`: Session ID, session name, or run ID.
- `-o, --out <path>`: Write the Markdown report to a file.
- `--json`: Output report as JSON.
- `-r, --remote <uri>`: Remote SSH workspace URI for hash verification.
- `-c, --container <uri>`: Container workspace URI for hash verification.

### Output Content
- Executive goal and status.
- Agent event timeline.
- Files changed with before/after line counts.
- Test and linter results.
- Itemized token usage and cost calculation.
- Auto-generated PR description.

---

## 10. `devx usage`

Analyze token spend and USD pricing across sessions and individual runs.

```bash
devx usage [OPTIONS]
```

### Options
- `--session <id>`: Filter usage to a specific session.
- `--run <id>`: Filter usage to a specific run ID.
- `--markdown`: Render as an ASCII / Markdown usage receipt.
- `--json`: Output machine-readable usage records.

---

## 11. `devx audit`

Export a tamper-evident audit stream of all agent operations.

```bash
devx audit [OPTIONS]
```

### Options
- `--session <id>`: Restrict audit export to one session.
- `--format <json|jsonl>`: Output format (default: `json`).
- `--out <path>`: Write output to a file instead of stdout.
- `--verify`: Verify hash integrity of audited change entries.

---

## 12. `devx doctor`

Run comprehensive diagnostic checks on configuration, database integrity, and remote environments.

```bash
devx doctor [OPTIONS]
```

### Options
- `--remote <uri>`: Run an SSH preflight check (checks connectivity, auth, SFTP write permissions, git presence, and `.venv` status).
- `--ssh-key <path>`: SSH key for remote check.
- `--ssh-known-hosts <path>`: Known hosts file.
- `--accept-new-keys`: Auto-accept unseen host keys.
- `--json`: Output diagnostics as JSON.

---

## 13. `devx health`

Verify component health, database connectivity, and provider latencies.

```bash
devx health [OPTIONS]
```

### Options
- `--component <name>`: Filter check to `session_db`, `cache_db`, `workspace`, or `provider`.
- `--json`: Output health status as JSON.

---

## 14. `devx index`

Index codebase using local ONNX embeddings for semantic search.

```bash
devx index [OPTIONS]
```

### Options
- `--watch`: Run continuously in the background, re-indexing files on save.
- `--force`: Clear the semantic cache and re-index all project files from scratch.

---

## 15. `devx metrics`

Export operational metrics for monitoring and alerting.

```bash
devx metrics [OPTIONS]
```

### Options
- `--format <prometheus|json>`: Exposition format (default: `prometheus`).
- `--reset`: Reset metrics after exporting.

Exports counters for total agent runs, tool calls, failure counts, and Prometheus cumulative histograms for LLM request latency.

---

## 16. `devx lab`

Manage disposable, isolated Docker workspaces for safe agent execution.

```bash
devx lab up
devx lab down <CONTAINER_NAME>
```

- `devx lab up`: Boots a sandboxed container bound to `127.0.0.1` on a random port and prints ready-to-use `--remote` and `--container` connection commands.
- `devx lab down`: Stops and removes the disposable container.

---

## 17. `devx team`

Coordinate shared team workspaces using a local or network-mounted manifest (`.devx/team.json`).

```bash
# Initialize a team workspace
devx team init --name "Backend Engineering"

# View team workspace status and active members
devx team status

# Add a team member identifier
devx team add-member "alice@company.internal"

# Export team manifest
devx team export
```

---

## 18. `devx review`

Execute an automated multi-perspective code review with local static analysis pre-checks.

```bash
devx review [OPTIONS]
```

### Options
- `-s, --staged`: Review staged git changes only.
- `-b, --branch <name>`: Review changes between current branch and target branch (e.g. `main` or `origin/main`).
- `-c, --commit <hash>`: Review changes introduced in a specific git commit.
- `--save / --no-save`: Automatically persist review report to `.devx/reviews/` (Default: enabled).
- `--json`: Output report as machine-readable JSON.

### Examples
```bash
# Review current working tree diff
devx review

# Review staged changes
devx review --staged

# Review PR branch against main and emit JSON
devx review --branch main --json
```

---

## 19. `devx design`

Inspect repository topology, synthesize Mermaid architecture diagrams, create Architectural Decision Records (ADRs), or trace symbol call graphs.

```bash
devx design [OPTIONS]
```

### Options
- `-d, --diagram <kind>`: Synthesize Mermaid diagram: `flowchart` (default), `class`, or `sequence`.
- `-f, --focus <name>`: Focus diagram or analysis on a specific module or package name.
- `--adr <title>`: Create a new Architectural Decision Record in `.devx/design/ADR-XXXX-<title>.md`.
- `-e, --explore <symbol>`: Perform deep multi-hop research tracing definition, parameters, and all callers.

### Examples
```bash
# Generate architecture flowchart in Mermaid format
devx design --diagram flowchart

# Generate class hierarchy for specific module
devx design --diagram class --focus models

# Author a new ADR
devx design --adr "Adopt SQLite WAL Mode"

# Explore symbol references across files
devx design --explore "verify_token"
```

---

## 20. `devx test`

Run intelligent test selection, test flakiness and determinism probes, or integration tests.

```bash
devx test [ACTION] [TARGET] [OPTIONS]
```

### Arguments
- `ACTION`: Test action to run: `impact` (default), `flaky`, `unit`, `integration`.
- `TARGET`: Target test path, function name, or modified source file.

### Options
- `-r, --runs <n>`: Number of test execution passes for flaky check (Default: 3).

### Examples
```bash
# Find and run tests impacted by uncommitted git changes (TIA)
devx test impact

# Run tests impacted by a specific source file
devx test impact src/devx/rules.py

# Probe a test for flakiness across 5 runs with randomized seeds
devx test flaky tests/test_calc.py --runs 5

# Run integration tests
devx test integration
```

---

## Next Steps

- Learn how to interactively navigate `devx` in the [TUI Guide](04-tui-guide.md).
- Understand SSH, Container, and Lab workspaces in [Workspace Backends](05-workspace-backends.md).
- Learn about automated policy enforcement in [Governance and Safety](06-governance-and-safety.md).
