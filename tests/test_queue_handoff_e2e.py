from types import SimpleNamespace

from core.workers import WorkerRegistry
from interface.main_terminal import MoTui


class _FakeOutput:
    def get_size(self):
        return SimpleNamespace(rows=20, columns=100)


class _FakeApp:
    output = _FakeOutput()

    def invalidate(self):
        pass


def _make_tui():
    agent = SimpleNamespace(session=SimpleNamespace(token_log=[]), provider_name="mock", model="mock", config={}, workers=WorkerRegistry())
    gateway = SimpleNamespace()
    tui = MoTui(agent, gateway)
    tui._app = _FakeApp()
    return tui


def test_queued_input_promotes_to_same_worker_record(monkeypatch):
    tui = _make_tui()
    record = tui.agent.workers.create(kind="queue", source="ghost", route="queue", objective="fix queued bug", state="accepted")
    handled = []
    monkeypatch.setattr(tui, "_handle_input", lambda text: handled.append(text))

    tui._queue_input("fix queued bug", worker_id=record.id, source="ghost")
    tui._process_next_queued_input()

    assert handled == ["fix queued bug"]
    assert tui._active_main_worker_id == record.id
    assert tui.agent.workers.get(record.id).state == "running"
    assert tui.agent.workers.get(record.id).note == "queued item promoted to MO"


def test_queued_goal_promotes_registered_goal_worker(monkeypatch):
    tui = _make_tui()
    record = tui.agent.workers.create(kind="goal", source="user", route="background", objective="review docs", state="accepted")
    started = []
    monkeypatch.setattr(tui, "_start_goal_thread", lambda: started.append(True))

    tui._pending_inputs.put({"text": "[GOAL_START]", "worker_id": record.id})
    tui._process_next_queued_input()

    assert started == [True]
    assert tui.agent._goal_worker_id == record.id
    assert tui.agent.workers.get(record.id).state == "running"
