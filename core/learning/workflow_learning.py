"""Staging and explicit promotion for MO local skill candidates.

Candidates are local/inert. Only explicit operator promotion creates compact,
relevance-gated local skill guidance for later turns. Taskboard truth still lives
with Gateway/Agent evidence, never here.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_text
from ..utils.env_utils import int_env
from ..utils.jsonl_utils import read_jsonl, write_jsonl
from ..utils.text_safety import contains_secret_value
from ..gates.threat_scan import scan_text
from ..gates.capture_detection import direct_operator_directives
from ..runtime.lock import file_byte_lock

_WORKFLOW_PROMOTION_THREAD_LOCK = threading.RLock()

_DURABLE_SIGNAL_RE = re.compile(
    r"\b(?:next time|from now on|when i ask(?: you)?(?: to| for)?|i prefer|remember this)\b|"
    r"(?:^|[.!?]\s+)(?:mo[, ]+)?(?:always|never)\b|"
    r"\b(?:always|never)\s+(?:for|when|on)\b",
    re.IGNORECASE,
)
_WORK_MARKERS = (
    "build", "fix", "review", "investigate", "audit", "debug", "test", "verify",
    "taskboard", "task board", "evidence", "scope", "profile", "report", "gateway",
    "docs", "documentation", "style", "process", "method", "workflow", "skill",
)
WORKFLOW_CANDIDATE_NOTICE = "Skill staged: approve latest"
WORKFLOW_REPEAT_NOTICE = "Skill repeated 3x: approve latest?"


def _durable_signal_text(text: str) -> str:
    """Return only the sentence carrying an explicit future-work preference."""
    clean = str(text or "").strip()
    if not clean:
        return ""
    for sentence, _match in direct_operator_directives(clean, _DURABLE_SIGNAL_RE):
        return sentence.strip()
    return ""


def extract_workflow_candidate(user_text: str, assistant_text: str = "") -> dict[str, Any]:
    """Return an inert local skill candidate from explicit high-signal feedback only."""
    text = str(user_text or "").strip()
    signal_text = _durable_signal_text(text)
    low = signal_text.lower()
    if not signal_text or not any(marker in low for marker in _WORK_MARKERS):
        return {}
    threat = scan_text(text, surface="workflow candidate")
    if threat.blocked or _has_secret_warning(threat):
        return {}
    digest = hashlib.sha1(signal_text.encode("utf-8", errors="ignore")).hexdigest()[:12]
    candidate = {
        "id": f"workflow-candidate:{digest}",
        "status": "candidate",
        "trigger": _sentence_with_marker(signal_text, _WORK_MARKERS) or "explicit operator workflow preference",
        "behavior": _compact_behavior(signal_text),
        "evidence": "explicit operator text",
        "scope": "build/fix/review/investigate turns where the trigger applies",
        "anti_pattern": "do not apply to unrelated chat or broaden the user's scope",
        "promotion": "requires explicit operator approval before active use",
        "assistant_excerpt": str(assistant_text or "")[:240],
        "created_at": time.time(),
    }
    warnings = [finding.kind for finding in threat.warnings]
    if warnings:
        candidate["threat_warnings"] = warnings
    return candidate


def record_workflow_candidate(profile: Any, user_text: str, assistant_text: str = "") -> bool:
    """Append an inert candidate under memory/ only when extraction is high-signal."""
    return bool(record_workflow_candidate_result(profile, user_text, assistant_text).get("recorded"))


def record_workflow_candidate_result(profile: Any, user_text: str, assistant_text: str = "") -> dict[str, Any]:
    """Append an inert candidate and return notice metadata for the caller.

    Promotion remains explicit-approval only. Repeat counts create only a compact
    approval prompt so repeated operator feedback does not silently become an
    active workflow.
    """
    candidate = extract_workflow_candidate(user_text, assistant_text)
    if not candidate:
        return {"recorded": False, "reason": "no high-signal skill candidate"}
    path = _candidate_path(profile)
    try:
        added = _append_candidate_record(
            path,
            candidate,
            active_ids=_materialized_workflow_ids(profile),
        )
    except OSError as exc:
        return {"recorded": False, "reason": f"write failed: {type(exc).__name__}", "candidate": candidate}
    repeat_count = int(candidate.get("repeat_count") or 1)
    if added and repeat_count >= 3:
        notice = f"Workflow suggestion repeated {repeat_count}x: open /learning to review, then Approve or Dismiss."
    else:
        notice = "Workflow suggestion staged (not active): open /learning to review, then Approve or Dismiss."
    return {
        "recorded": bool(added),
        "duplicate": not added,
        "candidate": candidate,
        "id": candidate.get("id", ""),
        "repeat_count": repeat_count,
        "notice": notice,
    }


def stage_workflow_source_candidate(
    profile: Any,
    source_text: str,
    *,
    source_label: str = "inline workflow text",
    source_kind: str = "text",
    request_text: str = "",
) -> dict[str, Any]:
    """Stage an inert skill candidate from an external file/link/paste.

    External "skills" are untrusted source material. This function scans and
    compacts them into MO's existing candidate format; it does not
    promote, execute, create commands, or change taskboard truth.
    """
    source = str(source_text or "").strip()
    if not source:
        return {"staged": False, "reason": "empty workflow source"}
    scan = scan_text(source, surface=f"workflow source:{source_kind}")
    if scan.blocked or _has_secret_value(source):
        reason = scan.reason() if scan.blocked else "secret-bearing workflow source"
        return {"staged": False, "blocked": True, "reason": reason, "scan": scan.as_dict()}
    candidate = extract_workflow_candidate_from_source(
        source,
        source_label=source_label,
        source_kind=source_kind,
        request_text=request_text,
        warnings=[finding.kind for finding in scan.warnings],
    )
    path = _candidate_path(profile)
    try:
        added = _append_candidate_record(
            path,
            candidate,
            active_ids=_materialized_workflow_ids(profile),
        )
        return {
            "staged": True,
            "duplicate": not added,
            "id": candidate["id"],
            "candidate": candidate,
            "path": str(path),
        }
    except OSError as exc:
        return {"staged": False, "reason": f"write failed: {type(exc).__name__}", "id": candidate.get("id", "")}


def extract_workflow_candidate_from_source(
    source_text: str,
    *,
    source_label: str = "inline workflow text",
    source_kind: str = "text",
    request_text: str = "",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Return an inert workflow candidate from reviewed external source text."""
    source = str(source_text or "").strip()
    label = _one_line(source_label or source_kind or "workflow source", 180)
    digest = hashlib.sha1((str(source_kind) + "\0" + str(source_label) + "\0" + source).encode("utf-8", errors="ignore")).hexdigest()[:12]
    candidate = {
        "id": f"workflow-candidate:{digest}",
        "status": "candidate",
        "trigger": _source_trigger(source, request_text),
        "behavior": _source_behavior(source),
        "evidence": f"external workflow source: {label}",
        "scope": _source_scope(source, request_text),
        "anti_pattern": "do not execute external code, supersede system/profile/Gateway truth, mutate taskboards, or apply outside matching user scope",
        "promotion": "requires explicit approval before active use",
        "source_kind": str(source_kind or "text")[:40],
        "source_label": label,
        "source_sha1": hashlib.sha1(source.encode("utf-8", errors="ignore")).hexdigest(),
        "source_excerpt": _one_line(source, 500),
        "source_text": source[:12000],
        "created_at": time.time(),
    }
    if request_text:
        candidate["adoption_request"] = str(request_text or "")[:240]
    if warnings:
        candidate["threat_warnings"] = list(dict.fromkeys(warnings))[:8]
    return candidate


