"""Evidence gate for explicit cross-surface MO Board launch requests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..runtime.turn_intent import looks_like_explicit_board_open_request
from ..tasking.results import ToolExecutionRecord, tool_execution_records

BOARD_OPEN_MAX_CONTINUATIONS = 2


@dataclass(frozen=True)
class BoardCompletionResult:
    count: int
    instruction: str = ""
    blocked_text: str = ""


def successful_board_open(tool_sequence: list[ToolExecutionRecord] | None) -> bool:
    """Return whether this turn successfully opened or focused Board view."""
    return any(
        event.tool == "mo_design"
        and event.action in {"open", "show"}
        and event.view == "board"
        and event.successful
        for event in tool_execution_records(tool_sequence)
    )


def run_board_completion_gate(
    agent: Any,
    user_input: str,
    tool_sequence: list[ToolExecutionRecord],
    *,
    count: int,
    max_continuations: int = BOARD_OPEN_MAX_CONTINUATIONS,
    on_activity: Any = None,
    monitor: Any = None,
) -> BoardCompletionResult:
    """Require the canonical Board tool instead of prose or app substitution."""
    raw_input = getattr(agent, "_conversation_user_input", lambda value: value)(user_input)
    if not looks_like_explicit_board_open_request(raw_input):
        return BoardCompletionResult(count=count)
    if successful_board_open(tool_sequence):
        return BoardCompletionResult(count=count)

    if monitor is not None:
        try:
            monitor.emit("board_completion", {
                "count": int(count),
                "max_continuations": int(max_continuations),
                "tools_run": [
                    event.tool
                    for event in tool_execution_records(tool_sequence)
                ][-12:],
            })
        except Exception:
            pass

    if count < max_continuations:
        if on_activity:
            on_activity("opening the shared Board…")
        return BoardCompletionResult(
            count=count + 1,
            instruction=(
                "[MO BOARD COMPLETION GATE] The operator explicitly requested the shared "
                "Board, but no successful mo_design open/show call with view=board exists "
                "in this turn. Call mo_design with view=board now: use action=open for a "
                "new Board, or action=show only when the exact existing design_id is known. Do not "
                "substitute Paint, another app, prose, a shortcut, or a terminal handoff. "
                "Do not claim the Board opened until that tool succeeds."
            ),
        )
    return BoardCompletionResult(
        count=count,
        blocked_text=(
            "I could not open the shared Board through MO's Board tool in this turn. "
            "I did not substitute another drawing app or claim it opened."
        ),
    )


__all__ = [
    "BOARD_OPEN_MAX_CONTINUATIONS",
    "BoardCompletionResult",
    "run_board_completion_gate",
    "successful_board_open",
]
