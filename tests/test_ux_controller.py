from __future__ import annotations

from types import SimpleNamespace

from UX.controller import PreviewBackend, RuntimeBackend, UxCallbacks, UxController, read_only_snapshot
from UX.models import BoardRow, SessionSnapshot, TranscriptItem


def test_preview_controller_captures_input_without_runtime():
    controller = UxController(PreviewBackend())

    result = controller.handle_input("hello from preview")
    snapshot = controller.snapshot()

    assert "UX preview captured locally" in result
    assert snapshot.busy is False
    assert snapshot.transcript[-2].speaker == "user"
    assert snapshot.transcript[-1].speaker == "ux"


def test_controller_on_change_callback_is_used():
    changed = []
    controller = UxController(PreviewBackend())

    controller.handle_input("hello", on_change=lambda: changed.append("changed"))

    assert changed


def test_preview_controller_exit_is_local():
    controller = UxController(PreviewBackend())

    assert controller.handle_input("/exit") == "[EXIT]"
    assert controller.exit_requested is True


def test_runtime_backend_uses_handle_run_turn_and_snapshots():
    calls: list[tuple[str, bool]] = []

    class FakeHandle:
        def snapshot(self):
            return SessionSnapshot(
                project="repo",
                provider="provider",
                model="model",
                transcript=(TranscriptItem("user", "existing"),),
            )

        def run_turn(self, text, *, callbacks=None):
            calls.append((text, isinstance(callbacks, UxCallbacks)))
            callbacks.on_activity("thinking")
            callbacks.on_assistant_text("interim")
            return "done"

    backend = RuntimeBackend(FakeHandle())
    result = backend.submit("real turn", callbacks=UxCallbacks())
    snapshot = backend.snapshot()

    assert result == "done"
    assert calls == [("real turn", True)]
    assert snapshot.notice == "MO runtime turn finished"
    assert snapshot.transcript[-1].text == "done"


def test_runtime_backend_hides_raw_exception_details():
    class FakeHandle:
        def snapshot(self):
            return SessionSnapshot(project="repo")

        def run_turn(self, text, *, callbacks=None):
            raise PermissionError("C:\\Users\\Admin\\.mo\\memory\\profile\\facts.md")

    backend = RuntimeBackend(FakeHandle())
    result = backend.submit("real turn", callbacks=UxCallbacks())
    snapshot = backend.snapshot()

    assert "PermissionError" in result
    assert "Details hidden" in result
    assert "C:\\Users" not in result
    assert ".mo" not in result
    assert "facts.md" not in result
    assert snapshot.notice == result


def test_read_only_snapshot_fills_empty_board_and_lanes():
    handle = SimpleNamespace(snapshot=lambda: SessionSnapshot(project="repo"))
    snapshot = read_only_snapshot(handle)

    assert snapshot.board == (BoardRow("readonly", "Idle - no active runtime task board", "pending", kind="read-only"),)
    assert snapshot.lanes[0].name == "runtime"
