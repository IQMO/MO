import queue
import threading
from types import SimpleNamespace

from interface.command_palette import CommandPalette
from interface.transcript_state import TranscriptStateMixin


class FakeOutput:
    def __init__(self, rows=8, columns=34):
        self._size = SimpleNamespace(rows=rows, columns=columns)

    def get_size(self):
        return self._size


class FakeApp:
    def __init__(self, rows=8, columns=34):
        self.output = FakeOutput(rows, columns)
        self.invalidated = 0

    def invalidate(self):
        self.invalidated += 1


class TranscriptHarness(TranscriptStateMixin):
    def __init__(self):
        self._lines = []
        self._snapshot = (("class:dim", ""),)
        self._dirty = False
        self._ui_lock = threading.RLock()
        self._transcript_scroll_from_bottom = 0
        self._app = FakeApp()
        self.busy = False
        self._goal_worker_active = False
        self.board_text = ""
        self._palette = CommandPalette()
        self._ghost_panel_open = False
        self._pending_inputs = queue.Queue()
        self._goal_backgrounded = False
        self._goal_board_text = ""

    def _visible_goal_board_text(self):
        return "" if self._goal_backgrounded else self._goal_board_text


def _plain(fragments):
    return "".join(text for _style, text in fragments)


def test_transcript_state_appends_and_preserves_manual_scroll_position():
    harness = TranscriptHarness()
    for index in range(12):
        harness._add("class:mo-response", f"line {index}")
    harness._scroll_transcript(4)
    before = _plain(harness._get_transcript())

    harness._add("class:dim", "background notice")
    after = _plain(harness._get_transcript())

    assert "background notice" not in after
    assert before.splitlines()[0] == after.splitlines()[0]
    assert harness._app.invalidated > 0


def test_transcript_state_clear_resets_snapshot_and_invalidates():
    harness = TranscriptHarness()
    harness._add("class:mo-response", "hello")

    harness._clear_transcript()

    assert harness._lines == []
    assert harness._logical_transcript_lines() == [[]]
    # bottom-anchor padding is whitespace-only; a cleared transcript shows nothing
    assert _plain(harness._get_transcript()).strip() == ""


def test_transcript_state_visible_height_accounts_for_live_panels():
    harness = TranscriptHarness()
    harness._app = FakeApp(rows=24, columns=80)
    harness.busy = True
    harness.board_text = "2 tasks\n→ Main"
    harness._goal_board_text = "1 tasks\n→ Goal"
    harness._palette.show()
    harness._ghost_panel_open = True

    assert harness._visible_transcript_height() == 1

    harness._ghost_expanded = True
    assert harness._visible_transcript_height() == 1
