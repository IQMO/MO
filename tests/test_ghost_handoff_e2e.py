from types import SimpleNamespace

from core.ghost.ghost_routing import GhostRouteSuggestion
from core.workers import ensure_worker_registry
from interface.main_terminal import MoTui


class _FakeOutput:
    def get_size(self):
        return SimpleNamespace(rows=20, columns=100)


class _FakeApp:
    output = _FakeOutput()

    def invalidate(self):
        pass


def _make_tui():
    agent = SimpleNamespace(session=SimpleNamespace(token_log=[]), provider_name="mock", model="mock", config={})
    gateway = SimpleNamespace()
    tui = MoTui(agent, gateway)
    tui._app = _FakeApp()
    return tui


def test_ghost_to_main_handoff_registers_receiver_running(monkeypatch):
    tui = _make_tui()
    started = []
    monkeypatch.setattr(tui, "_handle_input", lambda objective: started.append(objective))

    text = tui._execute_ghost_route(GhostRouteSuggestion("main", "review UI", "idle"))

    assert started == ["review UI"]
    assert text == "MO routed"
    records = tui.agent.workers.recent()
    assert records[-1].kind == "main"
    assert records[-1].state == "running"


def test_route_confirmation_uses_enhanced_pending_objective_and_starts_transition(monkeypatch):
    tui = _make_tui()
    started = []
    transitions = []
    tui._ghost_pending_route = GhostRouteSuggestion("main", "him lets dig into lgos and meterics and performance", "idle")
    monkeypatch.setattr(tui, "_handle_input", lambda objective: started.append(objective))
    monkeypatch.setattr(tui, "_start_ghost_route_transition", lambda user_text, response: transitions.append((user_text, response)))

    assert tui._handle_ghost_route_reply("yes") is True

    # After simplification: passes through original objective (no longer stamps "Audit...")
    assert "him lets dig into lgos" in started[0]
    assert transitions and transitions[0][1] == "MO routed"


def test_ghost_to_queue_handoff_registers_waiting(monkeypatch):
    tui = _make_tui()
    tui.busy = True
    queued = []
    monkeypatch.setattr(tui, "_queue_input", lambda objective, **kwargs: queued.append((objective, kwargs)))

    text = tui._execute_ghost_route(GhostRouteSuggestion("queue", "fix bug", "busy"))

    assert queued and queued[0][0] == "fix bug"
    assert queued[0][1]["worker_id"]
    assert text == "MO queued"
    records = tui.agent.workers.recent()
    assert records[-1].kind == "queue"
    assert records[-1].state == "accepted"


def test_ghost_to_background_handoff_blocks_conflicting_worker():
    tui = _make_tui()
    ensure_worker_registry(tui.agent).create(kind="worker", source="ghost", route="background", objective="edit core/agent.py", state="running")

    text = tui._execute_ghost_route(GhostRouteSuggestion("background", "review `core/agent.py`", "independent"))

    assert "Worker unavailable" in text
    assert "workspace conflict" in text
    assert tui.agent.workers.recent()[-1].state == "blocked"


def test_ghost_to_background_handoff_registers_worker(monkeypatch):
    tui = _make_tui()
    started = []

    def fake_start(objective, worker_id=None):
        started.append((objective, worker_id))
        tui.agent.workers.update(worker_id, "running", "fake worker running")
        return tui.agent.workers.get(worker_id)

    monkeypatch.setattr(tui, "_start_background_worker_from_ghost", fake_start)

    text = tui._execute_ghost_route(GhostRouteSuggestion("background", "scan docs", "independent"))

    assert started and started[0][0] == "scan docs"
    assert text == "Worker routed"
    records = tui.agent.workers.recent()
    assert records[-1].kind == "worker"
    assert records[-1].state == "running"
