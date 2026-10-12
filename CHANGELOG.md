# Changelog

## Unreleased (0.2.0-dev.0)

- Fixed `devx init` crashing on Windows: `UserConfig`/`PROVIDERS`/`catalog_models` are now imported from `devx.configuration` (they are not part of `devx.config`'s `Settings` module).
- Fixed a Windows console crash class: CLI status output (`doctor`, `health`, `verify`, `init`, `report`) no longer prints glyphs the legacy cp1252 console cannot encode (`✓`/`✗`/`⚠`/`↩`/`→` are now `[ok]`/`[err]`/`[warn]`/`[rev]`/`->`), so piped/redirected output works in any terminal.

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
- Fixed remote/container rollback and drift checks: the change journal now records each write's backend-relative path (`rel_path`) alongside the display path, and `devx changes reject` / `devx verify --remote|--container` resolve recorded files against the backend root instead of mis-deriving a relative path from a recorded local-absolute one (previously remote drift checks reported every change as `missing`).
- Fixed `devx usage` reporting: LLM token usage is now recorded at every model call (`invoke_with_retry` and agent streaming), usage rows are keyed canonically (session names resolve to session ids, anonymous rows stay NULL), and `devx usage --run` aggregates run-scoped across all keyings so receipts reflect real tokens and USD cost instead of zero.
- Hardened the SSH workspace sandbox: escape detection now uses real path boundaries, so a sibling path sharing a prefix with the root (e.g. `/srv/app2` vs root `/srv/app`) is rejected.
- Fixed SSH URI port precedence: an explicit `ssh://host:PORT/path` port always wins over a `~/.ssh/config` `Port` stanza.
- Container commands now signal the in-container process on cancel/timeout (the command logs its pid to a marker file and a second `docker exec` signals it), instead of only killing the `docker exec` CLI client and leaving the command running.
- SSH host keys are now verified by default (`RejectPolicy`): unknown or silently rekeyed hosts are refused. Set `DEVX_SSH_KNOWN_HOSTS` (`ssh_known_hosts` in `.devx/config.json`) to a `known_hosts` file to permit hosts you trust, and enable `DEVX_SSH_ALLOW_UNKNOWN_KEYS=1` or the new `--accept-new-keys` flag on `devx ask`/`devx doctor` only for throwaway targets (`devx lab`, CI ephemeral servers) — `devx lab up` prints the `--accept-new-keys` one-liner since each lab container boots with a fresh host key.
- Fixed Windows command tokenisation: quoted arguments are now split into clean argv tokens without backslash-escape mangling (`split_command_args` in `permissions.py`), so `C:\...` paths and `"a b.txt"` arguments survive the deny-list check and reach the shell intact.
- Bounded remote file discovery: `git ls-files`-based discovery on SSH now enforces a 15s deadline and falls back to the slower SFTP walk (closing the hung channel) instead of blocking the tool call indefinitely.
- Write-journal backups are now read without the size cap, so a pre-existing file larger than `DEVX_MAX_FILE_BYTES` is still backed up in full and `devx changes reject` restores it byte-for-byte instead of truncating it.
- `devx lab` failures are now surfaced clearly: a failed `docker run` reports the daemon's stderr, a container that never becomes ready is torn down and reported, and `stop_lab` is idempotent (returns `False` — CLI prints "No running lab container" — instead of falsely claiming success).
- Secret scanning now guards the write path: in `block` mode a write whose content contains a credential is refused (the file is not created), and `warn` mode attaches a warning to the write result.
- `devx audit export --run <id>` no longer over-exposes other sessions: the export resolves the run's owner session, emits that run's events once, and filters DLQ entries and token usage to the run, so a `--run` audit contains only that run's rows.
- Documented the remote/container sandbox boundary: escape detection is lexical on SSH/container targets (no remote `readlink`), so symlinks inside the workspace pointing outside it are a known limitation (`docs/governed-remote.md`, `docs/threat-model.md`).
- Fixed the public-release pipeline: the `release.yml` `publish` job now declares `id-token: write` (job-level permissions previously overrode the workflow-wide OIDC permission, which would have made trusted publishing to PyPI fail with "missing or insufficient OIDC token permissions"), and the `changelog` job falls back to full-history output when no previous tag exists so the first `v*` tag release succeeds (`docs/gitflow.md` now documents the trusted-publisher setup and end-to-end release flow).
- Hardened release tagging: `version.yml` now creates the `vX.Y.Z` tag from the re-fetched branch tip (so the tag always carries the version-bump commit, never the pre-bump SHA that triggered the run) and is idempotent when the tag already exists. `docs/gitflow.md` now contains a step-by-step "cutting a release" walkthrough, hotfix flow, first-release notes, and troubleshooting table; `docs/production-roadmap.md` mirrors the automated tag/publish ordering.

## 0.1.0

- Initial local-first coding-agent package.
- LangGraph supervisor workflow with planner, coder, and tester stages.
- Approval-gated workspace tools, atomic writes, diffs, backups, and rollback.
- SQLite sessions, event history, and checkpoint-compatible run IDs.
- Textual TUI and Typer CLI.
- Optional local embeddings and semantic cache.
- Cross-platform CI and package-release workflows.

