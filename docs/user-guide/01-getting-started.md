# Getting Started with `devx`

Welcome to **`devx`**, a local-first, approval-gated AI coding partner designed for developers who demand complete control over their codebase.

This guide walks you through installing `devx`, configuring your language model provider, validating your environment, and running your first task.

---

## 1. Prerequisites

Before installing `devx`, ensure your machine has:
- **Python**: Version 3.11, 3.12, or 3.13.
- **Git**: Installed and available on your system `PATH`.
- **Operating System**: Windows, macOS, or Linux.
- **Optional**:
  - **Docker**: Required only if you intend to use container workspaces (`--container`) or the throwaway container lab (`devx lab`).
  - **OpenSSH Client**: Required only for remote SSH execution (`--remote`).

Verify your Python installation:
```bash
python --version
# Expected: Python 3.11.x, 3.12.x, or 3.13.x
```

---

## 2. Installation

`devx` adheres strictly to standard **PEP 517 / PEP 621** packaging conventions via [`pyproject.toml`](../../pyproject.toml). All optional features and dependencies are declared as packaging extras.

### Installation Options by Extras

| Extra | Included Dependencies | Purpose |
|---|---|---|
| `cli` | `typer`, `rich` | Command-line interface commands (`devx ask`, `devx init`, etc.) |
| `tui` | `textual` | Interactive full-screen terminal user interface (`devx tui`) |
| `agent` | `langchain`, `langgraph`, `aiosqlite`, `langchain-openai` | Supervisor agent engine and state graph |
| `ssh` | `paramiko` | Remote execution and SFTP file operations |
| `embeddings` | `onnxruntime`, `tokenizers`, `sentence-transformers` | Local, offline ONNX INT8 semantic search embeddings |
| `tracing` | `langsmith` | LangSmith execution tracing and observability |
| `dev` | `pytest`, `ruff`, `pytest-cov`, `bandit` | Testing and development tools |
| `all` | *All runtime extras above* | Complete installation (excludes `dev`) |

### Development / Editable Mode (Recommended)

Installing in editable mode (`-e`) links the `devx` executable directly to your working repository. Any code modifications under `src/devx` take effect immediately without reinstalling:

```bash
# Clone the repository
git clone https://github.com/cosq-network/devx.git
cd devx

# Full runtime setup (CLI, TUI, Agent, SSH, Embeddings):
pip install -e ".[all]"

# Or include development tools:
pip install -e ".[all,dev]"
```

### Distributable Wheel Build

If you want to install `devx` into an isolated production or CI environment:
```bash
python -m pip install build
python -m build
pip install dist/*.whl
```

---

## 3. Global Executable Configuration

Installing `devx` places the command executable into your Python environment's binary directory.

### Windows (PowerShell)
Verify that `devx` resolves:
```powershell
where.exe devx
```
If `where.exe devx` returns an error, add your Python Scripts directory to your User `PATH`:
```powershell
$scripts = "$env:LOCALAPPDATA\Programs\Python\Python313\Scripts"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($userPath -split ";") -notcontains $scripts) {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$scripts", "User")
    Write-Host "Added $scripts to User PATH. Please restart your terminal."
}
```

### macOS and Linux (Bash/Zsh)
Ensure your user binary directory is in your `$PATH`:
```bash
which devx
# If not found:
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc  # or ~/.zshrc
```

---

## 4. Initializing Your Project

Run `devx init` inside the root directory of any repository or workspace:

```bash
cd /path/to/your/project
devx init
```

### Interactive TUI Wizard (Default)

Running `devx init` in an interactive terminal opens the full-screen centered setup wizard:

1. **Select Provider**: Choose from curated options:
   - **OpenRouter**: Access to top models, including free community models (`openrouter/free`).
   - **OpenAI**: GPT-4.1, GPT-4o, GPT-4o-mini, o3-mini.
   - **Anthropic**: Claude 3.5 Sonnet, Claude 3.7 Sonnet.
   - **Google**: Gemini 2.0 Flash, Gemini 1.5 Pro (no base URL required).
   - **Azure**: Azure OpenAI deployments.
   - **Ollama**: Free local models (`llama3.1:8b`, `qwen2.5-coder`).
