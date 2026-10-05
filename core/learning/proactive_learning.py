"""Offline learning-suggestion review and activation lifecycle.

This module mines episodic memory, owns suggestion status/reconciliation, and
adapts or materializes confirmed rows. It does not write profile learning,
workflow promotions, system prompts, or task truth.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_text
from ..runtime.backend_monitor import redact_monitor_text
from ..runtime.lock import file_byte_lock
from ..utils.env_utils import int_env
from ..utils.jsonl_utils import read_jsonl, write_jsonl
from ..utils.text_safety import contains_secret_value
from ..gates.threat_scan import scan_text
from .feedback_learning import is_explicit_feedback
from ..gates.capture_detection import direct_operator_directives


_LEARNING_SUGGESTIONS_THREAD_LOCK = threading.RLock()


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
    auto_promoted: bool = False

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
        re.compile(
            r"\b(?:concise|brief|short|compact|too long|less words|direct)\b[^.!?;\n]{0,60}"
            r"\b(?:answers?|replies|reply|responses?|explanations?)\b|"
            r"\b(?:answers?|replies|reply|responses?|explanations?)\b[^.!?;\n]{0,60}"
            r"\b(?:concise|brief|short|compact|too long|less words|direct)\b", re.I,
        ),
        "Consider a communication preference for concise/direct routine replies unless evidence/report depth is needed.",
    ),
    (
        "clean_finish",
        re.compile(r"\b(no dirty|dirty work|legacy|left behind|duplicate mechanism|finish clean|cleanly)\b", re.I),
        "Consider a clean-finish workflow candidate for edit turns: remove abandoned paths, avoid duplicate mechanisms, and verify before completion claims.",
    ),
    (
        "assumption_control",
        re.compile(r"\b(?:do\s+not|don'?t|stop)\s+(?:assum\w*|rush\w*|guess\w*)\b|\bverify\b.{0,100}\bbefore\b.{0,80}\b(?:assuming|deciding)\b", re.I | re.S),
        "Consider a no-assumption behavior rule: inspect current evidence and the existing owner before deciding or acting.",
    ),
    (
        "acceptance_fidelity",
        re.compile(r"\b(?:exact|original|requested)\b.{0,100}\b(?:requirement|acceptance|interaction|workflow)\b|\bdo\s+not\b.{0,100}\b(?:substitute|workaround|manual)\b", re.I | re.S),
        "Consider an acceptance-fidelity rule: preserve the exact requested interaction and never label partial infrastructure work complete.",
    ),
    (
        "visual_preference",
        re.compile(
            r"(?:\b(?:design|ui|interface|visual|render(?:ing)?|radius|rounded|premium|quality)\b"
            r".{0,180}\b(?:prefer|avoid|high[- ]quality|premium|round|rounded|rounded[ -]edges?|border[ -]radius|cloud|glass|generic)\b|"
            r"\b(?:prefer|avoid|high[- ]quality|premium)\b"
            r".{0,180}\b(?:design|ui|interface|visual|render(?:ing)?|radius|rounded|premium|quality|cloud|glass|codex|generic)\b)",
            re.I | re.S,
        ),
        "Consider an operator visual-preference candidate for MO Design: preserve repeated preferences about rendering quality, hierarchy, edge/radius language, and distinctive UI direction; approval is required before profile use.",
    ),
)


def learning_suggestion_kinds(text: str) -> tuple[str, ...]:
    """Return the suggestion kinds this new operator turn can affect."""
    clean = str(text or "")
    if not clean.strip() or is_explicit_feedback(clean):
        return ()
    return tuple(
        kind
        for kind, pattern, _recommendation in _PATTERNS
        if any(direct_operator_directives(clean, pattern))
    )

def _resolve_suggestions_path(
    path: str | Path | None = None, *, profile: Any = None, config: dict | None = None,
) -> Path:
    """Resolve an explicit ledger or the active profile's existing learning store."""
    from ..state.paths import resolve_state_path
    if path is None and getattr(profile, "_path", None):
        path = Path(profile._path).parent / "learning" / "suggestions.jsonl"
    return Path(resolve_state_path(path or "memory/learning/suggestions.jsonl", config))


