# `devx`: Architecture and Implementation Notes

## Executive Summary

`devx` is a locally installed AI coding agent that works with any OpenAI-compatible inference API (including tool calling). It uses **LangChain** and **LangGraph** for the agentic harness, **Textual** for the terminal UI, **Typer** for the CLI entrypoint, and local embeddings for client-side token optimization. Users reference files and directories with `@` mentions and control sessions and agents with a minimal set of slash commands.

This document combines the original design blueprint with the current
implementation. Code snippets labeled as examples are illustrative; the
authoritative runtime paths are under `src/devx/`. The implemented workflow is
planner → coder → tester with bounded repair, approval-gated mutations,
checkpoint-compatible sessions, plan-only execution, cancellation, and
journaled recovery.

---

## 1. Architecture Overview

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
│  │  ┌─────────┐ ┌──────────────┐ ┌────────────────┐    │  │
│  │  │ Planner │ │Coder/Parallel│ │ Tester/Parallel│    │  │
│  │  └─────────┘ └──────────────┘ └────────────────┘    │  │
│  └──────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│              Token Optimization Layer (Client-Side)          │
│  ┌────────────────────┐  ┌─────────────────────────────┐  │
│  │ Local Embeddings   │  │ Semantic Cache + Context    │  │
│  │ (ONNX INT8)        │  │ Selection (@ file mentions) │  │
│  └────────────────────┘  └─────────────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│           Model Abstraction & Resilience Layer               │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ChatOpenAI Adapter + Circuit Breaker + Retry Policy │  │
│  │  → OpenAI / Anthropic / Google / Azure / OpenRouter  │  │
│  └──────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│            Observability & Recovery Subsystem               │
│  ┌─────────────────┐ ┌──────────────────┐ ┌─────────────┐  │
│  │ Structured Logs │ │ Prometheus/JSON  │ │ SQLite DLQ  │  │
│  │ & Tracing       │ │ Metrics & Health │ │ & Rollbacks │  │
│  └─────────────────┘ └──────────────────┘ └─────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Technology Choices and Dependencies

### Core Frameworks

| Component | Library | Purpose |
|---|---|---|
| Agent orchestration | `langchain` + `langgraph` | State machine, tool calling, multi-agent coordination |
| TUI | `textual` | Rich terminal interface with containers, events, actions |
| CLI | `typer` | Command registration, argument parsing, help text |
| Input completion | `prompt_toolkit` | `@` file/directory fuzzy completion |
| Local embeddings | `sentence-transformers` or `onnxruntime` | Client-side token optimization, zero API calls |
| Cache storage | SQLite (`SemanticCache`) | Namespaced semantic cache and file fingerprints |
| Model client | `langchain-openai` | Any OpenAI-compatible API |
| Observability | `devx.observability` | Structured JSON logging, health checks, Prometheus/JSON metrics |
| Resilience | `CircuitBreaker` + DLQ | Circuit breaker pattern, rate limit retry, Dead Letter Queue |

### Why These Libraries

**LangGraph over a simple AgentExecutor**: Coding tasks require multi-step state management (read file → analyze → edit → test → fix). LangGraph provides an explicit state machine with conditional branching, loops, and interrupt/resume. Multi-agent patterns (Supervisor / Swarm) are production-proven.

**Local embeddings for token optimization**: Embeddings are optional and run
locally through the configured `sentence-transformers` model. They never make
provider API calls. When disabled or unavailable, deterministic lexical file
selection still enforces the context-file limit.

---

## 3. Project Structure

