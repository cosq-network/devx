# Governance, Safety & Secret Hygiene in `devx`

`devx` is engineered with a **defense-in-depth security model**. It assumes that coding models are stochastic and provides hard, deterministic constraints to guarantee that an autonomous agent cannot exfiltrate secrets, escape its sandbox, or overwrite your work without permission.

---

## 1. Secret Hygiene & Leak Prevention

`devx` includes an integrated credential scanner that monitors all files read by the agent, all files written to disk, and all context sent to the language model.

### Detection Coverage
The scanner matches:
- **OpenAI API Keys**: `sk-[A-Za-z0-9_-]{20,}`
- **Anthropic API Keys**: `sk-ant-[A-Za-z0-9_-]{20,}`
- **GitHub Personal Access Tokens**: `ghp_...`, `gho_...`, `github_pat_...`
- **AWS Access Keys**: `AKIA[0-9A-Z]{16}`
- **Slack Tokens**: `xox[baprs]-[0-9A-Za-z-]{10,}`
- **Private Key Blocks**: `-----BEGIN (RSA|OPENSSH|EC|DSA|PRIVATE) KEY-----`

### Pre-Masked Credential Storage
Whenever a secret is detected, `devx` immediately passes it through `mask_secret()`. Only the first and last four characters are kept (e.g. `sk-a1b2...9z8y`). **The raw credential is never returned in scan snippets, never written to log files, and never echoed into the TUI transcript.**

### Secret Scanning Modes (`DEVX_SECRET_SCAN`)

Configure runtime secret governance in your `.env` or environment:

```env
DEVX_SECRET_SCAN=warn   # off | warn | redact | block
```

| Mode | Reads | Writes |
|---|---|---|
| `off` | No scanning performed | No scanning performed |
| `warn` *(default)* | Scans read files; logs warnings if secrets are present | Scans written files; logs warnings |
| `redact` | Replaces secrets with `[REDACTED]` before sending to LLM | Writes proceed, warnings logged |
| `block` | Refuses to read secret-bearing files (`PermissionError`) | Refuses to write secret-bearing files |

### Prompt Context Redaction

During context assembly (`read_context` and file manifests), text is automatically filtered through `redact_secrets()` before being embedded into the model prompt. This guarantees that real credentials never reach external model providers.

### Pre-Commit / CI Sweep (`devx scan-secrets`)

Run `devx scan-secrets` to audit files locally before opening a pull request or in CI:

```bash
# Scan the entire workspace
devx scan-secrets

# Scan a specific directory
devx scan-secrets src/

# Machine-readable output for automated pipelines
devx scan-secrets --json
```

If any credentials are discovered, `devx scan-secrets` prints the location, line number, and masked snippet, and exits with code `1`.

---

## 2. Policy Presets (`DEVX_POLICY_PRESET`)

Policy presets bundle hard constraints designed for production and governed environments. They override individual agent preferences:

```bash
DEVX_POLICY_PRESET=strict devx ask "refactor the API" --remote dev-box:/srv/app
```

| Preset | Target Workspace | Behavior & Constraints |
|---|---|---|
| `default` | Local or Remote | Standard interactive approval prompts for file writes and shell commands |
| `remote_only` | Remote | Completely forbids running shell commands on the local workstation |
| `deny_local_writes` | Remote | Completely forbids modifying the local filesystem |
| `protected` | Local or Remote | Mandates strict approval for writes to sensitive files (`.env*`, `.git/*`, keys) |
| `strict` | Remote | Combines `remote_only` and `deny_local_writes` — all execution and writes happen remotely |

Presets are enforced before tool execution. For example, if an agent under `strict` attempts a local write, it is rejected immediately:
`PermissionError: Local filesystem modifications are forbidden under policy preset 'strict'.`

---

## 3. Granular Project Permissions (`.devx/config.json`)

For team-level enforcement, define fine-grained permission rules inside your project's `.devx/config.json`:

