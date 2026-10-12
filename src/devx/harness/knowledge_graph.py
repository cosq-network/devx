from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# Typed dictionaries for graph node/edge attributes
# ---------------------------------------------------------------------------

class NodeAttrs(TypedDict, total=False):
    """Attributes attached to a graph node."""
    title: str
    description: str
    created_at: float
    metadata: dict[str, Any]


class EdgeAttrs(TypedDict, total=False):
    """Attributes attached to a graph edge."""
    relation: str
    strength: float  # 0.0 = no relation, 1.0 = strong relation
    date_added: float


# ---------------------------------------------------------------------------
# KnowledgeGraph – simple node–edge store backed by SQLite
# ---------------------------------------------------------------------------

NODE_TABLE = "kg_nodes"
EDGE_TABLE = "kg_edges"


class KnowledgeGraph:
    """Simple node–edge knowledge graph persisted in SQLite.

    Use cases:
    - Track frequently mentioned files/concepts
    - Store lessons learned from task outcomes
    - Enable cross-session context retrieval
    - Support similarity-based retrieval for reduced repetition
    """

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = str(Path.home() / ".devx" / "knowledge_graph.db")
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ------------------------------------------------------------------
    # Database initialisation
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self.db_path)
        cur = self._conn.cursor()
        # —— nodes table ——
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {NODE_TABLE} (
                id TEXT PRIMARY KEY,
                title TEXT,
                description TEXT,
                created_at REAL,
                metadata TEXT
            )
            """
        )
        # —— edges table ——
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {EDGE_TABLE} (
                source_id TEXT,
                target_id TEXT,
                relation TEXT,
                strength REAL,
                date_added REAL,
                PRIMARY KEY (source_id, target_id)
            )
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Node methods
    # ------------------------------------------------------------------

    def set_node(
        self,
        node_id: str,
        title: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Set a node's attributes."""
        if self._conn is None:
            self._init_db()
        cur = self._conn.cursor()
        cur.execute(
            f"""
            INSERT OR REPLACE INTO {NODE_TABLE} (id, title, description, created_at, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                node_id,
                title,
                description,
                time.time(),
                json.dumps(metadata) if metadata else None,
            ),
        )
        self._conn.commit()

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get a node's attributes."""
        if self._conn is None:
            self._init_db()
        cur = self._conn.cursor()
        cur.execute(f"SELECT * FROM {NODE_TABLE} WHERE id = ?", (node_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "title": row[1],
            "description": row[2],
            "created_at": row[3],
            "metadata": json.loads(row[4]) if row[4] else None,
        }

    # ------------------------------------------------------------------
    # Edge methods
    # ------------------------------------------------------------------

    def set_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str = "",
        strength: float = 0.0,
    ) -> None:
        """Set an edge (relation) between two nodes."""
        if self._conn is None:
            self._init_db()
        cur = self._conn.cursor()
        cur.execute(
            f"""
            INSERT OR REPLACE INTO {EDGE_TABLE} (source_id, target_id, relation, strength, date_added)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source_id, target_id, relation, strength, time.time()),
        )
        self._conn.commit()

    def get_edges(self, node_id: str) -> list[dict[str, Any]]:
        """Get all edges originating from a node."""
        if self._conn is None:
            self._init_db()
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT * FROM {EDGE_TABLE} WHERE source_id = ?", (node_id,)
        )
        rows = cur.fetchall()
        return [
            {
                "target_id": row[1],
                "relation": row[2] or "",
                "strength": row[3] if row[3] is not None else 0.0,
                "date_added": row[4],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Convenience property for external access
    # ------------------------------------------------------------------

    @property
    def kg(self) -> "KnowledgeGraph":
        """Return self for compatibility with SessionStore expectations."""
        return self