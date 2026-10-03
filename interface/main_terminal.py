"""MO terminal interface — prompt-toolkit TUI with managed scrolling."""
from __future__ import annotations

import queue
import threading
import traceback
from typing import Any

from .display_delegates import DisplayDelegatesMixin
from .command_palette import CommandPalette
from .tui_goal import GoalUiMixin
from .input_dispatch import InputDispatchMixin
from .queueing import QueueingMixin
from .response_mixin import ResponseMixin
from .transcript_state import TranscriptStateMixin
from .turn_runner import TurnRunnerMixin
from .tui_app import TuiAppMixin
from .worker_status import WorkerStatusMixin
from .workspace import WorkspaceController
from . import input as _input_module

if _input_module.HAS_PROMPT_TOOLKIT:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.history import InMemoryHistory

# ── Prompt-toolkit TUI ───────────────────────────────────────────────

class MoTui(
    GoalUiMixin,
    WorkerStatusMixin,
    QueueingMixin,
    InputDispatchMixin,
    TranscriptStateMixin,
    ResponseMixin,
    DisplayDelegatesMixin,
    TurnRunnerMixin,
    TuiAppMixin,
):
    """Prompt-toolkit TUI: styled transcript, fixed bottom, managed scrolling."""

    def __init__(self, agent: Any, gateway: Any):
        self.agent = agent
        self.gateway = gateway
        try:
            self.agent.tui = self
        except Exception:
            traceback.print_exc()
        self.activity_text = ""
        self.activity_started_at = 0.0
        self.board_text = ""
        self._goal_board_text = ""
        self.busy = False
        self._app: Application | None = None
        self._input_buf: Buffer | None = None
        # Transcript: append-only list of (style, text) pairs
        self._lines: list[tuple[str, str]] = []
        self._snapshot: tuple[tuple[str, str], ...] = (("class:dim", ""),)
        self._dirty = False
        self._ui_lock = threading.RLock()
        self._native_scrollback_pending: list[tuple[tuple[str, str], ...]] = []
        self._native_scrollback_pending_fragments = 0
        self._pending_inputs: queue.Queue[Any] = queue.Queue()
        self._last_queued_input: dict[str, Any] | None = None
        self._steered_inputs: list[dict[str, Any]] = []
        self._consumed_steer_workers: list[dict[str, Any]] = []
        self._current_turn_cancel_event: threading.Event | None = None
        self._turn_thread: threading.Thread | None = None
        self._refresh_stop = threading.Event()
        self._transcript_scroll_from_bottom = 0
        self._notice_text = ""
        self._notice_until = 0.0
        self._live_tool_label = ""
        self._live_tool_started_at = 0.0
        self._live_tool_timeout = 0
        self._live_tool_timeout_checked_at = 0.0
        self._turn_edit_additions = 0
        self._turn_edit_deletions = 0
        self._reasoning_gist_shown = False
        self._paste_holder_text = ""
        self._paste_holder_active = False
        self._pre_paste_buffer_text = ""
        # Ctrl+E prompt-enhance: stash the operator's original message so Esc can
        # revert the enhanced text back to exactly what they typed.
        self._pre_enhance_text = ""
        self._enhance_holder_active = False
        self._enhance_in_flight = False
        # One-time-per-session low-balance notice (DeepSeek official API only).
        self._low_balance_notified = False
        self._busy_escape_count = 0
        # One terminal window may own one MO pane plus local blank terminal
        # panes. The controller is inert until `/workspace`, preserving the
        # original single-pane transcript and native scrollback path exactly.
        self._workspace = WorkspaceController(self)

        # Goal UI state
        self._goal_running = False
        self._goal_worker_active = False
        self._goal_queued = False
        self._goal_backgrounded = False
        self._goal_started_at = 0.0
        self._goal_stage = ""
        self._goal_last_transcript_progress_key: tuple[object, ...] | None = None
        show_cfg = getattr(self.agent, "config", {}).get("interface", {}).get("show", {})
        self._show_tool_activity = bool(show_cfg.get("tools", True))
        self._show_reasoning = bool(show_cfg.get("reasoning", False))
        self._palette = CommandPalette()
        self._goal_board_scroll_from_bottom = 0
        self._board_scroll_from_bottom = 0
        self._prt_done_unread = False
        self._goal_done_unread = False
        self._input_history = InMemoryHistory()
        self.agent._native_async_notice = self._show_worker_notice
        # _terminal_columns()/_terminal_rows() are inherited from
        # TerminalMetricsMixin (via the display/response/transcript mixins).
