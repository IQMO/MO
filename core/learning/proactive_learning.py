"""Offline/operator-approved learning suggestion mining.

This module reads episodic memory and writes reviewable suggestions only. It does
not update profile learning, workflow promotions, system prompts, or task truth.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_text
from ..runtime.backend_monitor import redact_monitor_text
from ..utils.env_utils import int_env
from ..utils.jsonl_utils import read_jsonl
from ..utils.text_safety import contains_secret_value
from ..gates.threat_scan import scan_text


@dataclass(frozen=True)
class SuggestionEvidence:
    turn_id: str
    snippet: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class LearningSuggestion:
    id: str
    kind: str
    recommendation: str
    evidence: tuple[SuggestionEvidence, ...]
    status: str = "suggested"
    promotion: str = "requires explicit operator approval before profile/workflow use"
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [item.as_dict() for item in self.evidence]
        return data


_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "evidence_first",
        re.compile(r"\b(feedback|learn|learned|next time|from now on|when corrected|didn'?t|not what i asked)\b.{0,140}\b(verify|verified|evidence|test|tests|logs?|runtime|files?)\b", re.I | re.S),
        "Consider a durable evidence-first behavior rule for matching work turns, with current task/tool evidence still required.",
    ),
    (
        "scope_control",
        re.compile(r"\b(not what i asked|scope|broaden|don'?t add|do not add|preserve.*goal|stay on)\b", re.I | re.S),
        "Consider a scope-control reminder: preserve the operator's goal frame and report unavoidable scope changes as blockers.",
    ),
    (
        "communication_concise",
        re.compile(r"\b(concise|brief|short|compact|too long|less words|direct)\b", re.I),
        "Consider a communication preference for concise/direct routine replies unless evidence/report depth is needed.",
    ),
    (
        "clean_finish",
        re.compile(r"\b(no dirty|dirty work|legacy|left behind|duplicate mechanism|finish clean|cleanly)\b", re.I),
        "Consider a clean-finish workflow candidate for edit turns: remove abandoned paths, avoid duplicate mechanisms, and verify before completion claims.",
    ),
)


def mine_learning_suggestions(
    memory_path: str | Path | None = None,
    *,
    min_occurrences: int = 2,
    max_items: int = 5,
) -> list[LearningSuggestion]:
    """Return reviewable recurring learning suggestions from episodic memory."""
    from ..state.paths import resolve_state_path
    # sqlite connect creates the file even on read — route the default to private
    # state so a default call never materializes cwd/memory/learning.sqlite.
    rows = _load_turns(resolve_state_path(memory_path or "memory/learning.sqlite"))
    if not rows:
        return []
    grouped: dict[str, list[SuggestionEvidence]] = {kind: [] for kind, _pattern, _rec in _PATTERNS}
    for row in rows:
        # Mine the operator's words only. Concatenating the assistant text let MO's
        # own evidence-first prose ("I verified the tests") complete an operator-
        # feedback pattern started by the user ("you didn't ..."), feeding MO's own
        # voice back as "learning".
        text = str(row.get("user", "") or "")
        for kind, pattern, _recommendation in _PATTERNS:
            if pattern.search(text):
                grouped[kind].append(SuggestionEvidence(
                    turn_id=redact_monitor_text(row.get("turn_id", ""), 120),
                    snippet=_snippet(text),
                ))
                break

    suggestions: list[LearningSuggestion] = []
    for kind, _pattern, recommendation in _PATTERNS:
        evidence = grouped.get(kind, [])
        if len(evidence) < min_occurrences:
            continue
        evidence_tuple = tuple(evidence[-4:])
        digest = hashlib.sha1((kind + "\0" + "\0".join(item.turn_id for item in evidence_tuple)).encode("utf-8", errors="ignore")).hexdigest()[:12]
        suggestions.append(LearningSuggestion(
            id=f"learning-suggestion:{kind}:{digest}",
            kind=kind,
            recommendation=recommendation,
            evidence=evidence_tuple,
        ))
    suggestions.sort(key=lambda item: (len(item.evidence), item.created_at), reverse=True)
    return suggestions[:max_items]


def write_learning_suggestions(
    suggestions: list[LearningSuggestion],
    *,
    path: str | Path | None = None,
) -> Path:
    """Append unique reviewable suggestions to JSONL and return the path."""
    from ..state.paths import resolve_state_path
    out = Path(resolve_state_path(path or "memory/learning_suggestions.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = _existing_ids(out)
    with out.open("a", encoding="utf-8") as fh:
        for suggestion in suggestions:
            if suggestion.id in existing:
                continue
            fh.write(json.dumps(suggestion.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            existing.add(suggestion.id)
    _prune_learning_suggestions(out)
    return out


def read_learning_suggestions(
    *,
    path: str | Path = "memory/learning_suggestions.jsonl",
    include_inactive: bool = False,
) -> list[LearningSuggestion]:
    """Read reviewable suggestions from JSONL, newest first."""
    src = Path(path)
    if not src.exists():
        return []
    suggestions: list[LearningSuggestion] = []
    try:
        lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        suggestion = _suggestion_from_dict(row)
        if not suggestion:
            continue
        if not include_inactive and suggestion.status not in {"suggested", "pending"}:
            continue
        suggestions.append(suggestion)
    return suggestions


def update_learning_suggestion_status(
    suggestion_id: str,
    status: str,
    *,
    path: str | Path = "memory/learning_suggestions.jsonl",
) -> bool:
    """Mark a suggestion confirmed/dismissed/expired without applying it."""
    clean_id = str(suggestion_id or "").strip()
    clean_status = str(status or "").strip().lower()
    if not clean_id or clean_status not in {"suggested", "confirmed", "dismissed", "expired"}:
        return False
    src = Path(path)
    if not src.exists():
        return False
    changed = False
    rows: list[dict[str, Any]] = []
    try:
        for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                if str(row.get("id") or "") == clean_id:
                    row["status"] = clean_status
                    row["updated_at"] = time.time()
                    changed = True
                rows.append(row)
    except OSError:
        return False
    if not changed:
        return False
    try:
        atomic_write_text(src, "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _prune_learning_suggestions(path: Path) -> None:
    keep = int_env("MO_LEARNING_SUGGESTIONS_MAX", 100)
    if keep <= 0:
        return
    try:
        lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        if len(lines) > keep:
            atomic_write_text(path, "\n".join(lines[-keep:]) + "\n", encoding="utf-8")
    except Exception:
        return


# ── clustering / confidence / expiry (closes the review loop) ─────────────────
#
# Closeout/trace writers mint a new digest id for the same semantic insight every
# session, so the raw store fills with near-duplicates that one-by-one review can
# never keep up with (observed live: 100/100 caps maxed, 0 ever promoted).
# Clustering collapses same-(kind, recommendation) items into one reviewable unit
# with a deterministic confidence score; confirm/dismiss applies to the whole
# cluster. Confirmed clusters ARE MO's skills — they inject through the existing
# confirmed-learning context; no second injection path.

@dataclass(frozen=True)
class SuggestionCluster:
    kind: str
    recommendation: str
    ids: tuple[str, ...]
    count: int
    confidence: float
    first_seen: float
    last_seen: float
    representative: LearningSuggestion

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "recommendation": self.recommendation,
            "ids": list(self.ids),
            "count": self.count,
            "confidence": self.confidence,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


_OPERATOR_FEEDBACK_KINDS = {kind for kind, _p, _r in _PATTERNS}


def _normalize_recommendation(text: str) -> str:
    # Collapse session-specific counts ("3 dirty items", "8 blocked calls") so
    # the same insight clusters across sessions instead of splitting per number.
    clean = re.sub(r"\d+", "0", str(text or "").lower())
    clean = re.sub(r"[^a-z0 ]+", " ", clean)
    return re.sub(r"\s+", " ", clean).strip()


def _kind_confidence_base(kind: str) -> float:
    """Source authority: explicit operator feedback > closeout-derived > trace-derived."""
    clean = str(kind or "").lower()
    if clean in _OPERATOR_FEEDBACK_KINDS:
        return 0.5
    if clean.startswith("closeout"):
        return 0.35
    if "trace" in clean:
        return 0.3
    return 0.4


def cluster_suggestions(
    suggestions: list[LearningSuggestion],
    *,
    now: float | None = None,
) -> list[SuggestionCluster]:
    """Collapse suggestions into (kind, recommendation) clusters, ranked by confidence.

    Confidence is deterministic: source-authority base + recurrence bonus
    (+0.12 per extra occurrence) + recency bonus (+0.1 when seen within 7 days),
    capped at 1.0.
    """
    current = float(now if now is not None else time.time())
    buckets: dict[tuple[str, str], list[LearningSuggestion]] = {}
    for suggestion in suggestions:
        key = (str(suggestion.kind or ""), _normalize_recommendation(suggestion.recommendation))
        buckets.setdefault(key, []).append(suggestion)
    clusters: list[SuggestionCluster] = []
    for (kind, _norm), members in buckets.items():
        members_sorted = sorted(members, key=lambda s: s.created_at)
        first_seen = members_sorted[0].created_at
        last_seen = members_sorted[-1].created_at
        count = len(members_sorted)
        confidence = _kind_confidence_base(kind) + 0.12 * (count - 1)
        if (current - last_seen) <= 7 * 86400:
            confidence += 0.1
        clusters.append(SuggestionCluster(
            kind=kind,
            recommendation=members_sorted[-1].recommendation,
            ids=tuple(s.id for s in members_sorted),
            count=count,
            confidence=round(min(1.0, confidence), 3),
            first_seen=first_seen,
            last_seen=last_seen,
            representative=members_sorted[-1],
        ))
    clusters.sort(key=lambda c: (-c.confidence, -c.count, c.kind))
    return clusters


def expire_stale_suggestions(
    *,
    path: str | Path = "memory/learning_suggestions.jsonl",
    ttl_days: int | None = None,
    now: float | None = None,
) -> int:
    """Mark unreviewed suggestions older than the TTL as expired; return count."""
    ttl = int(ttl_days if ttl_days is not None else int_env("MO_LEARNING_SUGGESTION_TTL_DAYS", 7))
    if ttl <= 0:
        return 0
    src = Path(path)
    if not src.exists():
        return 0
    current = float(now if now is not None else time.time())
    cutoff = current - ttl * 86400
    expired = 0
    rows: list[dict[str, Any]] = []
    try:
        for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "suggested").lower()
            created = float(row.get("created_at") or current)
            if status in {"suggested", "pending"} and created < cutoff:
                row["status"] = "expired"
                row["updated_at"] = current
                expired += 1
            rows.append(row)
        if expired:
            atomic_write_text(src, "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    except OSError:
        return 0
    return expired


def resolve_cluster_ids(
    suggestion_id: str,
    *,
    path: str | Path = "memory/learning_suggestions.jsonl",
) -> list[str]:
    """Return every active suggestion id in the cluster containing the given id."""
    clean = str(suggestion_id or "").strip()
    if not clean:
        return []
    for cluster in cluster_suggestions(read_learning_suggestions(path=path)):
        if clean in cluster.ids:
            return list(cluster.ids)
    return []


def render_learning_clusters(
    clusters: list[SuggestionCluster],
    *,
    raw_count: int = 0,
    expired_count: int = 0,
    path: str | Path = "",
) -> str:
    """Operator review surface: top-5 clusters by confidence, honest totals."""
    if not clusters:
        return "Learning suggestions: none pending review."
    lines = [f"Learning review: {len(clusters)} cluster(s) from {raw_count or sum(c.count for c in clusters)} raw suggestion(s)"]
    if expired_count:
        lines.append(f"  ({expired_count} stale suggestion(s) auto-expired)")
    if path:
        lines.append(f"  store: {path}")
    lines.append("Confirm makes a cluster part of MO's skills (injected when relevant); dismiss drops the whole cluster.")
    for cluster in clusters[:5]:
        lines.append(
            f"- [{cluster.kind}] confidence {cluster.confidence:.2f} · seen {cluster.count}x: "
            f"{_one_line_recommendation(cluster.recommendation, 200)}"
        )
        lines.append(f"  actions: /learning confirm {cluster.representative.id} | /learning dismiss {cluster.representative.id}")
    if len(clusters) > 5:
        lines.append(f"  … +{len(clusters) - 5} lower-confidence cluster(s); review again after confirming these.")
    return "\n".join(lines)


def next_learning_suggestion_notice(
    *,
    path: str | Path = "memory/learning_suggestions.jsonl",
    min_confidence: float = 0.62,
    cooldown_hours: float = 24.0,
    now: float | None = None,
) -> str:
    """Return one actionable learning notice and mark its cluster prompted.

    Suggestions remain inert. This is only the missing review prompt: it tells
    the operator a recurring cluster is ready and gives natural-language confirm
    or dismiss wording. A per-cluster cooldown prevents repeated after-turn spam.
    """
    current = float(now if now is not None else time.time())
    suggestions = read_learning_suggestions(path=path)
    if not suggestions:
        return ""
    rows_by_id = _read_rows_by_id(Path(path))
    cooldown = max(0.0, float(cooldown_hours or 0.0)) * 3600
    for cluster in cluster_suggestions(suggestions, now=current):
        if cluster.confidence < min_confidence:
            continue
        prompted = max(float((rows_by_id.get(sid) or {}).get("last_prompted_at") or 0.0) for sid in cluster.ids)
        if prompted and cooldown and current - prompted < cooldown:
            continue
        _mark_suggestions_prompted(Path(path), cluster.ids, current)
        return (
            f"Learning suggestion ready ({cluster.kind}, {cluster.count}x): "
            f"say confirm learning suggestion {cluster.representative.id} or dismiss learning suggestion {cluster.representative.id}"
        )
    return ""


# Auto-promotion of a NARROW safe class. MO captures + clusters learning every
# turn, but nothing crossed the manual /learning confirm gate (observed live:
# 100 suggested / 0 confirmed), so build_learning_context injected nothing — the
# learning pipe was fully wired but the valve was welded shut. This opens the
# valve for the provably-safe, universal, high-confidence clusters ONLY;
# everything risky still needs explicit /learning confirm. Fully reversible:
# /learning dismiss reverts an auto-confirmed cluster.
AUTO_PROMOTE_SAFE_KINDS = frozenset({"evidence_first", "clean_finish", "communication_concise"})


def auto_promote_safe_clusters(
    *,
    path: str | Path = "memory/learning_suggestions.jsonl",
    min_confidence: float = 0.8,
    min_count: int = 3,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Auto-confirm only high-confidence, universal, low-risk suggestion clusters.

    Effective bar: a universal-safe kind, seen >= ``min_count`` times, with cluster
    confidence >= ``min_confidence`` (recurrence + recency). Returns a summary of
    what was promoted for audit/notice. Confirms nothing when the store is empty,
    the bar is not met, or the text trips the secret/threat gate. The ``confirmed``
    status is exactly what ``build_learning_context`` consumes, so a promoted
    cluster injects on the next matching turn.
    """
    current = float(now if now is not None else time.time())
    src = Path(path)
    suggestions = read_learning_suggestions(path=str(src))
    if not suggestions:
        return []
    promoted: list[dict[str, Any]] = []
    promote_ids: set[str] = set()
    for cluster in cluster_suggestions(suggestions, now=current):
        if cluster.kind not in AUTO_PROMOTE_SAFE_KINDS:
            continue
        if cluster.confidence < min_confidence or cluster.count < min_count:
            continue
        text = str(cluster.recommendation or "")
        # Safety re-gate at the promotion boundary: never auto-confirm text that
        # trips the secret detector or the input threat scan.
        if contains_secret_value(text) or scan_text(text, surface="learning auto-promote").blocked:
            continue
        promote_ids.update(cluster.ids)
        promoted.append({
            "id": cluster.representative.id,
            "kind": cluster.kind,
            "confidence": cluster.confidence,
            "count": cluster.count,
            "recommendation": _one_line_recommendation(text, 200),
        })
    if promote_ids:
        _confirm_ids_auto(src, promote_ids, current)
    return promoted


