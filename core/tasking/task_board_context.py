"""Read-only taskboard context compiler.

The compiler centralizes compact taskboard summaries for provider/Ghost/handoff
surfaces without moving truth ownership away from Gateway/Agent/TaskBoard.
"""
from __future__ import annotations

from typing import Any

from ..runtime.backend_monitor import redact_monitor_text
from .task_board import OPEN, TaskBoard, TaskItem, status_marker


def compile_board_context(
    board: TaskBoard | None,
    *,
    max_tasks: int = 8,
    max_evidence: int = 4,
    max_chars: int = 1400,
) -> dict[str, Any]:
    """Return compact taskboard context without mutating board state."""
    if not board:
        return _empty_context()
    tasks = board.tasks
    active_id = str(board.active_task_id() or "")
    ready = board.next_ready_task()
    ready_id = str(ready.id if ready else "")
    graph = board.validate_graph()
    lines: list[str] = [
        f"Task board `{redact_monitor_text(board.title, 120)}`: {len(tasks)} tasks ({board.done_count()} done, {board.open_count()} open)",
    ]
    objective = board.objective.strip()
    if objective:
        lines.append(f"objective: {redact_monitor_text(objective, 260)}")
    if active_id:
        active = board.task(active_id)
        lines.append(_task_detail_line("active", active))
    elif ready:
        lines.append(_task_detail_line("ready", ready))
    for row in tasks[:max_tasks]:
        lines.append(_task_row_line(row))
        evidence = [redact_monitor_text(str(item), 180) for item in row.evidence[:max_evidence]]
        if evidence:
            lines.append("  evidence: " + "; ".join(evidence))
        expected = [redact_monitor_text(str(item), 140) for item in row.expected_evidence[:max_evidence]]
        if expected:
            lines.append("  expected evidence: " + "; ".join(expected))
        criteria = [redact_monitor_text(str(item), 140) for item in row.acceptance_criteria[:max_evidence]]
        if criteria:
            lines.append("  acceptance: " + "; ".join(criteria))
        strategy = row.test_strategy.strip()
        if strategy:
            lines.append("  test strategy: " + redact_monitor_text(strategy, 160))
    if len(tasks) > max_tasks:
        lines.append(f"… {len(tasks) - max_tasks} more task(s)")
    issues = list(graph.get("issues") or [])[:5]
    if issues:
        lines.append("graph diagnostics: " + "; ".join(_issue_summary(issue) for issue in issues))
    text = redact_monitor_text("\n".join(lines), max_chars)
    return {
        "present": True,
        "board_id": board.board_id,
        "turn_id": board.turn_id,
        "session_id": board.session_id,
        "title": board.title,
        "objective": objective,
        "total": len(tasks),
        "completed": int(board.done_count()),
        "open": int(board.open_count()),
        "active_task_id": active_id,
        "ready_task_id": ready_id or str(graph.get("ready_task_id") or ""),
        "graph": graph,
        "lines": lines,
        "text": text,
    }


