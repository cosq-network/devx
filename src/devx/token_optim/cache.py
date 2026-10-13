from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def fingerprint_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted((Path(item) for item in paths), key=lambda item: str(item)):
        digest.update(str(path).encode())
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()


class SemanticCache:
    """Optional cache; callers supply an embedder to avoid mandatory model downloads."""
    def __init__(self, db_path: Path, threshold: float = 0.95, embedder=None, namespace: str = ""):
        self.db_path, self.threshold, self.embedder = Path(db_path).expanduser(), threshold, embedder
        self.namespace = namespace
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, query TEXT, response TEXT, embedding TEXT, namespace TEXT NOT NULL DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(cache)")}
            if "embedding" not in columns:
                conn.execute("ALTER TABLE cache ADD COLUMN embedding TEXT")
            if "namespace" not in columns:
                conn.execute("ALTER TABLE cache ADD COLUMN namespace TEXT NOT NULL DEFAULT ''")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        """Transaction-scoped connection that always closes.

        ``sqlite3.Connection`` is not a context manager on exit that closes the
        handle, so every bare ``with self._connect()`` leaked a connection.
        """
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def key(self, query: str, fingerprint: str = "") -> str:
        return hashlib.sha256((self.namespace + "\0" + query.strip() + "\0" + fingerprint).encode()).hexdigest()

    def get(self, query: str, fingerprint: str = "") -> str | None:
        with self._session() as conn:
            row = conn.execute("SELECT response FROM cache WHERE key=?", (self.key(query, fingerprint),)).fetchone()
            if row:
                return row[0]
            if self.embedder is None:
                return None
            query_vector = self.embedder.embed([query])[0]
            for response, serialized in conn.execute(
                "SELECT response,embedding FROM cache WHERE embedding IS NOT NULL AND namespace=?",
                (self.namespace,),
            ):
                vector = json.loads(serialized)
                denominator = math.sqrt(sum(x * x for x in query_vector) * sum(x * x for x in vector))
                if denominator and sum(a * b for a, b in zip(query_vector, vector)) / denominator >= self.threshold:
                    return response
        return None

    def set(self, query: str, response: str, fingerprint: str = "") -> None:
        embedding = None
        if self.embedder is not None:
            embedding = json.dumps([float(value) for value in self.embedder.embed([query])[0]])
        with self._session() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache(key,query,response,embedding,namespace) VALUES(?,?,?,?,?)",
                (self.key(query, fingerprint), query, response, embedding, self.namespace),
            )

    def clear(self, namespace: str | None = None) -> int:
        """Delete cached rows, optionally for one namespace only.

        Returns the number of rows removed. With ``namespace=None`` the whole
        cache is cleared. Backs the ``devx index --force`` re-index path.
        """
        with self._session() as conn:
            if namespace is None:
                cursor = conn.execute("DELETE FROM cache")
            else:
                cursor = conn.execute("DELETE FROM cache WHERE namespace=?", (namespace,))
            return cursor.rowcount
