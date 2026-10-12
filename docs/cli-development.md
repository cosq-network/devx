# Cross-Platform CLI Development Guide

This guide explains how to install, run, test, and troubleshoot the `devx`
command while developing this project on Linux, macOS, or Windows.

The project exposes the CLI through the following declaration in
`pyproject.toml`:

```toml
[project.scripts]
devx = "devx.cli.app:main"
```

Installing the project creates a platform-specific launcher named `devx`.
Installing in editable mode means that changes under `src/devx` are used
immediately without rebuilding or reinstalling the package.

## Prerequisites

Install Python 3.11 or newer and Git. Verify both tools before starting.

Linux and macOS:

```bash
python3 --version
git --version
```

Windows PowerShell:

```powershell
py --version
git --version
```

The CI matrix tests Python 3.11, 3.12, and 3.13 on Ubuntu, Windows, and
macOS. Python 3.12 is a good local default because it matches the packaging
workflow.

## Create a development environment

Run these commands from the repository root.

### Linux

```bash
python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -c constraints.txt -e ".[agent,tui,cli,dev]"
```

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -c constraints.txt -e ".[agent,tui,cli,dev]"
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -c constraints.txt -e ".[agent,tui,cli,dev]"
```

### What the installation command does

```text
python -m pip install -c constraints.txt -e ".[agent,tui,cli,dev]"
```

Each part has a specific purpose:

| Part | Meaning |
|---|---|
| `python -m pip` | Runs pip through the active Python interpreter, preventing installation into a different Python installation. |
| `install` | Installs the project and its dependencies. |
| `-c constraints.txt` | Applies the repository's tested dependency versions where specified. It improves reproducibility without replacing the package's dependency declarations. |
| `-e` | Installs the project in editable mode. Source changes under `src/devx` are available immediately without reinstalling. |
| `.` | Installs the project from the current repository directory. |
| `[agent,tui,cli,dev]` | Installs the selected optional dependency groups. |

The selected extras provide:

- `agent`: LangChain, LangGraph, synchronous/asynchronous SQLite checkpoint support, and the OpenAI-compatible model adapter.
- `tui`: Textual for the terminal user interface.
- `cli`: Typer for the `devx` command and subcommands.
- `ssh`: Paramiko for remote SSH/SFTP workspace execution and SFTP file operations.
- `dev`: pytest, coverage tooling, Ruff, and Pyright for development and validation.

The command also installs the project's base dependencies, including the
configuration/runtime libraries and `prompt-toolkit` for file completion.

### Why is there no `requirements.txt` file?

Modern Python projects (using **PEP 517** and **PEP 621** specifications) use [`pyproject.toml`](../pyproject.toml) as the single source of truth for dependencies and build configuration.

1. **Declarative Extras vs Flat Lists**:  
   Traditional `requirements.txt` files only support flat lists of packages. In contrast, `pyproject.toml` declares modular dependency groups (`[project.optional-dependencies]`) such as `[agent]`, `[tui]`, `[ssh]`, `[embeddings]`, and `[dev]`, allowing users to install only what they need (e.g., `pip install -e ".[cli]"` vs `pip install -e ".[all]"`).
2. **Console Entry Point Definition**:  
   `pyproject.toml` declares `[project.scripts] devx = "devx.cli.app:main"`, which tells build backends (Hatchling) to create the platform-specific `devx` console executable. A `requirements.txt` file cannot define entry points.
3. **Reproducibility with `constraints.txt`**:  
   Rather than locking versions inside a rigid `requirements.txt`, reproducible builds are achieved using [`constraints.txt`](../constraints.txt), which pins validated versions for CI and local consistency without obscuring the abstract dependency ranges in `pyproject.toml`.
4. **Security Audits with `requirements-audit.txt`**:  
   CI vulnerability scanners (e.g. `pip-audit`) use [`requirements-audit.txt`](../requirements-audit.txt) to audit optional runtime dependencies.

If an external tool or container setup requires a flat `requirements.txt`, you can generate one on demand:
```bash
pip freeze > requirements.txt
# or export direct dependencies using pip-tools:
# pip-compile pyproject.toml --extra all -o requirements.txt
```

### Why use the constraints file?

`constraints.txt` records versions validated by the repository's CI workflow.
It limits dependency drift while still allowing pip to resolve transitive
dependencies required by the selected extras. When intentionally upgrading a
dependency, update the constraints file deliberately and rerun the complete
test suite across the supported Python versions.

The constraints file does not install packages by itself. This command is
still required because the project and its extras are installed by the `-e`
argument.

### Verify the installation

Confirm that pip and Python resolve to the same virtual environment:

Linux and macOS:

```bash
python -c "import sys; print(sys.executable)"
python -m pip --version
```

Windows PowerShell:

```powershell
python -c "import sys; print(sys.executable)"
python -m pip --version
```

Then verify the installed project and console launcher:

```text
python -m pip show devx-coding-agent
devx --help
devx doctor
```

`pip show` should report the repository location as the editable project
location. `devx doctor` should report the workspace and installed optional
dependencies.

### Common installation variations

Install only the CLI when agent and TUI dependencies are not needed:

```text
python -m pip install -e ".[cli]"
```

Install the full optional dependency set, including embeddings and tracing:

```text
python -m pip install -e ".[all]"
```

Reapply the validated constraints after changing branches or pulling updated
dependency metadata:

```text
python -m pip install --upgrade -c constraints.txt -e ".[agent,tui,cli,dev]"
```

Avoid installing into the system Python for development. Keeping the project
inside `.venv` makes the `devx` launcher, tests, and dependency versions
isolated from other projects.

## How the `devx` command is created

The editable installation creates a console launcher inside the virtual
environment:

| Platform | Launcher |
|---|---|
| Linux | `.venv/bin/devx` |
| macOS | `.venv/bin/devx` |
| Windows | `.venv\\Scripts\\devx.exe` |

Activating the virtual environment adds that directory to the current shell's
`PATH`. Therefore this resolves to the project launcher:

```text
devx doctor
```

Check the resolved executable with:

Linux and macOS:

```bash
which devx
```

Windows PowerShell:

```powershell
Get-Command devx
where.exe devx
```

The result should point inside the repository's `.venv` directory. The
launcher can also be called directly without activation:

```bash
.venv/bin/devx doctor
```

```powershell
.\.venv\Scripts\devx.exe doctor
```

## Make `devx` available from every directory

For ongoing development, use a dedicated user virtual environment instead of
installing into the system Python. Install the repository in editable mode and
add that environment's launcher directory to the user `PATH`.

### Windows PowerShell

```powershell
py -3.12 -m venv "$env:USERPROFILE\.venvs\devx-coding-agent"
& "$env:USERPROFILE\.venvs\devx-coding-agent\Scripts\python.exe" `
  -m pip install -c C:\Projects\devx\constraints.txt `
  -e "C:\Projects\devx[agent,tui,cli,dev]"