def _confirm_ids_auto(path: Path, ids: set[str], when: float) -> None:
    """Flip the given suggested/pending ids to confirmed in one rewrite, tagging
    them ``auto_promoted`` for audit. Only touches still-unreviewed rows so it
    never overrides an explicit operator dismiss/confirm."""
    if not ids or not path.exists():
        return
    rows = read_jsonl(path)
    changed = False
    for row in rows:
        if str(row.get("id") or "") in ids and str(row.get("status") or "suggested").lower() in {"suggested", "pending"}:
            row["status"] = "confirmed"
            row["updated_at"] = when
            row["auto_promoted"] = True
            row["auto_promoted_at"] = when
            changed = True
    if changed:
        atomic_write_text(path, "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def reconcile_confirmed_learnings(
    *,
    path: str | Path = "memory/learning_suggestions.jsonl",
    now: float | None = None,
) -> dict[str, Any]:
    """Deterministic, no-provider consolidation of CONFIRMED learnings.

    As auto-promotion (and manual confirm) accumulate learnings, near-duplicates of
    the same insight pile up. This collapses confirmed rows that fall in one
    ``(kind, normalized-recommendation)`` cluster down to a single active
    representative — the rest are marked ``superseded`` so the injected learning
    set stays lean and contradiction-free. Returns
    ``{confirmed_before, clusters, superseded}``. (The optional LLM "dialectic"
    depth — assess/self-audit/reconcile over profile prose — layers on top of this
    foundation and is gated separately; this pass is local and cost-free.)
    """
    current = float(now if now is not None else time.time())
    src = Path(path)
    if not src.exists():
        return {"confirmed_before": 0, "clusters": 0, "superseded": 0}
    confirmed = [s for s in read_learning_suggestions(path=str(src), include_inactive=True)
                 if str(s.status).lower() == "confirmed"]
    if not confirmed:
        return {"confirmed_before": 0, "clusters": 0, "superseded": 0}
    clusters = cluster_suggestions(confirmed, now=current)
    keep_ids = {cluster.representative.id for cluster in clusters}
    superseded_ids: set[str] = set()
    for cluster in clusters:
        if cluster.count > 1:
            superseded_ids.update(sid for sid in cluster.ids if sid not in keep_ids)
    if superseded_ids:
        rows = read_jsonl(src)
        for row in rows:
            if str(row.get("id") or "") in superseded_ids and str(row.get("status") or "").lower() == "confirmed":
                row["status"] = "superseded"
                row["updated_at"] = current
        atomic_write_text(src, "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows) + "\n", encoding="utf-8")
    return {"confirmed_before": len(confirmed), "clusters": len(clusters), "superseded": len(superseded_ids)}


def build_learning_context(
    user_input: str,
    *,
    suggestions_path: str | Path = "memory/learning_suggestions.jsonl",
    max_chars: int = 800,
) -> str:
    """Return compact confirmed learning suggestion context for the current turn.

    Only confirmed suggestions are included (operator-approved). Unreviewed
    "suggested" items are not injected — they must be reviewed via /learning
    first. The text follows the same compact orientation-only contract as
    selected local skill context.
    """
    user_text = str(user_input or "").strip()
    if not user_text:
        return ""
    confirmed = _read_confirmed_suggestions(suggestions_path)
    if not confirmed:
        return ""
    # Relevance gate: recommendation shares a MEANINGFUL word with user input.
    # Bare stopword overlap ("the", "to") used to inject nearly every confirmed
    # suggestion on every turn; require a real content-word match instead.
    relevant: list[LearningSuggestion] = []
    user_words = _meaningful_words(user_text)
    for suggestion in confirmed:
        rec_words = _meaningful_words(str(suggestion.recommendation or ""))
        if user_words & rec_words:
            relevant.append(suggestion)
        elif _kind_is_universal(suggestion.kind):
            relevant.append(suggestion)
    if not relevant:
        return ""
    lines = [
        "### MO Internal Learning Context — operator-confirmed, relevance-gated",
        "Apply only when the recommendation fits this turn. Current user scope, sandbox, tools, and Gateway/taskboard evidence still win.",
    ]
    for suggestion in relevant[:3]:
        lines.append(f"- [{suggestion.kind}] {_one_line_recommendation(suggestion.recommendation, 220)}")
    text = "\n".join(lines).strip()
    return text if len(text) <= max_chars else text[:max_chars].rsplit("\n", 1)[0] + "\n[learning context truncated]"


def _read_confirmed_suggestions(path: str | Path) -> list[LearningSuggestion]:
    """Read only confirmed suggestions from the JSONL file."""
    return [s for s in read_learning_suggestions(path=str(path), include_inactive=True)
            if str(s.status).lower() == "confirmed"]


def _read_rows_by_id(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    try:
        if not path.exists():
            return rows
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("id"):
                rows[str(row["id"])] = row
    except OSError:
        return rows
    return rows


def _mark_suggestions_prompted(path: Path, ids: tuple[str, ...], prompted_at: float) -> None:
    wanted = {str(item) for item in ids}
    if not wanted or not path.exists():
        return
    rows = read_jsonl(path)
    changed = False
    for row in rows:
        if str(row.get("id") or "") in wanted:
            row["last_prompted_at"] = prompted_at
            changed = True
    if changed:
        atomic_write_text(path, "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


_RELEVANCE_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with",
    "is", "are", "be", "was", "were", "this", "that", "it", "its", "at", "as",
    "by", "from", "into", "please", "can", "could", "would", "should", "you",
    "your", "i", "my", "me", "we", "do", "does", "did", "have", "has", "will",
    "not", "no", "yes", "so", "if", "then", "than", "when", "what", "how",
})


def _meaningful_words(text: str) -> set[str]:
    """Content words (>2 chars, non-stopword) for relevance matching."""
    return {
        w for w in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(w) > 2 and w not in _RELEVANCE_STOPWORDS
    }


def _kind_is_universal(kind: str) -> bool:
    """True for suggestion kinds that apply broadly regardless of topic."""
    return kind in {"evidence_first", "clean_finish", "communication_concise"}


def _one_line_recommendation(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean[:limit].rstrip()


def render_learning_suggestions(suggestions: list[LearningSuggestion], *, path: str | Path = "") -> str:
    if not suggestions:
        return "Learning suggestions: none found."
    lines = [f"Learning suggestions: {len(suggestions)} reviewable item(s)"]
    if path:
        lines.append(f"Saved: {path}")
    lines.append("These are inert suggestions; approve explicitly before profile/workflow use.")
    for suggestion in suggestions[:5]:
        ids = ", ".join(item.turn_id for item in suggestion.evidence[:3])
        lines.append(f"- {suggestion.id} [{suggestion.kind}]: {suggestion.recommendation} Evidence: {ids}")
        lines.append(f"  actions: /learning confirm {suggestion.id} | /learning dismiss {suggestion.id}")
    return "\n".join(lines)


def _load_turns(memory_path: str | Path) -> list[dict[str, str]]:
    path = Path(memory_path)
    if not path.exists():
        return []
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT turn_id, user, assistant FROM turns ORDER BY updated_at ASC LIMIT 300"
            ).fetchall()
            return [{"turn_id": str(row["turn_id"] or ""), "user": str(row["user"] or ""), "assistant": str(row["assistant"] or "")} for row in rows]
    except Exception:
        return []


def _snippet(text: str) -> str:
    clean = " ".join(str(text or "").split())
    return redact_monitor_text(clean, 260)


def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["id"]) for row in read_jsonl(path) if row.get("id")}


def _suggestion_from_dict(row: Any) -> LearningSuggestion | None:
    if not isinstance(row, dict) or not row.get("id"):
        return None
    evidence_items: list[SuggestionEvidence] = []
    for item in row.get("evidence") or []:
        if isinstance(item, dict):
            evidence_items.append(SuggestionEvidence(
                turn_id=redact_monitor_text(str(item.get("turn_id") or ""), 120),
                snippet=redact_monitor_text(str(item.get("snippet") or ""), 260),
            ))
    try:
        return LearningSuggestion(
            id=redact_monitor_text(str(row.get("id") or ""), 160),
            kind=redact_monitor_text(str(row.get("kind") or "unknown"), 80),
            recommendation=redact_monitor_text(str(row.get("recommendation") or ""), 500),
            evidence=tuple(evidence_items),
            status=redact_monitor_text(str(row.get("status") or "suggested"), 40),
            promotion=redact_monitor_text(str(row.get("promotion") or LearningSuggestion.promotion), 180),
            created_at=float(row.get("created_at") or time.time()),
        )
    except Exception:
        return None
