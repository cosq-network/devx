# Threat model

This document describes the security model for `devx`, with particular
attention to **governed remote execution** (running the agent against an SSH
host or container). It states the assets, the trust boundaries, the threats we
mitigate, and — just as importantly — the threats we explicitly do **not**
mitigate.

## Scope and assets

Protected assets, in rough order of severity:

1. **Your source code and secrets** in the workspace (remote or local).
2. **Your infrastructure** — hosts reachable from the workspace, credentials,
   and production systems.
3. **Your provider credentials** (API keys) and **spend**.
4. **Integrity of your repository history** — changes must be attributable and
   reversible.

## Trust boundaries

```
 ┌──────────────┐   provider API    ┌──────────────┐
 │  Model        │◄─────────────────►│  devx        │
 │  (untrusted   │   prompts, tool   │  (trusted    │
 │   output)     │   calls           │   code)      │
 └──────────────┘                   └──────┬───────┘
                                           │ SSH / docker exec
                                    ┌──────▼───────┐
                                    │ Remote host  │
                                    │ or container │
                                    └──────────────┘
```

- **The model is untrusted.** Its output is data, not instructions. Tool calls
  derived from model output pass through the policy engine and approvals.
- **The workspace is trusted-by-placement, not by content.** Files could
  contain prompt-injection payloads; the model may be misled, but it still
  cannot exceed the workspace sandbox or skip approvals.
- **`devx` itself is trusted.** It runs with your user's privileges.

## Threats and mitigations

### T1. Path traversal outside the workspace

**Threat.** A prompt or a model-planned tool call reads or writes `/etc/passwd`,
`~/.ssh/id_rsa`, or `../../secrets`.

**Mitigation.** Every backend resolves relative and absolute paths against the
configured workspace root and raises on escape. Covered by unit tests for the
local, SSH, and container backends, and by the hermetic SSH end-to-end suite.

**Residual risk.** None known while the backend enforces `_resolve`; a custom
backend that skips it is out of scope.

### T2. Unbounded or lingering remote processes

**Threat.** A command starts a background server, an infinite loop, or a test
runner; the operator cancels, but the process keeps running on the host.

**Mitigation.** Commands run in their own session (`setsid`); their process
group is captured and the whole group is signalled on cancel or timeout. The
hermetic e2e test asserts that a `sleep`-then-write marker never appears after
cancellation.

**Residual risk.** A command that daemonizes into a *new* session
(double-fork) escapes the process group. Prefer bounded, foreground commands.

### T3. Silent connection loss / host confusion

**Threat.** The SSH link drops and the tool reconnects to a different host, or
fails ambiguously and the operator assumes success.

**Mitigation.** Liveness is probed before each operation; a dropped transport
is re-established only against the *same* configured host with backoff. A drop
mid-command raises a clear `ConnectionError` and invalidates the session.
Host keys are verified by default (`RejectPolicy`): an unknown or silently
rekeyed host is refused unless its key is in the `known_hosts` file you point at
(`DEVX_SSH_KNOWN_HOSTS`) or you explicitly opt in with `--accept-new-keys` /
`DEVX_SSH_ALLOW_UNKNOWN_KEYS` (throwaway `devx lab` containers, CI ephemeral
servers).

**Residual risk.** The `--accept-new-keys` / `DEVX_SSH_ALLOW_UNKNOWN_KEYS`
opt-in skips first-connection verification by design; use it only for
disposable targets. Outside that, host-key confusion is mitigated by the
refusal-to-connect default.

### T4. Irreversible edits

**Threat.** The agent corrupts or deletes code and the damage cannot be undone.

**Mitigation.** Every write is journaled with before/after hashes. Undo
(`/undo`, `devx changes reject`) restores a file only when its current hash
matches the run's output; a concurrently edited file is reported as a conflict
and left intact. Deletions of newly created files are the inverse operation and
are equally hash-guarded.

**Residual risk.** A file modified *and then reverted to its recorded hash* by
another party would be undone. This is a very narrow window.

### T5. Unapproved side effects

**Threat.** The agent edits files or runs shell commands without consent.

**Mitigation.** Writes and commands require approval unless explicitly
allow-listed by project rules or `--approve-all`. Policy presets add hard
constraints (`remote_only`, `deny_local_writes`, `strict`) that apply even to
approved actions. Destructive/system command detection and inline
destructive-pattern scanning run before approval.

**Residual risk.** `--approve-all` disables interactive review by design; use
it only in disposable environments.

### T6. Sensitive data leaving your infrastructure

**Threat.** Source code or secrets are exfiltrated to a third party.

**Mitigation.** The workspace is read and written in place. Providers are
configured separately and can be self-hosted; no code is copied to `devx`
servers. Audit exports you generate stay wherever you write them.

**Residual risk.** The *model provider you choose* sees whatever the agent
sends it (file contents selected for context, prompts). Choose a provider whose
data-handling terms you accept, or run a local model.

### T7. Untraceable changes

**Threat.** A change appears in the repo and no one can say which run or
approval produced it.

**Mitigation.** The change journal records run id, path, before/after hash, and
whether the action was approved. `devx audit export` packages sessions, events,
changes, dead-lettered work, and token usage into one portable stream.

**Residual risk.** Edits made outside `devx` are not journaled.

### T8. Credential exposure in logs

**Threat.** Passwords or keys leak into logs or error output.

**Mitigation.** Connection errors report host and port, not the password.
`detect-secrets` runs in CI, and security scans (bandit, pip-audit, trivy) gate
the build.

**Residual risk.** A private key path may appear in diagnostics; key *contents*
are never logged.

## Non-goals

- **Defending against a malicious `devx` build.** A compromised binary has your
  privileges. Verify what you install.
- **Sandboxing the model's reasoning.** We constrain *effects* (tools,
  approvals, sandbox), not the model's internal thoughts.
- **Network egress control.** `devx` does not firewall the remote host; a
  command you approve can still make outbound connections.
- **Multi-tenant isolation inside one container.** Containers are assumed to be
  per-developer or otherwise isolated by the platform.
- **Following remote symlinks.** On SSH/container backends the sandbox is
  lexical — the client rejects path strings that escape the root, but it does
  not resolve symlinks on the far side. A symlink inside the workspace root
  pointing outside it would be followed by SFTP/`docker exec`. Keep untrusted
  remote workspaces free of absolute symlinks (the local backend resolves and
  rejects escaping symlinks itself).

## Operating recommendations

- Run remote commands as a **least-privilege user** whose access is scoped to
  the workspace.
- Prefer `DEVX_POLICY_PRESET=strict` for remote runs.
- Preflight with `devx doctor --remote <uri>` before trusting a new host.
- Keep `--approve-all` out of shared or production environments.
- Export audit trails to storage you control.

## Reporting

Found a gap between this model and the implementation? Report it as a security
issue rather than a public bug. See the repository's security policy for the
private channel.
