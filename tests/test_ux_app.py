from __future__ import annotations

import pytest
from rich.console import Console

import UX.app as app
from UX.models import SessionSnapshot


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


def test_once_snapshot_render_preserves_read_only_hint():
    console = Console(record=True, width=90, color_system=None)
    snapshot = SessionSnapshot(project="repo", composer_hint="read-only mode; no messages are sent")

    app.UxPreviewApp(console).run(once=True, snapshot=snapshot)

    text = console.export_text(clear=False)
    assert "read-only mode; no messages are sent" in text
    assert "preview only; /exit closes" not in text
