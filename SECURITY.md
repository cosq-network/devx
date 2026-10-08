# Security Policy

## Defaults

The agent requires approval for file writes, command execution, and project
extension startup by default.
Workspace paths are resolved and must remain inside the configured workspace.
Shell execution uses argument arrays with `shell=False`, bounded output, timeouts,
and process cleanup.

Do not use `--approve-all` in an untrusted or shared workspace.

## Reporting

Do not disclose security issues publicly before maintainers have had an
opportunity to investigate. Include reproduction steps, affected version, and
platform details in a private maintainer report.

