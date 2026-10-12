# Production Readiness Roadmap

This roadmap defines the work required to move `devx` from a strong staging
implementation to a production-ready coding agent. It is intentionally gated:
each phase has explicit acceptance criteria, and a later phase must not be
considered complete because an earlier phase is unfinished.

## Current baseline

The project currently has:

- A local-first CLI and Textual TUI.
- Provider and model configuration through `.env`, user configuration, and
  the reusable `/setup` panel.
- Curated provider model catalogs for coding and tool-calling models.
- Slash-command autocomplete and `@` workspace mentions.
- Approval-gated workspace tools.
- Session persistence, export/import, rollback support, and async checkpoint
  support.
- Bounded approval previews, `/diff`, `/undo`, `/plan`, and CLI `--plan-only`.
- Configurable lint/test checks, bounded context selection, and structured
  cancellation.
- Model-aware semantic caching and a deterministic offline benchmark suite.
- Ordered project permissions, bounded context compaction, a shared tool
  registry, workspace plugins, and stdio MCP tool support.
- Unit, TUI, mocked integration, and configuration tests.

The latest local validation includes 73 passing tests, with newly implemented
observability (structured JSON logs, correlation IDs, Prometheus/JSON metrics, health probes),
resilience (CircuitBreaker pattern, exponential backoff, Dead Letter Queue), parallel execution
(parallel coder with batch reads, concurrent lint/test), and project initialization (`devx init`), provider-contract
coverage, linting, compilation, diff validation, and passing offline
benchmarks. This is not yet equivalent to live-provider, cross-platform,
security, or release acceptance.

## Phase 1: Runtime foundation (implemented; hardening remains)

### Objectives

The core runtime behavior expected from a reliable coding agent is implemented.
Remaining work is focused on cross-platform and live-provider hardening.

### Work items

1. ~~Replace custom synchronous orchestration with a durable LangGraph
   `StateGraph` for planner, coder, tester, and supervisor stages.~~
2. ~~Persist graph state and checkpoints so interrupted runs can resume safely.~~
3. ~~Implement a bounded tester/fix loop with explicit maximum attempts.~~
4. ~~Detect common project test frameworks and select safe test commands.~~
5. ~~Integrate token and agent-event streaming into the TUI transcript.~~
6. ~~Complete conversational session resume, including transcript restoration,
   active session switching, and recovery after process restart.~~
7. ~~Make provider and model changes refresh the active status and apply clearly
   to future runs.~~
8. ~~Keep all command failures user-visible and non-fatal to the TUI.~~

### Exit criteria

- The implemented runtime acceptance criteria are covered by deterministic and
  mocked tests; credentialed live acceptance and full cross-platform recovery
  testing remain release gates.

## Phase 2: Test and quality hardening

### Objectives

Make regressions difficult to introduce and make failures diagnosable.

### Required test layers

#### Unit tests

Cover:

- Configuration precedence and secret masking.
- Provider and model catalog filtering.
- Command parsing and autocomplete.
- Workspace path containment.
- Approval policies.
- Context bounds and deterministic file selection.
- Approval previews, diff/undo, plan-only, lint configuration, and benchmark
  behavior.
- Session serialization, import, export, rename, and recovery.
- Retry and cancellation state transitions.

#### Integration tests

Use mocked providers and temporary workspaces to verify:

- OpenAI-compatible model discovery.
- Tool-call request and response handling.
- Provider authentication and rate-limit errors.
- Planner-to-coder-to-tester transitions.
- Approval prompts around file and command operations.
- Session persistence across multiple runs.
- TUI command flows and modal interactions.

#### Live acceptance tests

Run separately from the deterministic suite using explicit credentials. Verify
at least one supported provider can:

- Answer a normal prompt.
- Produce a tool/function call.
- Read a workspace file.
- Make an approved minimal edit.
- Run a project test command.
- Report test failures and perform a bounded repair.
- Handle cancellation and provider errors cleanly.

### Exit criteria

- The deterministic suite passes without network access.
- Mocked integration tests cover every critical external boundary.
- Live acceptance tests pass against at least one credentialed provider.
- No test depends on credentials, a developer home directory, or a dirty
  workspace.
- Existing warnings are either resolved or documented with an owner and
  deadline.

## Phase 3: Cross-platform CI/CD

### Required matrix

Run the required checks on:

| Operating system | Python versions |
|---|---|
| Ubuntu Linux | supported minimum and latest supported |
| Windows | supported minimum and latest supported |
| macOS | supported minimum and latest supported |

### Required CI checks

```text
python -m pip install --upgrade pip
python -m pip install -c constraints.txt -e ".[agent,tui,cli,dev]"
python -m pytest -q
python -m ruff check src tests scripts
python -m compileall -q src tests scripts
python scripts/benchmark.py
```

Add separate jobs for:

- Package build.
- Clean wheel installation.
- TUI smoke tests with Textual installed.
- Dependency and security scanning.
- Optional live-provider acceptance tests.

### Branch and release rules

