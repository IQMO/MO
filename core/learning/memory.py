"""SQLite episodic memory search index for MO."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from pathlib import Path
import traceback
from typing import Callable

from .embeddings import cosine


def _emit_memory_event(event_type: str, payload: dict) -> None:
    """Lazy import to avoid circular dependency."""
    try:
        from ..runtime.backend_monitor import get_monitor
        monitor = get_monitor()
        if monitor:
            monitor.emit(event_type, payload)
    except Exception:
        traceback.print_exc()


class EpisodicMemory:
    """Lightweight episodic interaction index using SQLite FTS5."""

    _fts5_warned: bool = False

    def __init__(self, path: str | Path | None = None,
                 embedder: Callable[[str], list[float]] | None = None):
        from ..state.paths import resolve_state_path
        # Route the default through private-state resolution so it lands in
        # ~/.mo (or MO_STATE_HOME), never the project cwd. Explicit paths pass
        # through unchanged (absolute preserved by resolve_state_path).
        self.path = Path(resolve_state_path(path or "memory/learning.sqlite"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Optional semantic-recall backend. None → keyword (bm25) recall only.
        self.embedder = embedder
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
                # Optional embedding vectors for semantic recall (JSON-encoded list).
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS turn_vectors ("
                    "  turn_id TEXT PRIMARY KEY,"
                    "  vector TEXT"
                    ")"
                )
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

        # Guard against automated-loop / repeated-input spam:
        # if the same exact user input has appeared > 5 times in the most
        # recent 50 entries (or whatever the DB holds), skip indexing.
        # This stops autopilot/DEVMODE loops from flooding the memory DB
        # and crowding out real operator turns.
        dup_count = self._count_recent_duplicates(u, window=50, threshold=5)
        if dup_count >= 5:
            _emit_memory_event("memory_index_skipped_repeat", {"turn_id": turn_id, "dup_count": dup_count})
            return

        # Compute the embedding OUTSIDE the DB transaction (network I/O must not hold
        # the sqlite lock). None when no embedder / on failure → keyword recall only.
        vec_json = None
        if self.embedder is not None:
            vec = self._embed_safe(f"{u}\n{a}")
            if vec:
                vec_json = json.dumps(vec)

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
                if vec_json is not None:
                    conn.execute(
                        "INSERT OR REPLACE INTO turn_vectors (turn_id, vector) VALUES (?, ?)",
                        (turn_id, vec_json)
                    )
                # Adaptive cleanup: keep max 200 turns, remove oldest
                removed = self._cleanup(conn)
                _emit_memory_event("memory_index", {"turn_id": turn_id, "chars": len(a), "cleanup_removed": removed})
        except Exception as e:
            _emit_memory_event("memory_index_error", {"turn_id": turn_id, "error": str(e)[:200]})

    def _count_recent_duplicates(self, user_input: str, window: int = 50, threshold: int = 5) -> int:
        """Count how many times the exact same user input appears in the most recent `window` entries."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT user FROM turns ORDER BY updated_at DESC LIMIT ?",
                    (window,)
                ).fetchall()
                return sum(1 for (u,) in rows if str(u or "").strip() == user_input.strip())
        except Exception:
            return 0

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
                    conn.execute("DELETE FROM turn_vectors WHERE turn_id=?", (tid,))
                    removed += 1
                new_count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
                _emit_memory_event("memory_cleanup", {"removed": removed, "remaining": new_count})
                return removed
        except Exception as e:
            _emit_memory_event("memory_cleanup_error", {"error": str(e)[:200]})
        return 0

    def _embed_safe(self, text: str) -> list[float] | None:
        if self.embedder is None:
            return None
        try:
            vec = self.embedder(text)
            return [float(x) for x in vec] if vec else None
        except Exception:
            _emit_memory_event("memory_embed_error", {"chars": len(str(text or ""))})
            return None

    def _semantic_recall(self, query: str, limit: int) -> list[dict[str, str]] | None:
        """Cosine-rank stored turn vectors against the query embedding.

        Returns ranked turns, or None to signal the caller to fall back to keyword
        recall (no embedder, embedding failed, or nothing has been embedded yet).
        """
        qvec = self._embed_safe(query)
        if not qvec:
            return None
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT v.turn_id, v.vector, t.user, t.assistant FROM turn_vectors v "
                    "JOIN turns t ON v.turn_id = t.turn_id"
                ).fetchall()
        except Exception:
            return None
        scored = []
        for r in rows:
            try:
                vec = json.loads(r["vector"])
            except Exception:
                continue
            score = cosine(qvec, vec)
            if score > 0.0:
                scored.append((score, r))
        if not scored:
            return None  # nothing embedded yet → let keyword recall handle it
        scored.sort(key=lambda x: -x[0])
        return [
            {"turn_id": r["turn_id"], "user": r["user"], "assistant": r["assistant"]}
            for _s, r in scored[:limit]
        ]

    def recall(self, query: str, limit: int = 3) -> list[dict[str, str]]:
        q = str(query or "").strip()
        if not q:
            return []

        # Hybrid recall: when an embedder is configured, FUSE semantic (meaning) and
        # keyword (bm25) rankings via reciprocal rank fusion instead of using one OR the
        # other. Fusion recovers lexical-gap paraphrases bm25 alone misses (e.g.
        # "credential" vs "secret") while keeping bm25's exact-term precision. Falls back
        # to keyword-only when there is no embedder or nothing has been embedded yet.
        if self.embedder is not None:
            try:
                pool = max(limit * 4, 12)  # widen the candidate pool, then fuse down to `limit`
                sem = self._semantic_recall(q, pool)
                if sem is not None:
                    fused = self._rrf_fuse((sem, self._keyword_recall(q, pool)), limit)
                    _emit_memory_event("memory_recall", {"query": q[:80], "results": len(fused), "mode": "hybrid"})
                    return fused
            except Exception:
                traceback.print_exc()

        results = self._keyword_recall(q, limit)
        _emit_memory_event("memory_recall", {"query": q[:80], "results": len(results), "mode": "bm25"})
        return results

    @staticmethod
    def _rrf_fuse(rankings: "tuple[list[dict[str, str]], ...]", limit: int, rrf_k: int = 60) -> list[dict[str, str]]:
        """Reciprocal Rank Fusion — combine ranked lists by summing 1/(rrf_k + rank).

        An item ranked highly by EITHER ranker, or moderately by BOTH, rises to the top:
        a semantic-only hit (paraphrase) and a keyword-only hit (exact term) both surface,
        fused by agreement. Preserves each item's dict; dedupes by turn_id.
        """
        scores: dict[str, float] = {}
        items: dict[str, dict[str, str]] = {}
        for ranking in rankings:
            for rank, item in enumerate(ranking or (), start=1):
                tid = item.get("turn_id")
                if not tid:
                    continue
                scores[tid] = scores.get(tid, 0.0) + 1.0 / (rrf_k + rank)
                items.setdefault(tid, item)
        return sorted(items.values(), key=lambda it: -scores[it["turn_id"]])[:limit]

    def _keyword_recall(self, q: str, limit: int) -> list[dict[str, str]]:
        """bm25 (FTS5) keyword recall, with a substring fallback when FTS5 is disabled."""
        # Sanitize query for FTS5 (strip punctuation to prevent match syntax errors)
        clean_terms = [t for t in q.replace('"', '').replace("'", "").split() if len(t) > 2]
        if not clean_terms:
            return []

        fts_query = " OR ".join(f'"{term}"' for term in clean_terms[:8])
        results: list[dict[str, str]] = []

        try:
            with self._connect() as conn:
                try:
                    # Rank by relevance (FTS5 native BM25 — better/rarer term matches
                    # score higher) instead of pure recency, so the most RELEVANT past
                    # turn surfaces even if it isn't the newest. Recency breaks ties.
                    # (BM25 is lexical relevance, not embedding/semantic similarity.)
                    rows = conn.execute(
                        "SELECT f.turn_id, f.user, f.assistant FROM turns_fts f "
                        "JOIN turns t ON f.turn_id = t.turn_id "
                        "WHERE turns_fts MATCH ? ORDER BY bm25(turns_fts), t.updated_at DESC LIMIT ?",
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
