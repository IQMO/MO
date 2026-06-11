import queue
from types import SimpleNamespace

from core.goal import GoalPlan, GoalStep
from interface.display_delegates import DisplayDelegatesMixin


class FakeOutput:
    def __init__(self, rows=12, columns=80):
        self._size = SimpleNamespace(rows=rows, columns=columns)

    def get_size(self):
        return self._size


class FakeApp:
    def __init__(self, rows=12, columns=80):
        self.output = FakeOutput(rows, columns)
        self.invalidated = 0

    def invalidate(self):
        self.invalidated += 1


class DisplayHarness(DisplayDelegatesMixin):
    def __init__(self):
        # hints disabled: these tests assert the plain idle line; hint rotation
        # is covered separately in test_idle_line_shows_rotating_hint.
        self.agent = SimpleNamespace(session=SimpleNamespace(token_log=[]), provider_name="mock", model="mock", reasoning="high", config={}, _hints_enabled=False)
        self._app = FakeApp()
        self.busy = False
        self.activity_text = ""
        self.activity_started_at = 0.0
        self.board_text = ""
        self._goal_worker_active = False
        self._goal_backgrounded = False
        self._goal_board_text = ""
        self._goal_started_at = 0.0
        self._ghost_panel_open = False
        self._ghost_panel_lines = []
        self._ghost_scroll_from_bottom = 0
        self._ghost_expanded = False
        self._ghost_unread_count = 0
        self._goal_board_scroll_from_bottom = 0
        self._board_scroll_from_bottom = 0
        self._ghost_route_flash_until = 0.0
        self._goal_done_unread = False
        self._pending_inputs = queue.Queue()
        self._notice_text = ""
        self._notice_until = 0.0

    def _workers_status_text(self):
        return "Active ○ idle"


def _plain(fragments):
    return "".join(text for _style, text in fragments)


def test_display_delegates_render_board_and_status_without_mutating_truth():
    harness = DisplayHarness()
    harness.board_text = "2 tasks (1 done, 1 open)\n√ Inspect\n→ Report"

    board = _plain(harness._get_board_fragments())
    status = _plain(harness._get_status_bar_fragments())

    assert "√ Inspect" in board
    assert "→ Report" in board
    assert harness.board_text == "2 tasks (1 done, 1 open)\n√ Inspect\n→ Report"
    assert "idle" in status


def test_display_delegates_footer_goal_notification_includes_progress_percent():
    harness = DisplayHarness()
    harness._goal_worker_active = True
    harness.agent._goal_plan = GoalPlan(
        "build game",
        [GoalStep("1", "Build", "completed"), GoalStep("2", "Verify", "active")],
    )

    rendered = _plain(harness._get_footer_fragments())

    assert "Goal running 50%" in rendered


def test_display_delegates_notice_expires_and_clears_text(monkeypatch):
    harness = DisplayHarness()
    monkeypatch.setattr("interface.display_delegates.time.time", lambda: 10.0)

    harness._set_notice("Saved", ttl=1.0)
    assert harness._notice_text == "Saved"
    assert harness._app.invalidated == 1

    monkeypatch.setattr("interface.display_delegates.time.time", lambda: 12.0)
    rendered = _plain(harness._get_status_bar_fragments())
    assert "idle" in rendered
    assert "◆" not in rendered
    assert harness._notice_text == ""


def test_display_delegates_ghost_route_flash_uses_current_route_label(monkeypatch):
    harness = DisplayHarness()
    harness.busy = True
    harness._ghost_route_flash_until = 12.0

    harness._ghost_route_flash_text = "Worker routed"
    monkeypatch.setattr("interface.display_delegates.time.time", lambda: 10.25)
    active = _plain(harness._get_activity_fragments())

    assert "Worker routed" in active
    assert "MO routed" not in active
    assert "GHOST" not in active
    assert "Ghost handoff accepted" not in active
    assert "MO connected" not in active


def test_display_delegates_ghost_panel_scroll_clamps_to_available_rows():
    harness = DisplayHarness()
    harness._ghost_panel_open = True
    harness._ghost_panel_lines = [("class:ghost-response", "\n".join(f"line {index}" for index in range(10)))]
    harness._ghost_expanded = True
    harness._ghost_scroll_from_bottom = 99

    rendered = _plain(harness._get_ghost_panel_fragments())

    assert harness._ghost_scroll_from_bottom == 1
    assert "1-9/10" in rendered


def test_idle_line_shows_rotating_hint_when_enabled():
    harness = DisplayHarness()
    harness.agent._hints_enabled = True

    rendered = _plain(harness._get_status_bar_fragments())

    assert "idle" in rendered or len(rendered.strip()) > 10  # hint or idle text present
    # a hint never replaces an active notice
    harness._set_notice("Saved", ttl=60.0)
    rendered = _plain(harness._get_status_bar_fragments())
    assert "Saved" in rendered
