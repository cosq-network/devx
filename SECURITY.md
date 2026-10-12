# Security Policy

## Defaults

The agent requires approval for file writes, command execution, and project
extension startup by default.
Workspace paths are resolved and must remain inside the configured workspace.
Shell execution uses argument arrays with `shell=False`, bounded output, timeouts,
and process cleanup.

Do not use `--approve-all` in an untrusted or shared workspace.

The CLI refuses to resolve approvals non-interactively: `--approval-cache` and
`--approval-matching` fail closed (exit code 2) unless an interactive terminal or
`--approve-all` is present.

## Governance and audit

- **Approval trail.** Every approval request is appended to an in-process history
  (`ApprovalManager.requests`) and, when a knowledge graph is attached, persisted
  as a node with the decision, risk score, and approval level. This is best-effort
  and never changes an approval outcome.
- **Risk overrides.** Actions with `risk_score > 80` are denied regardless of a
  permissive handler, and expired approvals (`approval_ttl` in the past) are
  denied.
- **Least privilege.** Ordered permission rules can `allow`, `ask`, or `deny`
  reads, edits, shell commands, plugins, and MCP tools. Only explicit `allow`
  rules can auto-approve under `--approval-matching`.
- **Team workspaces.** `devx team` stores a portable manifest and delegates to a
  shared SQLite session database. Do not place the shared database on an
  untrusted or world-writable share. See [`docs/team-layer.md`](docs/team-layer.md).

## Reporting

Do not disclose security issues publicly before maintainers have had an
opportunity to investigate. Include reproduction steps, affected version, and
platform details in a private maintainer report.

