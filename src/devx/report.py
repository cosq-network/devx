"""PR-ready run and session reports (``devx report``).

A report stitches together everything devx already records about a run — the
agent event timeline, the hash-guarded change journal, and token usage — into a
single human- and machine-readable summary that can be dropped straight into a
PR description or an audit review.
"""

from __future__ import annotations

from typing import Any

from devx.harness.session import SessionStore
from devx.pricing import format_cost


def build_report(store: SessionStore, journal: Any, identifier: str) -> dict[str, Any]:
    """Build a structured report for a session (id/name) or a run id.

    ``identifier`` is resolved either as a saved session (via the session store)
    or as a run id searched across sessions. Raises ``KeyError`` when neither
    matches.
    """
    loaded = store.load(identifier)
    if loaded is not None:
        session_id = loaded["id"]
        run_id = (loaded.get("metadata") or {}).get("last_run_id")
        state = loaded.get("state") or {}
        events = store.events(session_id, run_id=run_id) if run_id else store.events(session_id)
    else:
        session_id, events = store.find_run(identifier)
        if not events:
            raise KeyError(f"No session or run found for {identifier!r}")
        run_id = identifier
        # For run lookups that resolve to a saved session, pull its state too.
        state = {}
        if session_id:
            session_row = store.load(session_id)
            if session_row is not None:
                state = session_row.get("state") or {}

    session_rows = [row for row in store.list_sessions() if row["id"] == session_id]
    created_at = session_rows[0]["created_at"] if session_rows else ""
    updated_at = session_rows[0]["updated_at"] if session_rows else ""

    usage = store.aggregate_usage(run_id=run_id) if run_id else store.aggregate_usage(session_id)
    changes = journal.for_run(run_id) if run_id else []
    verified = {entry["path"]: entry["status"] for entry in journal.verify(run_id)} if run_id else {}

    change_rows: list[dict[str, Any]] = []
    for change in changes:
        change_rows.append(
            {
                "path": change.path,
                "approved": change.approved,
                "before_hash": change.before_hash,
                "after_hash": change.after_hash,
                "status": verified.get(change.path, "intact"),
            }
        )

    return {
        "identifier": identifier,
        "session_id": session_id,
        "session_name": loaded.get("name") if loaded else None,
        "created_at": created_at,
        "updated_at": updated_at,
        "run_id": run_id,
        "goal": _goal(state, events),
        "result": state.get("result", ""),
        "status": state.get("status", "unknown"),
        "timeline": _timeline(events),
        "checks": _checks(events),
        "usage": usage,
        "changes": change_rows,
    }


def _goal(state: dict[str, Any], events: list[dict[str, Any]]) -> str:
    task = state.get("task")
    if task:
        return str(task)
    for item in state.get("messages", []):
        if isinstance(item, dict) and item.get("role") == "user":
            return str(item.get("content", ""))[:2000]
    for entry in events:
        event = entry["event"]
        if event.get("type") == "user_message":
            return str(event.get("content", ""))[:2000]
    return ""


def _timeline(raw_events: list[dict[str, Any]]) -> list[dict[str, str]]:
    timeline: list[dict[str, str]] = []
    for item in raw_events:
        event = item["event"]
        etype = str(event.get("type", ""))
        entry: dict[str, str] = {"at": str(item.get("created_at", "")), "type": etype}
        if etype in {"agent_started", "agent_finished"}:
            entry["detail"] = f"{event.get('agent')} {etype.removeprefix('agent_')}"
        elif etype == "agent_error":
            entry["detail"] = f"{event.get('agent')}: {str(event.get('error', ''))[:300]}"
        elif etype in {"lint_finished", "test_finished"}:
            status = "ok" if event.get("ok") else "failed"
            entry["detail"] = f"{status} (exit {event.get('exit_code')})"
        elif etype == "context_compacted":
            entry["detail"] = f"{event.get('removed_messages')} message(s) removed"
        elif etype in {"context_selected", "context_auto_discovered"}:
            entry["detail"] = f"{len(event.get('files') or [])} file(s)"
        elif etype == "cache_hit":
            entry["detail"] = str(event.get("kind", ""))
        elif etype in {"cancelled", "embedding_unavailable"}:
            entry["detail"] = str(event.get("error", "")) or etype
        else:
            extra = {k: v for k, v in event.items() if k != "type"}
            entry["detail"] = str(extra)[:200]
        timeline.append(entry)
    return timeline


