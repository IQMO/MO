"""Unified categorized knowledge store for MO learning modules.

Provides a single query surface across MO's distributed learning systems
(feedback, workflow, proactive, trace, terms, episodic memory).

Uses the existing memory/learning.sqlite database — additive, not replacement.
Existing learning modules continue to function independently.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class KnowledgeStore:
    """Unified knowledge store backed by SQLite.

    Categories are the primary organization axis. Kind tracks the source module.
    Query supports full-text search across content and metadata.
    """

    def __init__(self, path: str | Path = "memory/learning.sqlite"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS knowledge_entries ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  kind TEXT NOT NULL DEFAULT '',"
                "  category TEXT NOT NULL DEFAULT '',"
                "  content TEXT NOT NULL DEFAULT '',"
                "  metadata_json TEXT NOT NULL DEFAULT '{}',"
                "  created_at REAL NOT NULL DEFAULT 0"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_kind "
                "ON knowledge_entries(kind)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_category "
                "ON knowledge_entries(category)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_created "
                "ON knowledge_entries(created_at)"
            )

    # ── write ──────────────────────────────────────────────

    def record(
        self,
        kind: str,
        category: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Record a knowledge entry. Returns the row id."""
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO knowledge_entries (kind, category, content, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind, category, content, metadata_json, time.time()),
            )
            return cur.lastrowid

    # ── query ──────────────────────────────────────────────

    def query(
        self,
        kind: str | None = None,
        category: str | None = None,
        text: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Query knowledge entries with optional filters.

        text search is a simple LIKE across content + metadata_json.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if text:
            clauses.append("(content LIKE ? OR metadata_json LIKE ?)")
            like = f"%{text}%"
            params.extend([like, like])

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT id, kind, category, content, metadata_json, created_at "
            "FROM knowledge_entries "
            f"{where} "
            "ORDER BY created_at DESC "
            "LIMIT ?"
        )
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_dict(r) for r in rows]

    def get_by_kind(self, kind: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent entries of a specific kind."""
        return self.query(kind=kind, limit=limit)

    def get_by_category(self, category: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent entries in a category."""
        return self.query(category=category, limit=limit)

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get most recent entries across all kinds/categories."""
        return self.query(limit=limit)

    def get_categories(self) -> list[str]:
        """List all distinct categories in the store."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM knowledge_entries ORDER BY category"
            ).fetchall()
            return [r["category"] for r in rows if r["category"]]

    def get_kinds(self) -> list[str]:
        """List all distinct kinds in the store."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT kind FROM knowledge_entries ORDER BY kind"
            ).fetchall()
            return [r["kind"] for r in rows if r["kind"]]

    def count(self, kind: str | None = None, category: str | None = None) -> int:
        """Count entries, optionally filtered."""
        clauses: list[str] = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if category:
            clauses.append("category = ?")
            params.append(category)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM knowledge_entries {where}", params
            ).fetchone()
            return row["cnt"] if row else 0

    # ── maintenance ────────────────────────────────────────

    def prune(self, max_entries: int = 1000) -> int:
        """Remove oldest entries beyond max_entries. Returns count removed."""
        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM knowledge_entries"
            ).fetchone()["cnt"]
            if count <= max_entries:
                return 0
            excess = count - max_entries
            conn.execute(
                "DELETE FROM knowledge_entries WHERE id IN ("
                "  SELECT id FROM knowledge_entries ORDER BY created_at ASC LIMIT ?"
                ")",
                (excess,),
            )
            return excess


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "category": row["category"],
        "content": row["content"],
        "metadata": json.loads(row["metadata_json"]),
        "created_at": row["created_at"],
    }


# ── singleton ─────────────────────────────────────────────

_store: KnowledgeStore | None = None


def get_knowledge_store(path: str | Path = "memory/learning.sqlite") -> KnowledgeStore:
    """Get or create the singleton KnowledgeStore instance."""
    global _store
    if _store is None:
        _store = KnowledgeStore(path)
    return _store
