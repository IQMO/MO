from __future__ import annotations

import threading

from UX.controller import PreviewBackend, UxController
from UX.shell import tui
from UX.models import BoardRow, LaneSnapshot, SessionSnapshot, TranscriptItem


def test_run_tui_builds_fullscreen_prompt_toolkit_app(monkeypatch):
    created = {}

    class FakeApplication:
        def __init__(self, **kwargs):
            created.update(kwargs)

        def run(self):
            return None

    monkeypatch.setattr(tui, "Application", FakeApplication)

    tui.run_tui(UxController(PreviewBackend()))

    assert created["full_screen"] is True
    assert created["mouse_support"] is False
    assert created["paste_mode"] is True
    assert created["refresh_interval"] == tui.ANIMATION_INTERVAL_SECONDS
    assert created["layout"].current_buffer is not None
    assert created["layout"].current_buffer.multiline() is True
    assert created["layout"].current_buffer.enable_history_search() is True


def test_tui_landing_contract_is_prompt_first_not_dashboard():
    controller = UxController(PreviewBackend())
    signal_text = "".join(fragment for _style, fragment in tui._signal_field_fragments(0, 120))
    hero_text = "".join(fragment for _style, fragment in tui._hero_fragments(controller, tui.TuiAnimation()))
    joined = "\n".join(("".join(tui.LOGO_LINES), signal_text, hero_text))

    assert "MO UX" in joined
    assert "Agent Lanes" not in joined
    assert "Ops Rail" not in joined
    assert "Task Board" not in joined


def test_tui_main_fragments_show_landing_before_transcript(monkeypatch):
    from UX.shell.app import preview_landing_snapshot

    monkeypatch.setattr(tui, "_terminal_width", lambda default=120: 120)
    controller = UxController(PreviewBackend(preview_landing_snapshot()))
    text = "".join(fragment for _style, fragment in tui._main_fragments(controller))

    assert "MO UX" in text
    assert "Type a message" not in text
    assert "Build the next interface" not in text


def test_tui_work_fragments_show_compact_runtime_truth(monkeypatch):
    monkeypatch.setattr(tui, "_terminal_width", lambda default=120: 120)
    snapshot = SessionSnapshot(
        project="repo",
        provider="provider",
        model="model",
        lanes=(LaneSnapshot("execution", "running", "turn active", "model"),),
        board=(BoardRow("1", "Verify task board rendering", "active", kind="verify"),),
        transcript=(TranscriptItem("user", "hello"), TranscriptItem("mo", "working on it")),
    )
    controller = UxController(PreviewBackend(snapshot))

    text = "".join(fragment for _style, fragment in tui._main_fragments(controller, tui.TuiAnimation(), tui.TuiSessionState()))

    assert "AGENTS" in text
    assert "TASKS" in text
    assert "TRANSCRIPT" in text
    assert "Verify task board rendering" in text
    assert "Ops Rail" not in text


def test_tui_command_palette_visible_for_slash_input(monkeypatch):
    monkeypatch.setattr(tui, "_terminal_width", lambda default=120: 120)
    fragments = tui._command_palette_fragments("/sta", tui.TuiSessionState())
    text = "".join(fragment for _style, fragment in fragments)

    assert "Command Palette" in text
    assert "/status" in text


def test_tui_submit_runs_backend_in_background():
    started = threading.Event()
    release = threading.Event()
    invalidations = []

    class SlowBackend:
        name = "slow"

        def snapshot(self):
            return SessionSnapshot()

        def submit(self, text, callbacks=None):
            started.set()
            release.wait(timeout=2)
            callbacks.on_activity("done")
            return f"done:{text}"

    ui_state = tui.TuiSessionState()
    controller = UxController(SlowBackend())

    thread = tui._submit_in_background(
        controller,
        ui_state,
        "hello",
        invalidate=lambda: invalidations.append("invalidate"),
        exit_app=lambda: None,
    )

    assert thread is not None
    assert started.wait(timeout=2)
    assert ui_state.turn_running is True
    release.set()
    thread.join(timeout=2)
    assert ui_state.turn_running is False
    assert controller.last_result == "done:hello"
    assert invalidations


def test_tui_signal_animation_changes_between_frames():
    frame_0 = "".join(fragment for _style, fragment in tui._signal_field_fragments(0, 120))
    frame_1 = "".join(fragment for _style, fragment in tui._signal_field_fragments(1, 120))

    assert frame_0 != frame_1
    assert len(frame_0) == len(frame_1)
    assert frame_0.count("\n") == tui.SIGNAL_FIELD_HEIGHT


def test_tui_status_rail_animates_between_frames():
    controller = UxController(PreviewBackend())
    first = tui.TuiAnimation()
    second = tui.TuiAnimation()
    second.advance()

    frame_0 = "".join(fragment for _style, fragment in tui._status_fragments(controller, first))
    frame_1 = "".join(fragment for _style, fragment in tui._status_fragments(controller, second))

    assert frame_0 != frame_1


def test_tui_animation_advances_frame():
    animation = tui.TuiAnimation()

    animation.advance()
    animation.advance()

    assert animation.frame == 2
