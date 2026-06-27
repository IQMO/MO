"""Shared coordination-state readers for workers, goals, and main-turn conflicts.

These helpers centralize formatting only. They do not own task truth, mutate
worker records, or treat registry/profile/graph state as proof of code
correctness.
"""
from __future__ import annotations

from typing import Any

from .backend_monitor import redact_monitor_text
from .worker import WorkerRecord, ensure_worker_registry, extract_worker_paths


BACKGROUND_CONFLICT_HEADER = "### Coordination conflict — priority 1"


def worker_summary_lines(agent: Any, *, limit: int = 6) -> list[str]:
    """Return active/recent worker registry rows in one shared format."""
    registry = getattr(agent, "workers", None)
    if not registry or not hasattr(registry, "summary"):
        return []
    try:
        summary = str(registry.summary(limit=limit) or "").strip()
    except Exception:
        return []
    return [redact_monitor_text(line.strip(), 240) for line in summary.splitlines() if line.strip()]


def goal_summary_lines(agent: Any, *, limit: int = 8, include_evidence: bool = False) -> list[str]:
    """Return compact goal rows shared by Ghost/workspace/handoff/closeout."""
    plan = getattr(agent, "_goal_plan", None)
    if not plan:
        return []
    rows: list[str] = []
    try:
        objective = redact_monitor_text(getattr(plan, "objective", ""), 240)
        state = str(getattr(plan, "state", "") or "unknown")
        completed = getattr(plan, "completed_count", lambda: 0)()
        total = len(getattr(plan, "steps", []) or [])
        rows.append(f"objective: {objective}")
        rows.append(f"state: {state}; iterations: {getattr(plan, 'iterations_run', 0)}; progress: {completed}/{total}")
        stop_reason = str(getattr(plan, "stop_reason", "") or "").strip()
        if stop_reason:
            rows.append(f"stop reason: {redact_monitor_text(stop_reason, 180)}")
        for step in list(getattr(plan, "steps", []) or [])[:limit]:
            status = str(getattr(step, "status", "") or "pending")
            title = redact_monitor_text(getattr(step, "title", ""), 220)
            blocker = redact_monitor_text(getattr(step, "blocker", ""), 160)
            suffix = f" — {blocker}" if blocker else ""
            if include_evidence:
                evidence = "; ".join(str(item) for item in list(getattr(step, "evidence", []) or [])[:3])
                if evidence:
                    suffix += f" evidence=[{redact_monitor_text(evidence, 200)}]"
            rows.append(f"{status}: {title}{suffix}")
    except Exception:
        return []
    return [row for row in rows if row.strip()]


def active_conflicts_for_text(agent: Any, text: str, *, exclude: str = "") -> tuple[list[str], list[WorkerRecord]]:
    """Return explicit path claims in text and active registry conflicts."""
    paths = extract_worker_paths(text)
    if not paths:
        return [], []
    registry = ensure_worker_registry(agent)
    conflicts = registry.conflicts(paths, exclude=exclude)
    return paths, conflicts


def build_main_coordination_context(agent: Any, user_input: str) -> str:
    """Return a priority-1 warning when a main turn names worker-claimed files."""
    paths, conflicts = active_conflicts_for_text(agent, user_input)
    if not paths or not conflicts:
        return ""
    lines = [
        BACKGROUND_CONFLICT_HEADER,
        "The current user request names paths claimed by active background work. Coordinate before edits; do not overwrite or duplicate another worker's work.",
        "Requested paths: " + ", ".join(redact_monitor_text(path, 160) for path in paths[:8]),
    ]
    for record in conflicts[:5]:
        claimed = ", ".join(redact_monitor_text(path, 120) for path in list(getattr(record, "claimed_paths", []) or [])[:5])
        objective = redact_monitor_text(getattr(record, "objective", ""), 180)
        lines.append(f"- active {record.kind}/{record.id}: {getattr(record, 'state', '')} · {objective}" + (f" · claims {claimed}" if claimed else ""))
    lines.append("Action: mention the conflict briefly if relevant, inspect state, then either wait/coordinate or work on non-conflicting files only.")
    return "\n".join(lines)
