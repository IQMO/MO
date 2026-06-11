"""Compact workspace/worker awareness for MO turns.

This is not a tool result and not a replacement for verification. It gives MO a
small coordination note so it can avoid conflicting with existing work and speak
naturally about visible repo/worker state.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .backend_monitor import redact_monitor_text
from .tasking.task_board_context import compile_board_context
from .coordination_state import goal_summary_lines, worker_summary_lines
from .gateway_helpers import select_template


_STATUS_WORDS = {
    "status", "state", "changes", "changed", "uncommitted", "dirty", "commit", "push",
    "close", "closing", "done", "finish", "cooking", "cook", "worker", "workers", "goal", "ghost",
}

_GREETING_ONLY = {"hi", "hello", "hey", "yo", "hi mo", "hello mo", "hey mo"}


def should_include_workspace_awareness(user_input: str) -> bool:
    text = str(user_input or "").strip().lower()
    if not text:
        return False
    if text in _GREETING_ONLY:
        return False
    if select_template(text) != "simple_chat":
        return True
    return any(word in text for word in _STATUS_WORDS) or "what's going on" in text or "what is going on" in text


def build_workspace_awareness(agent: Any, *, cwd: str | None = None, max_files: int = 12) -> str:
    """Return a short safe context block for main MO coordination."""
    lines: list[str] = []
    git_summary = _git_status_summary(cwd or os.getcwd(), max_files=max_files)
    if git_summary:
        lines.append(git_summary)

    worker_summary = _worker_summary(agent)
    if worker_summary:
        lines.append(worker_summary)

    if not lines:
        return ""

    guidance = (
        "Use this only as coordination context. If there are uncommitted changes or active workers, "
        "mention one brief natural coordination note only when relevant to the user's request, avoid conflicting edits, and keep working on the user's request. "
        "Do not over-report this note and do not treat it as proof of code correctness."
    )
    return "### Workspace / worker awareness\n" + "\n".join(lines) + "\n" + guidance


def _git_status_summary(cwd: str, *, max_files: int) -> str:
    try:
        proc = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=str(Path(cwd)),
            text=True,
            capture_output=True,
            timeout=5,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""

    raw_lines = [line.rstrip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if not raw_lines:
        return "Git state: not available"

    branch = raw_lines[0] if raw_lines and raw_lines[0].startswith("##") else ""
    changes = [line for line in raw_lines[1:] if line.strip()]
    if not changes:
        branch_text = f" ({redact_monitor_text(branch, 120)})" if branch else ""
        return f"Git state: clean{branch_text}"

    preview = [redact_monitor_text(line, 180) for line in changes[:max_files]]
    suffix = f"; +{len(changes) - max_files} more" if len(changes) > max_files else ""
    branch_text = f" {redact_monitor_text(branch, 120)};" if branch else ""
    return f"Git state:{branch_text} {len(changes)} uncommitted file(s): " + "; ".join(preview) + suffix


def _worker_summary(agent: Any) -> str:
    parts: list[str] = []
    workers = worker_summary_lines(agent, limit=5)
    if workers:
        parts.append("Registered workers:\n" + "\n".join(workers))
    goal_rows = goal_summary_lines(agent, limit=3)
    if goal_rows:
        plan = getattr(agent, "_goal_plan", None)
        objective_line = next((row for row in goal_rows if row.startswith("objective:")), "")
        objective = objective_line.replace("objective: ", "") or "background goal"
        if getattr(agent, "_goal_active", False):
            completed = getattr(plan, "completed_count", lambda: 0)() if plan else 0
            total = len(getattr(plan, "steps", []) or []) if plan else 0
            count = f" · {completed}/{total} done" if total else ""
            parts.append(f"Background MO worker active: {objective}{count}")
        else:
            state = getattr(plan, "state", "")
            if state in {"paused", "blocked"}:
                parts.append(f"Background MO worker {state}: {objective}")

    gateway = getattr(agent, "gateway", None)
    board = getattr(gateway, "last_task_board", None) if gateway else None
    if board and any(getattr(task, "is_open", False) for task in getattr(board, "tasks", []) or []):
        try:
            context = compile_board_context(board, max_tasks=1, max_evidence=0, max_chars=240)
            first = f"{context.get('total', 0)} tasks ({context.get('completed', 0)} done, {context.get('open', 0)} open)"
        except Exception:
            first = "task board active"
        parts.append(f"Recent task board: {redact_monitor_text(first, 160)}")

    return "\n".join(parts)


def prt_safe_to_mutate(agent: Any) -> tuple[bool, str]:
    """Check if it is safe for PRT to auto-fix or mutate the workspace."""
    cwd = str(getattr(agent, "project_cwd", "") or getattr(agent, "workspace", "") or os.getcwd())
    git_status = _git_status_summary(cwd, max_files=1)
    if "uncommitted" in git_status:
        return False, "Workspace has uncommitted changes"
        
    registry = getattr(agent, "workers", None)
    if registry:
        for w in registry.active():
            if w.kind in {"main", "goal", "queue"} and w.state == "running":
                return False, f"Conflicting active worker: {w.kind}/{w.id}"
                
    if getattr(agent, "_goal_active", False):
        return False, "Goal Runner is currently active"
        
    return True, ""
