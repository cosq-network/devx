"""Portable audit export.

Flattens the local session store, change journal, dead-letter queue and token
usage into a stream of JSON-serialisable records. This gives compliance-minded
users a single, greppable artifact instead of several SQLite tables.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from devx.harness.changes import ChangeJournal
from devx.harness.session import SessionStore


def collect_audit(
    store: SessionStore,
    journal: ChangeJournal | None = None,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Collect audit records, each tagged with a ``type`` discriminator.

    ``run_id`` narrows the export to a single run: only the owning session (when
    one exists) is included, events are merged from whichever keying recorded
    them, and DLQ/usage records are filtered to that run, so a ``--run`` export
    never drags in unrelated sessions' rows.
    """
    records: list[dict[str, Any]] = []
    run_filter = run_id is not None

    if run_filter:
        owner_sid, run_events = store.find_run(run_id)
        sessions = [store.load(owner_sid)] if owner_sid and store.load(owner_sid) else []
        for event in run_events:
            records.append(
                {
                    "type": "event",
                    "session_id": owner_sid,
                    "run_id": event.get("run_id"),
                    "event": event.get("event"),
                    "created_at": event.get("created_at"),
                }
            )
    elif session_id:
        session = store.load(session_id)
        sessions = [session] if session else []
    else:
        sessions = store.list_sessions()

    for item in sessions:
        sid = item["id"]
        records.append(
            {
                "type": "session",
                "id": sid,
                "name": item.get("name", ""),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
            }
        )
        if not run_filter:
            for event in store.events(sid):
                records.append(
                    {
                        "type": "event",
                        "session_id": sid,
                        "run_id": event.get("run_id"),
                        "event": event.get("event"),
                        "created_at": event.get("created_at"),
                    }
                )
        entries = store.get_dlq_entries(sid)
        if run_filter:
            entries = [entry for entry in entries if entry.get("run_id") == run_id]
        for entry in entries:
            records.append({"type": "dlq", **entry})
        usage = store.aggregate_usage(sid, run_id=run_id) if run_filter else store.aggregate_usage(sid)
        records.append({"type": "usage", "session_id": sid, **usage})

    if journal is not None:
        changes = journal.for_run(run_id) if run_filter else journal.all_changes()
        for change in changes:
            records.append({"type": "change", **asdict(change)})

    return records


def render_audit(records: Iterable[dict[str, Any]], *, fmt: str = "jsonl") -> str:
    """Render audit records as JSONL or indented JSON."""
    items = list(records)
    if fmt == "json":
        return json.dumps(items, default=str, indent=2)
    if fmt == "jsonl":
        return "\n".join(json.dumps(record, default=str) for record in items)
    raise ValueError("format must be 'jsonl' or 'json'")


def export_audit(
    store: SessionStore,
    journal: ChangeJournal | None,
    path: Path | str,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    fmt: str = "jsonl",
) -> tuple[Path, int]:
    """Write the audit trail to ``path``; returns the path and record count."""
    records = collect_audit(store, journal, session_id=session_id, run_id=run_id)
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = render_audit(records, fmt=fmt)
    if fmt == "jsonl" and payload:
        payload += "\n"
    destination.write_text(payload, encoding="utf-8")
    return destination, len(records)
