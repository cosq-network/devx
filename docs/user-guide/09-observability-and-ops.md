# Observability, Operations & Team Layer in `devx`

Operating an autonomous coding agent in production requires deep visibility into latency, token expenditures, error states, and team collaboration. `devx` provides a production-grade operations suite covering Prometheus metrics, LangSmith tracing, automated Dead-Letter Queue (DLQ) recovery, and team coordination.

```mermaid
graph TD
    DEVX[devx Agent Execution] --> METRICS[Prometheus Metrics (/metrics)]
    DEVX --> TRACING[LangSmith Tracing]
    DEVX --> PRICING[Token Cost Accounting (devx usage)]
    DEVX --> AUDIT[Audit Log Stream (devx audit)]
    
    DEVX -->|Unrecoverable Error| DLQ[Dead Letter Queue (devx session dlq)]
    DLQ --> RETRY[Retry / Replay Pipeline]
    
    DEVX --> TEAM[Team Manifest (.devx/team.json)]
```

---

## 1. Metrics & Prometheus Export

`devx` includes an integrated metrics collector supporting the Prometheus / OpenMetrics text exposition format.

### Exporting Metrics
Export metrics to stdout or pipe directly into a file:

```bash
# Export Prometheus exposition format
devx metrics

# Or machine-readable JSON
devx metrics --format json

# Reset metric counters after scraping
devx metrics --reset
```

### Metrics Exported:
- **Gauges**: Active agent runs, memory utilization.
- **Counters**:
  - `devx_runs_total`: Total runs categorized by status (`status="completed"`, `status="error"`).
  - `devx_tool_calls_total`: Tool calls categorized by tool name (`tool="write_file"`, `tool="run_command"`).
  - `devx_circuit_breaker_trips_total`: Count of tripped circuit breakers.
- **Histograms**:
  - Cumulative buckets (`10ms`, `50ms`, `100ms`, `250ms`, `500ms`, `1s`, `5s`, `10s`, `30s`, `60s`, `+Inf`).
  - Measures LLM invocation latency, test execution durations, and file I/O operations.
  - Proper label escaping for OpenMetrics scrapers.

---

## 2. Distributed Tracing with LangSmith

Track execution graphs, prompt templates, tool calls, and model latency using LangSmith.

### Enabling Tracing

Set the following environment variables in your `.env` or system environment:

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_...
LANGSMITH_PROJECT=devx-coding-agent   # Optional: customize project dashboard
```

When enabled:
- LangChain model calls and LangGraph state transitions are automatically streamed to your LangSmith project.
- **Credential Safety**: `devx` ensures that API keys and authentication tokens are never copied into trace payloads, logs, or state dictionaries.

---

## 3. Operational Resilience: DLQ & Circuit Breakers

### The Dead Letter Queue (DLQ)

When an agent run fails unexpectedly due to unrecoverable errors (e.g. abrupt network disruption, server crash, or API timeout), the full session state is saved to the **Dead Letter Queue (DLQ)** in SQLite.

```bash
# 1. View all failed runs in the DLQ
devx session dlq

# Example Output:
# ID: 3 | Session: 4a2f8b1c | Run: 9e10ab42 | Retries: 0 | 2026-10-17T12:00:00Z
#   Error: ConnectionResetError: Remote end closed connection without response

# 2. Replay and retry the failed run from its exact state
devx session dlq-retry 3

# 3. Discard a DLQ entry once resolved
devx session dlq-remove 3
```

### Circuit Breakers (`llm.py`)

To protect your budget against cascading failures or infinite retry loops during provider outages, all LLM calls pass through a three-state **Circuit Breaker**:

1. **CLOSED**: Normal operation. Calls pass through.
2. **OPEN**: If consecutive API failures exceed the failure threshold (default: 5), the circuit trips to `OPEN`. All subsequent calls immediately fail fast with `ModelRateLimitError`, preventing runaway retry storms.
3. **HALF_OPEN**: After a recovery timeout (default: 30 seconds), the circuit enters `HALF_OPEN` and permits a single test call. If successful, it returns to `CLOSED`; if it fails, it trips back to `OPEN`.

---

## 4. Cost Accounting & Pricing Table

`devx` tracks exact prompt tokens, completion tokens, and cached tokens across every execution step.

### Viewing Spend with `devx usage`

```bash
# Total spend across all recorded sessions
devx usage

# Filter usage by session
devx usage --session feature-auth

# Generate a single-run Markdown cost receipt
devx usage --run 4f2a1c --markdown
```

Example Markdown receipt:
```text
# Usage receipt for run 4f2a1c

| provider   | model       | calls | prompt tokens | completion tokens | total tokens | cost (USD) |
|------------|-------------|-------|---------------|-------------------|--------------|------------|
| openrouter | gpt-4o      | 2     | 4,210         | 680               | 4,890        | $0.0152    |
| openrouter | free-router | 1     | 18,200        | 1,200             | 19,400       | $0.0000    |
| **Total**  |             | **3** | **22,410**    | **1,880**         | **24,290**   | **$0.0152**|
```

### Free Model Zero-Rating
Models with the `:free` tag (such as `openrouter/free` or `poolside/laguna-s-2.1:free`) and local Ollama models (`qwen2.5-coder`, `llama3.1:8b`) are recognized by `devx.pricing` and explicitly zero-rated in cost receipts.

---

## 5. Tamper-Evident Audit Streams

Export a unified, sequential audit stream containing all session events, tool calls, change records, and DLQ entries:

```bash
# Export full audit stream in JSON Lines format
devx audit --format jsonl --out audit-trail.jsonl

# Export a single session in JSON with hash verification
devx audit --session my-session --verify --format json
```

With `--verify`, `devx audit` checks before/after SHA-256 hashes for every file modification in the change journal, ensuring that the audit log cannot be forged.

---

## 6. Team Workspace Layer (`devx team`)

`devx` includes a lightweight, serverless collaboration layer for engineering teams who share repositories or common compute servers.

### The Team Manifest (`.devx/team.json`)
The team layer coordinates shared state through a portable JSON manifest located at `.devx/team.json`:

```json
{
  "team_id": "c71a39f0-28b4-4b5c-b17d-84e1b42d765a",
  "name": "Backend Infrastructure",
  "shared_session_db": "/mnt/shared/devx/sessions.db",
  "members": [
    "alice@company.internal",
    "bob@company.internal"
  ],
  "created_at": "2026-10-17T12:00:00Z"
}
```

### Team CLI Commands:

```bash
# Initialize a team manifest in the workspace
devx team init --name "Core Platform Team"

# Check team workspace status
devx team status

# Register a team member identifier
devx team add-member "carol@company.internal"

# Export team configuration
devx team export
```

---

## Next Steps

- Return to the [User Guide Index](../user-guide.md).
- Explore the [CLI Reference](03-cli-reference.md) for all options.
- Review [Architecture Guide](../architecture.md) for deeper internals.