```
devx/
├── pyproject.toml
├── README.md
├── src/devx/
│   ├── __init__.py
│   ├── config.py               # Env vars, API endpoint config
│   ├── llm.py                  # ChatOpenAI factory
│   │
│   ├── agents/                 # Agent definitions
│   │   ├── __init__.py
│   │   ├── graph.py            # LangGraph state graph builder
│   │   ├── langgraph_supervisor.py # Production graph nodes
│   │   ├── supervisor.py       # Fallback supervisor runner
│   │   └── runtime.py          # Tool-calling agent and streaming helpers
│   │
│   ├── harness/                # Agentic harness
│   │   ├── __init__.py
│   │   ├── approval.py         # Approval requests and decisions
│   │   ├── backend.py          # WorkspaceBackend abstract protocol
│   │   ├── local_backend.py    # Local filesystem & process backend
│   │   ├── ssh_backend.py      # Remote SSH/SFTP backend with bastion & keepalive
│   │   ├── changes.py          # Journal, backups, and rollback
│   │   ├── runtime.py          # Cancellation token and cancellation error
│   │   ├── session.py          # Session persistence
│   │   ├── testing.py          # Test command detection
│   │   ├── tools.py            # Bounded workspace operations
│   │   ├── permissions.py      # Ordered file, shell, and extension policy rules
│   │   ├── tool_registry.py    # Built-in, plugin, and MCP tool registry
│   │   └── mcp.py              # Stdio MCP lifecycle and tool calls
│   │
│   ├── tui/                    # Textual interface
│   │   ├── app.py              # Main TUI app
│   │   └── components/         # Modular actions, commands, modals, runner, session
│   │
│   ├── observability/          # Observability subsystem
│   │   ├── logging.py          # Structured JSON logging & correlation IDs
│   │   ├── metrics.py          # Prometheus and JSON metrics collection
│   │   ├── health.py           # Component health checks
│   │   └── tracing.py          # LangSmith tracing setup
│   │
│   ├── cli/                    # Typer commands
│   │   └── app.py              # Typer app and subcommands
│   │
│   ├── token_optim/            # Token optimization
│   │   ├── __init__.py
│   │   ├── embeddings.py       # Local embedding wrapper
│   │   ├── cache.py            # Semantic cache
│   │   ├── context.py           # Relevant-file context selection
│   │   └── compaction.py        # Bounded session-history compaction
│   │
│   └── completion/             # @ completion
│       ├── __init__.py
│       ├── file_path.py        # File path completer
│       └── parser.py           # @ mention parser
│
└── tests/
    └── test_benchmark.py       # Offline developer-experience benchmarks
```

---

## 4. Core Implementation Details

### 4.1 Model Abstraction: Any OpenAI-Compatible API

`devx` is not bound to any provider. Users specify the endpoint via environment variables or a config file:

```python
# config.py
import os
from langchain_openai import ChatOpenAI

def create_llm() -> ChatOpenAI:
    """Create a ChatOpenAI instance pointing at any OpenAI-compatible endpoint."""
    base_url = os.getenv("DEVX_BASE_URL", os.getenv("DEV_BASE_URL", "http://localhost:4000/v1"))
    api_key = os.getenv("DEVX_API_KEY", os.getenv("DEV_API_KEY", "sk-placeholder"))
    model = os.getenv("DEVX_MODEL", os.getenv("DEV_MODEL", "gpt-4o"))

    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0.1,
        max_retries=2,
    )
```

**LiteLLM integration example**: LiteLLM Proxy exposes a unified OpenAI-compatible endpoint that routes to 100+ providers. Users start the proxy:

```bash
litellm --config litellm_config.yaml --port 4000
export DEVX_BASE_URL=http://localhost:4000
export DEVX_API_KEY=sk-your-master-key
export DEVX_MODEL=gpt-4o  # Must match model_name in the LiteLLM config
```

**Key constraint**: `DEVX_MODEL` must match the `model_name` alias in the LiteLLM config, not the upstream raw model name.

### 4.2 Agentic Harness (Multi-Agent Coordination)

Choose the **Supervisor pattern** because coding tasks require centralized control (who edited which file, when to test).

```python
# agents/graph.py
from langgraph.graph import StateGraph, END
from typing import Literal, TypedDict

class DevxState(TypedDict):
    messages: list
    task: str
    active_agent: str
    files_in_context: list[str]
    token_count: int

# Backwards-compatibility alias
DevState = DevxState

def build_devx_graph():
    """Build the supervisor-subagent state graph."""
    workflow = StateGraph(DevxState)

    # Nodes
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("planner", planning_agent_node)
    workflow.add_node("coder", coding_agent_node)
    workflow.add_node("tester", testing_agent_node)
    workflow.add_node("doc_writer", doc_agent_node)

    # Edges
    workflow.set_entry_point("supervisor")

    workflow.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "planner": "planner",
            "coder": "coder",
            "tester": "tester",
            "doc_writer": "doc_writer",
            "FINISH": END,
        }
    )

    # Subagents return to supervisor after completion
    for agent in ["planner", "coder", "tester", "doc_writer"]:
        workflow.add_edge(agent, "supervisor")

    return workflow.compile()

build_dev_graph = build_devx_graph
```

