# Changelog

## Unreleased (0.2.0-dev.0)

- Added Remote SSH Workspace support: Pluggable `WorkspaceBackend` architecture with `SshWorkspaceBackend` (SFTP filesystem operations, remote atomic edits, remote command/test execution, and remote path sandboxing) configurable via URI (`ssh://user@host:port/path`) or CLI options (`--remote`, `--ssh-key`).
- Added governed remote execution hardening: dropped SSH transports are detected and transparently reconnected with backoff (clear `ConnectionError` on failure), remote commands run under `setsid` with process-group cancellation on cancel/timeout, and `devx doctor --remote <uri>` preflights connection, path, writability, git, and venv before a run.
- Added a container workspace backend (`ContainerWorkspaceBackend`) targeting `container://name/path` or `docker://name/path` via `docker exec`, selectable with `devx ask --container` or `DEVX_CONTAINER` (no bind mount required).
- Added policy presets (`default`, `remote_only`, `deny_local_writes`, `strict`) with hard constraints enforced even for approved actions, configurable via `DEVX_POLICY_PRESET` or `.devx/config.json`.
- Added per-model pricing and cost reporting: token usage is persisted to the session DB and surfaced with `devx usage` (`pricing.py`).
- Added `devx audit export` for a portable audit trail of sessions, events, file changes, DLQ entries, and usage (`audit.py`).
- Added file-by-file change review: `devx changes list|accept|reject` reverts individual recorded files with the same hash-guarded undo.
- Added drift verification: `devx verify` compares the workspace against the recorded change journal — each change is reported `intact`, `reverted`, `conflict`, or `missing` (`--run`, `--remote`, `--container`, `--json`; exits 1 on conflict/missing).
- Added a secret guard: agent reads and search results are scanned for API keys, tokens, and private keys with `warn`/`redact`/`block` modes (`DEVX_SECRET_SCAN`), plus a CI-friendly `devx scan-secrets [path] [--json]` command (`secrets.py`).
- Added PR-ready reports: `devx report <session|run>` stitches the agent event timeline, hash-guarded changes, lint/test results, and token cost into Markdown or JSON with a suggested PR description (`report.py`).
- Added per-run cost receipts: `devx usage --run <id> [--markdown]` renders a Markdown receipt (`SessionStore.aggregate_usage` now filters by run id).
- Added disposable remote workspaces for demos and onboarding: `devx lab up`/`down` boots and removes the SSH container used by the e2e suite and prints ready-to-paste `--remote`/`--container` URIs (`harness/lab.py`).
- Added a hermetic, opt-in SSH end-to-end test suite (`tests/test_remote_e2e.py`) plus a dedicated CI job, exercising real connection, sandboxing, process-group cancellation, and rollback.
- Added `docs/governed-remote.md` and `docs/threat-model.md`.
- Added token consumption optimization: Test Diagnostic Distillation (`distill_test_output`) extracting isolated failure assertions, line diagnostics, and traceback frames from test runners (`pytest`, `vitest`, `jest`, `ruff`) to remove boilerplate and irrelevant output from repair loops. Token savings are workload-dependent; the repository ships an offline benchmark harness (`scripts/benchmark.py`) instead of publishing fixed percentages.
- Added token consumption optimization: Planner/Coder Context Decoupling (`generate_file_manifest`, `decoupled_coder_context`) supplying lightweight structural outlines to the planner and deferring line-level reads to on-demand coder tool calls, which shrinks initial planner payloads. The reduction scales with repository size and is measured by the benchmark harness rather than published as a fixed percentage.
- Integrated OpenRouter's official `openrouter/free` Models Router (200k context window, free inference, tool calling) with attribution headers (`HTTP-Referer`, `X-Title`) and upstream failure retry handling.
- Added project onboarding CLI: `devx init` command for interactive and automated (`--non-interactive`) workspace configuration.
- Added parallel execution support: parallel coder agent with batch file reading (`read_files`) and concurrent linter/tester execution.
- Added production resilience layer: `CircuitBreaker` pattern and exponential backoff retry policy for LLM calls with rate limit detection.
- Added Dead Letter Queue (DLQ): SQLite-backed persistent failed task queue with inspection (`devx session dlq`), retry (`devx session dlq-retry`), and pruning (`devx session dlq-remove`).
- Added observability subsystem: structured JSON logging with correlation IDs, Prometheus and JSON metrics collector/exporter (`devx metrics`), and component health probes (`devx health`).
- Added local workspace semantic indexing: `devx index` with optional `--watch` daemon for live change re-indexing.
- Added plan-first workflow by default in CLI (`devx ask --plan-first / --no-plan-first`), previewing plans and prompting for user confirmation before executing edits.
- Added TUI `/diff`, `/undo`, and `/plan` workflows with bounded approval previews.
- Added CLI `--plan-only`, configurable linting, bounded context selection, and structured cancellation.
- Namespaced semantic-cache entries by provider, model, and embedding model to prevent stale cross-model reuse.
- Added deterministic offline DX benchmarks for output limits, command safety, rollback, and context budgets.
- Added ordered project permissions with `allow`, `ask`, and `deny` effects while preserving workspace and destructive-command safeguards.
- Added bounded local context compaction, a shared tool registry, workspace Python plugins, and stdio MCP tool discovery/calls.
- Expanded regression test suite: 118 automated tests passing, provider-contract checks passing, offline benchmarks passing, and Ruff/compile/diff checks passing.

## 0.1.0

- Initial local-first coding-agent package.
- LangGraph supervisor workflow with planner, coder, and tester stages.
- Approval-gated workspace tools, atomic writes, diffs, backups, and rollback.
- SQLite sessions, event history, and checkpoint-compatible run IDs.
- Textual TUI and Typer CLI.
- Optional local embeddings and semantic cache.
- Cross-platform CI and package-release workflows.