def compile_board_context_from_snapshot(snapshot: dict[str, Any] | None, *, max_tasks: int = 8, max_evidence: int = 4, max_chars: int = 1400) -> dict[str, Any]:
    """Return compact context for a ledger snapshot without creating truth."""
    if not snapshot:
        return _empty_context()
    tasks = list((snapshot or {}).get("tasks") or [])
    completed = sum(1 for task in tasks if task.get("status") == "completed")
    open_count = sum(1 for task in tasks if task.get("status") in OPEN)
    lines = [
        f"Last recorded board `{redact_monitor_text(str(snapshot.get('title') or ''), 120)}`: {len(tasks)} tasks ({completed} done, {open_count} open) - ledger {snapshot.get('event', 'updated')}/{snapshot.get('state', 'active')}",
    ]
    objective = str(snapshot.get("objective") or "").strip()
    if objective:
        lines.append(f"objective: {redact_monitor_text(objective, 260)}")
    ready_id = ""
    active_id = ""
    for task in tasks[:max_tasks]:
        status = str(task.get("status") or "pending")
        task_id = str(task.get("id") or "")
        if status == "active" and not active_id:
            active_id = task_id
        if status == "pending" and not ready_id:
            ready_id = task_id
        marker = status_marker(status)
        line = f"{marker} {task_id}. {redact_monitor_text(str(task.get('title') or 'task'), 220)} [{status}]"
        blocker = str(task.get("blocker") or "").strip()
        if blocker:
            line += f" — blocker: {redact_monitor_text(blocker, 180)}"
        lines.append(line)
        evidence = [redact_monitor_text(str(item), 180) for item in list(task.get("evidence") or [])[:max_evidence]]
        if evidence:
            lines.append("  evidence: " + "; ".join(evidence))
    if len(tasks) > max_tasks:
        lines.append(f"… {len(tasks) - max_tasks} more task(s)")
    text = redact_monitor_text("\n".join(lines), max_chars)
    return {
        "present": True,
        "board_id": str(snapshot.get("board_id") or ""),
        "turn_id": str(snapshot.get("turn_id") or ""),
        "session_id": str(snapshot.get("session_id") or ""),
        "title": str(snapshot.get("title") or ""),
        "objective": objective,
        "total": len(tasks),
        "completed": completed,
        "open": open_count,
        "active_task_id": active_id,
        "ready_task_id": active_id or ready_id,
        "graph": {"valid": True, "issues": [], "ready_task_id": active_id or ready_id},
        "lines": lines,
        "text": text,
    }


def task_row_value(task: Any, key: str, default: Any = "") -> Any:
    if isinstance(task, dict):
        return task.get(key, default)
    return getattr(task, key, default)


def task_row_status(task: Any) -> str:
    return str(task_row_value(task, "status", "pending") or "pending")


def task_row_title(task: Any) -> str:
    return str(task_row_value(task, "title", "task") or "task").strip()


def task_row_blocker(task: Any) -> str:
    return str(task_row_value(task, "blocker", "") or "").strip()


def task_row_evidence(task: Any) -> list[Any]:
    return list(task_row_value(task, "evidence", []) or [])


def task_row_counts(tasks: list[Any]) -> dict[str, int]:
    return {
        "total": len(tasks),
        "completed": sum(1 for task in tasks if task_row_status(task) == "completed"),
        "open": sum(1 for task in tasks if task_row_status(task) in OPEN),
        "blocked": sum(1 for task in tasks if task_row_status(task) == "blocked"),
    }


def _task_row_line(row: TaskItem) -> str:
    status = row.status
    marker = status_marker(status)
    title = redact_monitor_text(row.title, 220)
    line = f"{marker} {row.id}. {title} [{status}]"
    if row.kind or row.completion_gate:
        line += f" · kind={row.kind or '-'} gate={row.completion_gate or '-'}"
    if row.depends_on:
        line += " · deps=" + ",".join(str(dep) for dep in row.depends_on)
    if row.parent_id:
        line += f" · parent={row.parent_id}"
    blocker = row.blocker.strip()
    if blocker:
        line += f" — blocker: {redact_monitor_text(blocker, 180)}"
    return line


def _task_detail_line(label: str, row: TaskItem) -> str:
    return f"{label}: {row.id}. {redact_monitor_text(row.title, 220)}"


def _issue_summary(issue: dict[str, Any]) -> str:
    code = str(issue.get("code") or "issue")
    task_id = str(issue.get("task_id") or "")
    suffix = f" task={task_id}" if task_id else ""
    return code + suffix


def _empty_context() -> dict[str, Any]:
    return {
        "present": False,
        "board_id": "",
        "turn_id": "",
        "session_id": "",
        "title": "",
        "objective": "",
        "total": 0,
        "completed": 0,
        "open": 0,
        "active_task_id": "",
        "ready_task_id": "",
        "graph": {"valid": True, "issues": [], "ready_task_id": ""},
        "lines": [],
        "text": "",
    }