**Supervisor routing logic**:

```python
def supervisor_node(state: DevxState) -> DevxState:
    """Supervisor decides which agent to delegate to next."""
    # Use an LLM to analyze the current state and task
    # Return an active_agent update
    ...

def route_from_supervisor(state: DevxState) -> Literal[
    "planner", "coder", "tester", "doc_writer", "FINISH"
]:
    return state["active_agent"]
```

**Performance trade-off**: The supervisor pattern adds one extra model call (results must be summarized by the supervisor) compared to a simple single-agent setup, but it provides centralized control. For a coding agent, that overhead is worth it because the supervisor maintains a global view of files.

### 4.3 Session Handling and Concurrent Agents

Session state is persisted to local SQLite:

```python
# harness/session.py
import sqlite3
import json
from pathlib import Path

class SessionStore:
    def __init__(self, db_path: Path = Path.home() / ".devx" / "sessions.db"):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP,
                    state TEXT,  -- JSON-serialized DevxState
                    metadata TEXT
                )
            """)

    def save(self, session_id: str, name: str, state: dict):
        """Save session state."""
        ...

    def load(self, session_id: str) -> dict:
        """Load a session."""
        ...

    def list_sessions(self) -> list[dict]:
        """List all sessions."""
        ...
```

**Concurrent agents**: Different sessions can run different agent-graph instances simultaneously. Each `devx` process maintains its own `SessionStore` connection and LangGraph state. For parallel agents (e.g., running coder and tester at the same time), use Python `asyncio`:

```python
# Run multiple agent tasks in parallel
async def run_parallel_agents(agents: list, state: DevxState):
    tasks = [agent.ainvoke(state) for agent in agents]
    results = await asyncio.gather(*tasks)
    return results
```

### 4.4 Dual-Mode TUI and CLI

**Typer CLI entrypoint**:

```python
# cli/app.py
import typer
from devx.tui.app import DevxTUI
from devx.harness.session import SessionStore

app = typer.Typer(help="devx - AI coding agent", no_args_is_help=False)

@app.command()
def tui(session: str = typer.Option(None, "--session", "-s")):
    """Launch the TUI interface."""
    tui_app = DevxTUI(session_id=session)
    tui_app.run()

@app.command()
def ask(prompt: str, session: str = typer.Option(None, "--session", "-s")):
    """Run a single CLI query."""
    ...

@app.command()
def sessions():
    """List all sessions."""
    store = SessionStore()
    for s in store.list_sessions():
        typer.echo(f"{s['id'][:8]}  {s['name']}  {s['updated_at']}")

if __name__ == "__main__":
    app()
```

Users launch the interactive interface with `devx tui`, or run a one-shot query with `devx ask "fix the login bug"`.

**Textual TUI structure**:

```python
# tui/app.py
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, Input, RichLog
from devx.agents.graph import build_devx_graph

class DevxTUI(App):
    CSS_PATH = "styles.tcss"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield RichLog(id="chat-view", wrap=True)
            yield Input(placeholder="Enter a task... use @ to reference files", id="input-bar")
            yield Static("agent: idle | tokens: 0", id="status-bar")

    def on_input_submitted(self, event: Input.Submitted):
        """Handle user input and trigger the agent graph."""
        user_input = event.value
        cleaned, files = parse_file_mentions(user_input)
        self.run_worker(self._invoke_agent(cleaned, files))

    async def _invoke_agent(self, text: str, files: list):
        """Run LangGraph inside a background worker."""
        graph = build_devx_graph()
        result = await graph.ainvoke({...})
        self.query_one("#chat-view").write(result["messages"][-1].content)
```

Textual's async worker model is a natural fit for long-running agent tasks without blocking the UI.

### 4.5 `@` File and Directory Mentions

Use `prompt_toolkit`'s `Completer` to implement `@`-triggered completion. Known pitfall: **do not decorate the completion method with `@staticmethod`**, because the completion path needs access to `self` to call file-search methods. Using `@staticmethod` causes a `NameError` crash on bare `@`.

