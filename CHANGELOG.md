# Changelog

## Unreleased (0.2.0-dev.0)

- Added Remote SSH Workspace support: Pluggable `WorkspaceBackend` architecture with `SshWorkspaceBackend` (SFTP filesystem operations, remote atomic edits, remote command/test execution, and remote path sandboxing) configurable via URI (`ssh://user@host:port/path`) or CLI options (`--remote`, `--ssh-key`).
- Added token consumption optimization: Test Diagnostic Distillation (`distill_test_output`) extracting isolated failure assertions, line diagnostics, and traceback frames from test runners (`pytest`, `vitest`, `jest`, `ruff`) to eliminate 80%–90% of repair loop noise.
- Added token consumption optimization: Planner/Coder Context Decoupling (`generate_file_manifest`, `decoupled_coder_context`) supplying lightweight structural outlines to the planner and deferring line-level reads to on-demand coder tool calls, reducing initial prompt payloads by 40%–60%.
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
- Expanded regression test suite: 73 automated tests passing, provider-contract checks passing, offline benchmarks passing, and Ruff/compile/diff checks passing.

## 0.1.0

- Initial local-first coding-agent package.
- LangGraph supervisor workflow with planner, coder, and tester stages.
- Approval-gated workspace tools, atomic writes, diffs, backups, and rollback.
- SQLite sessions, event history, and checkpoint-compatible run IDs.
- Textual TUI and Typer CLI.
- Optional local embeddings and semantic cache.
- Cross-platform CI and package-release workflows.

