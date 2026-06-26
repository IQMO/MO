from __future__ import annotations

import pytest
from rich.console import Console

import UX.shell.app as app
from UX.models import SessionSnapshot
from UX.controller import PreviewBackend, UxController


def test_runtime_unavailable_exits_cleanly(monkeypatch):
    from UX.runtime import RuntimeUnavailable

    class FakeConsole:
        def __init__(self):
            self.messages = []

        def print(self, value, *args, **kwargs):
            self.messages.append(str(value))

    console = FakeConsole()
    monkeypatch.setattr(app, "_runtime_handle", lambda: (_ for _ in ()).throw(RuntimeUnavailable("missing config")))

    with pytest.raises(SystemExit) as exc:
        app._create_runtime_or_exit(console)

    assert exc.value.code == 2
    assert console.messages == ["UX runtime unavailable: missing config"]


def test_width_defaults_to_terminal_auto():
    args = app.parse_args([])

    assert args.width is None


def test_preview_default_runs_real_tui(monkeypatch):
    called = []

    monkeypatch.setattr(app, "_run_tui", lambda controller: called.append(controller.snapshot().project))

    app.main([])

    assert called == ["E:\\MO-clean"]


def test_preview_landing_snapshot_starts_without_demo_transcript():
    snapshot = app.preview_landing_snapshot()

    assert snapshot.transcript == ()
    assert snapshot.board == ()
    assert snapshot.lanes == ()
    assert snapshot.notice == ""


def test_preview_once_uses_static_render(monkeypatch):
    called = []
    console = Console(record=True, width=90, color_system=None)

    monkeypatch.setattr(app, "_run_tui", lambda controller: called.append("tui"))
    monkeypatch.setattr(app, "_console_for_width", lambda width=None, **kwargs: console)

    app.main(["--once"])

    assert called == []
    assert "Composer" in console.export_text(clear=False)


def test_once_snapshot_render_preserves_read_only_hint():
    console = Console(record=True, width=90, color_system=None)
    snapshot = SessionSnapshot(project="repo", composer_hint="read-only mode; no messages are sent")

    app.UxPreviewApp(console).run(once=True, snapshot=snapshot)

    text = console.export_text(clear=False)
    assert "read-only mode; no messages are sent" in text
    assert "preview local; run" not in text


def test_single_message_renders_result_and_advances_preview():
    text = app.run_single_message(UxController(PreviewBackend()), "hello", width=90)

    assert "hello" in text
    assert "UX preview captured locally" in text


def test_preview_message_cli_starts_from_landing_snapshot(monkeypatch):
    console = Console(record=True, width=90, color_system=None)
    monkeypatch.setattr(app, "_console_for_width", lambda width=None, **kwargs: console)

    app.main(["--message", "hi"])

    text = console.export_text(clear=False)
    assert "hi" in text
    assert "UX preview captured locally" in text
    assert "Build the next interface" not in text


def test_read_only_rejects_message(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        app.main(["--read-only", "--message", "hello"])

    assert str(exc.value) == "--message cannot be used with --read-only"