```python
# completion/file_path.py
from prompt_toolkit.completion import Completer, Completion
from pathlib import Path

class FilePathCompleter(Completer):
    """Trigger file path completion after @."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        # Detect @ trigger
        if not text.endswith("@") and "@" not in text.split()[-1]:
            return

        # Extract the path fragment after @
        word = text.split()[-1]
        if not word.startswith("@"):
            return

        query = word[1:]  # Strip @
        yield from self._file_completions(query, limit=30)

    def _file_completions(self, query: str, limit: int = 30):
        """Fuzzy-match file paths."""
        cwd = Path.cwd()
        # If query contains /, resolve as a relative path
        if "/" in query:
            base = cwd / query.rsplit("/", 1)[0]
            partial = query.rsplit("/", 1)[1]
        else:
            base = cwd
            partial = query

        if not base.exists():
            return

        count = 0
        for item in base.iterdir():
            if count >= limit:
                break
            name = item.name
            if partial.lower() in name.lower():
                prefix = query.rsplit("/", 1)[0] + "/" if "/" in query else ""
                display = f"@{prefix}{name}"
                if item.is_dir():
                    display += "/"
                yield Completion(display, start_position=-len(word))
                count += 1
```

**`@` mention parser**: Extract file paths from user input and load them into context:

```python
# completion/parser.py
import re
from pathlib import Path

# Simple pattern: @ followed by valid filename chars, stopping at whitespace/punctuation
MENTION_PATTERN = re.compile(r'@([A-Za-z0-9._/~-]+)')

def parse_file_mentions(text: str) -> tuple[str, list[Path]]:
    """Extract @ file mentions; return cleaned text and resolved paths."""
    matches = MENTION_PATTERN.findall(text)
    files = []
    for match in matches:
        path = Path(match).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if path.exists():
            files.append(path.resolve())

    # Remove @ markers from text (or keep for display)
    cleaned = MENTION_PATTERN.sub(lambda m: f"[file:{m.group(1)}]", text)
    return cleaned, files
```

### 4.6 Token Optimization: Client-Side Local Embeddings

**Core idea**: Before sending anything to the inference API, local embeddings serve two purposes: (1) semantic caching of repeated queries, and (2) intelligent selection of relevant file context.

In the shipped implementation, embeddings are opt-in through
`DEVX_EMBEDDINGS_ENABLED`. `SemanticCache` stores JSON vectors in SQLite and
namespaces entries by provider, chat model, and embedding model. This prevents
responses produced by one model or embedder from being reused by another. File
fingerprints are included in exact cache keys. If embeddings are unavailable,
context selection falls back to deterministic filename-term scoring.

**Local embedding configuration**:

```python
# token_optim/embeddings.py
import onnxruntime as ort
from transformers import AutoTokenizer
import numpy as np

class LocalEmbedder:
    """ONNX INT8 local embeddings, no API calls."""

    def __init__(self, model_path: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.session = ort.InferenceSession(
            f"{model_path}/onnx/model_quantized.onnx",
            providers=["CPUExecutionProvider"]
        )

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return L2-normalized embedding vectors."""
        enc = self.tokenizer(
            texts, padding=True, truncation=True,
            max_length=512, return_tensors="np"
        )
        out = self.session.run(
            None,
            {"input_ids": enc["input_ids"], "attention_mask": enc["attention_mask"]}
        )[0]
        mask = enc["attention_mask"][..., None].astype(np.float32)
        pooled = (out * mask).sum(1) / mask.sum(1)
        return pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
```

**Semantic cache**: Embed user queries, compare against historical queries via cosine similarity. If similarity exceeds a threshold (e.g., 0.95), return the cached response with zero token cost.