_UNPROVEN_RETIREMENT_REASON = "generated skill retired after unproven use"


def _retired_suggestion_authority(path: str | Path) -> tuple[set[str], set[str]]:
    """Resolve recoverable id and semantic tombstones beside one ledger.

    Only the operator's dismissal blocks a recommendation for good. A pack
    retired automatically for unproven use keeps just its id tombstone, so the
    same pattern can be suggested again from new evidence.
    """
    source = _resolve_suggestions_path(path)
    owner_root = (
        source.parent.parent
        if source.parent.name.casefold() == "learning"
        else source.parent
    )
    skill_root = (
        owner_root.parent / "skills"
        if owner_root.name.casefold() == "memory"
        else owner_root / "skills"
    )
    try:
        from ..skills import retired_learning_authority

        ids, recommendations = retired_learning_authority(skill_root)
        semantic = {_normalize_recommendation(item) for item in recommendations if item}
        rows = read_jsonl(source) if source.is_file() else []
        unproven = {
            _normalize_recommendation(str(row.get("recommendation") or ""))
            for row in rows
            if str(row.get("status") or "").casefold() == "retired"
            and str(row.get("retirement_reason") or "") == _UNPROVEN_RETIREMENT_REASON
        }
        dismissed = {
            _normalize_recommendation(str(row.get("recommendation") or ""))
            for row in rows
            if str(row.get("status") or "").casefold() == "dismissed"
        }
        return ids, semantic - (unproven - dismissed)
    except Exception:
        return set(), set()


def mine_learning_suggestions(
    memory_path: str | Path | None = None,
    *,
    min_occurrences: int = 2,
    max_items: int = 5,
    kinds: tuple[str, ...] | None = None,
) -> list[LearningSuggestion]:
    """Return reviewable recurring learning suggestions from episodic memory."""
    from ..state.paths import resolve_state_path
    selected = set(kinds) if kinds is not None else None
    patterns = tuple(
        item for item in _PATTERNS
        if selected is None or item[0] in selected
    )
    if not patterns:
        return []
    # sqlite connect creates the file even on read — route the default to private
    # state so a default call never materializes cwd/memory/learning/episodes.sqlite.
    rows = _load_turns(resolve_state_path(memory_path or "memory/learning/episodes.sqlite"))
    if not rows:
        return []
    grouped: dict[str, list[SuggestionEvidence]] = {kind: [] for kind, _pattern, _rec in patterns}
    for row in rows:
        # Mine the operator's words only. Concatenating the assistant text let MO's
        # own evidence-first prose ("I verified the tests") complete an operator-
        # feedback pattern started by the user ("you didn't ..."), feeding MO's own
        # voice back as "learning".
        text = str(row.get("user", "") or "")
        # Direct corrections already enter the accepted profile-learning owner.
        # Mining the same turn into a reviewable/generated skill made one operator
        # instruction active through two independent lifecycle paths.
        if is_explicit_feedback(text):
            continue
        for kind, pattern, _recommendation in patterns:
            if any(direct_operator_directives(text, pattern)):
                grouped[kind].append(SuggestionEvidence(
                    turn_id=redact_monitor_text(row.get("turn_id", ""), 120),
                    snippet=_snippet(text),
                ))
                break

    suggestions: list[LearningSuggestion] = []
    for kind, _pattern, recommendation in patterns:
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


# Transient session-health diagnostics, NOT durable learnings: these describe THIS
# session's provider/context/indexing state, carry no cross-session behavioral
# lesson, and at volume (e.g. context_pressure fires every pressured session) they
# flood the capped store and crowd out genuine operator-feedback before it can
# accumulate. They belong in session telemetry/monitor, not the durable learning
# store, so they are dropped at the write boundary (the single sink for all sources).
TRANSIENT_DIAGNOSTIC_PREFIXES = ("trace:", "closeout:")


