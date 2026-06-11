from types import SimpleNamespace

from core.tasking.task_board import TaskBoard, TaskItem
import interface.native_terminal as native_terminal


class DummyAgent:
    provider_name = "mock"
    model = "model"
    active_lane = None

    def __init__(self):
        self.commands = []
        self.autosaved = 0

    def process_slash_command(self, text):
        self.commands.append(text)
        if text == "/status":
            return "status ok"
        if text == "/prt":
            return "[PRT STARTED] Reviewing HEAD in background..."
        if text.startswith("/vs05"):
            self._slash_pending_input = "start VS05" + text[len("/vs05"):]
            return "[RUN_TURN]"
        if text == "/exit":
            return "[EXIT]"
        return None

    def autosave_session(self):
        self.autosaved += 1


def test_native_terminal_prints_final_board_once_after_normal_turn(monkeypatch, capsys):
    inputs = iter(["build thing", "/exit"])
    agent = DummyAgent()
    gateway = SimpleNamespace(calls=[])

    def fake_read(_agent, _console):
        return next(inputs)

    def fake_run_turn(text):
        gateway.calls.append(text)
        gateway.last_task_board = TaskBoard(tasks=[TaskItem("1", "Inspect", "completed"), TaskItem("2", "Report", "completed")])
        return "answer ok"

    gateway.last_task_board = None
    gateway.run_turn = fake_run_turn
    monkeypatch.setattr(native_terminal, "read_native_user_input", fake_read)

    native_terminal.run_native_terminal_loop(agent, gateway, console=None)

    output = capsys.readouterr().out
    assert "Project:" in output
    assert "Runtime:" in output
    assert "Home:" not in output
    assert "workers clear" not in output
    assert output.count("answer ok") == 1
    assert output.count("2 tasks (2 done, 0 open)") == 1
    assert "Inspect" in output
    assert gateway.calls == ["build thing"]


def test_native_terminal_prints_background_completion_notice(monkeypatch, capsys):
    inputs = iter(["/prt", "/exit"])
    agent = DummyAgent()
    gateway = SimpleNamespace(calls=[], last_task_board=None, run_turn=lambda text: "")
    calls = {"count": 0}

    def fake_read(_agent, _console):
        value = next(inputs)
        if calls["count"] == 1:
            _agent._native_async_notice("PRT completed: PRT finished: 5.0/5.0 · detail /status")
        calls["count"] += 1
        return value

    monkeypatch.setattr(native_terminal, "read_native_user_input", fake_read)

    native_terminal.run_native_terminal_loop(agent, gateway, console=None)

    output = capsys.readouterr().out
    assert "[PRT STARTED] Reviewing HEAD in background..." in output
    assert "PRT completed: PRT finished: 5.0/5.0 · detail /status" in output
    assert agent.commands == ["/prt", "/exit"]


def test_native_terminal_handles_known_slash_commands_as_control_actions(monkeypatch, capsys):
    inputs = iter(["/status", "hello", "/exit"])
    agent = DummyAgent()
    gateway = SimpleNamespace(calls=[], last_task_board=None)

    def fake_read(_agent, _console):
        return next(inputs)

    def fake_run_turn(text):
        gateway.calls.append(text)
        return "answer ok"

    gateway.run_turn = fake_run_turn
    monkeypatch.setattr(native_terminal, "read_native_user_input", fake_read)

    native_terminal.run_native_terminal_loop(agent, gateway, console=None)

    output = capsys.readouterr().out
    assert "Type /help for commands, /status for details, /exit to quit." in output
    assert "status ok" in output
    assert "answer ok" in output
    assert agent.commands == ["/status", "/exit"]
    assert gateway.calls == ["hello"]
    assert agent.autosaved == 1


def test_native_terminal_routes_vs05_slash_to_normal_turn(monkeypatch, capsys):
    inputs = iter(["/vs05 E:\\ref-a E:\\ref-b", "/exit"])
    agent = DummyAgent()
    gateway = SimpleNamespace(calls=[], last_task_board=None)

    def fake_read(_agent, _console):
        return next(inputs)

    def fake_run_turn(text):
        gateway.calls.append(text)
        return "vs05 started"

    gateway.run_turn = fake_run_turn
    monkeypatch.setattr(native_terminal, "read_native_user_input", fake_read)

    native_terminal.run_native_terminal_loop(agent, gateway, console=None)

    output = capsys.readouterr().out
    assert "vs05 started" in output
    assert gateway.calls == ["start VS05 E:\\ref-a E:\\ref-b"]
    assert agent.autosaved == 1
