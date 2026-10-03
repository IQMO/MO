"""PRT settings, review ledger, and status reporting.

Review execution is owned by explicit ``/prt`` requests and the GitHub workflow.
This module records their verdicts and exposes bounded local history; committed
local reviews may delegate confirmed corrections to the normal Agent owner.
"""
from __future__ import annotations

import json
import re
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .prt_report import bounded_review_value, review_is_incomplete
from ..runtime.subprocess_flags import apply_windows_hidden_process_flags


@dataclass(frozen=True)
class MaintainerSettings:
    enabled: bool = True
    target: float = 4.5
    github: bool = False


def maintainer_settings(agent: Any) -> MaintainerSettings:
    """Resolve the canonical PRT enable, score, and GitHub-delivery settings."""
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    prt = cfg.get("prt", {}) if isinstance(cfg.get("prt", {}), dict) else {}

    def _flt(key: str, default: float) -> float:
        try:
            return float(prt.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    return MaintainerSettings(
        enabled=bool(prt.get("enabled", True)),
        target=_flt("score_target", 4.5),
        github=bool(prt.get("maintainer_github", False)),
    )


# ── Private PRT review ledger ────────────────────────────────────────────────

def _ledger_path(agent: Any) -> Path:
    from core.state.paths import resolve_state_path
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    return Path(resolve_state_path("memory/work/reviews/maintainer.jsonl", cfg))


_LEDGER_LOCK = threading.Lock()


def _record(agent: Any, entry: dict) -> None:
    """Append one PRT review verdict to the private ledger (bounded)."""
    sha = str((entry or {}).get("sha") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{7,64}", sha):
        return
    try:
        path = _ledger_path(agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        from core.runtime.lock import file_byte_lock
        with file_byte_lock(path.with_suffix(".lock"), _LEDGER_LOCK):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            from core.utils.jsonl_utils import prune_jsonl_log
            prune_jsonl_log(path, env_max_bytes_var="MO_MAINTAINER_MAX_BYTES", env_keep_lines_var="MO_MAINTAINER_KEEP_LINES")
    except Exception:
        pass


def recent_maintainer_entries(agent: Any, limit: int = 5) -> list[dict]:
    """Return the most recent PRT review verdicts for status reporting."""
    try:
        path = _ledger_path(agent)
        if not path.exists():
            return []
        lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        out: list[dict] = []
        for ln in lines[-max(1, int(limit or 5)):]:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out
    except Exception:
        return []


def _current_verdict(value: Any) -> str:
    """Project retired ledger verdicts onto the current target-aware model."""
    verdict = str(value or "")
    if verdict == "fixed":
        return "aligned"
    if verdict in {"stuck", "maxed"}:
        return "flagged"
    return verdict


def current_maintainer_entries(agent: Any, limit: int = 30) -> list[dict]:
    """Return deduplicated ledger rows in the current target-aware status model."""
    raw_entries = recent_maintainer_entries(agent, limit=max(30, int(limit or 30) * 5))
    seen: set[tuple[str, str, str, str, str, str]] = set()
    entries: list[dict] = []
    for entry in reversed(raw_entries):
        sha = str(entry.get("sha") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9]{7,64}", sha):
            continue
        if entry.get("accepted") or str(entry.get("verdict") or "") == "accepted":
            continue
        target_kind = str(entry.get("target_kind") or "commit")
        target_identity = (
            str(entry.get("evidence_digest") or entry.get("diff_ref") or "")
            if target_kind != "commit"
            else ""
        )
        key = (
            str(entry.get("surface") or "local"),
            sha,
            str(entry.get("pr") or ""),
            target_kind,
            target_identity,
            str(entry.get("source_digest") or ""),
        )
        if key in seen:
            continue
        verdict = _current_verdict(entry.get("verdict"))
        if verdict not in {"aligned", "flagged", "incomplete", "deferred"}:
            continue
        seen.add(key)
        current = dict(entry)
        current["verdict"] = verdict
        entries.append(current)
        if len(entries) >= max(1, int(limit or 30)):
            break
    entries.reverse()
    return entries


def maintainer_report(agent: Any, limit: int = 30) -> str:
    """Render a compact view of recent PRT review history."""
    entries = current_maintainer_entries(agent, limit=limit)
    if not entries:
        return "PRT review history: no activity recorded."
    from collections import Counter
    verdicts = Counter(str(e.get("verdict") or "?") for e in entries)
    target_kinds = Counter(str(e.get("target_kind") or "commit") for e in entries)
    scored = [float(e.get("score") or 0.0) for e in entries if e.get("score") is not None]
    avg = round(sum(scored) / len(scored), 2) if scored else 0.0
    lines = [
        f"PRT review history — last {len(entries)} review(s):",
        f"  aligned {verdicts.get('aligned', 0)} · flagged {verdicts.get('flagged', 0)} · "
        f"incomplete {verdicts.get('incomplete', 0)} · deferred {verdicts.get('deferred', 0)}",
        f"  targets: commit {target_kinds.get('commit', 0)} · range {target_kinds.get('range', 0)} · "
        f"worktree {target_kinds.get('path', 0)}",
        f"  average score: {avg}/5.0",
    ]
    latest = entries[-1]
    latest_kind = str(latest.get("target_kind") or "commit")
    latest_label = "worktree" if latest_kind == "path" else latest_kind
    if latest.get("correction_of"):
        latest_label = "corrected candidate"
    latest_ref = str(latest.get("diff_ref") or "HEAD")
    lines.append(
        f"  latest: {str(latest.get('sha', ''))[:7]} "
        f"{latest_label} {latest_ref} · {latest.get('verdict')} ({latest.get('surface', 'local')})"
    )
    execution = latest.get("execution") or {}
    if execution:
        from interface.activity import duration_text
        lines[-1] += f" · {duration_text(execution.get('duration_seconds', 0))}"
        if execution.get("instance_id"):
            lines[-1] += f" · MO {execution['instance_id']}"
    unresolved = int(latest.get("unresolved_count") or 0)
    if unresolved:
        lines.append(f"  latest unresolved: {unresolved}")
    reasons = [str(item).strip() for item in (latest.get("reasons") or []) if str(item).strip()]
    if reasons:
        lines.append("  latest reason: " + reasons[0])
    if latest.get("correction_outcome"):
        lines.append("  " + str(latest["correction_outcome"]))
    return "\n".join(lines)


def record_prt_review(agent: Any, report: Any, *, surface: str = "local") -> dict | None:
    """Record a manual/full PRT report in the history used by `/prt report`.

    Rows are review evidence. Committed-target correction is delegated by the
    slash-command lifecycle, never executed by this ledger owner.
    """
    if report is None:
        return None
    settings = maintainer_settings(agent)
    try:
        score = float(getattr(report, "score", 0.0) or 0.0)
    except Exception:
        score = 0.0
    try:
        target = float(getattr(report, "score_target", settings.target) or settings.target)
    except Exception:
        target = settings.target
    try:
        raw_unresolved = getattr(report, "unresolved_count", None)
        unresolved_count = (
            int(raw_unresolved)
            if raw_unresolved is not None
            else sum(
                1
                for finding in (getattr(report, "findings", None) or [])
                if not bool(getattr(finding, "resolved", False))
            )
        )
    except Exception:
        unresolved_count = 0
    incomplete = review_is_incomplete(report)
    if incomplete:
        verdict = "incomplete"
    elif bool(getattr(report, "is_target_met", score >= target and unresolved_count == 0)):
        verdict = "aligned"
    else:
        verdict = "flagged"
    ref = str(getattr(report, "diff_ref", "") or "HEAD")
    impact = getattr(report, "structural_impact", {})
    review_target = impact.get("review_target", {}) if isinstance(impact, dict) else {}
    if not isinstance(review_target, dict):
        review_target = {}
    reviewed_oid = str(review_target.get("reviewed_oid") or "").strip()
    sha = (
        reviewed_oid
        if re.fullmatch(r"[A-Fa-f0-9]{7,64}", reviewed_oid)
        else _resolve_sha(agent, ref if ref else "HEAD")
    )
    if not sha:
        return None
    entry_score = None if incomplete else score
    entry = _entry(sha, surface, entry_score, verdict, settings)
    entry["target"] = round(target, 1)
    entry["diff_ref"] = ref
    if isinstance(impact, dict) and impact.get("correction_outcome"):
        entry["correction_outcome"] = bounded_review_value(impact["correction_outcome"], max_chars=2000)
    if review_target.get("correction_of"):
        entry["correction_of"] = str(review_target["correction_of"])
    target_kind = str(review_target.get("kind") or "commit").strip().lower()
    if target_kind in {"commit", "range", "path"}:
        entry["target_kind"] = target_kind
    evidence_digest = str(review_target.get("evidence_digest") or "").strip().lower()
    if re.fullmatch(r"[a-f0-9]{64}", evidence_digest):
        entry["evidence_digest"] = evidence_digest
    snapshot = impact.get("snapshot", {}) if isinstance(impact, dict) else {}
    source_digest = str(snapshot.get("source_digest") or "") if isinstance(snapshot, dict) else ""
    if re.fullmatch(r"[a-f0-9]{64}", source_digest):
        entry["source_digest"] = source_digest
    execution = impact.get("execution", {}) if isinstance(impact, dict) else {}
    if execution:
        entry["execution"] = dict(execution)
    try:
        entry["files_changed"] = int(getattr(report, "files_changed", 0) or 0)
    except Exception:
        pass
    reasons = _finding_summaries(report)
    if reasons:
        entry["reasons"] = reasons
    entry["unresolved_count"] = unresolved_count
    _record(agent, entry)
    _emit(agent, entry)
    return entry


def _emit(agent: Any, entry: dict) -> None:
    try:
        from core.runtime.backend_monitor import get_monitor
        monitor = get_monitor()
        if monitor:
            monitor.emit("prt_maintainer", entry)
    except Exception:
        pass
    try:
        from core.runtime.heartbeat import record_heartbeat
        record_heartbeat(agent, gateway=getattr(agent, "gateway", None), surface="prt-maintainer", event="reviewed")
    except Exception:
        pass


def _resolve_sha(agent: Any, ref: str) -> str:
    try:
        import os
        import subprocess
        cwd = str(getattr(agent, "project_cwd", "") or getattr(agent, "workspace", "") or os.getcwd())
        run_kwargs = {
            "text": True, "encoding": "utf-8", "errors": "replace",
            "stderr": subprocess.DEVNULL, "cwd": cwd,
        }
        apply_windows_hidden_process_flags(run_kwargs)
        return subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{str(ref or 'HEAD')}^{{commit}}"],
            **run_kwargs,
        ).strip()
    except Exception:
        return ""


def _entry(
    sha: str,
    surface: str,
    score: float | None,
    verdict: str,
    settings: MaintainerSettings,
) -> dict:
    return {
        "sha": str(sha or "")[:12],
        "surface": str(surface or "local"),
        "score": round(float(score), 1) if score is not None else None,
        "target": settings.target,
        "verdict": verdict,
        "at": time.time(),
    }


def _finding_summaries(report: Any, *, limit: int = 50) -> list[str]:
    """Compact, redacted finding summaries for maintainer notices/ledger lines.

    The limit is high enough to capture all findings so the durable ledger's
    ``reasons`` list matches ``unresolved_count``.  Individual summaries are
    already capped at 180 chars and the ledger file is pruned by
    :func:`prune_jsonl_log`, so this does not grow the ledger unboundedly.
    """
    findings = list(getattr(report, "findings", None) or [])
    out: list[str] = []
    for finding in findings[: max(1, int(limit or 50))]:
        severity = bounded_review_value(getattr(finding, "severity", "") or "info", max_chars=24)
        file_name = bounded_review_value(getattr(finding, "file", "") or "", max_chars=80)
        line_range = getattr(finding, "line_range", None) or []
        line = line_range[0] if isinstance(line_range, (list, tuple)) and line_range else 0
        loc = f"{file_name}:{line}" if file_name and line else file_name
        message = bounded_review_value(getattr(finding, "message", "") or "", max_chars=110)
        if loc and message:
            summary = f"{severity} {loc}: {message}"
        else:
            summary = message or loc or severity
        # Infrastructure findings otherwise collapse to the unhelpful
        # ``Review generation failed: RuntimeError`` line in the maintainer
        # ledger/transcript. Preserve the provider/validation explanation for
        # retry diagnosis, while keeping the normal code-finding output compact.
        if str(file_name).strip().lower() == "<review>" or message.lower().startswith("review generation failed"):
            detail = bounded_review_value(
                getattr(finding, "explanation", "") or getattr(finding, "rationale", ""),
                max_chars=120,
            )
            if detail:
                # Keep both the failure class and its actionable provider/test
                # reason inside the bounded ledger field. Appending detail to a
                # full-length message lets the final 180-char cap erase the
                # diagnosis entirely.
                prefix = bounded_review_value(summary, max_chars=55)
                summary = f"{prefix} ({detail})"
        if summary:
            out.append(bounded_review_value(summary, max_chars=180))
    return out