def promote_workflow_candidate(profile: Any, user_text: str, assistant_text: str = "") -> dict[str, Any]:
    """Promote a staged skill candidate only on explicit operator approval."""
    from ..context.gateway_helpers import WORKFLOW_APPROVAL_RE

    text = str(user_text or "").strip()
    if not WORKFLOW_APPROVAL_RE.search(text):
        return {"promoted": False, "reason": "no explicit skill promotion request"}
    request_scan = scan_text(text, surface="workflow promotion request")
    if request_scan.blocked or _has_secret_warning(request_scan):
        return {"promoted": False, "blocked": True, "reason": request_scan.reason() or "secret-bearing approval text"}
    path = _candidate_path(profile)
    candidate_id = ""
    try:
        lock_path = path.parent / ".promotion.lock"
        with file_byte_lock(lock_path, _WORKFLOW_PROMOTION_THREAD_LOCK):
            active_ids = _materialized_workflow_ids(profile)
            candidate = _select_candidate(read_jsonl(path), text, active_ids=active_ids)
            if not candidate:
                return {"promoted": False, "reason": "no matching skill candidate"}
            candidate_id = str(candidate.get("id") or "")
            candidate_text = "\n".join(
                str(candidate.get(key) or "")
                for key in ("trigger", "behavior", "scope", "anti_pattern", "source_text")
            )
            candidate_scan = scan_text(candidate_text, surface="workflow candidate promotion")
            if candidate_scan.blocked or _has_secret_warning(candidate_scan):
                reason = candidate_scan.reason() if candidate_scan.blocked else "secret-bearing candidate text"
                return {"promoted": False, "blocked": True, "reason": reason, "id": candidate.get("id", "")}
            promoted = dict(candidate)
            promoted.update({
                "status": "promoted",
                "provenance": "promoted-workflow",
                "approved_at": time.time(),
                "approval_evidence": "explicit operator approval",
                "approval_excerpt": text[:240],
                "assistant_excerpt_at_approval": str(assistant_text or "")[:240],
            })
            from ..skills import write_skill_pack_from_candidate

            skill_path = write_skill_pack_from_candidate(promoted, profile=profile)
            promoted["skill_path"] = str(skill_path)
            promoted["skill_status"] = "active"
            return {"promoted": True, "id": promoted.get("id", ""), "skill_path": str(skill_path)}
    except Exception as exc:
        return {
            "promoted": False,
            "reason": f"write failed: {type(exc).__name__}",
            "id": candidate_id,
        }


