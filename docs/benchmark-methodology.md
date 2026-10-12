# Benchmark methodology

`devx` ships an **offline, deterministic benchmark harness** at
`scripts/benchmark.py` driven by `benchmarks/cases.json`. It exists so that the
guarantees the project makes — bounded reads, bounded commands, bounded context,
and rollback — are verified rather than asserted.

## Why not a token-savings leaderboard?

Token and cost savings depend on the repository, the task, the model, and the
provider. Publishing a single fixed percentage (for example, "reduces tokens by
80–90%") would be dishonest across those variables. Instead, the project:

- keeps token-reduction features (diagnostic distillation, planner/coder context
  decoupling, semantic cache) **off by default where they could surprise users**,
  or clearly configurable;
- measures the *mechanical* guarantees offline;
- points at the harness for anyone who wants to reproduce numbers on their own
  repository.

## What is measured

Each case in `benchmarks/cases.json` is run in a fresh temporary workspace and
must satisfy its own assertion. The run fails loudly if a guarantee regresses.

| Case | Guarantee |
|---|---|
| `bounded_read` | A read never returns more than the configured byte budget |
| `command_safety` | Destructive commands are blocked before execution |
| `journal_rollback` | Approved writes are journaled and can be restored |
| `context_budget` | Selected context never exceeds the configured byte budget |

## Running it

```bash
python scripts/benchmark.py
```

Output is JSON:

```json
{
  "results": [
    {"name": "bounded-read", "elapsed_ms": 6.8, "metrics": {"output_bytes": 128}},
    {"name": "command-safety", "elapsed_ms": 1.2, "metrics": {"blocked": true}}
  ]
}
```

`elapsed_ms` is informational only and must not be quoted as an SLA: it varies
with hardware and CI load.

## Adding a case

1. Append a case object to `benchmarks/cases.json` with a unique `name` and a
   `kind`.
2. Add the matching branch in `scripts/benchmark.py::_run_case`.
3. Make the case assert its own invariant and return machine-checkable
   `metrics`.
4. Wire it into CI (`.github/workflows/ci.yml` already runs the harness).

## Relationship to product claims

- Mechanical guarantees (bounded output, blocked destructive commands,
  rollback) may be stated as facts, because CI verifies them.
- Token/cost reductions are **workload-dependent** and must be described as such,
  with a pointer to this document.