def _is_transient_diagnostic_kind(kind: str) -> bool:
    kind = str(kind or "")
    return kind.startswith(TRANSIENT_DIAGNOSTIC_PREFIXES)


def write_learning_suggestions(
    suggestions: list[LearningSuggestion],
    *,
    path: str | Path | None = None,
) -> Path:
    """Append unique reviewable suggestions to JSONL and return the path.

    Transient diagnostic canaries (``trace:``/``closeout:``) are not persisted:
    they are session telemetry, not durable learnings, and would crowd the store.
    """
    from ..state.paths import resolve_state_path
    out = Path(resolve_state_path(path or "memory/learning/suggestions.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)
    retired_ids, retired_recommendations = _retired_suggestion_authority(out)
    with file_byte_lock(out.parent / ".suggestions.lock", _LEARNING_SUGGESTIONS_THREAD_LOCK):
        existing = _existing_ids(out)
        confirmed_recommendations = {
            _normalize_recommendation(str(row.get("recommendation") or ""))
            for row in read_jsonl(out)
            if str(row.get("status") or "").casefold() == "confirmed"
            and str(row.get("id") or "") not in retired_ids
        }
        with out.open("a", encoding="utf-8") as fh:
            for suggestion in suggestions:
                recommendation = _normalize_recommendation(suggestion.recommendation)
                if (
                    suggestion.id in existing
                    or recommendation in confirmed_recommendations
                    or recommendation in retired_recommendations
                    or _is_transient_diagnostic_kind(suggestion.kind)
                ):
                    continue
                fh.write(json.dumps(suggestion.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")
                existing.add(suggestion.id)
        _prune_learning_suggestions(out)
    return out


def read_learning_suggestions(
    *,
    path: str | Path = "memory/learning/suggestions.jsonl",
    include_inactive: bool = False,
) -> list[LearningSuggestion]:
    """Read reviewable suggestions from JSONL, newest first."""
    src = _resolve_suggestions_path(path)
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
        if _is_transient_diagnostic_kind(suggestion.kind):
            continue
        if not include_inactive and suggestion.status not in {"suggested", "pending"}:
            continue
        suggestions.append(suggestion)
    return suggestions


def update_learning_suggestion_status(
    suggestion_id: str,
    status: str,
    *,
    path: str | Path = "memory/learning/suggestions.jsonl",
) -> bool:
    """Mark a suggestion in one explicit lifecycle state without applying it."""
    clean_id = str(suggestion_id or "").strip()
    clean_status = str(status or "").strip().lower()
    if not clean_id or clean_status not in {"suggested", "confirmed", "dismissed", "expired", "retired"}:
        return False
    src = _resolve_suggestions_path(path)
    if not src.exists():
        return False
    with file_byte_lock(src.parent / ".suggestions.lock", _LEARNING_SUGGESTIONS_THREAD_LOCK):
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
            write_jsonl(src, rows)
        except OSError:
            return False
        return True


def retire_learning_suggestions(
    suggestion_ids: set[str] | tuple[str, ...] | list[str],
    *,
    path: str | Path = "memory/learning/suggestions.jsonl",
    now: float | None = None,
) -> int:
    """Retire confirmed source rows after their physical learned packs retire."""
    wanted = {str(item or "").strip() for item in suggestion_ids if str(item or "").strip()}
    if not wanted:
        return 0
    src = _resolve_suggestions_path(path)
    if not src.exists():
        return 0
    current = float(now if now is not None else time.time())
    with file_byte_lock(src.parent / ".suggestions.lock", _LEARNING_SUGGESTIONS_THREAD_LOCK):
        rows = read_jsonl(src)
        changed = 0
        for row in rows:
            if (
                str(row.get("id") or "") in wanted
                and str(row.get("status") or "").casefold() == "confirmed"
            ):
                row["status"] = "retired"
                row["retired_at"] = current
                row["retirement_reason"] = _UNPROVEN_RETIREMENT_REASON
                changed += 1
        if changed:
            write_jsonl(src, rows)
        return changed


def _prune_learning_suggestions(path: Path) -> None:
    keep = int_env("MO_LEARNING_SUGGESTIONS_MAX", 100)
    if keep <= 0:
        return
    try:
        lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        filtered = []
        for line in lines:
            try:
                kind = str((json.loads(line) or {}).get("kind") or "")
            except Exception:
                kind = ""
            if not _is_transient_diagnostic_kind(kind):
                filtered.append(line)
        if len(filtered) > keep:
            filtered = filtered[-keep:]
        if filtered != lines:
            atomic_write_text(path, ("\n".join(filtered) + "\n") if filtered else "", encoding="utf-8")
    except Exception:
        return


# ── clustering / confidence / expiry (closes the review loop) ─────────────────
#
# Closeout/trace writers mint a new digest id for the same semantic insight every
# session, so the raw store fills with near-duplicates that one-by-one review can
# never keep up with (observed live: 100/100 caps maxed, 0 ever promoted).
# Clustering collapses same-(kind, recommendation) items into one reviewable unit
# with a deterministic confidence score; confirm/dismiss applies to the whole
# cluster. Confirmed clusters become MO skills through the unified skills loader;
# there is no second Agent-context injection path.

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


def _merge_semantic_clusters(clusters: list[SuggestionCluster]) -> list[SuggestionCluster]:
    """Collapse equivalent recommendations even when legacy rows changed kind."""
    buckets: dict[str, list[SuggestionCluster]] = {}
    for cluster in clusters:
        buckets.setdefault(_normalize_recommendation(cluster.recommendation), []).append(cluster)
    merged: list[SuggestionCluster] = []
    for members in buckets.values():
        representative = max(
            members,
            key=lambda item: (item.confidence, item.count, item.last_seen, item.kind),
        )
        ids = tuple(dict.fromkeys(candidate_id for item in members for candidate_id in item.ids))
        merged.append(SuggestionCluster(
            kind=representative.kind,
            recommendation=representative.recommendation,
            ids=ids,
            count=sum(item.count for item in members),
            confidence=max(item.confidence for item in members),
            first_seen=min(item.first_seen for item in members),
            last_seen=max(item.last_seen for item in members),
            representative=representative.representative,
        ))
    merged.sort(key=lambda item: (-item.confidence, -item.count, item.kind))
    return merged


def suggestion_review_clusters(
    *,
    path: str | Path = "memory/learning/suggestions.jsonl",
    now: float | None = None,
    retired_ids: set[str] | None = None,
) -> tuple[list[SuggestionCluster], list[SuggestionCluster]]:
    """Return genuinely reviewable pending clusters and active authorities."""
    suggestions = read_learning_suggestions(
        path=_resolve_suggestions_path(path),
        include_inactive=True,
    )
    if retired_ids is None:
        retired, retired_recommendations = _retired_suggestion_authority(path)
    else:
        retired, retired_recommendations = set(retired_ids), set()
    active_rows = [
        item
        for item in suggestions
        if str(item.status).casefold() == "confirmed"
        and item.id not in retired
        and _normalize_recommendation(item.recommendation) not in retired_recommendations
    ]
    active = _merge_semantic_clusters(cluster_suggestions(active_rows, now=now))
    active_recommendations = {
        _normalize_recommendation(cluster.recommendation)
        for cluster in active
    }
    pending_rows = [
        item
        for item in suggestions
        if str(item.status).casefold() in {"suggested", "pending"}
        and _normalize_recommendation(item.recommendation) not in retired_recommendations
        and _normalize_recommendation(item.recommendation) not in active_recommendations
    ]
    return _merge_semantic_clusters(cluster_suggestions(pending_rows, now=now)), active


def expire_stale_suggestions(
    *,
    path: str | Path = "memory/learning/suggestions.jsonl",
    ttl_days: int | None = None,
    now: float | None = None,
) -> int:
    """Mark unreviewed suggestions older than the TTL as expired; return count."""
    ttl = int(ttl_days if ttl_days is not None else int_env("MO_LEARNING_SUGGESTION_TTL_DAYS", 7))
    if ttl <= 0:
        return 0
    src = _resolve_suggestions_path(path)
    if not src.exists():
        return 0
    current = float(now if now is not None else time.time())
    cutoff = current - ttl * 86400
    with file_byte_lock(src.parent / ".suggestions.lock", _LEARNING_SUGGESTIONS_THREAD_LOCK):
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
                write_jsonl(src, rows)
        except OSError:
            return 0
        return expired


def resolve_cluster_ids(
    suggestion_id: str,
    *,
    path: str | Path = "memory/learning/suggestions.jsonl",
    include_confirmed: bool = False,
) -> list[str]:
    """Return ids in one review cluster.

    Confirmation sees only pending rows. Dismissal may additionally select a
    confirmed cluster so an auto/manual activation can be reversed.
    """
    clean = str(suggestion_id or "").strip()
    if not clean:
        return []
    if not include_confirmed:
        pending, _active = suggestion_review_clusters(path=path)
        for cluster in pending:
            if clean in cluster.ids:
                return list(cluster.ids)
        return []
    suggestions = read_learning_suggestions(
        path=_resolve_suggestions_path(path),
        include_inactive=True,
    )
    retired_ids, retired_recommendations = _retired_suggestion_authority(path)
    suggestions = [
        item for item in suggestions
        if item.status in {"suggested", "pending", "confirmed"}
        and item.id not in retired_ids
        and _normalize_recommendation(item.recommendation) not in retired_recommendations
    ]
    for cluster in cluster_suggestions(suggestions):
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


_LEARNING_LABELS = {
    "evidence_first": "Verify before claiming completion",
    "scope_control": "Stay within your request",
    "communication_concise": "Keep routine replies concise",
    "clean_finish": "Finish edits cleanly",
}


def _cluster_evidence_count(cluster: SuggestionCluster) -> int:
    seen = {
        item.turn_id
        for item in cluster.representative.evidence
        if item.turn_id
    }
    return max(cluster.count, len(seen))


def render_learning_overview(
    pending: list[SuggestionCluster],
    active: list[SuggestionCluster],
    *,
    workflow_candidates: list[dict[str, Any]] | None = None,
    active_workflows: list[dict[str, str]] | None = None,
    auto_enabled: bool,
    expired_count: int = 0,
) -> str:
    """Plain-language learning status with one numbered review queue."""
    workflows = list(workflow_candidates or [])
    active_habits = list(active_workflows or [])
    review_count = len(pending) + len(workflows)
    lines = ["Learning"]
    auto_state = "On" if auto_enabled else "Off"
    lines.append(f"Automatic safe learning: {auto_state}")
    lines.append("  When on, repeated verification, clean-finish, and concise-reply feedback may activate automatically.")
    lines.append(f"Already active from learning review ({len(active) + len(active_habits)}):")
    if not active and not active_habits:
        lines.append("  None")
    for cluster in active[:5]:
        source = "learned automatically" if cluster.representative.auto_promoted else "approved by you"
        label = _LEARNING_LABELS.get(cluster.kind, cluster.kind.replace("_", " ").title())
        lines.append(f"  • {label} — {source}")
    for habit in active_habits[:max(0, 5 - len(active))]:
        lines.append(f"  • Workflow habit — {_one_line_recommendation(habit['summary'], 180)}")
    lines.append(f"Needs your review ({review_count}):")
    if not review_count:
        lines.append("  Nothing pending.")
    index = 1
    for cluster in pending[:5]:
        label = _LEARNING_LABELS.get(cluster.kind, cluster.kind.replace("_", " ").title())
        evidence_count = _cluster_evidence_count(cluster)
        eligibility = " · may auto-activate after repeated feedback" if auto_enabled and cluster.kind in AUTO_PROMOTE_SAFE_KINDS else ""
        lines.extend([
            f"  {index}. {label}{eligibility}",
            f"     What changes: {_one_line_recommendation(cluster.recommendation, 180)}",
            f"     Why suggested: based on {evidence_count} matching conversation(s).",
            f"     Choose: /learning confirm {index}  or  /learning dismiss {index}",
        ])
        index += 1
    for record in workflows[:max(0, 5 - len(pending))]:
        trigger = _one_line_recommendation(str(record.get("trigger") or "matching work"), 140)
        behavior = _one_line_recommendation(str(record.get("behavior") or "apply the reviewed workflow"), 180)
        lines.extend([
            f"  {index}. Workflow habit",
            f"     When: {trigger}",
            f"     What changes: {behavior}",
            "     Why suggested: you gave an explicit workflow preference.",
            f"     Choose: /learning confirm {index}  or  /learning dismiss {index}",
        ])
        index += 1
    hidden = review_count - min(review_count, 5)
    if hidden:
        lines.append(f"  … {hidden} older item(s) not shown")
    if expired_count:
        lines.append(f"Expired since last review: {expired_count}")
    lines.append("Pending items are inactive. Use /learning details <number> for an example.")
    lines.append("Other personalization: /profile · active skill packs: /skills")
    return "\n".join(lines)


def next_learning_suggestion_notice(
    *,
    path: str | Path = "memory/learning/suggestions.jsonl",
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
    pending, _active = suggestion_review_clusters(path=path, now=current)
    if not pending:
        return ""
    rows_by_id = _read_rows_by_id(_resolve_suggestions_path(path))
    cooldown = max(0.0, float(cooldown_hours or 0.0)) * 3600
    for cluster in pending:
        if cluster.confidence < min_confidence:
            continue
        prompted = max(float((rows_by_id.get(sid) or {}).get("last_prompted_at") or 0.0) for sid in cluster.ids)
        if prompted and cooldown and current - prompted < cooldown:
            continue
        _mark_suggestions_prompted(_resolve_suggestions_path(path), cluster.ids, current)
        return (
            f"Learning suggestion ready: {_LEARNING_LABELS.get(cluster.kind, cluster.kind.replace('_', ' '))}. "
            "Open /learning to select it, see what changes, and Approve or Dismiss."
        )
    return ""


# The accepted-turn owner may auto-confirm only these policy-eligible kinds
# after recurrence, recency, confidence and content checks. Other suggestions
# stay inactive until explicit review. /learning > Active learning exposes
# Undo through the same dismiss owner; there is no separate automatic ledger.
AUTO_PROMOTE_SAFE_KINDS = frozenset({"evidence_first", "clean_finish", "communication_concise"})


def auto_promote_safe_clusters(
    *,
    path: str | Path = "memory/learning/suggestions.jsonl",
    min_confidence: float = 0.8,
    min_count: int = 3,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Auto-confirm only high-confidence, universal, low-risk suggestion clusters.

    Effective bar: a universal-safe kind, seen >= ``min_count`` times, with cluster
    confidence >= ``min_confidence`` (recurrence + recency). Returns a summary of
    what was promoted for audit/notice. Confirms nothing when the store is empty,
    the bar is not met, or the text trips the secret/threat gate. The ``confirmed``
    status is exactly what the unified skills adapter consumes, so a promoted
    cluster becomes selectable on the next matching turn.
    """
    current = float(now if now is not None else time.time())
    src = _resolve_suggestions_path(path)
    pending, _active = suggestion_review_clusters(path=src, now=current)
    if not pending:
        return []
    promoted_by_id: dict[str, dict[str, Any]] = {}
    promote_ids: set[str] = set()
    for cluster in pending:
        if cluster.kind not in AUTO_PROMOTE_SAFE_KINDS:
            continue
        if cluster.confidence < min_confidence or cluster.count < min_count:
            continue
        text = str(cluster.recommendation or "")
        # Safety re-gate at the promotion boundary: never auto-confirm text that
        # trips the secret detector or the input threat scan.
        if contains_secret_value(text) or scan_text(text, surface="learning auto-promote").blocked:
            continue
        representative_id = cluster.representative.id
        promote_ids.add(representative_id)
        promoted_by_id[representative_id] = {
            "id": cluster.representative.id,
            "kind": cluster.kind,
            "confidence": cluster.confidence,
            "count": cluster.count,
            "recommendation": _one_line_recommendation(text, 200),
            "auto_promoted": True,
            "approval": "automatic-safe",
            # Carry the representative's own turn evidence so a materialized pack
            # can ground the behavioral rule in the turns that justified it.
            "evidence": [item.as_dict() for item in cluster.representative.evidence],
        }
    confirmed_ids = _confirm_ids_auto(src, promote_ids, current) if promote_ids else set()
    if confirmed_ids:
        # One confirmed representative owns the cluster; the existing
        # reconciliation owner retires its still-pending recurrence rows.
        reconcile_confirmed_learnings(path=src, now=current)
    return [
        summary
        for item_id, summary in promoted_by_id.items()
        if item_id in confirmed_ids
    ]


def _confirm_ids_auto(path: Path, ids: set[str], when: float) -> set[str]:
    """Flip the given suggested/pending ids to confirmed in one rewrite, tagging
    them ``auto_promoted`` for audit. Only touches still-unreviewed rows so it
    never overrides an explicit operator dismiss/confirm."""
    if not ids or not path.exists():
        return set()
    with file_byte_lock(path.parent / ".suggestions.lock", _LEARNING_SUGGESTIONS_THREAD_LOCK):
        rows = read_jsonl(path)
        changed = False
        confirmed: set[str] = set()
        for row in rows:
            if str(row.get("id") or "") in ids and str(row.get("status") or "suggested").lower() in {"suggested", "pending"}:
                row["status"] = "confirmed"
                row["updated_at"] = when
                row["auto_promoted"] = True
                row["auto_promoted_at"] = when
                changed = True
                confirmed.add(str(row.get("id") or ""))
        if changed:
            write_jsonl(path, rows)
        return confirmed


def reconcile_confirmed_learnings(
    *,
    path: str | Path = "memory/learning/suggestions.jsonl",
    now: float | None = None,
    profile: Any | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministically consolidate active learning suggestion clusters.

    One confirmed row remains authoritative for each normalized insight. Duplicate
    confirmed rows and still-pending rows already covered by that authority are
    marked ``superseded``. Physical packs are retired before their confirmed ledger
    rows so persisted skill authority cannot drift from the suggestion ledger.
    """
    empty = {
        "confirmed_before": 0,
        "clusters": 0,
        "superseded": 0,
        "pending_superseded": 0,
        "retirement_blocked": 0,
    }
    current = float(now if now is not None else time.time())
    src = _resolve_suggestions_path(path)
    if not src.exists():
        return empty
    with file_byte_lock(src.parent / ".suggestions.lock", _LEARNING_SUGGESTIONS_THREAD_LOCK):
        suggestions = read_learning_suggestions(path=src, include_inactive=True)
        retired_ids, retired_recommendations = _retired_suggestion_authority(src)
        confirmed = [
            item
            for item in suggestions
            if str(item.status).lower() == "confirmed"
            and item.id not in retired_ids
            and _normalize_recommendation(item.recommendation) not in retired_recommendations
        ]
        if not confirmed:
            return empty
        clusters = _merge_semantic_clusters(cluster_suggestions(confirmed, now=current))
        keep_ids = {cluster.representative.id for cluster in clusters}
        confirmed_recommendations = {
            _normalize_recommendation(cluster.recommendation)
            for cluster in clusters
        }
        superseded_ids: set[str] = set()
        for cluster in clusters:
            if cluster.count > 1:
                superseded_ids.update(sid for sid in cluster.ids if sid not in keep_ids)
        pending_superseded_ids = {
            item.id
            for item in suggestions
            if str(item.status).lower() in {"suggested", "pending"}
            and _normalize_recommendation(item.recommendation) in confirmed_recommendations
        }
        retirement_blocked: set[str] = set()
        if superseded_ids and profile is not None:
            from ..skills import materialized_learning_ids, retire_skill_packs_by_candidate_ids

            retire_skill_packs_by_candidate_ids(profile, superseded_ids, config=config)
            retirement_blocked = superseded_ids.intersection(
                materialized_learning_ids(profile, config=config)
            )
            superseded_ids.difference_update(retirement_blocked)
        if superseded_ids or pending_superseded_ids:
            rows = read_jsonl(src)
            for row in rows:
                row_id = str(row.get("id") or "")
                status = str(row.get("status") or "suggested").lower()
                if (
                    (row_id in superseded_ids and status == "confirmed")
                    or (row_id in pending_superseded_ids and status in {"suggested", "pending"})
                ):
                    row["status"] = "superseded"
                    row["updated_at"] = current
            write_jsonl(src, rows)
        return {
            "confirmed_before": len(confirmed),
            "clusters": len(clusters),
            "superseded": len(superseded_ids),
            "pending_superseded": len(pending_superseded_ids),
            "retirement_blocked": len(retirement_blocked),
        }


def materialize_confirmed_learning_clusters(
    profile: Any,
    *,
    path: str | Path = "memory/learning/suggestions.jsonl",
    runtime_home: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Backfill one physical skill pack for each historical confirmed cluster.

    Confirmed rows already work through the virtual skill adapter. This migration
    gives old confirmations the same durable pack form as new confirmations while
    preserving the single-surface dedupe contract.
    """
    _pending, clusters = suggestion_review_clusters(
        path=_resolve_suggestions_path(path),
    )
    try:
        from ..skills import (
            materialized_learning_authority,
            retired_learning_authority,
            skills_root,
            write_skill_pack_from_suggestion,
        )
        from ..skills._util import _one_line

        materialized, materialized_recommendations = materialized_learning_authority(
            profile,
            runtime_home=runtime_home,
            config=config,
        )
        retired, retired_recommendations = retired_learning_authority(
            skills_root(profile, runtime_home=runtime_home, config=config)
        )
    except Exception:
        return {"clusters": len(clusters), "created": 0, "covered": 0}
    created = 0
    covered = 0
    for cluster in clusters:
        # Physical ownership uses the skill loader's literal bounded signature,
        # not the broader normalization used to cluster diagnostic suggestions.
        signature = _one_line(cluster.representative.recommendation, 500).casefold()
        if (
            materialized.intersection(cluster.ids)
            or retired.intersection(cluster.ids)
            or signature in materialized_recommendations
            or signature in retired_recommendations
        ):
            covered += 1
            continue
        row = cluster.representative.as_dict()
        row["status"] = "confirmed"
        try:
            write_skill_pack_from_suggestion(
                row,
                profile=profile,
                runtime_home=runtime_home,
                config=config,
            )
            materialized.add(cluster.representative.id)
            materialized_recommendations.add(signature)
            created += 1
        except Exception:
            continue
    return {"clusters": len(clusters), "created": created, "covered": covered + created}


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
    with file_byte_lock(path.parent / ".suggestions.lock", _LEARNING_SUGGESTIONS_THREAD_LOCK):
        rows = read_jsonl(path)
        changed = False
        for row in rows:
            if str(row.get("id") or "") in wanted:
                row["last_prompted_at"] = prompted_at
                changed = True
        if changed:
            write_jsonl(path, rows)


def _one_line_recommendation(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean[:limit].rstrip()


def _load_turns(memory_path: str | Path) -> list[dict[str, str]]:
    path = Path(memory_path)
    if not path.exists():
        return []
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT turn_id, user, assistant FROM turns ORDER BY updated_at DESC, rowid DESC LIMIT 1000"
            ).fetchall()
            rows.reverse()
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
            auto_promoted=bool(row.get("auto_promoted", False)),
        )
    except Exception:
        return None
