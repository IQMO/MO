"""Shared mutable state for one Agent provider/tool turn."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable

from ..runtime.backend_monitor import BackendMonitor
from ..tasking.task_board import TaskBoard
from ..tasking.results import TaskTransitionResult, ToolExecutionRecord
from ..gates.security_check import run_turn_security_check


@dataclass
class TurnState:
    """State shared across provider rounds and the extracted tool phases.

    Callbacks and the lazily-created task board travel with the state so phase
    helpers receive one explicit turn contract instead of a widening parameter
    tuple. Provider/text-only retry state remains in ``run_turn``.
    """

    user_input: str
    task_board: TaskBoard | None = None
    monitor: BackendMonitor | None = None
    on_activity: object = None
    on_first_tool: object = None
    cancel_event: object = None
    on_assistant_text: object = None
    on_board_event: object = None
    on_action: object = None
    on_operator_visual: object = None
    on_operator_image: object = None
    tool_rounds: int = 0
    next_progress_reminder_at: float = field(default_factory=lambda: time.monotonic() + 60.0)
    offered_tool_names: frozenset[str] = field(default_factory=frozenset)
    catalog_mode: str = ""
    malformed_tool_prompts: int = 0
    consecutive_plan_revision_batches: int = 0
    noop_tool_searches: int = 0
    last_tool_batch_signature: str = ""
    repeated_tool_batch_count: int = 0
    completed_tool_batch_signature: str = ""
    completed_tool_batch_results: list[dict[str, Any]] = field(default_factory=list, repr=False)
    completed_tool_batch_replay_count: int = 0
    exhausted_tool_batch_signature: str = ""
    exhausted_tool_batch_reason: str = ""
    exhausted_tool_batch_guided: bool = False
    blocked_tool_batch_signature: str = ""
    blocked_tool_batch_reason: str = ""
    blocked_tool_batch_guided: bool = False
    missing_read_paths: set[str] = field(default_factory=set, repr=False)
    missing_read_guided_paths: set[str] = field(default_factory=set, repr=False)
    tool_call_counts: dict[str, int] = field(default_factory=dict)
    tool_error_counts: dict[str, int] = field(default_factory=dict)
    tool_sequence: list[ToolExecutionRecord] = field(default_factory=list)
    task_transitions: list[TaskTransitionResult] = field(default_factory=list)
    # Completed verification results are local to one provider/tool turn unless
    # Gateway's monitor turn id joins an admitted extension continuation to the same
    # outer turn. They are never serialized into taskboard or session state.
    verification_results: dict[str, str] = field(default_factory=dict, repr=False)
    # A selected run is not permission for automatic whole-file expansion.
    # Keep its scope when candidate edits invalidate the passing proof.
    verification_selected_roots: set[str] = field(default_factory=set, repr=False)
    verification_disclosure: str = ""
    # Read-only orientation for the next existing request, not verification proof.
    project_change_context: str | None = None
    final_gates_fired: set[str] = field(default_factory=set, repr=False)
    turn_provider_errors: int = 0
    turn_provider_fallbacks: int = 0
    turn_modified_files: list[tuple[str, str]] = field(default_factory=list)

    def clear_repeat_tracking(self) -> None:
        self.last_tool_batch_signature = ""
        self.repeated_tool_batch_count = 0

    def clear_completed_tool_batch(self) -> None:
        self.completed_tool_batch_signature = ""
        self.completed_tool_batch_results = []
        self.completed_tool_batch_replay_count = 0

    def clear_exhausted_tool_batch(self) -> None:
        self.exhausted_tool_batch_signature = ""
        self.exhausted_tool_batch_reason = ""
        self.exhausted_tool_batch_guided = False

    def clear_blocked_tool_batch(self) -> None:
        self.blocked_tool_batch_signature = ""
        self.blocked_tool_batch_reason = ""
        self.blocked_tool_batch_guided = False


def emit_security_check(
    turn_modified_files: list[tuple[str, str]],
    monitor: BackendMonitor | None,
    *,
    checker: Callable[..., Any] = run_turn_security_check,
) -> None:
    """Emit the existing turn-end security result when files changed."""
    if not turn_modified_files:
        return
    sec_result = checker(turn_modified_files)
    if sec_result.findings and monitor:
        monitor.emit("security_check", sec_result.as_dict())