- `develop` is the integration branch.
- Feature branches merge into `develop` through pull requests.
- `release/*` branches stabilize a release candidate.
- `main` contains production releases only.
- Required CI checks must pass before merging into `develop` or `main`.
- Release tags are created automatically from `release/*`/`hotfix/*` branch
  pushes and always point at the version-bump commit.
- Semantic versioning must be derived from the release branch and commit
  metadata, with no manual version drift between package metadata and runtime
  version information.

### Exit criteria

- The full matrix passes for every release candidate.
- A fresh checkout can build and install the wheel on all three operating
  systems.
- CI fails closed when tests, lint, build, or security checks fail.
- Offline benchmarks pass without provider credentials.
- Release artifacts are retained and checksummed.

## Phase 4: Security and dependency readiness

### Required scans

Run project-scoped scans in a clean environment:

```text
pip-audit
bandit -r src
```

Also review dependency licenses and transitive dependency changes during every
release.

### Security requirements

- Never write API keys to transcripts, session state, or event logs.
- Mask secrets in `/config show`, diagnostics, and error reports.
- Keep `.env` and user config files out of version control.
- Enforce workspace containment for `@` mentions and tool paths.
- Require approval for writes, deletes, shell commands, and other risky tools.
- Apply command timeouts, output limits, and tool-call budgets.
- Validate imported sessions before writing them to the session database.
- Avoid logging full prompts or file contents by default.
- Review all URL fetching and subprocess calls before release.

### Exit criteria

- No unresolved critical or high-severity project dependency vulnerabilities.
- Security scans run automatically in CI.
- A manual review confirms secret, workspace, approval, and subprocess
  boundaries.
- Security regression tests cover every previously fixed issue.

## Phase 5: Clean installation and operator validation

Test from a clean checkout and a clean user environment.

### Virtual environment validation

```text
python -m venv .venv
```

Windows:

```text
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -c constraints.txt -e ".[agent,tui,cli,dev]"
devx doctor
devx --help
devx
```

Linux and macOS:

```text
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -c constraints.txt -e ".[agent,tui,cli,dev]"
devx doctor
devx --help
devx
```

### Wheel validation

```text
python -m build
python -m pip install dist/*.whl
devx doctor
```

Verify that the installed command works from a directory outside the project
and that `DEVX_WORKSPACE` follows the command's current working directory as
documented.

### Exit criteria

- Editable and wheel installs both work.
- `devx`, `devx doctor`, `devx --help`, and the default TUI launch work.
- First-time provider setup works without pre-existing configuration.
- `.env` and user-wide configuration precedence is correct.
- Clipboard, autocomplete, modal navigation, and session commands work on all
  supported operating systems.

## Phase 6: Observability and operations

Add the controls needed to support real users without exposing sensitive data.

- Structured logs with run, session, provider, and model identifiers.
- Secret and sensitive-content redaction before logging.
- Clear error categories for configuration, provider, tool, test, timeout, and
  cancellation failures.
- Configurable retry, timeout, token, and tool-call budgets.
- Diagnostic export that excludes API keys and sensitive file contents.
- Health information through `devx doctor`.
- Documented recovery, rollback, and session-export procedures.
- A support process for provider outages and model catalog changes.

### Exit criteria

- A failed run can be diagnosed from redacted logs and a diagnostic export.
- Operators can distinguish configuration failures from provider failures.
- Recovery and rollback procedures are documented and tested.

## Phase 7: Release candidate and production gate

Create a release candidate only after Phases 1–6 are complete.

### Release candidate checklist

- [ ] All unit and integration tests pass.
- [ ] Live provider acceptance tests pass.
- [ ] Linux, Windows, and macOS CI passes.
- [ ] Clean editable and wheel installation passes.
- [ ] TUI works with the installed Textual runtime.
- [ ] `devx doctor` reports a healthy installation.
- [ ] Security and dependency scans pass.
- [ ] No critical or high-severity open defects remain.
- [ ] Documentation matches the shipped command and configuration behavior.
- [ ] Version, changelog, and release artifacts are consistent.
- [ ] Rollback and support procedures are ready.

### Production release procedure

1. Create a `release/x.y.z` branch from `develop`.
2. Run the complete CI matrix and live acceptance suite.
3. Review dependency, security, and release notes changes.
4. Push the release branch; `version.yml` commits the version bump and pushes
   the `vX.Y.Z` tag pointing at the bump commit automatically.
5. `release.yml` (triggered by the tag) builds the wheel/sdist, Sigstore-signs
   them, and publishes to PyPI via trusted publishing, emitting an SBOM and a
   changelog artifact (see `docs/gitflow.md` for the full walkthrough and the
   one-time PyPI trusted-publisher registration).
6. Verify the build, signature, and PyPI upload, then merge the release branch
   into `main` and back into `develop`.
7. Monitor the first release and record any operational issues.

## Definition of production-ready

The project is production-ready only when every release-candidate checklist
item is complete and the following statement is true:

> A new user can install `devx` on a supported operating system, configure a
> supported provider, run an approval-gated coding task, recover from a failed
> or interrupted run, and receive safe, diagnosable behavior without exposing
> credentials or leaving the workspace boundary.