```json
{
  "permissions": [
    { "action": "read", "resource": "**", "effect": "allow" },
    { "action": "edit", "resource": "tests/**", "effect": "allow" },
    { "action": "edit", "resource": "src/core/security.py", "effect": "deny" },
    { "action": "shell", "resource": "git status", "effect": "allow" },
    { "action": "shell", "resource": "rm -rf *", "effect": "deny" }
  ]
}
```

- Rules are evaluated sequentially.
- Deny rules take precedence.
- Supports glob matching (`**`, `*`, `?`).

---

## 4. Change Journal & Hash-Guarded Rollback

Every file created or modified during an agent run is recorded in a SQLite change journal with **before and after SHA-256 hashes**.

### 1. Instant Global Undo (`/undo`)
In the TUI, typing `/undo` instantly restores every file touched in the last run back to its pre-run state.

### 2. Surgical File-by-File Revert (`devx changes reject`)
If the agent modified three files, but you only want to discard one:

```bash
# Inspect changes from the last run
devx changes list

# Revert a single file back to its exact pre-run hash
devx changes reject src/auth.py
```

`devx changes reject` verifies that the file has not been modified since the run before restoring it, preventing accidental overwrites of manual edits.

### 3. Drift Detection (`devx verify`)
Verify whether the current workspace still matches the change journal:

```bash
devx verify
# Or check a remote workspace:
devx verify --remote dev-box:/srv/app
```

Each change is categorized as:
- `intact`: File matches post-run state exactly.
- `reverted`: File was intentionally rolled back to pre-run state.
- `conflict`: File was edited by someone else after the agent finished.
- `missing`: File was deleted.

If any file is in `conflict` or `missing`, `devx verify` exits with code `1`. Hook this into your CI pipeline to catch unreviewed file drift after agent runs.

---

## 5. Fail-Closed Approval Architecture

When running parallel tools across threads or processes, `devx` adheres strictly to **fail-closed security**:

- If a file write or command requires approval and no approval handler is present (e.g. running headlessly in CI without an interactive terminal), the operation is **denied by default**:
  - `write_file` -> `{"error": "WRITE DENIED"}`
  - `run_command` -> `{"error": "COMMAND DENIED"}`
- Autonomous execution in CI or containerized runners is allowed only when explicitly initiated with `--approve-all`:

```bash
devx ask --approve-all "run pytest and format code"
```

---

## 6. Repository Rules & Skills Discovery (`.devx/rules/`)

To steer agent behavior without hardcoding instructions into every prompt, `devx` automatically discovers and applies repository-level rules and custom skills:

### Rules (`.devx/rules/*.md`)
Create markdown files under `.devx/rules/` to enforce project-specific coding standards, architectural guardrails, and security policies:
- `.devx/rules/coding-style.md`: Type annotation requirements, docstring standards.
- `.devx/rules/security.md`: Authentication requirements, sanitization rules.

`devx` automatically indexes these files, bounds their total size, and injects them directly into the agent prompt context.

### Custom Skills (`.devx/skills/`)
Custom multi-step prompt workflows can be organized into `.devx/skills/<skill-name>/SKILL.md` (or `.devx/skills/<skill-name>.md`). When discovered, skills are tagged and made available for agent planning and execution.

---

## 7. Local Execution Sandboxing (`bwrap`)

On Linux hosts, `devx` supports kernel-level isolation for shell command execution via **Bubblewrap** (`bwrap`):

```env
DEVX_SANDBOX=bwrap   # none | bwrap
```

When enabled:
- System directories (`/usr`, `/bin`, `/lib`, `/etc`) are mounted strictly read-only.
- Essential pseudo-filesystems (`/proc`, `/dev`, and a tmpfs `/tmp`) are isolated.
- Write access is strictly confined to the active workspace directory.
- If running on Windows or macOS (or if `bwrap` is not installed), `devx` gracefully falls back to standard execution with clear diagnostic logging.

---

## Next Steps

- Learn how `devx` minimizes token usage in [Token Optimization](07-token-optimization.md).
- Discover how to extend `devx` with custom tools in [Extensibility & MCP](08-extensibility-and-mcp.md).
- Explore operational observability and DLQ recovery in [Observability & Operations](09-observability-and-ops.md).