```python
# token_optim/cache.py
import sqlite3
import numpy as np
from pathlib import Path
from devx.token_optim.embeddings import LocalEmbedder

class SemanticCache:
    def __init__(self, db_path: str = "~/.devx/cache.db", threshold: float = 0.95):
        self.db_path = Path(db_path).expanduser()
        self.threshold = threshold
        self.embedder = LocalEmbedder()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    query_embedding BLOB,
                    response TEXT,
                    created_at TIMESTAMP
                )
            """)

    def get(self, query: str) -> str | None:
        """Look up a semantically similar cached response."""
        query_vec = self.embedder.embed([query])[0]
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT query_embedding, response FROM cache"
            ).fetchall()
        for emb_blob, response in rows:
            emb = np.frombuffer(emb_blob, dtype=np.float32)
            similarity = float(np.dot(query_vec, emb))  # already normalized
            if similarity >= self.threshold:
                return response
        return None

    def set(self, query: str, response: str):
        """Store a query-response pair."""
        emb = self.embedder.embed([query])[0]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO cache (query_embedding, response, created_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (emb.tobytes(), response)
            )
```

**Intelligent context selection**: When the user references many files with `@`, local embeddings can filter down to the most relevant files instead of sending all of them:

```python
# token_optim/context.py
import numpy as np
from pathlib import Path
from devx.token_optim.embeddings import LocalEmbedder

def select_relevant_files(
    query: str,
    candidate_files: list[Path],
    max_files: int = 5,
    embedder: LocalEmbedder | None = None,
) -> list[Path]:
    """Select the N most relevant files for a query."""
    if len(candidate_files) <= max_files:
        return candidate_files

    # The shipped helper accepts embedder=None and uses deterministic lexical
    # scoring; callers enable LocalEmbedder explicitly when desired.
    if embedder is None:
        terms = {word.lower() for word in query.split() if len(word) > 2}
        scored = [(sum(term in path.name.lower() for term in terms), -index, path)
                  for index, path in enumerate(candidate_files)]
        return [item[2] for item in sorted(scored, reverse=True)[:max_files]]

    # Read a snippet of each file (first 500 chars)
    contents = []
    for f in candidate_files:
        try:
            contents.append(f.read_text()[:500])
        except Exception:
            contents.append("")

    query_vec = embedder.embed([query])
    file_vecs = embedder.embed(contents)

    similarities = (query_vec @ file_vecs.T).flatten()
    top_indices = np.argsort(similarities)[-max_files:][::-1]
    return [candidate_files[i] for i in top_indices]
```

Local embedding computation uses zero provider API tokens. Actual cache-hit
rates and context reduction depend on the repository and task, so the project
ships an offline benchmark harness rather than promising fixed savings.

### 4.7 Permissions and extensibility

Project-scoped non-secret configuration is loaded from `.devx/config.json`.
Permission rules are ordered and the last matching `allow`, `ask`, or `deny`
rule wins. Workspace containment, hard destructive-command blocks, bounded
execution, and atomic journaled writes remain active regardless of configured
rules.

The runtime registers built-in tools through `ToolRegistry`, then optionally
loads explicitly configured workspace Python plugins and stdio MCP servers.
Plugin and MCP tools use the shared per-run tool-call budget and require
approval by default. MCP server commands run without a shell and their working
directories must remain inside the workspace. See
[Extensibility](extensibility.md) for the configuration contract and examples.

Session messages are compacted locally when their configured message-count or
character budget is exceeded. Older messages become a bounded summary and
recent messages remain verbatim; compaction does not make an additional model
request.

### 4.8 TUI Slash Commands

The TUI supports the following core commands:

| Command | Purpose | Implementation |
|---|---|---|
| `/help` | Show available commands and usage | Static text |
| `/session` | List, switch, and name sessions | Calls `SessionStore` |
| `/agent` | Choose supervisor, coder, planner, or tester perspective | Updates `requested_agent` |
| `/clear` | Clear current session context | Resets the `messages` list |
| `/diff` | Show the bounded current Git diff | Calls `WorkspaceTools.git_diff` |
| `/undo` | Restore the last run's safe journaled changes | Calls `ChangeJournal.rollback` |
| `/plan <task>` | Produce a plan without coding or mutations | Runs planner only |
| `/cancel` | Request cancellation of an active run | Cancels `CancellationToken` |
| `/remote connect <uri>` | Connect the workspace to a remote host via SSH/SFTP | Updates active backend to `SshWorkspaceBackend` |
| `/remote disconnect` | Disconnect remote SSH workspace and revert to local directory | Restores `LocalWorkspaceBackend` |
| `/copy` | Copy selected or full transcript | Native clipboard helper |
| `/exit` | Quit the TUI | Triggers Textual `action_quit` |

