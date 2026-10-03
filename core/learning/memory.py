"""SQLite episodic memory search index for MO."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
import traceback
from typing import Callable

from .embeddings import cosine
from ..utils.env_utils import int_env
from ..utils.text_safety import sanitize_unicode_text


_DESKTOP_POLICY_PREFIX_RE = re.compile(
    r"^\s*\[MO Desktop (?:turn policy|request admission|request):[\s\S]*?\]"
    r"[ \t]*(?:\r?\n){2}",
    re.IGNORECASE,
)
_DESKTOP_ROLE_PREFIX_RE = re.compile(
    r"^\s*\[MO Desktop active user-selected conversation role:[\s\S]*?\][ \t]*(?:\r?\n){2}",
    re.IGNORECASE,
)


def _raw_user_input(text: str) -> str:
    """Remove MO Desktop's internal provider-only banners from a user message."""
    value = sanitize_unicode_text(text).strip()
    if not _DESKTOP_POLICY_PREFIX_RE.match(value):
        return value
    value = _DESKTOP_POLICY_PREFIX_RE.sub("", value, count=1)
    value = _DESKTOP_ROLE_PREFIX_RE.sub("", value, count=1)
    return value.strip()


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

    def __init__(self, path: str | Path | None = None,
                 embedder: Callable[[str], list[float]] | None = None):
        from ..state.paths import resolve_state_path
        # Route the default through private-state resolution so it lands in
        # ~/.mo (or MO_STATE_HOME), never the project cwd. Explicit paths pass
        # through unchanged (absolute preserved by resolve_state_path).
        self.path = Path(resolve_state_path(path or "memory/learning/episodes.sqlite"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fts5_available = True
        self._fts5_reason = ""
        self._fts5_warned = False
        self._last_recall_mode = "unqueried"
        self._last_recall_reason = ""
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
                # WAL: the brain is one shared store written by more than one
                # process now (terminal + MO Desktop, and multiple terminals under
                # the multi-instance model). WAL gives concurrent readers + one
                # writer without the reader/writer blocking of the default rollback
                # journal — no background sync service needed, just the ACID store.
                # Persistent once set; best-effort (a network FS would reject it).
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.OperationalError:
                    pass
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
                except sqlite3.OperationalError as exc:
                    self._fts5_available = False
                    self._fts5_reason = self._fts_failure_reason(exc, stage="schema")
                    if not self._fts5_warned:
                        self._fts5_warned = True
                        _emit_memory_event(
                            "memory_fts5_warning",
                            {
                                "mode": "substring_fallback",
                                "reason": self._fts5_reason,
                            },
                        )
                # Optional embedding vectors for semantic recall (JSON-encoded list).
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS turn_vectors ("
                    "  turn_id TEXT PRIMARY KEY,"
                    "  vector TEXT"
                    ")"
                )
                # COMPAT(memory-desktop-policy-rows): replaced-by indexing raw Desktop user turns; remove-when supported profiles have completed the repair migration
                self._repair_desktop_policy_rows(conn)
        except Exception as e:
            _emit_memory_event("memory_init_error", {"error": str(e)[:200]})

    @staticmethod
    def _fts_failure_reason(exc: BaseException, *, stage: str) -> str:
        text = str(exc).casefold()
        if "no such module" in text and "fts5" in text:
            return "module_unavailable"
        if "no such table" in text and "turns_fts" in text:
            return "schema_missing"
        if "no such function" in text and "bm25" in text:
            return "bm25_unavailable"
        if "malformed" in text or "database disk image" in text:
            return "database_error"
        return f"{stage}_error"

    def retrieval_status(self) -> dict[str, object]:
        """Return bounded database-local retrieval truth for health renderers."""
        return {
            "mode": self._last_recall_mode,
            "reason": self._last_recall_reason or self._fts5_reason,
            "fts5_available": bool(self._fts5_available),
        }

    @staticmethod
    def _repair_desktop_policy_rows(conn: sqlite3.Connection) -> int:
        """Repair old rows that indexed MO Desktop's provider-only prompt banner.

        The canonical ``turns`` row and its FTS copy must remain identical. Any
        vector is removed because it was embedded from the decorated text and can
        be regenerated by the existing embedding backfill path.
        """
        try:
            rows = conn.execute(
                "SELECT turn_id, user, assistant FROM turns "
                "WHERE ltrim(user) LIKE '[MO Desktop turn policy:%' "
                "OR ltrim(user) LIKE '[MO Desktop request admission:%' "
                "OR ltrim(user) LIKE '[MO Desktop request:%'"
            ).fetchall()
        except (AttributeError, sqlite3.Error, TypeError):
            return 0

        repaired = 0
        for row in rows:
            turn_id, decorated, assistant = row[0], row[1], row[2]
            user = _raw_user_input(decorated)
            if not user or user == str(decorated or "").strip():
                continue
            conn.execute("UPDATE turns SET user=? WHERE turn_id=?", (user, turn_id))
            try:
                conn.execute("DELETE FROM turns_fts WHERE turn_id=?", (turn_id,))
                conn.execute(
                    "INSERT INTO turns_fts (turn_id, user, assistant) VALUES (?, ?, ?)",
                    (turn_id, user, assistant),
                )
            except sqlite3.OperationalError:
                pass
            conn.execute("DELETE FROM turn_vectors WHERE turn_id=?", (turn_id,))
            repaired += 1
        return repaired

    def index_turn(
        self,
        turn_id: str,
        user: str,
        assistant: str,
        *,
        include_embedding: bool = True,
    ) -> None:
        """Persist a turn in the exact/FTS index and optionally embed it.

        Bare greetings remain durable and keyword-searchable but do not warrant a
        network/local-model embedding before a five-token response can finish.
        Real conversation keeps the existing semantic-recall behavior.
        """
        turn_id = sanitize_unicode_text(turn_id).strip()
        if not turn_id or not (user or assistant):
            return
        a = sanitize_unicode_text(assistant).strip()
        if not a or len(a) < 10:
            return

        # MO Desktop's lane/persona banners are provider instructions, never user
        # memory. The Agent normally supplies the raw conversation input, while
        # this boundary guard protects alternate or older callers too.
        u = _raw_user_input(user)

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
        if include_embedding and self.embedder is not None:
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

    def index_turn_embedding(self, turn_id: str, user: str, assistant: str) -> bool:
        """Best-effort semantic enrichment for an already durable exact turn.

        Agent user turns call this from a daemon thread after ``index_turn`` has
        committed the SQLite/FTS row. A slow local-model cold start or embedding
        endpoint therefore cannot hold the visible reply open; process exit can
        abandon this optional enrichment without losing the exact memory.
        """
        turn_id = sanitize_unicode_text(turn_id).strip()
        if not turn_id or self.embedder is None:
            return False
        a = sanitize_unicode_text(assistant).strip()
        if not a or len(a) < 10:
            return False
        u = _raw_user_input(user)
        vec = self._embed_safe(f"{u}\n{a}")
        if not vec:
            return False
        try:
            with self._connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM turns WHERE turn_id=? LIMIT 1",
                    (turn_id,),
                ).fetchone()
                if not exists:
                    return False
                conn.execute(
                    "INSERT OR REPLACE INTO turn_vectors (turn_id, vector) VALUES (?, ?)",
                    (turn_id, json.dumps(vec)),
                )
            _emit_memory_event("memory_embedding_index", {"turn_id": turn_id})
            return True
        except Exception as exc:
            _emit_memory_event(
                "memory_embedding_error",
                {"turn_id": turn_id, "error": str(exc)[:200]},
            )
            return False

    def backfill_embeddings(self) -> dict:
        """Embed stored turns that have no vector yet (one-time, after enabling embeddings).

        Turns indexed before embeddings were enabled have no vector, so semantic/RRF
        recall is blind to them until backfilled. Embeddings are computed OUTSIDE the
        sqlite transaction (network/CPU must not hold the lock). No-op without an
        embedder. Returns {embedded, failed, total}.
        """
        if self.embedder is None:
            return {"embedded": 0, "failed": 0, "total": 0, "reason": "no embedder configured"}
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT t.turn_id, t.user, t.assistant FROM turns t "
                    "LEFT JOIN turn_vectors v ON t.turn_id = v.turn_id WHERE v.turn_id IS NULL"
                ).fetchall()
        except Exception as e:
            _emit_memory_event("memory_backfill_error", {"error": str(e)[:200]})
            return {"embedded": 0, "failed": 0, "total": 0, "reason": str(e)[:120]}
        embedded = 0
        failed = 0
        for r in rows:
            vec = self._embed_safe(f"{r['user']}\n{r['assistant']}")
            if not vec:
                failed += 1
                continue
            try:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO turn_vectors (turn_id, vector) VALUES (?, ?)",
                        (r["turn_id"], json.dumps(vec)),
                    )
                embedded += 1
            except Exception:
                failed += 1
        _emit_memory_event("memory_backfill", {"embedded": embedded, "failed": failed, "total": len(rows)})
        return {"embedded": embedded, "failed": failed, "total": len(rows)}

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

    def _cleanup(self, conn: sqlite3.Connection, max_turns: int | None = None) -> int:
        # Keep recall bounded and configurable. FTS5 handles keyword lookup;
        # the optional vector pass remains bounded by the same retained rows.
        limit = max_turns if max_turns is not None else max(50, int_env("MO_MEMORY_MAX_TURNS", 1000))
        try:
            count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            if count > limit:
                excess = count - limit
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
                    keyword = self._keyword_recall(q, pool)
                    fused = self._rrf_fuse((sem, keyword), limit)
                    keyword_mode = self._last_recall_mode
                    mode = f"hybrid_{keyword_mode}"
                    self._last_recall_mode = mode
                    _emit_memory_event("memory_recall", {
                        "query": q[:80],
                        "results": len(fused),
                        "mode": mode,
                        "reason": self._last_recall_reason,
                    })
                    return fused
            except Exception:
                traceback.print_exc()

        results = self._keyword_recall(q, limit)
        _emit_memory_event("memory_recall", {
            "query": q[:80],
            "results": len(results),
            "mode": self._last_recall_mode,
            "reason": self._last_recall_reason,
        })
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
            self._last_recall_mode = "substring_fallback" if not self._fts5_available else "bm25"
            self._last_recall_reason = "no_search_terms"
            return []

        fts_query = " OR ".join(f'"{term}"' for term in clean_terms[:8])
        results: list[dict[str, str]] = []

        try:
            with self._connect() as conn:
                try:
                    if not self._fts5_available:
                        raise sqlite3.OperationalError(self._fts5_reason or "fts5 unavailable")
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
                    self._last_recall_mode = "bm25"
                    self._last_recall_reason = ""
                except sqlite3.OperationalError as exc:
                    # Database-local fallback: one broken/schema-missing DB must
                    # not disable BM25 for another EpisodicMemory instance.
                    reason = self._fts_reason_or_exception(exc)
                    self._fts5_available = False
                    self._fts5_reason = reason
                    self._last_recall_mode = "substring_fallback"
                    self._last_recall_reason = reason
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
            self._last_recall_mode = "substring_fallback"
            self._last_recall_reason = "database_error"
            _emit_memory_event("memory_recall_error", {"query": q[:80], "error": str(e)[:200]})

        return results

    def _fts_reason_or_exception(self, exc: BaseException) -> str:
        if not self._fts5_available and self._fts5_reason:
            return self._fts5_reason
        return self._fts_failure_reason(exc, stage="query")

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
