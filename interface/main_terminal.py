"""MO terminal interface — prompt-toolkit TUI with managed scrolling."""
from __future__ import annotations

import queue
import threading
import traceback
from typing import Any

from core.ghost.ghost_routing import GhostRouteSuggestion
from .display_delegates import DisplayDelegatesMixin
from .ghost_controller import GhostControllerMixin
from .ghost_history import GhostHistoryMixin
from .command_palette import CommandPalette
from .tui_goal import GoalUiMixin
from .input_dispatch import InputDispatchMixin
from .native_terminal import record_session
from .palette_mixin import PaletteMixin
from .queueing import QueueingMixin
from .response_mixin import ResponseMixin
from .transcript_state import TranscriptStateMixin
from .turn_runner import TurnRunnerMixin
from .tui_app import TuiAppMixin
from .worker_status import WorkerStatusMixin
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
    GhostHistoryMixin,
    GhostControllerMixin,
    TranscriptStateMixin,
    ResponseMixin,
    DisplayDelegatesMixin,
    TurnRunnerMixin,
    TuiAppMixin,
    PaletteMixin,
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
        self._pending_inputs: queue.Queue[Any] = queue.Queue()
        self._last_queued_input: dict[str, Any] | None = None
        self._current_turn_cancel_event: threading.Event | None = None
        self._refresh_stop = threading.Event()
        self._transcript_scroll_from_bottom = 0
        self._notice_text = ""
        self._notice_until = 0.0
        self._paste_holder_text = ""
        self._paste_holder_active = False
        self._pre_paste_buffer_text = ""
        # Ctrl+E prompt-enhance: stash the operator's original message so Esc can
        # revert the enhanced text back to exactly what they typed.
        self._pre_enhance_text = ""
        self._enhance_holder_active = False
        self._enhance_in_flight = False
        self._busy_escape_count = 0

        # Goal UI state
        self._goal_running = False
        self._goal_worker_active = False
        self._goal_queued = False
        self._goal_backgrounded = False
        self._goal_started_at = 0.0
        self._goal_stage = ""
        self._ghost_enabled = False
        self._show_tool_activity = False
        self._palette = CommandPalette()
        self._ghost_panel_lines: list[tuple[str, str]] = []
        self._ghost_pending_route: GhostRouteSuggestion | None = None
        self._ghost_history: list[dict[str, Any]] = []
        self._ghost_history_index: int | None = None
        self._ghost_panel_open = False
        self._ghost_expanded = False
        self._ghost_scroll_from_bottom = 0
        self._goal_board_scroll_from_bottom = 0
        self._board_scroll_from_bottom = 0
        self._ghost_request_seq = 0
        self._ghost_active_request_id = 0
        self._ghost_input_mode = False
        self._ghost_unread_count = 0
        self._prt_done_unread = False
        self._ghost_route_flash_text = ""
        self._ghost_route_flash_until = 0.0
        self._goal_done_unread = False
        self._input_history = InMemoryHistory()
        # _terminal_columns()/_terminal_rows() are inherited from
        # TerminalMetricsMixin (via the display/response/transcript mixins).


# ── Entrypoints ──────────────────────────────────────────────────────

def should_open_backend_monitor() -> bool:
    from .terminal_loop import should_open_backend_monitor as _should_open_backend_monitor

    return _should_open_backend_monitor()


def run_main_loop(agent: Any, gateway: Any, console, has_rich: bool):
    from .terminal_loop import run_main_loop as _run_main_loop

    return _run_main_loop(agent, gateway, console, has_rich)


def _record_session(agent: Any):
    record_session(agent)