def pending_workflow_candidates(profile: Any, *, max_items: int | None = None) -> list[dict[str, Any]]:
    """Return pending workflow candidates in stable review order."""
    candidates = read_jsonl(_candidate_path(profile))
    pending = _pending_candidate_records(candidates, _materialized_workflow_ids(profile))
    pending.sort(key=lambda record: float(record.get("created_at") or 0.0), reverse=True)
    if max_items is None:
        return pending
    return pending[:max(0, int(max_items or 0))]


def reconcile_workflow_candidates(profile: Any, *, now: float | None = None) -> dict[str, int]:
    """Retire redundant inert candidates while preserving operator-authored review items.

    Structural-graph hints duplicate the graph's own bounded query surface and must
    not become durable workflow authority. Historical exact duplicates are reduced
    to their newest pending representative.
    """
    path = _candidate_path(profile)
    result = {
        "pending_before": 0,
        "structural_retired": 0,
        "low_signal_retired": 0,
        "duplicates_retired": 0,
    }
    if not path.exists():
        return result
    current = float(now if now is not None else time.time())
    with file_byte_lock(path.parent / ".promotion.lock", _WORKFLOW_PROMOTION_THREAD_LOCK):
        records = read_jsonl(path)
        pending = _pending_candidate_records(records, _materialized_workflow_ids(profile))
        result["pending_before"] = len(pending)
        retire_reasons: dict[str, str] = {}
        newest_by_key: dict[str, dict[str, Any]] = {}
        for record in pending:
            record_id = str(record.get("id") or "")
            source_kind = str(record.get("source_kind") or "").strip().lower()
            if source_kind == "structural-graph":
                retire_reasons[record_id] = "mechanical structural-graph hint"
                result["structural_retired"] += 1
                continue
            if not source_kind and not _durable_signal_text(str(record.get("behavior") or "")):
                retire_reasons[record_id] = "legacy low-signal operator text"
                result["low_signal_retired"] += 1
                continue
            key = _normalize_candidate_text(record)
            previous = newest_by_key.get(key) if key else None
            if previous is None:
                if key:
                    newest_by_key[key] = record
                continue
            previous_time = float(previous.get("created_at") or 0.0)
            record_time = float(record.get("created_at") or 0.0)
            retired = previous if record_time >= previous_time else record
            kept = record if retired is previous else previous
            retire_reasons[str(retired.get("id") or "")] = "duplicate staged workflow"
            newest_by_key[key] = kept
            result["duplicates_retired"] += 1
        if retire_reasons:
            for record in records:
                reason = retire_reasons.get(str(record.get("id") or ""))
                if reason:
                    record["status"] = "retired"
                    record["retired_at"] = current
                    record["retirement_reason"] = reason
            write_jsonl(path, records)
    return result


