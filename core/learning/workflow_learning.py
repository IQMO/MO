"""Compatibility staging for MO local skill candidates.

Candidates are local/inert. Only explicit operator promotion creates compact,
relevance-gated local skill guidance for later turns. Taskboard truth still lives
with Gateway/Agent evidence, never here.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
import traceback

from ..atomic_write import atomic_write_text
from ..env_utils import int_env
from ..text_safety import contains_secret_value
from ..threat_scan import scan_text

_SIGNAL_MARKERS = (
    "next time", "from now on", "always", "never", "when i ask", "when I ask",
    "i prefer", "I prefer", "remember this", "workflow", "pattern", "workflow learning",
    "skill miner", "skill minor", "self-improvement", "self improvement", "improve yourself",
)
_WORK_MARKERS = (
    "build", "fix", "review", "investigate", "audit", "debug", "test", "verify",
    "taskboard", "task board", "evidence", "scope", "profile", "report", "gateway",
    "docs", "documentation", "style", "process", "method", "workflow", "skill",
)
_PROMOTION_MARKERS = (
    "promote workflow candidate", "approve workflow candidate", "promote workflow learning",
    "approve workflow learning", "approve workflow", "promote workflow", "activate workflow",
    "activate workflow candidate", "use workflow candidate", "wire workflow candidate",
    "promote skill candidate", "approve skill candidate", "activate skill candidate",
    "use skill candidate", "approve skill", "promote skill",
)
WORKFLOW_CANDIDATE_NOTICE = "Skill staged: approve latest"
WORKFLOW_REPEAT_NOTICE = "Skill repeated 3x: approve latest?"

def extract_workflow_candidate(user_text: str, assistant_text: str = "") -> dict[str, Any]:
    """Return an inert local skill candidate from explicit high-signal feedback only."""
    text = str(user_text or "").strip()
    low = text.lower()
    if not text or not any(marker.lower() in low for marker in _SIGNAL_MARKERS):
        return {}
    if not any(marker in low for marker in _WORK_MARKERS):
        return {}
    threat = scan_text(text, surface="workflow candidate")
    if threat.blocked or _has_secret_warning(threat):
        return {}
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:12]
    candidate = {
        "id": f"workflow-candidate:{digest}",
        "status": "candidate",
        "trigger": _sentence_with_marker(text, _WORK_MARKERS) or "explicit operator workflow correction",
        "behavior": _compact_behavior(text),
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
        added = _append_candidate_record(path, candidate)
    except OSError as exc:
        return {"recorded": False, "reason": f"write failed: {type(exc).__name__}", "candidate": candidate}
    repeat_count = int(candidate.get("repeat_count") or 1)
    if added and repeat_count >= 3:
        notice = f"Skill repeated {repeat_count}x: approve skill candidate {candidate.get('id', '')}"
    else:
        notice = f"Skill staged: approve skill candidate {candidate.get('id', '')}"
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
        added = _append_candidate_record(path, candidate)
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
    text = str(user_text or "").strip()
    low = text.lower()
    if not text or not any(marker in low for marker in _PROMOTION_MARKERS):
        return {"promoted": False, "reason": "no explicit skill promotion request"}
    request_scan = scan_text(text, surface="workflow promotion request")
    if request_scan.blocked or _has_secret_warning(request_scan):
        return {"promoted": False, "blocked": True, "reason": request_scan.reason() or "secret-bearing approval text"}
    path = _candidate_path(profile)
    candidate = _select_candidate(_read_jsonl(path), text)
    if not candidate:
        return {"promoted": False, "reason": "no matching skill candidate"}
    candidate_text = "\n".join(str(candidate.get(key) or "") for key in ("trigger", "behavior", "scope", "anti_pattern", "source_text"))
    candidate_scan = scan_text(candidate_text, surface="workflow candidate promotion")
    if candidate_scan.blocked or _has_secret_warning(candidate_scan):
        reason = candidate_scan.reason() if candidate_scan.blocked else "secret-bearing candidate text"
        return {"promoted": False, "blocked": True, "reason": reason, "id": candidate.get("id", "")}
    promoted = dict(candidate)
    promoted.update({
        "status": "promoted",
        "approved_at": time.time(),
        "approval_evidence": "explicit operator approval",
        "approval_excerpt": text[:240],
        "assistant_excerpt_at_approval": str(assistant_text or "")[:240],
    })
    try:
        from ..skills import write_skill_pack_from_candidate

        skill_path = write_skill_pack_from_candidate(promoted, profile=profile)
        promoted["skill_path"] = str(skill_path)
        promoted["skill_status"] = "active"
        _rewrite_candidate_status(path, str(candidate.get("id") or ""), promoted)
        _append_unique_record(_promoted_path(profile), promoted)
        _append_profile_learning(profile, promoted)
        return {"promoted": True, "id": promoted.get("id", ""), "path": str(_promoted_path(profile)), "skill_path": str(skill_path)}
    except Exception as exc:
        return {"promoted": False, "reason": f"write failed: {type(exc).__name__}", "id": candidate.get("id", "")}


def build_workflow_learning_context(profile: Any, user_input: str, *, max_chars: int = 900) -> str:
    """Return compact approved local skill guidance relevant to the current turn."""
    user_text = str(user_input or "").strip()
    if not user_text or not any(marker in user_text.lower() for marker in _WORK_MARKERS):
        return ""
    selected = _relevant_promoted(_read_jsonl(_promoted_path(profile)), user_text)
    if not selected:
        return ""
    lines = [
        "### MO Internal Local Skills - approved, relevance-gated",
        "Apply only when the trigger truly fits this turn. Current user scope, sandbox, tools, and Gateway/taskboard evidence still win.",
    ]
    for record in selected[:3]:
        lines.append(f"- When: {_one_line(record.get('trigger', ''), 150)}")
        lines.append(f"  Do: {_one_line(record.get('behavior', ''), 220)}")
        anti = _one_line(record.get("anti_pattern", ""), 160)
        if anti:
            lines.append(f"  Avoid: {anti}")
    text = "\n".join(lines).strip()
    return text if len(text) <= max_chars else text[:max_chars].rsplit("\n", 1)[0] + "\n[local skill context truncated]"


def load_promoted_workflows(profile: Any) -> list[dict[str, Any]]:
    """Load approved workflows for tests/internal maintenance."""
    return _read_jsonl(_promoted_path(profile))


def stage_structural_graph_candidates(profile: Any, *, root: str | Path | None = None, max_items: int = 8) -> dict[str, Any]:
    """Stage inert local skill candidates discovered from structural graph data.

    These are not promoted automatically. They use the same candidate schema as
    text-derived skill learning, so the operator must explicitly approve them
    before they influence future turns.
    """
    try:
        from ..graph.structural_graph import structural_patterns
        patterns = structural_patterns(root, max_items=max_items)
    except Exception as exc:
        return {"staged": False, "reason": f"graph unavailable: {type(exc).__name__}"}
    if not patterns:
        return {"staged": False, "reason": "no structural patterns"}
    path = _candidate_path(profile)
    added = 0
    duplicates = 0
    ids: list[str] = []
    for pattern in patterns[:max_items]:
        material = json.dumps(pattern, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha1(material.encode("utf-8", errors="ignore")).hexdigest()[:12]
        candidate = {
            "id": f"workflow-candidate:graph:{digest}",
            "status": "candidate",
            "trigger": _one_line(pattern.get("trigger", "graph structural pattern"), 180),
            "behavior": _one_line(pattern.get("behavior", "verify graph-indicated structure before acting"), 260),
            "evidence": _one_line(pattern.get("evidence", "structural graph analysis"), 200),
            "scope": _one_line(pattern.get("scope", "matching build/fix/review/investigate turns"), 200),
            "anti_pattern": "do not treat graph structure as proof; re-read files and run verification before claims",
            "promotion": "requires explicit operator approval before active use",
            "source_kind": "structural-graph",
            "structural_kind": _one_line(pattern.get("kind", "structural"), 80),
            "created_at": time.time(),
        }
        try:
            if _append_candidate_record(path, candidate):
                added += 1
            else:
                duplicates += 1
            ids.append(candidate["id"])
        except OSError:
            return {"staged": False, "reason": "write failed", "added": added, "ids": ids}
    return {"staged": bool(added), "added": added, "duplicates": duplicates, "ids": ids, "path": str(path)}


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

    Candidates are written with ``status == "candidate"`` and ``created_at``;
    promotion rewrites them to ``status == "promoted"``. The expiry keeps every
    promoted record and any candidate newer than the cutoff. (Previously this
    compared against ``"staged"``, a status no record ever has, so the TTL never
    expired anything.)
    """
    ttl = int(ttl_days if ttl_days is not None else int_env("MO_WORKFLOW_CANDIDATE_TTL_DAYS", 7))
    if ttl <= 0 or not path.exists():
        return 0
    cutoff = time.time() - ttl * 86400
    records = _read_jsonl(path)
    kept = [
        r for r in records
        if str(r.get("status") or "candidate") != "candidate" or float(r.get("created_at") or cutoff) >= cutoff
    ]
    dropped = len(records) - len(kept)
    if dropped:
        atomic_write_text(path, "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in kept) + ("\n" if kept else ""), encoding="utf-8")
    return dropped


def _append_candidate_record(path: Path, candidate: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    _expire_stale_candidates(path)
    records = _read_jsonl(path)
    if any(record.get("id") == candidate["id"] for record in records):
        return False
    norm = _normalize_candidate_text(candidate)
    if norm and any(_normalize_candidate_text(record) == norm for record in records):
        return False  # same trigger/behavior already staged under another id
    _annotate_repeat(candidate, records)
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
    from ..path_defaults import resolve_state_path

    profile_path = getattr(profile, "_path", None)
    return Path(profile_path).parent / "workflow_candidates.jsonl" if profile_path else Path(resolve_state_path("memory/workflow_candidates.jsonl"))


def _promoted_path(profile: Any) -> Path:
    from ..path_defaults import resolve_state_path

    profile_path = getattr(profile, "_path", None)
    return Path(profile_path).parent / "workflow_promoted.jsonl" if profile_path else Path(resolve_state_path("memory/workflow_promoted.jsonl"))


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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        if not path.exists():
            return []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    except OSError:
        return []
    return records


def _append_unique_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record_id = str(record.get("id") or "")
    if record_id and any(str(item.get("id") or "") == record_id for item in _read_jsonl(path)):
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    _prune_jsonl(path, "MO_WORKFLOW_PROMOTED_MAX", 50)


def _rewrite_candidate_status(path: Path, candidate_id: str, promoted: dict[str, Any]) -> None:
    records = _read_jsonl(path)
    if not records:
        return
    out = [promoted if str(record.get("id") or "") == candidate_id else record for record in records]
    atomic_write_text(path, "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in out), encoding="utf-8")
    _prune_jsonl(path, "MO_WORKFLOW_CANDIDATE_MAX", 100)


def _select_candidate(records: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
    requested_id = _candidate_id_from_text(text)
    pending = [record for record in records if str(record.get("status") or "candidate") != "promoted"]
    if requested_id:
        return next((record for record in pending if str(record.get("id") or "") == requested_id), None)
    return sorted(pending, key=lambda record: float(record.get("created_at") or 0.0))[-1] if pending else None


def _candidate_id_from_text(text: str) -> str:
    match = re.search(r"workflow-candidate:(?:graph:)?[a-f0-9]{8,40}", str(text or ""), flags=re.I)
    return match.group(0).lower() if match else ""


def _relevant_promoted(records: list[dict[str, Any]], user_text: str) -> list[dict[str, Any]]:
    user_words = _meaningful_words(user_text)
    scored: list[tuple[int, float, dict[str, Any]]] = []
    for record in records:
        if str(record.get("status") or "") != "promoted":
            continue
        words = _meaningful_words(f"{record.get('trigger', '')} {record.get('behavior', '')} {record.get('scope', '')}")
        overlap = len(user_words & words)
        marker_overlap = len({word for word in user_words & words if word in _WORK_MARKERS})
        if overlap >= 2 or marker_overlap >= 1:
            scored.append((overlap + marker_overlap, float(record.get("approved_at") or record.get("created_at") or 0.0), record))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [record for _score, _ts, record in scored]


def _meaningful_words(text: str) -> set[str]:
    stop = {"the", "and", "for", "that", "this", "with", "when", "then", "from", "next", "time", "always", "never", "ask"}
    return {word for word in re.findall(r"[a-z0-9_+-]{3,}", str(text or "").lower()) if word not in stop}


def _append_profile_learning(profile: Any, promoted: dict[str, Any]) -> None:
    if not profile or not hasattr(profile, "append_profile_learning"):
        return
    source_id = str(promoted.get("id") or "workflow").replace(":", "-")
    trigger = _one_line(promoted.get("trigger", ""), 140)
    behavior = _one_line(promoted.get("behavior", ""), 180)
    try:
        profile.append_profile_learning(
            "workflow-promoted:" + source_id,
            {
                "evolution": [f"Approved local skill: when {trigger}, {behavior}"],
                "core_traits": ["Apply approved local skills only when relevant; current scope, evidence, and taskboard truth still win"],
            },
        )
    except Exception:
        traceback.print_exc()


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
