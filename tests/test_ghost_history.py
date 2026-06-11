import json

from core.backend_monitor import BackendMonitor, set_monitor
from interface.ghost_history import GHOST_MODE_HINT, GhostHistoryMixin, ghost_history_item_lines, ghost_history_panel_lines


class GhostHistoryHarness(GhostHistoryMixin):
    def __init__(self):
        self._ghost_history = []
        self._ghost_history_index = None
        self._ghost_panel_lines = []
        self._ghost_scroll_from_bottom = 99
        self._ghost_panel_open = False
        self._ghost_unread_count = 3
        self._app = None


def test_ghost_history_item_lines_styles_notifications_as_hints():
    assert ghost_history_item_lines({"kind": "reply", "user": "ask", "response": "answer"}) == [
        ("class:ghost-user", "ask"),
        ("class:ghost-response", "answer"),
    ]
    assert ghost_history_item_lines({"kind": "notification", "response": "done"}) == [("class:ghost-hint", "done")]


def test_ghost_history_panel_lines_keeps_latest_six_items_or_hint():
    assert ghost_history_panel_lines([]) == [("class:ghost-hint", GHOST_MODE_HINT)]

    history = [{"kind": "reply", "user": f"u{index}", "response": f"r{index}"} for index in range(8)]
    rendered = ghost_history_panel_lines(history)

    assert ("class:ghost-user", "u0") not in rendered
    assert ("class:ghost-user", "u2") in rendered
    assert ("class:ghost-response", "r7") in rendered


def test_ghost_history_mixin_records_caps_and_navigates_visible_entries(monkeypatch):
    calls = []
    monkeypatch.setattr("interface.ghost_history.append_ghost_audit", lambda kind, **kwargs: calls.append((kind, kwargs)))
    harness = GhostHistoryHarness()

    for index in range(22):
        harness._record_ghost_history("reply", f"q{index}", f"a{index}")

    assert len(harness._ghost_history) == 20
    assert harness._ghost_history[0]["user"] == "q2"
    assert calls[-1][0] == "reply"

    assert harness._show_ghost_history(-1) is True
    assert harness._ghost_panel_open is True
    assert harness._ghost_scroll_from_bottom == 0
    assert harness._ghost_unread_count == 0
    assert ("class:ghost-user", "q20") in harness._ghost_panel_lines


def test_ghost_history_emits_backend_monitor_event(tmp_path, monkeypatch):
    monkeypatch.setattr("interface.ghost_history.append_ghost_audit", lambda *_args, **_kwargs: None)
    monitor = BackendMonitor(tmp_path / "backend_monitor.jsonl")
    set_monitor(monitor)
    try:
        harness = GhostHistoryHarness()
        harness._record_ghost_history("reply", "is mo stuck?", "looks stuck", route="steer")
    finally:
        set_monitor(None)

    rows = [json.loads(line) for line in monitor.path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["type"] == "ghost_event"
    assert rows[-1]["payload"]["kind"] == "reply"
    assert rows[-1]["payload"]["route"] == "steer"
    assert rows[-1]["payload"]["user_preview"] == "is mo stuck?"



def test_show_ghost_history_returns_false_when_empty():
    assert GhostHistoryHarness()._show_ghost_history(1) is False