def render_pending_workflow_candidates(profile: Any, *, max_items: int = 10) -> str:
    """Render bounded workflow review rows without exposing storage details."""
    all_pending = pending_workflow_candidates(profile)
    pending = all_pending[:max(0, int(max_items or 0))]
    lines = [f"Workflow suggestions needing review: {len(all_pending)}"]
    for index, record in enumerate(pending, start=1):
        trigger = _one_line(record.get("trigger", ""), 120) or "matching work"
        behavior = _one_line(record.get("behavior", ""), 180) or "no behavior recorded"
        lines.extend((
            f"  {index}. Workflow habit",
            f"     When: {trigger}",
            f"     What changes: {behavior}",
            f"     Choose: /learning confirm {index}  or  /learning dismiss {index}",
        ))
    if len(all_pending) > len(pending):
        lines.append(f"  ... {len(all_pending) - len(pending)} older suggestion(s) not shown")
    return "\n".join(lines)


def active_workflow_skills(profile: Any, *, config=None) -> list:
    """Project active approved workflows from their existing physical skill owner."""
    from ..skills import load_skills, skills_root

    return [
        skill for skill in load_skills([skills_root(profile, config=config)])
        if skill.candidate_id.startswith("workflow-candidate:")
    ]


def dismiss_workflow_candidate(profile: Any, candidate_id: str, *, config=None) -> bool:
    """Dismiss an inert candidate or recoverably retire its exact active packs."""
    clean_id = str(candidate_id or "").strip().lower()
    if not _candidate_id_from_text(clean_id) == clean_id:
        return False
    path = _candidate_path(profile)
    try:
        with file_byte_lock(path.parent / ".promotion.lock", _WORKFLOW_PROMOTION_THREAD_LOCK):
            if any(skill.candidate_id == clean_id for skill in active_workflow_skills(profile, config=config)):
                from ..skills import materialized_learning_ids, retire_skill_packs_by_candidate_ids

                retire_skill_packs_by_candidate_ids(profile, [clean_id], config=config)
                return clean_id not in materialized_learning_ids(profile, config=config)
            records = read_jsonl(path)
            pending_ids = {
                str(record.get("id") or "")
                for record in _pending_candidate_records(records, _materialized_workflow_ids(profile))
            }
            if clean_id not in pending_ids:
                return False
            out = []
            for record in records:
                if str(record.get("id") or "") == clean_id:
                    record = {**record, "status": "dismissed", "dismissed_at": time.time()}
                out.append(record)
            write_jsonl(path, out)
            return True
    except OSError:
        return False


def _normalize_candidate_text(record: dict[str, Any]) -> str:
    """Normalized (trigger, behavior) key so the same candidate doesn't restage
    every session under a fresh id (same flood disease the suggestion lane had:
    100 staged / 0 ever promoted, dominated by near-duplicates)."""
    import re as _re
    raw = f"{record.get('trigger', '')}\n{record.get('behavior', '')}".lower()
    raw = _re.sub(r"\d+", "0", raw)
    return _re.sub(r"\s+", " ", _re.sub(r"[^a-z0 ]+", " ", raw)).strip()


def _expire_stale_candidates(path: Path, *, ttl_days: int | None = None) -> int:
    """Drop never-promoted candidates older than the TTL.

    Current candidates stay immutable staging records; a physical skill pack owns
    promotion. Historical rows already marked promoted remain available for
    repair instead of being aged out as if their incomplete transition succeeded.
    """
    ttl = int(ttl_days if ttl_days is not None else int_env("MO_WORKFLOW_CANDIDATE_TTL_DAYS", 7))
    if ttl <= 0 or not path.exists():
        return 0
    cutoff = time.time() - ttl * 86400
    records = read_jsonl(path)
    kept = [
        r for r in records
        if str(r.get("status") or "candidate") != "candidate" or float(r.get("created_at") or cutoff) >= cutoff
    ]
    dropped = len(records) - len(kept)
    if dropped:
        write_jsonl(path, kept)
    return dropped


