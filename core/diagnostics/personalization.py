"""Canonical read-only personalization-maintenance diagnostic.

The report composes existing profile, learning, recall, session, retention,
system-health, and Git owners. It never writes state or grants apply authority.
The neutral product API replaces any profile-owned trigger or implementation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from typing import Any

from .system_health import check_file_health, check_graph_health
from ..learning.status import build_learning_status
from ..profile import _deduplicate_behavior_learning_text, _deduplicate_profile_learning_text
from ..session.session_closeout import DEFAULT_MAX_CLOSEOUTS, read_latest_closeout_meta
from ..session.sessions import (
    HANDOFF_SNAPSHOT_KEEP,
    SLOT_RETENTION_SECONDS,
    conversation_sessions_dir,
    handoff_sessions_dir,
)
from ..state.paths import PROFILE_PROSE_FILES, mo_home

SCHEMA_VERSION = 1


def _stat(path: Path) -> dict[str, Any]:
    try:
        value = path.stat()
        return {"exists": True, "bytes": value.st_size, "modified": value.st_mtime}
    except OSError:
        return {"exists": False, "bytes": 0, "modified": 0.0}


def _readable_json_object(path: Path) -> bool:
    try:
        return isinstance(json.loads(path.read_text(encoding="utf-8", errors="strict")), dict)
    except (OSError, UnicodeError, ValueError):
        return False


def _older_than(path: Path, *, now: float, seconds: float) -> bool:
    try:
        return now - path.stat().st_mtime > seconds
    except OSError:
        return False


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _profile_status(state_root: Path) -> dict[str, Any]:
    root = state_root / "memory" / "profile"
    documents = {name: _stat(root / name) for name in PROFILE_PROSE_FILES}
    missing = [name for name, status in documents.items() if not status["exists"]]
    unreadable: list[str] = []
    for name, status in documents.items():
        if not status["exists"]:
            continue
        try:
            (root / name).read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            unreadable.append(name)

    try:
        learning_text = (root / "learning.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        learning_text = ""
    try:
        behavior_text = (root / "behavior.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        behavior_text = ""
    _learning_preview, learning_audit = _deduplicate_profile_learning_text(learning_text)
    _behavior_preview, behavior_duplicates = _deduplicate_behavior_learning_text(behavior_text)
    proposals = list((root / "reconcile").glob("*.md")) if (root / "reconcile").is_dir() else []
    learning_duplicates = int(learning_audit.get("duplicates_removed") or 0)
    duplicate_count = learning_duplicates + int(behavior_duplicates)
    return {
        "status": "attention" if missing or unreadable or duplicate_count else "ok",
        "documents": documents,
        "missing": missing,
        "unreadable": unreadable,
        "learning_duplicate_entries": learning_duplicates,
        "behavior_duplicate_rules": int(behavior_duplicates),
        "reconciliation_proposals": len(proposals),
        "coverage": "structural and mechanical; semantic freshness is not inferred from file age",
        "semantic_changes": "proposal and operator decision only",
    }


def _learning_status(state_root: Path) -> dict[str, Any]:
    profile = SimpleNamespace(_path=str(state_root / "memory" / "mo.db"))
    status = build_learning_status(profile).as_dict()
    product_path = state_root / "memory" / "work" / "product-intent" / "candidates.jsonl"
    try:
        product_intents = sum(
            1 for line in product_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and str((json.loads(line) or {}).get("status") or "candidate") == "candidate"
        )
    except (OSError, json.JSONDecodeError):
        product_intents = 0
    pending = int(status["pending_suggestions"]) + int(status["workflow_candidates"])
    expected_skills = int(status["confirmed_suggestions"]) + int(status["promoted_workflows"])
    generated = int(status["generated_learning_skills"])
    inspection = str(status["memory_inspection"])
    aligned = generated == expected_skills
    return {
        "status": "unavailable" if inspection != "ready" else ("attention" if pending or not aligned else "ok"),
        "accepted_profile_events": int(status["profile_learning_entries"]),
        "active_behavior_rules": int(status["behavior_rules"]),
        "durable_facts": int(status["profile_facts"]),
        "operator_terms": int(status["operator_terms"]),
        "confirmed_authorities": int(status["confirmed_suggestions"]),
        "auto_promoted_authorities": int(status["auto_promoted_suggestions"]),
        "pending_review": pending,
        "pending_product_intents": product_intents,
        "review_pressure": str(status["review_pressure"]),
        "suggestion_ledger": dict(status["suggestions"]),
        "generated_learning_skills": generated,
        "authority_alignment": aligned,
        "memory_turns": int(status["memory_turns"]),
        "vector_count": int(status["vector_count"]),
        "recall_mode": str(status["recall_mode"]),
        "memory_inspection": inspection,
    }


def _session_status(state_root: Path, *, now: float) -> dict[str, Any]:
    root = state_root / "memory" / "sessions"
    conversations = list(conversation_sessions_dir(root).glob("*.json"))
    handoffs = list(handoff_sessions_dir(root).glob("*.json"))
    closeout_root = root / "closeouts"
    closeouts = list(closeout_root.glob("*.md")) if closeout_root.is_dir() else []
    sidecars = list(closeout_root.glob("*.json")) if closeout_root.is_dir() else []

    unreadable = sum(not _readable_json_object(path) for path in [*conversations, *handoffs, *sidecars])
    stale_slot_paths = {
        path for path in conversations
        if path.name.startswith("main-") and _older_than(path, now=now, seconds=SLOT_RETENTION_SECONDS)
    }
    stale_handoff_paths = {
        path for path in handoffs
        if _older_than(path, now=now, seconds=SLOT_RETENTION_SECONDS)
    }
    groups: dict[str, list[Path]] = {}
    for path in handoffs:
        if "-pre-handoff-" in path.name:
            groups.setdefault(path.name.split("-pre-handoff-", 1)[0], []).append(path)
    handoff_over_cap_paths: set[Path] = set()
    for paths in groups.values():
        ordered = sorted(paths, key=lambda path: (_mtime(path), path.name), reverse=True)
        handoff_over_cap_paths.update(ordered[HANDOFF_SNAPSHOT_KEEP:])
    ordered_closeouts = sorted(closeouts, key=lambda path: (_mtime(path), path.name), reverse=True)
    closeout_over_cap_paths = set(ordered_closeouts[DEFAULT_MAX_CLOSEOUTS:])
    missing_sidecars = sum(not path.with_suffix(".json").is_file() for path in closeouts)
    orphan_sidecar_paths = {path for path in sidecars if not path.with_suffix(".md").is_file()}
    retention_paths = (
        stale_slot_paths
        | stale_handoff_paths
        | handoff_over_cap_paths
        | closeout_over_cap_paths
    )
    cleanup_paths = retention_paths | orphan_sidecar_paths

    latest = read_latest_closeout_meta(closeout_root, max_age_hours=24 * 365 * 100)
    latest_summary = {
        "present": bool(latest),
        "status": str(latest.get("status") or "unknown") if latest else "missing",
        "age_hours": float(latest.get("age_hours") or 0.0) if latest else 0.0,
        "unresolved_count": int(latest.get("unresolved_count") or 0) if latest else 0,
        "continuity_warning_count": int(latest.get("continuity_warning_count") or 0) if latest else 0,
    }
    attention = unreadable or cleanup_paths or missing_sidecars or latest_summary["status"] == "unresolved"
    return {
        "status": "attention" if attention else "ok",
        "conversation_snapshots": len(conversations),
        "handoff_snapshots": len(handoffs),
        "closeouts": len(closeouts),
        "unreadable_json": int(unreadable),
        "stale_automatic_slots": len(stale_slot_paths),
        "stale_handoffs": len(stale_handoff_paths),
        "handoff_over_retention_cap": len(handoff_over_cap_paths),
        "closeout_over_retention_cap": len(closeout_over_cap_paths),
        "missing_closeout_sidecars": int(missing_sidecars),
        "orphan_closeout_sidecars": len(orphan_sidecar_paths),
        "retention_candidates": len(retention_paths),
        "cleanup_candidates": len(cleanup_paths),
        "latest_closeout": latest_summary,
        "extraction_owner": "accepted-turn automatic routing plus incremental retained-session reconciliation; product intent remains reviewable until source-backed consolidation",
        "named_sessions": "never automatic-retention candidates unless they use the reserved main-* namespace",
    }


def _recurrence_status(project_root: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "log", "--name-only", "--pretty=format:", "-n", "40"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return {"status": "unavailable", "window_commits": 40, "repeated": [], "health_effect": "signal only"}
    if result.returncode != 0:
        return {"status": "unavailable", "window_commits": 40, "repeated": [], "health_effect": "signal only"}
    counts = Counter(line.strip() for line in result.stdout.splitlines() if line.strip())
    rows = [{"count": count, "path": path} for path, count in counts.most_common(20) if count >= 2]
    return {
        "status": "detected" if rows else "clean",
        "window_commits": 40,
        "repeated": rows,
        "health_effect": "signal only; repeated edits do not prove a defect or cause",
    }


def build_personalization_report(
    *,
    state_root: str | Path | None = None,
    project_root: str | Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Build MO's official personalization evidence report without writing state."""
    state = Path(state_root or mo_home()).expanduser().resolve(strict=False)
    project = Path(project_root or Path.cwd()).expanduser().resolve(strict=False)
    current = float(time.time() if now is None else now)
    files = check_file_health(str(state))
    graph_status = check_graph_health(str(project))
    profile = _profile_status(state)
    learning = _learning_status(state)
    sessions = _session_status(state, now=current)
    recurrence = _recurrence_status(project)
    over_cap = sorted(name for name, item in files.items() if item.get("status") == "over_cap")

    issues = [
        name for name, section in (("profile", profile), ("learning", learning), ("sessions", sessions))
        if section["status"] == "attention"
    ]
    if learning["pending_product_intents"]:
        issues.append("product_intent")
    coverage_gaps = [
        name for name, section in (("profile", profile), ("learning", learning), ("sessions", sessions))
        if section["status"] == "unavailable"
    ]
    if over_cap:
        issues.append("file_retention")
    if not graph_status.get("exists") or graph_status.get("stale"):
        issues.append("graph")
    if recurrence["status"] == "unavailable":
        coverage_gaps.append("recurrence")
    signals = ["recurrence"] if recurrence["status"] == "detected" else []

    deterministic: list[str] = []
    operator_decisions: list[str] = []
    evidence_review: list[str] = []
    if profile["missing"]:
        deterministic.append(f"restore {len(profile['missing'])} missing canonical profile document(s)")
    if profile["unreadable"]:
        evidence_review.append(f"inspect {len(profile['unreadable'])} unreadable profile document(s)")
    duplicate_entries = int(profile["learning_duplicate_entries"]) + int(profile["behavior_duplicate_rules"])
    if duplicate_entries:
        deterministic.append(f"consolidate {duplicate_entries} duplicate accepted-learning entr{'y' if duplicate_entries == 1 else 'ies'}")
    if not learning["authority_alignment"]:
        deterministic.append("reconcile confirmed learning with generated skill authorities")
    if learning["pending_review"]:
        operator_decisions.append(f"review {learning['pending_review']} pending learning item(s)")
    product_intents = int(learning["pending_product_intents"])
    if product_intents:
        label = "candidate" if product_intents == 1 else "candidates"
        evidence_review.append(
            f"inspect {product_intents} staged product-intent {label} "
            "before source-backed project-history consolidation"
        )
    if learning["memory_inspection"] != "ready":
        evidence_review.append("restore or inspect episodic-memory availability")
    if (
        sessions["unreadable_json"]
        or sessions["missing_closeout_sidecars"]
        or sessions["orphan_closeout_sidecars"]
    ):
        evidence_review.append("inspect unreadable, incomplete, or orphaned session metadata")
    if sessions["retention_candidates"]:
        deterministic.append(
            f"apply established retention to {sessions['retention_candidates']} automatic session/closeout candidate(s)"
        )
    if sessions["latest_closeout"]["status"] == "unresolved":
        evidence_review.append("review the latest unresolved session closeout")
    if over_cap:
        deterministic.append("apply established retention to: " + ", ".join(over_cap))
    if not graph_status.get("exists"):
        deterministic.append("build the optional structural graph if project diagnostics need it")
    elif graph_status.get("stale"):
        deterministic.append("refresh the stale structural graph")
    if recurrence["status"] == "detected":
        evidence_review.append("inspect repeated project paths before inferring a cause or behavioral driver")

    if issues:
        verdict = "attention"
        detail = ", ".join(issues)
        suffix = f"; coverage unavailable: {', '.join(coverage_gaps)}" if coverage_gaps else ""
        if signals:
            suffix += "; recurrence signal detected"
        summary = f"Personalization diagnostic attention — review {detail}{suffix}; no changes were made."
    elif coverage_gaps:
        verdict = "partial"
        summary = (
            "Personalization diagnostic partial — unavailable: "
            f"{', '.join(coverage_gaps)}; no changes were made."
        )
    elif signals:
        verdict = "review"
        summary = "Personalization diagnostic review — recurrence signal detected; no changes were made."
    else:
        verdict = "clean"
        summary = "Personalization diagnostic clean — no maintenance is required; no changes were made."

    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "personalization",
        "verdict": verdict,
        "summary": summary,
        "issues": issues,
        "signals": signals,
        "coverage_gaps": coverage_gaps,
        "next_actions": {
            "deterministic_maintenance": deterministic,
            "operator_decisions": operator_decisions,
            "evidence_review": evidence_review,
        },
        "profile": profile,
        "learning": learning,
        "sessions": sessions,
        "system": {
            "over_cap_files": over_cap,
            "graph_available": bool(graph_status.get("exists")),
            "graph_stale": bool(graph_status.get("stale")),
        },
        "recurrence": recurrence,
        "changes": {
            "writes_performed": False,
            "apply_requires_separate_request": True,
            "execution_owner": "existing profile, learning, memory, session, project-history, and cleanup owners",
            "operator_decision_required_for": [
                "pending learning review",
                "semantic profile changes",
                "named-session deletion outside automatic retention",
            ],
        },
        "not_verified": [
            "semantic freshness or contradiction-free profile prose",
            "whether stored reconciliation proposals remain current or pending",
            "whether staged product-intent candidates remain current and source-backed",
            "causes behind repeated project-file changes",
            "whether reported maintenance should be applied",
        ],
    }