```

Add the launcher directory permanently to the user `PATH`:

```powershell
$devScripts = "$env:USERPROFILE\.venvs\devx-coding-agent\Scripts"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($userPath -split ";") -notcontains $devScripts) {
    [Environment]::SetEnvironmentVariable("Path", "$devScripts;$userPath", "User")
}
```

Open a new PowerShell window and verify:

```powershell
Get-Command devx
devx doctor
```

### Linux and macOS

```bash
python3 -m venv ~/.venvs/devx-coding-agent
~/.venvs/devx-coding-agent/bin/python \
  -m pip install -c /path/to/devx/constraints.txt \
  -e "/path/to/devx[agent,tui,cli,dev]"
```

Add the launcher directory to the shell startup file:

```bash
echo 'export PATH="$HOME/.venvs/devx-coding-agent/bin:$PATH"' >> ~/.profile
export PATH="$HOME/.venvs/devx-coding-agent/bin:$PATH"
```

For macOS users running Zsh, use `~/.zshrc` instead of `~/.profile`.
Verify with:

```bash
which devx
devx doctor
```

The editable install means changes under the repository's `src/devx` directory
are available from every working directory without reinstalling. Reinstall
only after changing dependencies or package metadata.

When invoking `devx` from another project, use the user-wide
`~/.devx/config.env` for provider settings. The CLI always uses the current
working directory as its workspace; change directories before invoking `devx`.
The programmatic `Settings.load(workspace=...)` override is reserved for
library callers and tests.

## Building and distributing a standalone wheel

While editable mode is best for local iterative coding, you can package `devx` into a self-contained wheel artifact:

1. Install the PEP 517 build frontend:
   ```bash
   python -m pip install build
   ```
2. Build wheel and source distributions from the repository root:
   ```bash
   python -m build
   ```
   This generates artifacts in `dist/`:
   - `dist/devx_coding_agent-0.2.0.dev0-py3-none-any.whl`
   - `dist/devx_coding_agent-0.2.0.dev0.tar.gz`
3. Inspect package metadata and contents:
   ```bash
   python -m zipfile -l dist/devx_coding_agent-0.2.0.dev0-py3-none-any.whl
   ```
4. Install the wheel cleanly into any Python environment:
   ```bash
   python -m pip install --force-reinstall "dist/devx_coding_agent-0.2.0.dev0-py3-none-any.whl[all]"
   ```

## Configure a provider

The CLI loads configuration from environment variables, a workspace `.env`
file, and (when present) `~/.devx/config.env`. Environment variables take
precedence over the workspace file, which takes precedence over the user file.
Copy `.env.example` to `.env` for local development. The real `.env` file is
ignored by Git.

For OpenRouter, which exposes an OpenAI-compatible endpoint, configure the
current shell as follows.

Linux and macOS:

```bash
export DEVX_BASE_URL="https://openrouter.ai/api/v1"
export DEVX_API_KEY="sk-or-v1-your-openrouter-key"
export DEVX_MODEL="poolside/laguna-s-2.1:free"
export DEVX_PROVIDER="openrouter"
export DEVX_APPROVAL_REQUIRED="true"
```

Windows PowerShell:

```powershell
$env:DEVX_BASE_URL = "https://openrouter.ai/api/v1"
$env:DEVX_API_KEY = "sk-or-v1-your-openrouter-key"
$env:DEVX_MODEL = "poolside/laguna-s-2.1:free"
$env:DEVX_PROVIDER = "openrouter"
$env:DEVX_APPROVAL_REQUIRED = "true"
```

For user-wide persistent configuration, create `~/.devx/config.env`. On Windows this is
normally `%USERPROFILE%\.devx\\config.env`.

```env
DEVX_PROVIDER=openrouter
DEVX_BASE_URL=https://openrouter.ai/api/v1
DEVX_API_KEY=sk-or-v1-your-openrouter-key
DEVX_MODEL=poolside/laguna-s-2.1:free
DEVX_APPROVAL_REQUIRED=true
DEVX_TEST_COMMAND=pytest
DEVX_LINT_COMMAND=ruff check
```

Never commit API keys or place them in tracked project files.

Project-scoped permissions, plugins, MCP servers, and context-compaction
limits are configured in `.devx/config.json`. Keep provider credentials in
`.env` or `~/.devx/config.env`; project JSON is for non-secret behavior. See
[Extensibility](extensibility.md) for examples and safety rules.

The recommended acceptance profile is `poolside/laguna-s-2.1:free`. Replace
the model with another OpenRouter model when testing compatibility.

## Verify the installation

Run the diagnostic command after configuration:

```text
devx doctor
```

It reports the workspace, provider, model, optional dependency status, and
overall configuration status. Inspect the complete command surface with:

```text
devx --help
```

## Initialize a project

Run the interactive onboarding wizard to configure providers, curated tool-calling
models, and local settings (a full-screen TUI setup wizard opens when run without
flags on a terminal):

```bash
devx init
```

Or run in automated non-interactive mode:

```bash
devx init --provider openai --model gpt-4o --api-key "$API_KEY" --non-interactive
```

## Run CLI tasks

Run a one-shot task:

```text
devx ask "inspect @src/devx/cli/app.py"
devx ask "run the tests and explain any failures"
```

The `@path` syntax supplies files or directories from the configured
workspace. Paths outside the workspace are rejected.

Use a named session to preserve conversational state:

```text
devx ask --session local-devx "inspect the project structure"
devx ask --session local-devx "suggest improvements"
devx sessions
devx session events local-devx
```

Inspect and retry failed runs with the Dead Letter Queue (DLQ):

```text
devx session dlq
devx session dlq-retry <ENTRY_ID>
devx session dlq-remove <ENTRY_ID>
```

Run component health checks and export metrics:

```text
devx health
devx health --json
devx metrics --format prometheus
devx metrics --format json
```

Build and maintain the local semantic search index:

```text
devx index
devx index --watch
```

Use JSON output for scripts or automation:

```text
devx ask --json "summarize the repository"
```

Generate a plan without invoking coding, testing, project commands, or file
mutations:

```text
devx ask --plan-only "design the authentication refactor"
```

By default, `devx ask` operates in **plan-first** mode, displaying the generated plan
and prompting for user confirmation before coding. Use `--no-plan-first` to skip the
initial plan confirmation:

```text
devx ask --no-plan-first "fix typo in @README.md"
```

For trusted, isolated workspaces, `--approve-all` automatically approves file
writes and commands:

```text
devx ask --approve-all "run the test suite and fix failures"
```

Only use automatic approval when the workspace and generated changes are
trusted.

The TUI provides the same plan-only workflow with `/plan <task>`. During a
normal run, `/cancel` requests structured cancellation; `/diff` shows the
bounded current Git diff, and `/undo` restores the last run's journaled changes
when the files still match their recorded post-change hashes. Approval dialogs
show a bounded proposed diff for write and replacement actions.

## Run the TUI

The Textual user interface is included by the `tui` extra:

```text
devx
devx tui
```

Resume a named session from the TUI when supported by the installed version:

```text
devx tui --session local-devx
```

The prompt is focused automatically when the TUI starts. Slash commands and
`@` workspace mentions offer autocomplete suggestions; press the right arrow
to accept one. Press `F1` for help,
`Ctrl+L` to clear the chat, `F2` or `Ctrl+Insert` to copy the transcript to the
OS clipboard, or `Ctrl+C` to cancel the active run (copy the transcript when
idle). Press `Ctrl+C` twice to quit, or use `/copy`; it copies the full
transcript when no text is selected.

### TUI provider and model commands

The TUI supports provider configuration without placing secrets in the
conversation history:

```text
/provider
/provider 2
/provider openrouter
/provider list
/provider use openrouter
/setup
/model
/model catalog
/model catalog free
/model live
/model live free
/model live tools
/model free
/model tools
/model 1
/model list
/model use poolside/laguna-s-2.1:free
/api-key set
/config show
/config reload
```

`/provider` shows the configured provider choices. `/model` and `/model catalog`
show a curated, provider-specific catalogue containing models selected for
coding and tool/function-calling agent runs. Choose one with `/model 1` or
`/model use MODEL_ID`; `/model catalog free` filters the curated catalogue.
The same catalogue is available in the first-time provider setup panel, where
changing the provider refreshes the model choices automatically.

`/setup` reopens the same provider/model panel at any time. It is the easiest
way to change provider, API key, base URL, and curated coding model together.

Live discovery remains available with `/model live`, `/model live free`,
`/model live tools`, or the backwards-compatible `/model free`, `/model tools`,
and `/model TEXT` forms. These commands query the active OpenAI-compatible provider's
`/models` endpoint and are useful for experimenting with models outside the
curated catalogue. Live discovery does not guarantee that every returned model
supports coding tools, so the curated catalogue is the recommended default.
`/api-key set` opens a masked input and stores the key in the
user-wide configuration file. `/config show` masks the configured key. New
agent runs use the updated settings; an active run is not interrupted.

The provider aliases `openrouter` and `ollama` use the OpenAI-compatible
adapter internally while preserving their provider names in configuration.

If the TUI import is unavailable, install the TUI extra:

```bash
python -m pip install -e ".[tui]"
```

## Development and test commands

Run the complete local test suite:

```text
python -m pytest -q
```

Run linting:

```text
python -m ruff check src tests scripts
```

Run the deterministic offline developer-experience benchmarks:

```text
python scripts/benchmark.py
```

Compile-check the Python sources:

```text
python -m compileall -q src tests scripts
```

Run focused tests while developing:

```text
python -m pytest -q tests/test_core.py
python -m pytest -q tests/test_tui_smoke.py
python -m pytest -q tests/test_version.py
python -m pytest -q tests/test_provider_contract.py
```

The complete deterministic validation set is:

```text
python -m pytest -q
python -m pytest -q tests/test_provider_contract.py
python scripts/benchmark.py
python -m ruff check src tests scripts
python -m compileall -q src tests scripts
git diff --check
```

The current suite includes extension tests for ordered permissions, bounded
compaction, workspace plugins, and stdio MCP tools.

The CI workflow additionally runs dependency auditing, provider contract
tests, package building, wheel installation, and the `devx doctor` smoke test.

## Common problems

### `devx` is not recognized

The virtual environment is probably not active, or the editable install did
not complete. Activate the environment and reinstall:

```bash
source .venv/bin/activate
python -m pip install -c constraints.txt -e ".[agent,tui,cli,dev]"
```

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -c constraints.txt -e ".[agent,tui,cli,dev]"
```