def _append_candidate_record(
    path: Path,
    candidate: dict[str, Any],
    *,
    active_ids: set[str] | None = None,
) -> bool:
    if str(candidate.get("source_kind") or "").strip().lower() == "structural-graph":
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_byte_lock(path.parent / ".promotion.lock", _WORKFLOW_PROMOTION_THREAD_LOCK):
        _expire_stale_candidates(path)
        records = read_jsonl(path)
        if str(candidate.get("id") or "") in (active_ids or set()):
            return False
        if any(record.get("id") == candidate["id"] for record in records):
            return False
        norm = _normalize_candidate_text(candidate)
        if norm and any(_normalize_candidate_text(record) == norm for record in records):
            return False  # same trigger/behavior already staged under another id

        candidate_signature = _repeat_signature(candidate)
        similar_pending_ids = {
            str(record.get("id") or "")
            for record in records
            if str(record.get("status") or "candidate").lower() == "candidate"
            and _similar_repeat(candidate_signature, _repeat_signature(record))
        }
        _annotate_repeat(candidate, records)
        if similar_pending_ids:
            superseded_at = time.time()
            for record in records:
                if str(record.get("id") or "") in similar_pending_ids:
                    record["status"] = "superseded"
                    record["superseded_at"] = superseded_at
            records.append(candidate)
            write_jsonl(path, records)
        else:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(candidate, ensure_ascii=False, sort_keys=True) + "\n")
        _prune_jsonl(path, "MO_WORKFLOW_CANDIDATE_MAX", 100)
        return True


def _annotate_repeat(candidate: dict[str, Any], records: list[dict[str, Any]]) -> None:
    signature = _repeat_signature(candidate)
    if not signature:
        return
    count = 1 + sum(1 for record in records if _similar_repeat(signature, _repeat_signature(record)))
    candidate["repeat_key"] = "|".join(sorted(signature)[:10])
    candidate["repeat_count"] = count
    if count >= 3:
        candidate["approval_notice"] = WORKFLOW_REPEAT_NOTICE


def _repeat_signature(record: dict[str, Any]) -> set[str]:
    # Use trigger/behavior only. Scope often contains generic build/fix/review
    # fallback text and would make unrelated candidates look repeated.
    material = " ".join(str(record.get(field) or "") for field in ("trigger", "behavior"))
    words = _meaningful_words(material)
    return {
        word for word in words
        if word in _WORK_MARKERS or word in {"evidence", "verify", "verified", "verification", "actual", "files", "tests", "scope", "report", "findings", "taskboard"}
    }


