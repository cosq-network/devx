# Positioning

This document is the canonical description of what `devx` is, who it is for, and
what it deliberately is **not**. It exists so that README, release notes, and
marketing copy stay consistent.

## Position statement

> `devx` is a **local-first, approval-gated AI coding agent** for teams that need
> to run agents against real repositories — remote machines included — without
> handing over unbounded file writes, shell execution, or token spend.

We differentiate on three axes that generic "AI coding agent" tools treat as
afterthoughts:

1. **Remote-first workspaces** — a pluggable `WorkspaceBackend` (local or SSH)
   means the same agent, approvals, and journals apply whether the code lives on
   your laptop or a jump-hosted server.
2. **Cost visibility and control** — bounded reads, bounded commands, bounded
   context, local embeddings, and an offline benchmark harness. Savings are
   measured, not promised (see [`benchmark-methodology.md`](benchmark-methodology.md)).
3. **Governance** — approval gating, ordered permission rules, path sandboxing,
   a dead-letter queue for failed runs, and structured audit events.

## What `devx` is not

- Not a cloud-hosted agent platform with a proprietary backend.
- Not an "autonomous, approve-everything" agent. The default posture is deny.
- Not a benchmark leaderboard entry. We publish a reproducible *offline DX
  harness*, not fixed percentage savings.

## Ideal customer profile

| Signal | Fits `devx` | Does not fit |
|---|---|---|
| Repository size | Medium → large, multi-language | Toy single-file scripts |
| Environment | Local + remote/bastion servers | Browser-only workflows |
| Governance need | Approval trails, least-privilege rules | "Just let it edit everything" |
| Cost sensitivity | Needs per-run token awareness | Unmetered experimentation |
| Team shape | Small platform/DevEx teams | Individual hobbyist (core tier is enough) |

## Tier matrix

`devx` is distributed as a **free core** with an optional **team layer** in
development. The split is deliberately small: the core must remain fully usable
standalone.

| Capability | Core (free) | Team (prototype) |
|---|---|---|
| TUI + CLI agent loop | Yes | Yes |
| Approval gating & permission rules | Yes | Yes |
| Local + remote SSH workspaces | Yes | Yes |
| Sessions, DLQ, observability, benchmarks | Yes | Yes |
| Shared team manifest (`.devx/team.json`) | — | Yes |
| Team membership registry | — | Yes |
| Shared session database + usage counters | — | Yes |
| Central policy distribution | — | Planned |
| Per-member cost dashboards | — | Planned |
| SSO / audit export | — | Planned |

The prototype today is intentionally local: `devx team init/status/invite/
list-sessions/metrics` operate on files and SQLite. No server component is
required or shipped. See [`team-layer.md`](team-layer.md).

## Claims discipline

Any quantitative claim in README, CHANGELOG, or docs must be one of:

1. Reproducible with `python scripts/benchmark.py` (offline, deterministic), or
2. Explicitly labelled as workload-dependent, with a pointer to the benchmark
   methodology.

Unsubstantiated "X% faster/cheaper" copy is treated as a documentation bug.