Interceptors handle input beginning with `/` in the TUI's `Input` widget:

```python
def on_input_submitted(self, event: Input.Submitted):
    value = event.value.strip()
    if value.startswith("/"):
        self._handle_slash_command(value)
    else:
        self._handle_task(value)

def _handle_slash_command(self, cmd: str):
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if command == "/help":
        self.query_one("#chat-view").write(HELP_TEXT)
    elif command == "/session":
        self._handle_session_command(args)
    elif command == "/agent":
        self._handle_agent_switch(args)
    elif command == "/clear":
        self._clear_context()
    elif command == "/exit":
        self.exit()
    else:
        self.query_one("#chat-view").write(
            f"Unknown command: {command}. Type /help for available commands."
        )
```

### 4.9 Pluggable Workspace Backends (Local & Remote SSH)

The agent harness decouples file operations and process execution behind the `WorkspaceBackend` abstract protocol:

- **`LocalWorkspaceBackend`**: Direct local filesystem access and `subprocess.run` command execution for standard workspaces.
- **`SshWorkspaceBackend`**: Remote development over Paramiko SSH/SFTP:
  - Transparent parsing of SSH URIs (`ssh://user:pass@host:port/path` or `user@host:/path`).
  - OpenSSH config resolution (`~/.ssh/config`) with bastion jump-host `ProxyCommand` tunneling.
  - Fast single-roundtrip remote file discovery using `git ls-files -co --exclude-standard`.
  - Remote virtual environment auto-detection and activation (`.venv/bin/activate`).
  - Terminal ANSI escape code stripping and TCP transport keepalives.

---

## 5. Installation and Usage

### Installation

```bash
# From source
git clone https://github.com/cosq-network/devx.git
cd devx
pip install -e .

# Or from PyPI
pip install devx-coding-agent
```

### Configuration

Set the following in `~/.devx/config.env` (or `~/.dev/config.env`) or your shell environment:

```bash
export DEVX_BASE_URL="https://your-litellm-proxy.com/v1"
export DEVX_API_KEY="sk-..."
export DEVX_MODEL="claude-sonnet-4"  # or any LiteLLM alias
export DEVX_MAX_CONTEXT_FILES="5"
export DEVX_LINT_COMMAND="ruff check src tests"
export DEVX_EMBEDDINGS_ENABLED="false"
```

### Usage

```bash
# Launch the TUI
devx tui

# Launch the TUI with a named session
devx tui --session "auth-bug-fix"

# One-shot CLI query
devx ask "Fix the login validation logic in @src/auth.py"

# Planning without edits, tests, or project commands
devx ask --plan-only "Design the login validation refactor"

# List sessions
devx sessions
```

---

## 6. Key Design Decisions Summary

| Decision | Choice | Rationale |
|---|---|---|
| Agent orchestration | LangGraph Supervisor | Coding tasks require centralized file-state management; production-proven |
| TUI framework | Textual | Async worker model fits long-running agents; rich layout system |
| CLI framework | Typer | Clean command registration; automatic help text |
| Local embeddings | Configured sentence-transformers model | Optional local relevance scoring; zero provider API calls |
| Model interface | ChatOpenAI with base_url | Any OpenAI-compatible endpoint; 100+ providers via LiteLLM |
| `@` completion | prompt_toolkit Completer | Mature fuzzy matching; avoid the `@staticmethod` crash pitfall |
| Session storage | SQLite | Zero configuration; supports concurrent sessions; easy to query |

---

## 7. Extension Directions

1. **Richer Git review**: Add file-by-file accept/reject decisions and patch staging around the existing `/diff` workflow.
2. **Project-aware test policy**: Add per-project test selection and richer lint/test diagnostics around the existing bounded tester loop.
3. **Project memory**: Embed `AGENTS.md` and code conventions into a local vector store; the agent auto-loads relevant memory at startup.
4. **Sandboxed execution**: For untrusted generated code, run tests in a subprocess with restricted filesystem write access.
5. **Richer streaming**: Stream structured tool-call deltas and timing data to the TUI for a more responsive feel on long edits.
6. **Per-session model switching**: `/agent` could also switch the underlying model per subagent (e.g., a cheap model for planning, a strong model for coding).
