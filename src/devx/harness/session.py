from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from devx.harness.knowledge_graph import KnowledgeGraph as _KnowledgeGraph


class SessionStore:
    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            default_path = Path.home() / ".devx" / "sessions.db"
            legacy_path = Path.home() / ".dev" / "sessions.db"
            self.db_path = (legacy_path if not default_path.exists() and legacy_path.exists() else default_path).expanduser()
        else:
            self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            if conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
                conn.execute("INSERT INTO schema_version VALUES (1)")
            conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, state TEXT NOT NULL, metadata TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS session_events (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, run_id TEXT NOT NULL,
                event TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS dead_letter_queue (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, run_id TEXT NOT NULL,
                error TEXT NOT NULL, state TEXT NOT NULL, metadata TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_retry_at TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY, session_id TEXT, run_id TEXT NOT NULL,
                model TEXT NOT NULL, provider TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def save(self, session_id: str | None, name: str, state: dict[str, Any], metadata: dict[str, Any] | None = None) -> str:
        sid = session_id or str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            old = conn.execute("SELECT created_at FROM sessions WHERE id=?", (sid,)).fetchone()
            created = old[0] if old else now
            conn.execute(
                """INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                   updated_at=excluded.updated_at, state=excluded.state,
                   metadata=excluded.metadata""",
                (sid, name, created, now, json.dumps(state), json.dumps(metadata or {})),
            )
        return sid

    def load(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=? OR name=? LIMIT 1", (session_id, session_id)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "name": row["name"], "state": json.loads(row["state"]), "metadata": json.loads(row["metadata"]), "updated_at": row["updated_at"]}

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id,name,created_at,updated_at FROM sessions ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def delete(self, session_id: str) -> bool:
        with self._connect() as conn:
            rows = conn.execute("SELECT id FROM sessions WHERE id=? OR name=?", (session_id, session_id)).fetchall()
            if not rows:
                return False
            ids = [row[0] for row in rows]
            conn.executemany("DELETE FROM session_events WHERE session_id=?", [(sid,) for sid in ids])
            conn.executemany("DELETE FROM sessions WHERE id=?", [(sid,) for sid in ids])
            return True

    def rename(self, session_id: str, name: str) -> bool:
        """Rename a saved session, returning whether it existed."""
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Session name must not be empty")
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            return conn.execute("UPDATE sessions SET name=?, updated_at=? WHERE id=? OR name=?",
                                (clean_name, now, session_id, session_id)).rowcount > 0

    def export_session(self, session_id: str, path: Path) -> Path:
        """Export one session and its event history as portable JSON."""
        session = self.load(session_id)
        if not session:
            raise KeyError(f"Session not found: {session_id}")
        payload = {"session": session, "events": self.events(session["id"])}
        destination = Path(path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return destination

    def import_session(self, path: Path, name: str | None = None) -> str:
        """Import a previously exported session and return its new session ID."""
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        source = payload.get("session")
        if not isinstance(source, dict) or not isinstance(source.get("state"), dict):
            raise TypeError("Invalid session export")
        sid = self.save(None, name or str(source.get("name", "imported")), source["state"], source.get("metadata", {}))
        for item in payload.get("events", []):
            event = item.get("event")
            if isinstance(event, dict):
                self.append_event(sid, str(item.get("run_id", sid)), event)
        return sid

    def append_event(self, session_id: str, run_id: str, event: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO session_events(session_id,run_id,event) VALUES(?,?,?)",
                         (session_id, run_id, json.dumps(event, default=str)))

    def events(self, session_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT run_id,event,created_at FROM session_events WHERE session_id=?"
        args: tuple[Any, ...] = (session_id,)
        if run_id:
            query += " AND run_id=?"
            args += (run_id,)
        query += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [{"run_id": row[0], "event": json.loads(row[1]), "created_at": row[2]} for row in rows]

    def find_run(self, run_id: str) -> tuple[str | None, list[dict[str, Any]]]:
        """Locate a run id across sessions.

        Returns ``(session_id, events)`` where ``session_id`` is ``None`` when
        the run has no owning session (events only). Runs are keyed under the
        session id, the session *name*, or their own run id when the run was
        anonymous, so this probes the events table directly instead of assuming
        a particular key.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id FROM session_events WHERE run_id=? ORDER BY id LIMIT 1",
                (run_id,),
            ).fetchone()
        if not row:
            return None, []
        key = row[0]
        loaded = self.load(key) if key else None
        session_id = loaded["id"] if loaded else key
        return session_id, self.events(key, run_id=run_id)

    # Dead Letter Queue methods
    def add_to_dlq(self, session_id: str, run_id: str, error: str, state: dict[str, Any], metadata: dict[str, Any] | None = None) -> int:
        """Add a failed run to the dead letter queue."""
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO dead_letter_queue(session_id, run_id, error, state, metadata)
                   VALUES(?,?,?,?,?)""",
                (session_id, run_id, error, json.dumps(state), json.dumps(metadata or {}))
            )
            return cursor.lastrowid

    def get_dlq_entries(self, session_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Get dead letter queue entries, optionally filtered by session."""
        query = "SELECT id, session_id, run_id, error, state, metadata, retry_count, created_at, last_retry_at FROM dead_letter_queue"
        args: tuple[Any, ...] = ()
        if session_id:
            query += " WHERE session_id=?"
            args = (session_id,)
        query += " ORDER BY created_at DESC LIMIT ?"
        args += (limit,)
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [
            {
                "id": row[0],
                "session_id": row[1],
                "run_id": row[2],
                "error": row[3],
                "state": json.loads(row[4]),
                "metadata": json.loads(row[5]),
                "retry_count": row[6],
                "created_at": row[7],
                "last_retry_at": row[8],
            }
            for row in rows
        ]

    def retry_dlq_entry(self, dlq_id: int) -> dict[str, Any] | None:
        """Mark a DLQ entry as retried and return its state for re-execution."""
        with self._connect() as conn:
            row = conn.execute("SELECT session_id, run_id, state, metadata FROM dead_letter_queue WHERE id=?", (dlq_id,)).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE dead_letter_queue SET retry_count=retry_count+1, last_retry_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), dlq_id)
            )
        return {"session_id": row[0], "run_id": row[1], "state": json.loads(row[2]), "metadata": json.loads(row[3])}

    def remove_dlq_entry(self, dlq_id: int) -> bool:
        """Remove a DLQ entry after successful retry or manual cleanup."""
        with self._connect() as conn:
            return conn.execute("DELETE FROM dead_letter_queue WHERE id=?", (dlq_id,)).rowcount > 0

    # Token usage / cost accounting methods
    def record_usage(
        self,
        session_id: str | None,
        run_id: str,
        entries: list[Any],
    ) -> int:
        """Persist one or more token-usage entries for cost reporting.

        ``entries`` may be objects exposing ``model``, ``provider``,
        ``prompt_tokens``, ``completion_tokens`` and ``total_tokens`` (the
        ``TokenUsage`` dataclass) or equivalent mappings.
        """
        def _field(item: Any, name: str, default: Any = 0) -> Any:
            if isinstance(item, dict):
                return item.get(name, default)
            return getattr(item, name, default)

        rows = [
            (
                session_id,
                run_id,
                str(_field(entry, "model", "")),
                str(_field(entry, "provider", "")),
                int(_field(entry, "prompt_tokens", 0)),
                int(_field(entry, "completion_tokens", 0)),
                int(_field(entry, "total_tokens", 0)),
            )
            for entry in entries
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """INSERT INTO token_usage(
                       session_id, run_id, model, provider,
                       prompt_tokens, completion_tokens, total_tokens)
                   VALUES(?,?,?,?,?,?,?)""",
                rows,
            )
        return len(rows)

    def aggregate_usage(self, session_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        """Aggregate recorded token usage and estimate USD cost.

        Cost is the sum of priced models; ``unpriced_models`` lists identifiers
        with no configured price so the estimate is never silently misleading.
        Filtering is by session and/or run id; both are optional.
        """
        from devx.pricing import estimate_cost

        query = (
            "SELECT model, provider, COUNT(*), SUM(prompt_tokens), "
            "SUM(completion_tokens), SUM(total_tokens) FROM token_usage"
        )
        filters: list[str] = []
        args: tuple[Any, ...] = ()
        if session_id:
            filters.append("session_id=?")
            args += (session_id,)
        if run_id:
            filters.append("run_id=?")
            args += (run_id,)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " GROUP BY model, provider ORDER BY SUM(total_tokens) DESC"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()

        by_model: list[dict[str, Any]] = []
        total_prompt = total_completion = total_tokens = total_calls = 0
        total_cost = 0.0
        unpriced: list[str] = []
        for row in rows:
            model, provider, calls, prompt, completion, total = (
                row[0], row[1], row[2] or 0, row[3] or 0, row[4] or 0, row[5] or 0,
            )
            cost = estimate_cost(model, prompt, completion)
            if cost is None:
                unpriced.append(model)
            else:
                total_cost += cost
            by_model.append(
                {
                    "model": model,
                    "provider": provider,
                    "calls": calls,
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": total,
                    "estimated_cost_usd": cost,
                }
            )
            total_calls += calls
            total_prompt += prompt
            total_completion += completion
            total_tokens += total

        return {
            "calls": total_calls,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "estimated_cost_usd": total_cost,
            "unpriced_models": unpriced,
            "by_model": by_model,
        }


def _kg(self) -> _KnowledgeGraph:
    """Knowledge graph for cross-session context and task history."""
    if not hasattr(self, "_kg_instance"):
        self._kg_instance = _KnowledgeGraph()
    return self._kg_instance

SessionStore.kg = property(_kg)
