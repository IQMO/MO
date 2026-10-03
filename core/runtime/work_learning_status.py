"""Truthful count-only work and learning status for lightweight surfaces."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..learning.status import LearningStatus, build_learning_status


SCHEMA_VERSION = 1
_STATUS_QUERY_RE = re.compile(
    r"\b(?:work and learning status|learning status|pending learning review)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WorkLearningStatus:
    schema_version: int
    state: str
    pending_review: int
    task_open: int
    task_completed: int
    task_total: int
    task_blocked: int
    resumable_tasks: int
    boundary_findings: int
    detail_items: tuple[str, ...]
    drivers: tuple[str, ...]
    short_label: str
    basis: str = "direct taskboard, boundary, and durable learning counts; no composite score"
    source: str = "taskboard/session counts and durable learning counts"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state": self.state,
            "pending_review": int(self.pending_review),
            "task_open": int(self.task_open),
            "task_completed": int(self.task_completed),
            "task_total": int(self.task_total),
            "task_blocked": int(self.task_blocked),
            "resumable_tasks": int(self.resumable_tasks),
            "boundary_findings": int(self.boundary_findings),
            "detail_items": list(self.detail_items),
            "drivers": list(self.drivers),
            "short_label": self.short_label,
            "basis": self.basis,
            "raw_content_hidden": True,
            "source": self.source,
        }


def build_work_learning_status(
    agent: Any = None,
    *,
    learning_status: LearningStatus | None = None,
) -> WorkLearningStatus:
    """Build direct state from observable counts without inventing a percentage."""
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    learning = learning_status or build_learning_status(getattr(agent, "profile", None), config=cfg)
    counts = _taskboard_counts(agent)
    boundary_findings = _boundary_finding_count(agent)
    pending_review = int(learning.pending_suggestions) + int(learning.workflow_candidates)
    state = _state_for_counts(
        counts,
        pending_review=pending_review,
        boundary_findings=boundary_findings,
    )
    details = (
        f"tasks {counts['completed']}/{counts['total']} done",
        f"open {counts['open']}",
        f"review {pending_review}",
        f"boundary {boundary_findings}",
    )
    drivers = _driver_items(
        counts,
        pending_review=pending_review,
        boundary_findings=boundary_findings,
    )
    return WorkLearningStatus(
        schema_version=SCHEMA_VERSION,
        state=state,
        pending_review=pending_review,
        task_open=int(counts["open"]),
        task_completed=int(counts["completed"]),
        task_total=int(counts["total"]),
        task_blocked=int(counts["blocked"]),
        resumable_tasks=int(counts["resumable"]),
        boundary_findings=boundary_findings,
        detail_items=details,
        drivers=drivers,
        short_label=_short_label(state, counts, pending_review, boundary_findings),
    )


def render_work_learning_status(status: WorkLearningStatus | dict[str, Any] | None) -> str:
    data = _status_dict(status)
    if not data:
        return "unavailable"
    driver_text = _driver_text(data)
    text = f"{str(data.get('state') or 'unknown')}"
    return f"{text} · {driver_text}" if driver_text else text


def looks_like_work_learning_question(text: str) -> bool:
    """Recognize explicit questions about the direct-count learning surface."""
    return bool(_STATUS_QUERY_RE.search(str(text or "")))


def render_work_learning_context(status: WorkLearningStatus | dict[str, Any] | None) -> str:
    data = _status_dict(status)
    if not data:
        return ""
    drivers = _driver_text(data, limit=3) or "no count drivers available"
    return (
        "### MO Work and Learning Status\n"
        f"- Current state: {data.get('state') or 'unknown'}.\n"
        f"- Tasks: {int(data.get('task_completed') or 0)}/{int(data.get('task_total') or 0)} done; "
        f"{int(data.get('task_open') or 0)} open; {int(data.get('task_blocked') or 0)} blocked; "
        f"{int(data.get('resumable_tasks') or 0)} resumable.\n"
        f"- Learning review: {int(data.get('pending_review') or 0)} pending. "
        f"Boundary findings: {int(data.get('boundary_findings') or 0)}.\n"
        f"- Drivers: {drivers}.\n"
        f"- Basis: {data.get('basis') or 'direct observable counts'}.\n"
        "- Boundary: read-only status for explanation; not model confidence, alignment, task truth, or proof that learning helped."
    )


def _taskboard_counts(agent: Any) -> dict[str, int]:
    gateway = None
    board = None
    try:
        gateway = getattr(agent, "gateway", None)
        board = getattr(gateway, "last_task_board", None)
    except Exception:
        board = None
    if board is None:
        board = getattr(agent, "_active_task_board", None)
    counts = _counts_from_board(board)
    if counts["total"] <= 0:
        try:
            resumable = getattr(gateway, "last_resumable_board", None)
        except Exception:
            resumable = None
        resume_counts = _counts_from_board(resumable)
        if resume_counts["open"] > 0:
            counts["resumable"] = resume_counts["open"]
    return counts


def _counts_from_board(board: Any) -> dict[str, int]:
    try:
        summary = board.summary() if board is not None and hasattr(board, "summary") else {}
    except Exception:
        summary = {}
    tasks = list(summary.get("tasks") or getattr(board, "tasks", []) or [])
    total = int(summary.get("total") or len(tasks) or 0)
    completed = int(summary.get("done") or summary.get("completed") or 0)
    open_count = int(summary.get("open") or 0)
    blocked = 0
    if tasks:
        completed = 0
        open_count = 0
        for task in tasks:
            status = _task_status(task)
            if status == "completed":
                completed += 1
            elif status in {"pending", "active", "blocked", "open", "running", "queued"}:
                open_count += 1
                if status == "blocked":
                    blocked += 1
    return {"total": total, "completed": completed, "open": open_count, "blocked": blocked, "resumable": 0}


def _task_status(task: Any) -> str:
    if isinstance(task, dict):
        return str(task.get("status") or "").strip().lower()
    return str(getattr(task, "status", "") or "").strip().lower()


def _boundary_finding_count(agent: Any) -> int:
    report = getattr(agent, "_last_consistency_boundary_report", None)
    try:
        return len(getattr(report, "findings", ()) or ())
    except Exception:
        return 0


def _state_for_counts(counts: dict[str, int], *, pending_review: int, boundary_findings: int) -> str:
    if int(counts.get("blocked") or 0) > 0:
        return "blocked"
    if boundary_findings > 0 or pending_review > 0:
        return "attention"
    if int(counts.get("open") or 0) > 0 or int(counts.get("resumable") or 0) > 0:
        return "active"
    if int(counts.get("total") or 0) > 0:
        return "complete"
    return "idle"


def _short_label(state: str, counts: dict[str, int], pending_review: int, boundary_findings: int) -> str:
    if int(counts.get("blocked") or 0) > 0:
        return f"{int(counts['blocked'])} blocked"
    if boundary_findings > 0:
        return f"{boundary_findings} boundary"
    if int(counts.get("open") or 0) > 0:
        return f"{int(counts['open'])} open"
    if int(counts.get("resumable") or 0) > 0:
        return f"{int(counts['resumable'])} resumable"
    if pending_review > 0:
        return f"{pending_review} review"
    return state


def _driver_items(counts: dict[str, int], *, pending_review: int, boundary_findings: int) -> tuple[str, ...]:
    total = int(counts.get("total") or 0)
    completed = int(counts.get("completed") or 0)
    open_count = int(counts.get("open") or 0)
    blocked = int(counts.get("blocked") or 0)
    resumable = int(counts.get("resumable") or 0)
    if total > 0:
        task = f"tasks {completed}/{total} done, {open_count} open"
        if blocked > 0:
            task += f", {blocked} blocked"
    elif resumable > 0:
        task = f"resumable tasks {resumable} open"
    else:
        task = "no active taskboard"
    review = "review clear" if pending_review <= 0 else f"review {pending_review} pending"
    boundary = "boundary clean" if boundary_findings <= 0 else f"boundary {boundary_findings} finding"
    if boundary_findings > 1:
        boundary += "s"
    return (task, review, boundary)


def _driver_text(data: dict[str, Any], *, limit: int = 2) -> str:
    drivers = [str(item or "").strip() for item in (data.get("drivers") or []) if str(item or "").strip()]
    return " · ".join(drivers[: max(0, int(limit or 0))])


def _status_dict(status: WorkLearningStatus | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(status, WorkLearningStatus):
        return status.as_dict()
    if isinstance(status, dict):
        return dict(status)
    return {}