def _similar_repeat(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    overlap = left & right
    # Require a real workflow/work marker plus shared behavior/evidence words.
    return len(overlap) >= 3 and bool(overlap & set(_WORK_MARKERS))


def _has_secret_value(text: str) -> bool:
    return contains_secret_value(text)


def _source_trigger(source: str, request_text: str = "") -> str:
    combined = f"{request_text}\n{source}".lower()
    work_words = [word for word in _WORK_MARKERS if word in combined]
    if work_words:
        return f"external workflow for {', '.join(dict.fromkeys(work_words[:4]))} work"
    for raw in str(source or "").splitlines():
        clean = raw.strip().strip("#-*•0123456789. )\t")
        if clean and len(clean) >= 8:
            return _one_line(clean, 220)
    return "external workflow source"


def _source_behavior(source: str) -> str:
    selected: list[str] = []
    in_fence = False
    behavior_markers = (
        "check", "inspect", "verify", "read", "grep", "search", "run", "test", "report", "classify",
        "separate", "use", "prefer", "avoid", "never", "always", "must", "should", "do ", "do not",
    )
    for raw in str(source or "").splitlines():
        line = " ".join(raw.strip().strip("-•*#0123456789. )\t").split())
        if not line:
            continue
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or len(line) < 8:
            continue
        lowered = line.lower()
        if any(marker in lowered for marker in behavior_markers):
            selected.append(line)
        if len(selected) >= 4:
            break
    if not selected:
        selected = [line.strip() for line in str(source or "").splitlines() if line.strip()][:3]
    behavior = "; ".join(_one_line(item, 140) for item in selected if item)
    return _one_line(behavior or "Use this workflow as compact guidance after explicit approval", 320)


def _source_scope(source: str, request_text: str = "") -> str:
    text = f"{request_text}\n{source}".lower()
    scopes = []
    for marker, label in (
        ("review", "review"), ("audit", "audit"), ("investigate", "investigate"),
        ("test", "test/verification"), ("debug", "debug"), ("fix", "fix"),
        ("build", "build"), ("docs", "docs"), ("documentation", "documentation"),
    ):
        if marker in text and label not in scopes:
            scopes.append(label)
    if not scopes:
        scopes.append("matching build/fix/review/investigate")
    return "/".join(scopes[:5]) + " turns where the current user request truly matches the approved workflow"


def _has_secret_warning(scan_result: Any) -> bool:
    return any(getattr(finding, "kind", "") == "secret_bearing_text" for finding in getattr(scan_result, "warnings", ()))


def _candidate_path(profile: Any) -> Path:
    from ..state.paths import WORKFLOW_CANDIDATES_PATH, resolve_state_path

    profile_path = getattr(profile, "_path", None)
    return Path(profile_path).parent / "learning" / "workflows" / "candidates.jsonl" if profile_path else Path(resolve_state_path(WORKFLOW_CANDIDATES_PATH))


def _prune_jsonl(path: Path, env_name: str, default: int) -> None:
    keep = int_env(env_name, default)
    if keep <= 0:
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        lines = [line for line in lines if line.strip()]
        if len(lines) <= keep:
            return
        atomic_write_text(path, "\n".join(lines[-keep:]) + "\n", encoding="utf-8")
    except Exception:
        return


def _select_candidate(
    records: list[dict[str, Any]],
    text: str,
    *,
    active_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    requested_id = _candidate_id_from_text(text)
    pending = _pending_candidate_records(records, active_ids or set())
    if requested_id:
        return next((record for record in pending if str(record.get("id") or "") == requested_id), None)
    return sorted(pending, key=lambda record: float(record.get("created_at") or 0.0))[-1] if pending else None


def pending_workflow_candidate_count(
    candidate_records: list[dict[str, Any]],
    active_ids: set[str],
) -> int:
    """Count staged rows not backed by a materialized active skill pack."""
    return len(_pending_candidate_records(candidate_records, active_ids))


def _pending_candidate_records(
    records: list[dict[str, Any]],
    active_ids: set[str],
) -> list[dict[str, Any]]:
    return [
        record for record in records
        if str(record.get("id") or "") not in active_ids
        and str(record.get("status") or "candidate").strip().lower()
        not in {"dismissed", "expired", "retired", "superseded"}
    ]


def _materialized_workflow_ids(profile: Any) -> set[str]:
    """Return active and retired workflow ids already owned by physical packs."""
    from ..skills import (
        materialized_learning_ids,
        retired_learning_candidate_ids,
        skills_root,
    )

    candidate_ids = materialized_learning_ids(profile)
    candidate_ids.update(retired_learning_candidate_ids(skills_root(profile)))
    return {
        candidate_id
        for candidate_id in candidate_ids
        if candidate_id.startswith("workflow-candidate:")
    }


def _candidate_id_from_text(text: str) -> str:
    match = re.search(r"workflow-candidate:(?:graph:)?[a-f0-9]{8,40}", str(text or ""), flags=re.I)
    return match.group(0).lower() if match else ""


def _meaningful_words(text: str) -> set[str]:
    stop = {"the", "and", "for", "that", "this", "with", "when", "then", "from", "next", "time", "always", "never", "ask"}
    return {word for word in re.findall(r"[a-z0-9_+-]{3,}", str(text or "").lower()) if word not in stop}


def _sentence_with_marker(text: str, markers: tuple[str, ...]) -> str:
    for part in re.split(r"(?<=[.!?])\s+|\n+", text):
        clean = " ".join(part.split()).strip(" .")
        if clean and any(marker.lower() in clean.lower() for marker in markers):
            return clean[:220]
    return ""


def _compact_behavior(text: str) -> str:
    clean = " ".join(str(text or "").split()).strip()
    clean = re.sub(r"^(?:mo[,\s]+)?", "", clean, flags=re.I)
    return clean[:260].rsplit(" ", 1)[0] + "..." if len(clean) > 260 else clean


def _one_line(value: Any, limit: int) -> str:
    clean = " ".join(str(value or "").split()).strip()
    return clean[:limit].rsplit(" ", 1)[0] + "..." if len(clean) > limit else clean
