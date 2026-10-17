# `devx` User Guide

Welcome to **`devx`**, your local-first, approval-gated AI coding partner.

`devx` pairs you with an autonomous coding agent that respects your code and your authority. It delivers a fast, terminal-native "vibe coding" experience — expressing intent, reviewing implementation plans, inspecting unified diffs, and keeping 100% control over every change.

---

## Complete Multi-Page Guide Chapters

Explore the comprehensive topic guides in the [`docs/user-guide/`](user-guide/) suite:

```mermaid
graph TD
    PORTAL[devx User Guide Portal]
    
    PORTAL --> CH1["01. Getting Started"]
    PORTAL --> CH2["02. Core Concepts"]
    PORTAL --> CH3["03. CLI Reference"]
    PORTAL --> CH4["04. TUI Guide"]
    PORTAL --> CH5["05. Workspace Backends"]
    PORTAL --> CH6["06. Governance & Safety"]
    PORTAL --> CH7["07. Token Optimization"]
    PORTAL --> CH8["08. Extensibility & MCP"]
    PORTAL --> CH9["09. Observability & Operations"]
```

| Chapter | Overview |
|---|---|
| [**1. Getting Started**](user-guide/01-getting-started.md) | Prerequisites, packaging extras, global PATH setup, interactive init wizard, diagnostics, and first task |
| [**2. Core Concepts**](user-guide/02-core-concepts.md) | Supervisor state machine (Planner, Coder, Parallel Coder, Tester, Repair Loop), approval gates, sessions, and knowledge graph |
| [**3. CLI Reference**](user-guide/03-cli-reference.md) | Exhaustive documentation of all 17 CLI commands and command-line flags |
| [**4. TUI Workflow Guide**](user-guide/04-tui-guide.md) | Full layout breakdown, modal dialogues, all slash commands, shortcuts, and layered clipboard mechanisms |
| [**5. Workspace Backends**](user-guide/05-workspace-backends.md) | Governed execution across Local, SSH (`--remote`), Docker Containers (`--container`), and throwaway Lab (`devx lab`) |
| [**6. Governance, Safety & Secret Hygiene**](user-guide/06-governance-and-safety.md) | Automated secret scanning/redaction, policy presets (`strict`, `protected`), granular permissions, and change journal rollbacks |
| [**7. Token Optimization & Caching**](user-guide/07-token-optimization.md) | Local ONNX INT8 embeddings, semantic caching, conversation compaction, decoupled context, and diagnostic distillation |
| [**8. Extensibility, Plugins & MCP**](user-guide/08-extensibility-and-mcp.md) | Writing Python workspace plugins (`.devx/plugins/`), stdio MCP servers, custom tool registration, and safety boundaries |
| [**9. Observability, Operations & Team**](user-guide/09-observability-and-ops.md) | Prometheus metrics exposition, LangSmith tracing, DLQ triage & replay, circuit breakers, cost receipts, and team manifests |

---

## 5-Minute Quickstart

### Step 1: Install `devx`

In your Python 3.11+ environment:

```bash
# Clone the repository
git clone https://github.com/cosq-network/devx.git
cd devx

# Install with all runtime extras (CLI, TUI, Agent, SSH, Embeddings):
pip install -e ".[all]"
```

### Step 2: Initialize Your Workspace

Run the initialization wizard in your project root:

```bash
cd /path/to/your/project
devx init
```

The full-screen TUI wizard will guide you through picking your LLM provider (OpenRouter, OpenAI, Anthropic, Google, Azure, Ollama), selecting an agent-ready coding model, entering your API key, and toggling local embeddings.

*For automated CI / container setups, use non-interactive mode:*
```bash
devx init --provider openrouter --model openrouter/free --api-key "$API_KEY" -y
```

### Step 3: Verify Environment Health

```bash
devx doctor
devx health
```

Ensure configuration, session database, and extras are marked healthy (`[ok]`).

### Step 4: Run Your First Task

```bash
# In the CLI:
devx ask "explain how authentication works in @src/auth.py"

# Or launch the interactive terminal UI:
devx tui
```

---

## Quick Reference Cheat Sheet

### Common CLI Tasks

```bash
# Plan-only mode (preview plan without making any changes)
devx ask --plan-only "architect database migration to PostgreSQL"

# Skip the initial plan confirmation prompt
devx ask --no-plan-first "fix typo in @README.md"

# Auto-approve all edits (use only in isolated/trusted containers)
devx ask --approve-all "run pytest and format all files"

# Continue a named session
devx ask --session feature-auth "add rate limiting to the token endpoint"

# Remote server development over SSH
devx ask "fix healthcheck in @app.py" --remote dev-box:/srv/app

# Docker container development (zero bind mounts required)
devx ask "run pytest" --container container://devx-lab-8f12a3bc/config/repo

# Revert a file touched in the last run
devx changes reject src/auth.py

# Verify workspace drift against change journal
devx verify

# Pre-commit secret sweep
devx scan-secrets src/

# PR summary and token cost receipt
devx report my-session --out pr-body.md
devx usage --run 4f2a1c --markdown
```

### Common TUI Shortcuts & Commands

| Key / Command | Action |
|---|---|
| `/setup` | Configure provider, coding model, and API key |
| `/diff` | Display the current bounded Git diff |
| `/undo` | Roll back all approved changes from the previous run |
| `/plan <task>` | Create an implementation plan without modifying files |
| `/cancel` | Cancel an active agent run immediately |
| `/session` | List or switch active sessions |
| `/remote connect <uri>` | Switch to a remote SSH workspace |
| **Ctrl+C** *(single)* | Cancel active run / copy transcript when idle |
| **Ctrl+C** *(twice)* | Quit the TUI |
| **Ctrl+Q** | Quit the TUI with remote backend cleanup |
| **Ctrl+L** | Clear transcript view |
| **F1** | Open Help panel |
| **F2** / **Ctrl+Insert** | Copy transcript to system clipboard |

---

## Additional References

- [Architecture Guide](architecture.md) — Deep dive into state graphs, process groups, and serialization formats.
- [Threat Model](threat-model.md) — Attack surfaces, threat actors, and containment boundaries.
- [Governed Remote Execution](governed-remote.md) — Full security guarantees for SSH and container backends.
- [Extensibility Guide](extensibility.md) — In-depth guide to writing workspace plugins and MCP integrations.
