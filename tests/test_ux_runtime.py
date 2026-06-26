from __future__ import annotations

from types import SimpleNamespace

from UX.controller import UxCallbacks
from UX.runtime.adapters import lanes_from_runtime
from UX.runtime import RuntimeHandle


def test_runtime_handle_run_turn_wires_gateway_callbacks():
    events: list[str] = []

    class FakeGateway:
        last_task_board = None

        def run_turn(self, text, **kwargs):
            events.append(text)
            kwargs["on_activity"]("thinking")
            kwargs["on_token"]("x")
            kwargs["on_assistant_text"]("interim")
            kwargs["on_board_update"]("rich")
            kwargs["on_board_event"]({"type": "board"})
            return "final"

    changed = []
    callbacks = UxCallbacks(on_change=lambda: changed.append("changed"))
    handle = RuntimeHandle(
        agent=SimpleNamespace(project_cwd="repo", runtime_home="home", provider_name="provider", model="model"),
        gateway=FakeGateway(),
    )

    assert handle.run_turn("hello", callbacks=callbacks) == "final"
    assert events == ["hello"]
    assert callbacks.activity == "receiving answer"
    assert callbacks.assistant_chunks == ["interim"]
    assert len(changed) >= 4


def test_runtime_handle_snapshot_uses_display_adapter():
    board = SimpleNamespace(summary=lambda: {"tasks": [{"id": "1", "title": "Task", "status": "active"}]}, open_count=lambda: 1)
    handle = RuntimeHandle(
        agent=SimpleNamespace(
            project_cwd="repo",
            runtime_home="home",
            provider_name="provider",
            model="model",
            messages=[{"role": "assistant", "content": "answer"}],
        ),
        gateway=SimpleNamespace(last_task_board=board),
    )

    snapshot = handle.snapshot()

    assert snapshot.project == "repo"
    assert snapshot.board[0].title == "Task"
    assert snapshot.transcript[0].text == "answer"


def test_runtime_lanes_use_reported_values_without_inventing_defaults():
    lanes = lanes_from_runtime(
        SimpleNamespace(provider_name="provider", model="model"),
        SimpleNamespace(lanes=[{"name": "execution", "status": "running", "detail": "real lane"}], last_task_board=None),
    )

    assert [lane.name for lane in lanes] == ["execution"]
    assert lanes[0].detail == "real lane"


def test_runtime_lanes_fall_back_to_neutral_runtime_row():
    lanes = lanes_from_runtime(SimpleNamespace(provider_name="provider", model="model"), SimpleNamespace(last_task_board=None))

    assert [lane.name for lane in lanes] == ["runtime"]
    assert lanes[0].detail == "no active lanes reported"