2. **Select Coding Model**: Filtered to models specifically evaluated for code generation and tool calling.
3. **Configure API Key**: Masked input dialog (never displayed in plain text or echoed to transcript).
4. **Base URL**: Set automatically for standard providers, or customized for proxy servers / local Ollama (`http://localhost:11434/v1`).
5. **Local Embeddings**: Toggle local ONNX embeddings for semantic codebase search.

### Non-Interactive / Scripted Initialization

For automated container startup, devcontainers, or CI runners, pass `--non-interactive` (`-y`):

```bash
# OpenAI Setup
devx init --provider openai --model gpt-4o --api-key "$OPENAI_API_KEY" -y

# Zero-Cost Setup with OpenRouter's 200k Context Free Model
devx init --provider openrouter --model openrouter/free --api-key "$OPENROUTER_API_KEY" -y

# Local Ollama Setup (requires no API key)
devx init --provider ollama --model qwen2.5-coder --base-url http://localhost:11434/v1 -y
```

### What Gets Created?

Initialization creates two clean configuration files:
- **`workspace/.devx/config.json`**: Non-secret project settings, permission policies, and context compaction limits. Safe to commit to version control.
- **`workspace/.env`**: Secret credentials (`DEVX_API_KEY`), provider name, and model selection. **Must be added to `.gitignore`**.

---

## 5. Verifying Your Setup

Run diagnostic preflight checks before starting work:

```bash
devx doctor
```

`devx doctor` verifies:
- Workspace path resolution and write permissions.
- Database integrity for SQLite session storage (`~/.devx/sessions.db`) and semantic cache (`~/.devx/cache.db`).
- Provider and model configuration validity.
- Availability of optional extras (ONNX runtime, Textual, Paramiko).
- Git repository state.

Example output:
```text
devx doctor
[ok] workspace: C:\Projects\my-app
[ok] session database: C:\Users\user\.devx\sessions.db
[ok] cache database: C:\Users\user\.devx\cache.db
[ok] provider: openrouter (model: openrouter/free)
[ok] api key: configured (masked)
[ok] git repository: clean working tree
[ok] extras: agent, tui, cli, ssh, embeddings
```

### Component Health Checks

For deeper runtime latency and dependency checks:
```bash
devx health
```
Or machine-readable JSON for monitoring:
```bash
devx health --json
```

---

## 6. Your First Task

You can interact with `devx` in two modes: one-shot CLI commands or the interactive full-screen TUI.

### One-Shot CLI Execution (`devx ask`)

Ask `devx` to inspect your project or perform a task:

```bash
# Ask a question referencing a project file
devx ask "explain how authentication works in @src/auth.py"
```

By default, any task that proposes modifying files or running commands uses **plan-first** mode:
1. `devx` analyzes the context and prints a step-by-step plan.
2. It prompts: `Proceed with this plan? [y/N]:`
3. If confirmed, the coder generates diffs and prompts for approval before touching your disk.

### Launching the Interactive TUI (`devx tui`)

For an interactive, conversational terminal IDE experience:

```bash
devx tui
# Or simply:
devx
```

In the TUI, you can:
- Describe tasks naturally with `@file` autocomplete.
- View real-time streaming model output and unified diff previews.
- Approve or reject actions using modal dialogs or keyboard shortcuts.
- Roll back changes instantly with `/undo`.
- Use `/help` to discover all slash commands and shortcuts.

---

## Next Steps

- Explore [Core Concepts](02-core-concepts.md) to understand the agent state machine, approval gates, and session lifecycle.
- Check out the [CLI Reference](03-cli-reference.md) for a complete catalog of all 17 commands.
- Master the [TUI Guide](04-tui-guide.md) to supercharge your terminal workflow.
