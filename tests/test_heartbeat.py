from types import SimpleNamespace

from core.heartbeat import (
    _prune_heartbeat_ledger,
    build_surface_continuity_context,
    read_recent_heartbeats,
    record_heartbeat,
    render_heartbeat_status,
)
from core.session.session import Session


def test_prune_heartbeat_ledger_trims_to_recent(tmp_path):
    # Regression: the append-only ledger used to grow unbounded.
    path = tmp_path / "heartbeats.jsonl"
    path.write_text("\n".join('{"n": %d}' % i for i in range(5000)) + "\n", encoding="utf-8")

    _prune_heartbeat_ledger(path, max_lines=100, max_bytes=1000)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100
    assert lines[-1] == '{"n": 4999}'  # newest snapshots kept


class DummyAgent:
    provider_name = "test-provider"
    model = "test-model"
    config = {}

    def __init__(self):
        self.session = Session("system")
        self._sessions = SimpleNamespace(current_name="main")

    def _provider_context_max_chars(self):
        return 100_000


def test_heartbeat_records_and_reads_explicit_path(tmp_path):
    path = tmp_path / "heartbeats.jsonl"
    agent = DummyAgent()

    snapshot = record_heartbeat(agent, surface="telegram", event="turn_start", path=path)

    assert snapshot is not None
    recent = read_recent_heartbeats(path=path)
    assert len(recent) == 1
    assert recent[0]["surface"] == "telegram"
    assert recent[0]["event"] == "turn_start"


def test_surface_continuity_context_mentions_recent_other_surface(tmp_path):
    path = tmp_path / "heartbeats.jsonl"
    agent = DummyAgent()
    record_heartbeat(agent, surface="telegram", event="turn_end", path=path)

    context = build_surface_continuity_context(agent, current_surface="terminal", path=path)

    assert "Surface Continuity" in context
    assert "telegram" in context
    assert "Current surface: terminal" in context


def test_render_heartbeat_status_from_snapshot(tmp_path):
    path = tmp_path / "heartbeats.jsonl"
    agent = DummyAgent()
    record_heartbeat(agent, surface="terminal", event="periodic", path=path)

    text = render_heartbeat_status(path=path)

    assert "Heartbeat:" in text
    assert "surface: terminal" in text
    assert "session id:" in text
    assert "session slot: main" in text
    assert "model:        test-provider / test-model" in text
    assert "session:" not in text
