"""SQLite episodic memory search index for MO."""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from pathlib import Path
import traceback


def _emit_memory_event(event_type: str, payload: dict) -> None:
    """Lazy import to avoid circular dependency."""
    try:
        from ..backend_monitor import get_monitor
        monitor = get_monitor()
        if monitor:
            monitor.emit(event_type, payload)
    except Exception:
        traceback.print_exc()


class EpisodicMemory:
    """Lightweight episodic interaction index using SQLite FTS5."""

    _fts5_warned: bool = False

    def __init__(self, path: str | Path = "memory/learning.sqlite"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # Enable short timeouts for local TUI responsive operations
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS turns ("
                    "  turn_id TEXT PRIMARY KEY,"
                    "  user TEXT,"
                    "  assistant TEXT,"
                    "  updated_at REAL"
                    ")"
                )
                try:
                    conn.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5("
                        "  turn_id UNINDEXED,"
                        "  user,"
                        "  assistant"
                        ")"
                    )
                except sqlite3.OperationalError:
                    if not EpisodicMemory._fts5_warned:
                        EpisodicMemory._fts5_warned = True
                        sys.stderr.write(
                            "[memory] FTS5 unavailable — recall disabled. "
                            "Rebuild Python with sqlite3 FTS5 support or install pysqlite3-binary.\n"
                        )
                        _emit_memory_event("memory_fts5_warning", {"message": "FTS5 unavailable — recall disabled"})
                    pass
        except Exception as e:
            _emit_memory_event("memory_init_error", {"error": str(e)[:200]})

    def index_turn(self, turn_id: str, user: str, assistant: str) -> None:
        if not turn_id or not (user or assistant):
            return
        # Don't index empty/failed assistant responses
        a = str(assistant or "").strip()
        if not a or len(a) < 10:
            return
        
        u = str(user or "").strip()
        
        # Async-safe try/except write to handle parallel workspace accesses gracefully
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO turns (turn_id, user, assistant, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (turn_id, u, a, time.time())
                )
                try:
                    conn.execute("DELETE FROM turns_fts WHERE turn_id=?", (turn_id,))
                    conn.execute(
                        "INSERT INTO turns_fts (turn_id, user, assistant) VALUES (?, ?, ?)",
                        (turn_id, u, a)
                    )
                except sqlite3.OperationalError:
                    pass
                # Adaptive cleanup: keep max 200 turns, remove oldest
                removed = self._cleanup(conn)
                _emit_memory_event("memory_index", {"turn_id": turn_id, "chars": len(a), "cleanup_removed": removed})
        except Exception as e:
            _emit_memory_event("memory_index_error", {"turn_id": turn_id, "error": str(e)[:200]})

    def _cleanup(self, conn: sqlite3.Connection, max_turns: int = 200) -> int:
        try:
            count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            if count > max_turns:
                excess = count - max_turns
                old_ids = conn.execute(
                    "SELECT turn_id FROM turns ORDER BY updated_at ASC, rowid ASC LIMIT ?", (excess,)
                ).fetchall()
                removed = 0
                for (tid,) in old_ids:
                    conn.execute("DELETE FROM turns WHERE turn_id=?", (tid,))
                    try:
                        conn.execute("DELETE FROM turns_fts WHERE turn_id=?", (tid,))
                    except sqlite3.OperationalError:
                        pass
                    removed += 1
                new_count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
                _emit_memory_event("memory_cleanup", {"removed": removed, "remaining": new_count})
                return removed
        except Exception as e:
            _emit_memory_event("memory_cleanup_error", {"error": str(e)[:200]})
        return 0

    def recall(self, query: str, limit: int = 3) -> list[dict[str, str]]:
        q = str(query or "").strip()
        if not q:
            return []

        # Sanitize query for FTS5 (strip punctuation to prevent match syntax errors)
        clean_terms = [t for t in q.replace('"', '').replace("'", "").split() if len(t) > 2]
        if not clean_terms:
            return []
        
        fts_query = " OR ".join(f'"{term}"' for term in clean_terms[:8])
        results = []
        
        try:
            with self._connect() as conn:
                try:
                    rows = conn.execute(
                        "SELECT f.turn_id, f.user, f.assistant FROM turns_fts f "
                        "JOIN turns t ON f.turn_id = t.turn_id "
                        "WHERE turns_fts MATCH ? ORDER BY t.updated_at DESC LIMIT ?",
                        (fts_query, limit)
                    ).fetchall()
                    for r in rows:
                        results.append({
                            "turn_id": r["turn_id"],
                            "user": r["user"],
                            "assistant": r["assistant"]
                        })
                except sqlite3.OperationalError:
                    # Fallback to standard substring search if FTS5 fails or is disabled
                    term_clauses = " AND ".join(["(user LIKE ? OR assistant LIKE ?)" for _ in clean_terms[:4]])
                    sql = f"SELECT turn_id, user, assistant FROM turns WHERE {term_clauses} ORDER BY updated_at DESC LIMIT ?"
                    params = []
                    for t in clean_terms[:4]:
                        params.extend([f"%{t}%", f"%{t}%"])
                    params.append(limit)
                    
                    rows = conn.execute(sql, tuple(params)).fetchall()
                    for r in rows:
                        results.append({
                            "turn_id": r["turn_id"],
                            "user": r["user"],
                            "assistant": r["assistant"]
                        })
        except Exception as e:
            _emit_memory_event("memory_recall_error", {"query": q[:80], "error": str(e)[:200]})

        _emit_memory_event("memory_recall", {"query": q[:80], "results": len(results)})
        return results

    def record_miss(self, query: str) -> None:
        """Track searched terms that produced no useful recall result."""
        terms = [term for term in re.findall(r"[a-zA-Z0-9_]{4,}", str(query or "").lower())[:10]]
        if not terms:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS recall_misses ("
                    "term TEXT PRIMARY KEY, count INTEGER DEFAULT 1, last_missed_at REAL)"
                )
                now = time.time()
                for term in terms:
                    conn.execute(
                        "INSERT INTO recall_misses (term, count, last_missed_at) VALUES (?, 1, ?) "
                        "ON CONFLICT(term) DO UPDATE SET count = count + 1, last_missed_at = ?",
                        (term, now, now),
                    )
        except Exception as e:
            _emit_memory_event("memory_miss_error", {"error": str(e)[:200]})
