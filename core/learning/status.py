"""Count-only durable learning status for UI/dashboard consumers.

This module summarizes existing learning stores without exposing raw memory,
profile prose, suggestion text, or skill bodies. It is a read-only helper for
status surfaces; learning writes remain owned by the existing learning modules.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..state.paths import resolve_state_path
from ..utils.jsonl_utils import read_jsonl
from .workflow_learning import pending_workflow_candidate_count


SCHEMA_VERSION = 1
_SUGGESTION_STATUS_BUCKETS = frozenset({"suggested", "pending", "confirmed", "dismissed", "expired", "retired", "superseded"})


@dataclass(frozen=True)
class LearningStatus:
    schema_version: int
    confirmed_suggestions: int
    auto_promoted_suggestions: int
    pending_suggestions: int
    promoted_workflows: int
    workflow_candidates: int
    profile_learning_entries: int
    behavior_rules: int
    profile_facts: int
    operator_terms: int
    memory_turns: int
    generated_learning_skills: int
    review_pressure: str
    fts5: bool
    vector_count: int
    recall_mode: str
    memory_path_present: bool
    suggestions: dict[str, int]
    memory_inspection: str = "ready"
    source: str = "learning sqlite and suggestions ledger"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "confirmed_suggestions": int(self.confirmed_suggestions),
            "auto_promoted_suggestions": int(self.auto_promoted_suggestions),
            "pending_suggestions": int(self.pending_suggestions),
            "promoted_workflows": int(self.promoted_workflows),
            "workflow_candidates": int(self.workflow_candidates),
            "profile_learning_entries": int(self.profile_learning_entries),
            "behavior_rules": int(self.behavior_rules),
            "profile_facts": int(self.profile_facts),
            "operator_terms": int(self.operator_terms),
            "memory_turns": int(self.memory_turns),
            "generated_learning_skills": int(self.generated_learning_skills),
            "review_pressure": self.review_pressure,
            "fts5": bool(self.fts5),
            "vector_count": int(self.vector_count),
            "recall_mode": self.recall_mode,
            "suggestions": dict(self.suggestions),
            "memory_path_present": bool(self.memory_path_present),
            "memory_inspection": self.memory_inspection,
            "raw_memory_hidden": True,
            "source": self.source,
        }


def build_learning_status(profile: Any = None, *, config: dict[str, Any] | None = None) -> LearningStatus:
    """Return count-only durable learning status from existing private stores."""
    cfg = config if isinstance(config, dict) else {}
    memory_root = _memory_root(profile, cfg)
    learning_root = memory_root / "learning"
    suggestions_path = learning_root / "suggestions.jsonl"
    candidates_path = learning_root / "workflows" / "candidates.jsonl"
    profile_root = memory_root / "profile"
    profile_learning_path = memory_root / "profile" / "learning.md"
    memory_path = learning_root / "episodes.sqlite"

    suggestions = _suggestion_counts(suggestions_path)
    suggestion_rows = read_jsonl(suggestions_path)
    from .proactive_learning import suggestion_review_clusters

    pending_clusters, confirmed_clusters = suggestion_review_clusters(path=suggestions_path)
    confirmed = len(confirmed_clusters)
    auto_promoted_ids = {
        str(row.get("id") or "")
        for row in suggestion_rows
        if str(row.get("status") or "").strip().lower() == "confirmed" and bool(row.get("auto_promoted"))
    }
    auto_promoted = sum(
        1 for cluster in confirmed_clusters
        if auto_promoted_ids.intersection(cluster.ids)
    )
    pending = len(pending_clusters)
    from ..skills import materialized_learning_ids

    materialized_ids = materialized_learning_ids(profile, config=cfg)
    workflow_ids = {
        candidate_id
        for candidate_id in materialized_ids
        if candidate_id.startswith("workflow-candidate:")
    }
    promoted_workflows = len(workflow_ids)
    workflow_candidates = pending_workflow_candidate_count(
        read_jsonl(candidates_path),
        workflow_ids,
    )
    profile_entries = _profile_learning_entries(profile_learning_path)
    behavior_rules = _count_lines(
        profile_root / "behavior.md",
        re.compile(r"^- \[[^\]]+\]\s+.+?<!-- insight:[a-f0-9]{12} -->\s*$"),
    )
    profile_facts = _count_lines(
        profile_root / "facts.md",
        re.compile(r"^- \[[^\]]+\]\s+.+?<!-- fact:[a-f0-9]{10} -->\s*$"),
    )
    operator_terms = _count_lines(
        profile_root / "terms.md",
        re.compile(r"^-\s+\*\*[^*]+\*\*\s*(?:—|-|:)\s*.+$"),
    )
    memory_turns, fts5, vector_count, memory_inspection = _memory_counts(memory_path)
    recall_mode = "unavailable" if memory_inspection == "unavailable" else (
        "hybrid bm25+vector" if fts5 and vector_count > 0 else (
            "bm25" if fts5 else "substring fallback"
        )
    )
    generated_skills = _generated_learning_skill_count(profile, config=cfg)
    return LearningStatus(
        schema_version=SCHEMA_VERSION,
        confirmed_suggestions=confirmed,
        auto_promoted_suggestions=auto_promoted,
        pending_suggestions=pending,
        promoted_workflows=promoted_workflows,
        workflow_candidates=workflow_candidates,
        profile_learning_entries=profile_entries,
        behavior_rules=behavior_rules,
        profile_facts=profile_facts,
        operator_terms=operator_terms,
        memory_turns=memory_turns,
        generated_learning_skills=generated_skills,
        review_pressure=_review_pressure(pending + workflow_candidates),
        fts5=fts5,
        vector_count=vector_count,
        recall_mode=recall_mode,
        memory_path_present=memory_path.is_file(),
        suggestions=suggestions,
        memory_inspection=memory_inspection,
    )


def _memory_root(profile: Any, cfg: dict[str, Any]) -> Path:
    raw = getattr(profile, "_path", None)
    if raw and not type(raw).__module__.startswith("unittest.mock"):
        try:
            return Path(raw).expanduser().parent
        except (TypeError, ValueError, OSError):
            pass
    return Path(resolve_state_path("memory", cfg))


def _suggestion_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in read_jsonl(path):
        status = _suggestion_status_bucket(row.get("status"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def _suggestion_status_bucket(value: Any) -> str:
    status = str(value or "").strip().lower()
    return status if status in _SUGGESTION_STATUS_BUCKETS else "other"


def _memory_counts(path: Path) -> tuple[int, bool, int, str]:
    if not path.is_file():
        return 0, False, 0, "missing"
    try:
        with sqlite3.connect(path, timeout=2.0) as conn:
            turns = int(conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0])
            fts = bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turns_fts'"
                ).fetchone()
            )
            vectors = int(conn.execute("SELECT COUNT(*) FROM turn_vectors").fetchone()[0]) if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turn_vectors'"
            ).fetchone() else 0
        return turns, fts, vectors, "ready"
    except Exception:
        return 0, False, 0, "unavailable"


def _profile_learning_entries(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    pattern = re.compile(r"^## \S+T\S+Z\s+—\s+profile learning\s*$")
    return sum(1 for line in text.splitlines() if pattern.match(line.strip()))


def _count_lines(path: Path, pattern: re.Pattern[str]) -> int:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    return sum(1 for line in lines if pattern.match(line.strip()))


def _generated_learning_skill_count(
    profile: Any,
    *,
    config: dict[str, Any] | None = None,
    strict: bool = False,
) -> int:
    """Count active learned skills across physical and virtual representations."""
    try:
        from ..skills import load_generated_learning_skills, load_skills, skills_root

        root = skills_root(profile, config=config)
        if strict:
            import os
            from ..skills.model import _iter_skill_files
            if root.is_dir():
                with os.scandir(root) as entries:
                    list(entries)  # an unreadable inventory is not empty
            for path in _iter_skill_files([root]):
                with path.open("r", encoding="utf-8") as stream:
                    stream.read(1)
        physical = load_skills([root])
        physical_count = sum(
            1
            for skill in physical
            if skill.provenance == "confirmed-learning"
            or skill.candidate_id.startswith(("learning-suggestion:", "workflow-candidate:"))
        )
        virtual = load_generated_learning_skills(profile, config=config)
        return physical_count + len(virtual)
    except Exception:
        if strict:
            raise
        return 0


def launch_learning_summary(profile: Any = None, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Local launch projection; reuse skill selection and accepted-learning owners.

    No episodic database query, mining, promotion or new persisted state. A failed
    read is unavailable, not a fabricated zero. Titles are display-only metadata.
    """
    from datetime import datetime
    from ..tooling.sandbox import redact_sensitive_text
    from .proactive_learning import suggestion_review_clusters

    cfg = config if isinstance(config, dict) else {}
    root = _memory_root(profile, cfg)
    result: dict[str, Any] = {"skills": None, "pending": None, "title": "", "at": 0.0, "available": False}
    try:
        # The existing loaders intentionally tolerate missing stores. Check read
        # failures here before asking them for a count so failure is not zero.
        for path in (root / "learning" / "suggestions.jsonl", root / "learning" / "workflows" / "candidates.jsonl"):
            if path.exists():
                with path.open("r", encoding="utf-8") as stream:
                    for line in stream:
                        if line.strip():
                            import json
                            if not isinstance(json.loads(line), dict):
                                raise ValueError("Invalid learning record")
        result["skills"] = _generated_learning_skill_count(profile, config=cfg, strict=True)
        from ..skills import materialized_learning_ids
        pending, confirmed = suggestion_review_clusters(path=root / "learning" / "suggestions.jsonl")
        workflow_ids = materialized_learning_ids(profile, config=cfg)
        result["pending"] = len(pending) + pending_workflow_candidate_count(
            read_jsonl(root / "learning" / "workflows" / "candidates.jsonl"), workflow_ids,
        )
        recent = [(row.last_seen, row.recommendation) for row in confirmed]
        # Profile's accepted event ledger owns these timestamps; file mtime is
        # not an acceptance date. Ignore source markers and unmarked prose.
        path = root / "profile" / "learning.md"
        if path.exists():
            stamp = 0.0
            for line in path.read_text(encoding="utf-8").splitlines():
                heading = re.match(r"^## (\S+) — profile learning$", line)
                if heading:
                    try:
                        stamp = datetime.fromisoformat(heading[1].replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        stamp = 0.0
                entry = re.match(r"^- (?:core_traits|current_focus|communication_style|evolution): (.+?) <!-- insight:[a-f0-9]{12}(?: category:[^>]+)? -->$", line)
                if entry and stamp:
                    recent.append((stamp, entry[1]))
        if recent:
            at, title = max(recent, key=lambda item: item[0])
            title = redact_sensitive_text(title)
            # Remove terminal control/format characters as well as whitespace.
            import unicodedata
            result.update(at=at, title=" ".join("".join(c for c in title if not unicodedata.category(c).startswith("C")).split())[:180])
        result["available"] = True
    except (OSError, ValueError, TypeError, RuntimeError):
        result.update(skills=None, pending=None)
    return result


def _review_pressure(pending: int) -> str:
    if pending <= 0:
        return "clear"
    label = "item" if pending == 1 else "items"
    return f"review {pending} {label}"