def render_personalization_report(report: dict[str, Any]) -> str:
    """Render a compact human report; a fully clean run stays one line."""
    if report.get("verdict") == "clean":
        return str(report.get("summary") or "Personalization diagnostic clean.")
    profile = report["profile"]
    learning = report["learning"]
    sessions = report["sessions"]
    system = report["system"]
    recurrence = report["recurrence"]
    present = sum(bool(item.get("exists")) for item in profile["documents"].values())
    graph = "stale" if system["graph_stale"] else ("ready" if system["graph_available"] else "missing")
    lines = [
        str(report["summary"]),
        f"  Profile: {profile['status']} · {present}/{len(PROFILE_PROSE_FILES)} documents · semantic freshness not inferred",
        f"  Learning: {learning['status']} · review {learning['pending_review']} · authorities {'aligned' if learning['authority_alignment'] else 'misaligned'}",
        f"  Memory: {learning['memory_inspection']} · {learning['memory_turns']} turns · {learning['recall_mode']}",
        f"  Sessions: {sessions['status']} · cleanup candidates {sessions['cleanup_candidates']} · latest closeout {sessions['latest_closeout']['status']}",
        f"  System: file caps {'ok' if not system['over_cap_files'] else 'attention'} · graph {graph}",
        f"  Recurrence: {recurrence['status']} · {len(recurrence['repeated'])} repeated paths / {recurrence['window_commits']} commits · signal only",
    ]
    if learning["pending_product_intents"]:
        lines.insert(
            3,
            f"  Product intent: {learning['pending_product_intents']} staged · source-backed project-history review",
        )
    actions = report.get("next_actions") or {}
    for label, key in (
        ("Maintenance", "deterministic_maintenance"),
        ("Operator decision", "operator_decisions"),
        ("Evidence review", "evidence_review"),
    ):
        values = list(actions.get(key) or [])
        if values:
            lines.append(f"  {label}: " + "; ".join(str(item) for item in values))
    owner_controls = ["pending learning", "semantic profile", "named-session deletion"]
    if learning["pending_product_intents"]:
        owner_controls.insert(1, "product-intent consolidation")
    lines.append(
        "  Changes: none · apply requires a separate request; "
        + ", ".join(owner_controls)
        + " decisions remain owner-controlled"
    )
    return "\n".join(lines)


def render_personalization_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render MO's read-only personalization-maintenance diagnostic.")
    parser.add_argument("--state-root")
    parser.add_argument("--project-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_personalization_report(state_root=args.state_root, project_root=args.project_root)
    print(render_personalization_json(report) if args.json else render_personalization_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
