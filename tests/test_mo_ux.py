import queue
from types import SimpleNamespace

from prompt_toolkit.document import Document

import interface.input as input_ui
from interface.main_terminal import MoTui
from interface.tui_app import startup_header_fragment_lines


def test_drain_queued_inputs_preserves_prompt_order():
    q = queue.Queue()
    q.put(" second prompt ")
    q.put("")
    q.put("third prompt")

    assert input_ui.drain_queued_inputs(q) == ["second prompt", "third prompt"]
    assert q.empty()


def test_live_input_worker_queues_entered_text(monkeypatch):
    class FakeInput:
        def __init__(self):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read_keys(self):
            self.calls += 1
            if self.calls == 1:
                return [SimpleNamespace(data="n"), SimpleNamespace(data="e"), SimpleNamespace(data="x"), SimpleNamespace(data="t"), SimpleNamespace(data="\r")]
            raise RuntimeError("stop")

    q = queue.Queue()
    stop = type("Stop", (), {"is_set": lambda self: False})()
    monkeypatch.setattr(input_ui, "create_input", lambda: FakeInput())

    buf = []
    input_ui.live_key_worker(q, stop, buf)

    assert q.get_nowait() == "next"
    assert q.empty()





def test_slash_command_completer_lists_commands():
    completer = input_ui.SlashAndPathCompleter()

    completions = list(completer.get_completions(Document("/mo"), None))

    assert [completion.text for completion in completions] == ["/model", "/moon"]


def test_tui_startup_header_keeps_logo_and_orientation():
    agent = SimpleNamespace(
        provider_name="mock-local",
        model="mock-model",
        project_cwd="E:/project",
        config={"heartbeat": {"enabled": False}, "telegram": {"enabled": False}},
    )
    rows = startup_header_fragment_lines(agent, SimpleNamespace())
    rendered = ["".join(text for _style, text in row) for row in rows]

    assert rendered[0].startswith("  █   █   ███")
    assert "MO v1.0 — mock-local / mock-model" in rendered[0]
    assert "Project: E:/project" in rendered[1]
    assert "Runtime: heartbeat disabled · telegram disabled" in rendered[2]
    assert "Type /help for commands, /status for details" in rendered[3]
    assert "Home:" not in "\n".join(rendered)


def test_tui_startup_header_adds_only_actionable_hidden_state_hints():
    agent = SimpleNamespace(
        provider_name="mock-local",
        model="mock-model",
        project_cwd="E:/project",
        config={"heartbeat": {"enabled": False}, "telegram": {"enabled": False}},
        _pending_interrupted_work={"user": "finish previous edit"},
        last_fallback_notice="Switched to fallback/model: raw_tool_payload",
    )
    rows = startup_header_fragment_lines(agent, SimpleNamespace())
    rendered = "\n".join("".join(text for _style, text in row) for row in rows)

    assert "paused work available" in rendered
    assert "provider fallback active" in rendered
    assert "raw_tool_payload" not in rendered


def test_goal_worker_does_not_block_main_chat_queueing():
    tui = MoTui(agent=SimpleNamespace(), gateway=SimpleNamespace())
    tui.busy = False
    tui._goal_worker_active = True

    assert tui._work_active() is False

    tui.busy = True
    assert tui._work_active() is True



# (rich_view shim removed 2026-06-10: no live path used it; the live working
# indicator is covered by activity_fragments tests.)
