# Team layer (prototype)

The team layer is the optional, paid direction for `devx`. This document
describes what exists today, what is deliberately deferred, and the data model so
contributors can extend it without breaking the free core.

## Design principles

1. **Local-first.** No server component is required. Team state lives in a JSON
   manifest plus a shared SQLite session database.
2. **Opt-in.** The core never reads team state unless a `devx team` command is
   invoked.
3. **Cheap to verify.** Every command is offline and covered by unit tests.

## Current surface

```
devx team init <name>          # create .devx/team.json
devx team invite <member>      # add a member id (deduplicated)
devx team status               # show team info + usage counters
devx team metrics              # alias of status (machine-readable keys)
devx team list-sessions        # list sessions from the shared session DB
```

## Data model

`.devx/team.json` (see `src/devx/team.py::TeamManifest`):

```json
{
  "team_id": "8f2c…",
  "name": "acme",
  "shared_session_db": "/shared/acme/.devx/sessions.db",
  "members": ["alice", "bob"],
  "created_at": "2026-10-12T00:00:00+00:00"
}
```

`shared_session_db` defaults to `<workspace>/.devx/sessions.db`. Pointing it at a
network share is what makes usage counters "team" rather than "local".

Counters are derived from the existing session schema — no new tables:

| Metric | Source |
|---|---|
| `sessions` | `SessionStore.list_sessions()` |
| `events` | `SessionStore.events(session_id)` summed |
| `dlq_entries` | `SessionStore.get_dlq_entries()` |
| `members` | `TeamManifest.members` |

## Deliberately deferred

These require a control plane and are **not** implemented:

- Central policy distribution (push permission rules to members).
- Per-member token/cost attribution (needs a usage table and provider hooks).
- SSO and audit-log export.
- Remote worker coordination.

Until then, the tier matrix in [`positioning.md`](positioning.md) marks them as
planned.

## Extending safely

- Add behavior to `src/devx/team.py`, not to the CLI directly, so it stays
  testable without CliRunner.
- Keep `TeamManifest` JSON backward compatible; treat unknown keys as
  forward-compatible and missing optional keys as defaults.
- Any new command must work with no network access and add a unit test in
  `tests/test_team.py`.
