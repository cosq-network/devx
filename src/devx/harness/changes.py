from __future__ import annotations

import hashlib
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from devx.harness.backend import WorkspaceBackend


def file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Change:
    run_id: str
    path: str
    before_hash: str | None
    after_hash: str | None
    approved: bool
    backup_path: str | None = None


class ChangeJournal:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS changes (
                id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, path TEXT NOT NULL,
                before_hash TEXT, after_hash TEXT, approved INTEGER NOT NULL,
                backup_path TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(changes)")}
            if "backup_path" not in columns:
                conn.execute("ALTER TABLE changes ADD COLUMN backup_path TEXT")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    def record(self, run_id: str, path: Path, before_hash: str | None, after_hash: str | None, approved: bool,
               backup_path: Path | None = None) -> Change:
        change = Change(run_id, str(path), before_hash, after_hash, approved, str(backup_path) if backup_path else None)
        with self._connection() as conn:
            conn.execute("INSERT INTO changes(run_id,path,before_hash,after_hash,approved,backup_path) VALUES(?,?,?,?,?,?)",
                         (run_id, change.path, before_hash, after_hash, int(approved), change.backup_path))
        return change

    def for_run(self, run_id: str) -> list[Change]:
        with self._connection() as conn:
            rows = conn.execute("SELECT run_id,path,before_hash,after_hash,approved,backup_path FROM changes WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [Change(row[0], row[1], row[2], row[3], bool(row[4]), row[5]) for row in rows]

    def all_changes(self) -> list[Change]:
        """Return every recorded change, oldest first, for audit export."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT run_id,path,before_hash,after_hash,approved,backup_path FROM changes ORDER BY id"
            ).fetchall()
        return [Change(row[0], row[1], row[2], row[3], bool(row[4]), row[5]) for row in rows]

    def rollback(self, run_id: str, backend: WorkspaceBackend | None = None) -> list[str]:
        """Restore only files that still match the recorded post-change hash."""
        restored: list[str] = []
        for change in reversed(self.for_run(run_id)):
            if self._restore_change(change, backend):
                restored.append(change.path)
        return restored

    def rollback_one(
        self, run_id: str, path: str | Path, backend: WorkspaceBackend | None = None
    ) -> bool:
        """Restore exactly one recorded file (file-by-file reject).

        Returns ``True`` only when the change was found *and* restored. The same
        hash guard as :meth:`rollback` applies, so a manually-edited file is left
        untouched.
        """
        for change in reversed(self.for_run(run_id)):
            if not self._matches(change.path, str(path)):
                continue
            return self._restore_change(change, backend)
        return False

    def runs(self) -> list[str]:
        """Return run ids that produced changes, most recent first."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT run_id, MAX(id) AS latest FROM changes GROUP BY run_id ORDER BY latest DESC"
            ).fetchall()
        return [row[0] for row in rows]

    @staticmethod
    def _matches(recorded: str, requested: str) -> bool:
        recorded_norm = recorded.replace("\\", "/")
        requested_norm = requested.replace("\\", "/")
        return (
            recorded_norm == requested_norm
            or recorded_norm.endswith("/" + requested_norm)
            or Path(recorded_norm).name == Path(requested_norm).name
        )

    @staticmethod
    def _rel(change_path: str, backend: WorkspaceBackend | None) -> str:
        """Resolve a recorded change path to a backend-relative path.

        Local changes are stored as absolute paths; remote/container backends
        work with paths relative to their workspace root.
        """
        if backend is not None and getattr(backend, "is_remote", False):
            import posixpath
            clean = change_path.strip().replace("\\", "/")
            if clean.startswith("/"):
                root_norm = posixpath.normpath(backend.root)
                clean_norm = posixpath.normpath(clean)
                if clean_norm.startswith(root_norm):
                    return posixpath.relpath(clean_norm, root_norm)
                return clean_norm.lstrip("/")
            return clean
        return change_path

    def verify(self, run_id: str | None = None, backend: WorkspaceBackend | None = None) -> list[dict[str, Any]]:
        """Compare recorded change hashes against the current workspace.

        Each recorded change gets one of the following statuses:

        * ``intact`` — the file still matches the recorded post-change hash.
        * ``reverted`` — the file matches its pre-change hash (restored) or a
          created file is gone again.
        * ``conflict`` — the file exists but matches neither recorded hash
          (edited by hand, or by another run since).
        * ``missing`` — a recorded file no longer exists.
        """
        records = self.for_run(run_id) if run_id else self.all_changes()
        verified: list[dict[str, Any]] = []
        for change in records:
            rel = self._rel(change.path, backend)
            current = (
                backend.file_hash(rel)
                if backend is not None
                else file_hash(Path(change.path))
            )
            if change.before_hash is None:
                status = "reverted" if current is None else (
                    "intact" if current == change.after_hash else "conflict"
                )
            elif current is None:
                status = "missing"
            elif current == change.after_hash:
                status = "intact"
            elif current == change.before_hash:
                status = "reverted"
            else:
                status = "conflict"
            verified.append(
                {
                    "run_id": change.run_id,
                    "path": change.path,
                    "status": status,
                    "current_hash": current,
                    "before_hash": change.before_hash,
                    "after_hash": change.after_hash,
                    "approved": change.approved,
                }
            )
        return verified

    def _restore_change(self, change: Change, backend: WorkspaceBackend | None) -> bool:
        """Restore a single change, honoring the recorded post-change hash.

        Returns ``True`` when bytes were actually restored/removed.
        """
        if backend is not None and getattr(backend, "is_remote", False):
            rel = self._rel(change.path, backend)
            if backend.file_hash(rel) != change.after_hash:
                return False
            if change.backup_path:
                backup = Path(change.backup_path)
                if backup.exists():
                    backend.write_bytes(rel, backup.read_bytes())
                    return True
            elif change.before_hash is None and backend.exists(rel):
                backend.remove(rel)
                return True
            return False

        target = Path(change.path)
        if file_hash(target) != change.after_hash:
            return False
        if change.backup_path:
            backup = Path(change.backup_path)
            if backup.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
                return True
        elif change.before_hash is None and target.exists():
            target.unlink()
            return True
        return False
