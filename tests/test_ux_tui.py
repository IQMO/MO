from __future__ import annotations

from UX.controller import PreviewBackend, UxController
from UX.shell import tui


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
    assert created["layout"].current_buffer is not None


def test_tui_landing_contract_is_prompt_first_not_dashboard():
    source = (tui.LOGO_LINES, tui.SIGNAL_LINES)
    joined = "\n".join("\n".join(lines) for lines in source)

    assert "MO" in joined
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