def _checks(raw_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in raw_events:
        event = item["event"]
        if event.get("type") == "lint_finished":
            checks.append({"name": "lint", "ok": bool(event.get("ok")), "exit_code": event.get("exit_code")})
        elif event.get("type") == "test_finished":
            checks.append({"name": "test", "ok": bool(event.get("ok")), "exit_code": event.get("exit_code")})
    return checks


def render_report(report: dict[str, Any]) -> str:
    """Render a report dict as Markdown."""
    lines = ["# devx report", ""]
    if report["session_name"]:
        lines.append(f"Session **{report['session_id']}** · *{report['session_name']}*")
    else:
        lines.append(f"Session **{report['session_id']}**")
    if report["run_id"]:
        lines.append(f"Run `{report['run_id']}`")
    if report.get("created_at"):
        lines.append(f"Created {report['created_at']} · updated {report['updated_at']}")

    goal = (report.get("goal") or "").strip()
    if goal:
        lines += ["", "## Goal", "", goal]

    result = (report.get("result") or "").strip()
    if result:
        lines += ["", "## Result", "", result]

    if report["timeline"]:
        lines += ["", "## Timeline", ""]
        for entry in report["timeline"]:
            lines.append(f"- `{entry['type']}` — {entry['detail']}")

    if report["checks"]:
        lines += ["", "## Checks", ""]
        for check in report["checks"]:
            icon = "[ok]" if check["ok"] else "[err]"
            extra = f" (exit {check['exit_code']})" if check.get("exit_code") is not None else ""
            lines.append(f"- {icon} {check['name']}: {'passed' if check['ok'] else 'failed'}{extra}")

    if report["changes"]:
        lines += ["", "## Files changed", "", "| path | status | approved | before -> after |",
                  "| --- | --- | --- | --- |"]
        for change in report["changes"]:
            approved = "yes" if change["approved"] else "no"
            lines.append(
                f"| `{change['path']}` | {change['status']} | {approved} | "
                f"`{_short_hash(change['before_hash'])}` -> `{_short_hash(change['after_hash'])}` |"
            )
        lines += ["", "## Suggested PR description", "", _suggested_pr(report)]

    usage = report["usage"]
    lines += [
        "",
        "## Cost",
        "",
        f"- Calls: {usage['calls']} · tokens: {usage['total_tokens']} · "
        f"estimated: **{format_cost(usage['estimated_cost_usd'])}**",
    ]
    if usage.get("unpriced_models"):
        lines.append(f"- Unpriced models (cost excluded): {', '.join(usage['unpriced_models'])}")
    lines.append("")
    return "\n".join(lines)


def _suggested_pr(report: dict[str, Any]) -> str:
    goal = " ".join((report.get("goal") or "").split())[:120]
    summary = f"### Summary\n{goal}" if goal else "### Summary\nAutomated change"
    change_lines = "\n".join(
        f"- `{change['path']}` — {change['status']}" for change in report["changes"][:50]
    ) or "- no file changes recorded"
    validation_lines = "\n".join(
        f"- {check['name']}: {'passing' if check['ok'] else 'failing'}" for check in report["checks"]
    ) or "- no lint/test runs recorded"
    usage = report["usage"]
    return (
        f"{summary}\n\n### Changes\n{change_lines}\n\n"
        f"### Validation\n{validation_lines}\n\n"
        f"### Cost\n- Runs: {usage['calls']} call(s) · estimated "
        f"{format_cost(usage['estimated_cost_usd'])}"
    )


def _short_hash(value: str | None) -> str:
    return (value or "-")[:8]