### PowerShell blocks activation

If the machine policy permits it, enable locally created scripts for the
current user:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate the environment again.

### Provider or API errors

Check the active values without printing the secret:

```bash
echo "$DEVX_BASE_URL"
echo "$DEVX_MODEL"
echo "$DEVX_PROVIDER"
```

```powershell
$env:DEVX_BASE_URL
$env:DEVX_MODEL
$env:DEVX_PROVIDER
```

Verify that the selected provider package is installed and that the endpoint
supports the model and tool-calling behavior required by the agent.

### The CLI uses the wrong Python installation

Always invoke pip through the active interpreter:

```text
python -m pip --version
python -c "import sys; print(sys.executable)"
```

Both should point into `.venv`.

### Tests fail because of temporary-directory permissions

Pytest needs a writable temporary directory. On managed systems, configure a
writable temporary location before running tests:

```powershell
$env:TMP = "C:\\path\\to\\writable-temp"
$env:TEMP = $env:TMP
python -m pytest -q
```

Hosted CI runners provide writable temporary directories by default.

## Deactivate or recreate the environment

Deactivate the current environment with:

```text
deactivate
```

To recreate it, remove the `.venv` directory and repeat the setup commands.
Do not remove the project source or `.git` directory.

## Related documentation

- [README](../README.md) — project overview, configuration, and CLI reference
- [Architecture](architecture.md) — runtime and agent design
- [Extensibility](extensibility.md) — permissions, plugins, MCP, and compaction
- [GitFlow](gitflow.md) — branch and semantic-versioning policy
