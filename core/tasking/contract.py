"""Taskboard contract enforcement for turn completion.

Wraps the existing ``check_task_board_contract`` diagnostic with enforcement
parameters and produces a provider-facing continuation instruction when the
contract fails, so the agent cannot mark work complete with broken task truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .task_board import TaskBoard, check_task_board_contract


# Per-turn enforcement reasons: evidence, sync, and graph integrity.
# Mid-work turns may legitimately have open/blocked tasks, so we only
# enforce the evidence and sync dimensions.  The full "gold footer"
# (require_completed + no blocked) is reserved for final sign-off.
_ENFORCE_REASON_PREFIXES = ("missing_evidence:", "task_sync:", "graph:")


def enforce_contract_gate(
    task_board: TaskBoard | None,
    *,
    persisted_tasks: list[dict[str, Any]] | None = None,
    board_closing: bool = False,
    task_ids: set[str] | None = None,
) -> tuple[bool, list[str], str]:
    """Validate taskboard contract before turn finalization.

    Returns ``(ok, reasons, continuation_instruction)``.  When *ok* is False the
    caller must feed *continuation_instruction* back to the provider instead of
    finalising the turn.

    When *board_closing* is False (mid-work turn), only evidence and
    board-persisted sync are enforced.  When *board_closing* is True
    (gold footer), the full contract including ``require_completed`` is applied.

    When *task_ids* is provided, only reasons referencing those task IDs are
    enforced — this scopes enforcement to tasks completed in the current turn.
    """
    if not task_board:
        return True, [], ""

    contract = check_task_board_contract(
        task_board,
        require_completed=board_closing,
        require_evidence=True,
        persisted_tasks=persisted_tasks,
    )

    # Filter to enforcement-only reasons for mid-work turns;
    # for board-closing turns, all reasons are enforced.
    if board_closing:
        enforce_reasons = list(contract.reasons)
    else:
        enforce_reasons = [
            r for r in contract.reasons
            if any(r.startswith(p) for p in _ENFORCE_REASON_PREFIXES)
        ]

    # Further filter by task_ids when scoping to just-completed tasks
    if task_ids:
        enforce_reasons = [
            r for r in enforce_reasons
            if _reason_matches_task_ids(r, task_ids)
        ]

    if not enforce_reasons:
        return True, [], ""

    reasons_text = "; ".join(enforce_reasons[:5])
    instruction = (
        "[CONTRACT GATE] Taskboard contract issues before completion. "
        f"Reasons: {reasons_text}. "
        "Fix each issue above with real tool evidence before marking work complete. "
        "Do not fabricate completion — resolve every contract failure first."
    )
    return False, enforce_reasons, instruction


def _reason_matches_task_ids(reason: str, task_ids: set[str]) -> bool:
    """Return True if *reason* references any task ID in *task_ids*.

    Reasons look like ``missing_evidence:1`` or ``graph:cycle:2``.
    Reasons like ``taskboard_open:2`` or ``taskboard_empty`` have no task ID
    and always match (global issues apply regardless of scope).
    """
    # Global reasons with no task-id suffix — always enforced
    if ":" not in reason:
        return True
    # Extract the last colon-separated segment as a potential task ID
    last_segment = reason.rsplit(":", 1)[-1]
    if last_segment in task_ids:
        return True
    # graph:code:taskid has two colons
    parts = reason.split(":")
    if len(parts) >= 3 and parts[-1] in task_ids:
        return True
    # Reason like "taskboard_open:N" — global, always enforce
    if reason.startswith("taskboard_open:"):
        return True
    if reason.startswith("taskboard_empty"):
        return True
    return False


def load_persisted_tasks_for_contract(board: TaskBoard | None = None) -> list[dict[str, Any]]:
    """Best-effort load of persisted task rows for board-row sync.

    Returns an empty list when persistence is unavailable so callers can always
    pass the result to ``enforce_contract_gate`` without branching.

    When *board* is provided, rows are returned only if the persisted snapshot
    belongs to the same board (``board_id`` match).  Task IDs are board-local
    ("1", "2", ...), so cross-board comparison produces false ``task_sync``
    failures against stale state from earlier sessions.
    """
    try:
        from .task_manager import TaskManager
        from .task_board import _resolve_ledger_path

        # Read current.json from the SAME place record_snapshot writes it (the resolved
        # private ledger dir), not cwd/memory/taskboards — fixes a product read/write
        # mismatch and stops a stray empty memory/ in the project checkout.
        ledger = _resolve_ledger_path()
        if ledger is None:
            return []  # ledger disabled / no private home → nothing persisted to compare
        tm = TaskManager(Path.cwd(), tasks_dir=ledger.parent)
        if board is not None:
            snapshot = tm.load_snapshot()
            persisted_board_id = str(snapshot.get("board_id") or "")
            live_board_id = str(getattr(board, "board_id", "") or "")
            if not persisted_board_id or persisted_board_id != live_board_id:
                return []
        return tm.load_tasks()
    except Exception:
        return []
