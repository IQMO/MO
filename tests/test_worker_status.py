"""Tests for interface.worker_status — WorkerStatusMixin."""
from types import SimpleNamespace

from interface.worker_status import WorkerStatusMixin


def _make_mixin(**overrides):
    """Build a WorkerStatusMixin with sensible defaults, override as needed."""

    class Mock(WorkerStatusMixin):
        busy = False
        _goal_worker_active = False
        _goal_backgrounded = False
        _ghost_enabled = False
        _ghost_panel_lines = []
        agent = SimpleNamespace(workers=None)

        def _goal_elapsed_text(self):
            return "12s"

    obj = Mock()

    for attr, val in overrides.items():
        setattr(obj, attr, val)

    return obj


def test_idle_when_nothing_active():
    obj = _make_mixin()
    text = obj._workers_status_text()
    assert text == ""


def test_goal_active_in_foreground_shows_compact_goal():
    obj = _make_mixin(_goal_worker_active=True, _goal_backgrounded=False)
    text = obj._workers_status_text()
    assert "Goal" in text
    assert "12s" not in text


def test_goal_backgrounded_no_watching():
    obj = _make_mixin(_goal_worker_active=True, _goal_backgrounded=True)
    text = obj._workers_status_text()
    assert "Goal" in text
    assert "watching" not in text
    assert "12s" not in text


def test_ghost_panel_open_with_thinking():
    obj = _make_mixin(
        _ghost_enabled=True,
        _ghost_panel_lines=[("class:ghost-thinking", "thinking...")],
    )
    text = obj._workers_status_text()
    assert "Ghost active" in text


def test_ghost_panel_open_idle():
    obj = _make_mixin(_ghost_enabled=True, _ghost_panel_lines=[])
    text = obj._workers_status_text()
    assert "Ghost" in text
    assert "Ghost active" not in text


def test_busy_main():
    obj = _make_mixin(busy=True)
    text = obj._workers_status_text()
    assert "MO" in text


def test_three_active_gets_listed_not_collapsed():
    """3 active workers are joined, not collapsed (only >3 collapses)."""
    obj = _make_mixin(busy=True, _goal_worker_active=True, _ghost_enabled=True)
    text = obj._workers_status_text()
    assert "MO" in text
    assert "Goal" in text
    assert "Ghost" in text
    assert " · " in text


def test_more_than_three_active_collapses_to_count():
    """>3 workers collapse to 'N active'."""
    obj = _make_mixin(busy=True, _goal_worker_active=True, _ghost_enabled=True)
    # add a fake registry with 2 extra workers so total exceeds 3
    fake_registry = SimpleNamespace(
        active=lambda: [
            SimpleNamespace(kind="worker"),
            SimpleNamespace(kind="worker"),
        ]
    )
    obj.agent.workers = fake_registry
    text = obj._workers_status_text()
    # registry adds 2 workers + goal + ghost + busy main = 5
    assert "5 active" in text


def test_workers_from_registry():
    fake_registry = SimpleNamespace(
        active=lambda: [
            SimpleNamespace(kind="goal"),
            SimpleNamespace(kind="worker"),
            SimpleNamespace(kind="main", state="running"),
        ]
    )
    obj = _make_mixin()
    obj.agent.workers = fake_registry
    text = obj._workers_status_text()
    assert "Goal" in text
    assert "12s" not in text
    assert "Background" in text
    assert "MO" in text


def test_registry_error_graceful():
    fake_registry = SimpleNamespace(active=lambda: (_ for _ in ()).throw(Exception("boom")))
    obj = _make_mixin(busy=True)
    obj.agent.workers = fake_registry
    text = obj._workers_status_text()
    assert text.startswith("Active ")  # still produces output
