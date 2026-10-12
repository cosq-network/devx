# Governed remote execution

`devx` can run the coding agent **where the code lives** — over SSH or inside a
container — while keeping the same governance it applies locally: per-action
approvals, a change journal with hash-guarded undo, and a portable audit trail.
Your model and your keys stay on your side of the connection; code never has to
leave your infrastructure.

This document covers the supported remote targets, the safety guarantees, the
governance controls, and how to verify a setup before you trust it.

## Why

- **Code stays put.** The agent reads and edits the repository on the remote
  host or in the container. Nothing is copied to a third party.
- **Bring your own model.** Providers are configured independently of the
  workspace, so an on-prem or self-hosted model is a first-class option.
- **Reversible by construction.** Every write is journaled with a before/after
  hash; `/undo` and `devx changes reject` refuse to clobber a file that changed
  since the run.
- **Auditable.** `devx audit export` emits a single portable stream of
  sessions, events, file changes, dead-lettered work, and token usage.

## Remote targets

### SSH (`ssh://`)

```bash
devx doctor --remote deploy@bastion:/srv/app
devx ask "fix the failing test" --remote deploy@bastion:/srv/app
```

URI forms accepted by `SshConfig.from_uri`:

| Form | Example |
| --- | --- |
| `user@host:/path` | `deploy@prod:/srv/app` |
| `ssh://user:password@host:port/path` | `ssh://deploy:s3cret@prod:2222/srv/app` |

Details not in the URI are resolved from `~/.ssh/config` (host, user, port,
`IdentityFile`, `ProxyCommand`) and from the environment:

| Variable / setting | Purpose |
| --- | --- |
| `DEVX_REMOTE_SSH` | Default remote URI when `--remote` is not passed |
| `DEVX_SSH_KEY` | Private key path (`ask --ssh-key` overrides per run) |
| `.devx/config.json` `remote_ssh`, `ssh_key_path` | Project-level defaults |

### Containers (`container://` / `docker://`)

```bash
devx ask "run the suite" --container container://api-box/app
```

| Form | Meaning |
| --- | --- |
| `container://<name>/<path>` | Run in container `<name>`, workspace `<path>` |
| `container://<name>` | Workspace defaults to the container's working directory |
| `docker://<name>/<path>` | Alias for the same backend |

File operations use the container's shell (`docker exec`); no bind mount is
required, so the container's own filesystem view is authoritative. Set
`DEVX_CONTAINER` or `container` in `.devx/config.json` to make it the default.

## Safety guarantees

1. **Workspace sandbox.** All paths are resolved against the remote root; any
   path that escapes it (`../`, absolute paths outside the root) raises
   immediately. A compromised model prompt cannot reach `/etc/passwd`.
2. **Process-group cancellation.** Remote commands run under
   `setsid sh -c '…'`, and the process-group id is captured. Cancelling a run
   or hitting the timeout sends `SIGTERM` to the whole group, so background
   children (`&`, test runners, servers started by the command) die with it —
   closing the channel is not enough.
3. **Hash-guarded undo.** Each write records `before_hash` and `after_hash`.
   Undo restores a file only if its current hash still matches what the run
   produced; otherwise it reports a conflict and leaves the file alone.
4. **Fail closed.** Connection loss is surfaced as a clear `ConnectionError`
   and the session is invalidated, never silently retried against a different
   host.

## Connection health and recovery

- A dropped transport is detected before each operation and transparently
  re-established (with exponential backoff, three attempts).
- TCP keepalive is enabled on the transport to survive NAT and idle firewalls.
- `SshWorkspaceBackend.health_check()` runs a trivial remote command and
  reconnects once on failure; it returns a boolean and never raises.
- `devx doctor --remote <uri>` preflights connection, authentication, the
  workspace path, writability, `git`, and the virtualenv before you start.

## Governance controls

### Approvals

Remote runs use exactly the same approval path as local runs: file writes and
shell commands prompt before executing, and `--approve-all`, `--approval-cache`,
and `--approval-matching` behave identically.

### Policy presets

Presets are named bundles of hard constraints. Select one with
`DEVX_POLICY_PRESET` or `policy_preset` in `.devx/config.json`.

| Preset | Effect |
| --- | --- |
| `default` | Prompt for edits and shell commands on the selected workspace |
| `remote_only` | Refuse to execute shell commands on the local machine |
| `deny_local_writes` | Refuse to modify the local filesystem |
| `protected` | Always require approval for writes to sensitive files (`.env*`, keys, secrets) |
| `strict` | Remote execution only, no local writes (recommended for governed remote runs) |

```bash
DEVX_POLICY_PRESET=strict devx ask "refactor the parser" --remote deploy@prod:/srv/app
```

The constraints are enforced in `WorkspaceTools`: a local command under
`remote_only`, or a local write under `deny_local_writes`, raises before the
tool runs — regardless of whether the action was otherwise approved.

### File-by-file accept / reject

Every applied write is recorded in the change journal. Review and selectively
revert them:

```bash
devx changes list
devx changes list --run 4f2a1c --json
devx changes accept src/parser.py --run 4f2a1c   # keep
devx changes reject src/legacy.py --run 4f2a1c   # hash-guarded revert
```

`reject` reuses the same backend (`--remote` / `--container`) as the run, so it
reverts the file where the change was made. If the file changed since the run,
`reject` exits non-zero and leaves it untouched.

### Audit trail

```bash
devx audit export --format jsonl --out audit.jsonl
devx audit export --session my-session --format json
```

The export contains sessions, agent events, journal changes, the dead-letter
queue, and per-model token usage in one stream.

## Cost visibility

Remote runs record token usage, which `devx usage` turns into dollars using a
per-model pricing table:

```bash
devx usage
devx usage --session my-session --json
```

Models without a known price are listed as *unpriced* rather than estimated.

## Verifying a deployment

1. `devx doctor --remote <uri> --json` — connection, auth, path, write access,
   git, virtualenv.
2. `DEVX_POLICY_PRESET=strict devx ask "<small task>" --remote <uri>` — observe
   the approval prompts and the resulting change journal.
3. `devx changes list` then `devx changes reject <path>` — confirm a single-file
   revert works and is hash-guarded.
4. `devx audit export --out audit.jsonl` — confirm the run is fully recorded.

## Limitations

- SSH and container targets are supported; orchestrator/Kubernetes backends are
  on the roadmap, not in this release.
- Connection multiplexing (ControlMaster-style reuse across processes) is not
  yet implemented; a single `SshWorkspaceBackend` reuses one connection for the
  life of a run.
- Policy presets are local to each checkout today; team-wide policy
  distribution is planned.
