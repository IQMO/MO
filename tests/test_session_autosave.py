import json
from types import SimpleNamespace

from core.agent.agent import Agent
from core.session.session import Session
from core.session.sessions import SessionManager
from interface.main_terminal import _record_session
from interface.native_terminal import record_session


class FakeSessionManager:
    current_name = "main"

    def __init__(self):
        self.saved = []
        self.saved_meta = []

    def save(self, name, session, extra_meta=None):
        self.saved.append((name, len(session.messages)))
        if extra_meta:
            self.saved_meta.append(extra_meta)
        return "saved"

    def save_snapshot(self, name, session, extra_meta=None):
        self.saved.append((name, len(session.messages)))
        if extra_meta:
            self.saved_meta.append(extra_meta)
        return "snapshot saved"


class FakeProfile:
    def record_session(self, **_kwargs):
        pass


def test_agent_autosave_session_saves_non_empty_current_session():
    agent = Agent.__new__(Agent)
    agent._sessions = FakeSessionManager()
    agent.session = SimpleNamespace(messages=[{"role": "user", "content": "hi"}])

    agent.autosave_session()

    assert agent._sessions.saved == [("main", 1)]


def test_session_quarantines_unfinished_tool_tail_with_owner_user():
    session = Session("system")
    session.turn_count = 2
    session.messages = [
        {"role": "user", "content": "finished"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "build old thing"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "write_file", "arguments": '{"path":"x","content":"y"}'}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "blocked"},
    ]

    meta = session.quarantine_unfinished_tail()

    assert meta["changed"] is True
    assert meta["user"] == "build old thing"
    assert session.turn_count == 1
    assert [m["content"] for m in session.messages] == ["finished", "done"]


def test_session_save_strips_unfinished_terminal_tool_chain(tmp_path):
    session = SimpleNamespace(
        session_id="s1",
        turn_count=2,
        total_tokens=0,
        output_tokens=0,
        token_log=[],
        messages=[
            {"role": "user", "content": "finished"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "stale build"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"x"}'}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "file"},
        ],
    )
    manager = SessionManager(str(tmp_path / "sessions"))

    manager.save("main", session)

    saved = json.loads((tmp_path / "sessions" / "main.json").read_text(encoding="utf-8"))
    assert [m["content"] for m in saved["messages"]] == ["finished", "done"]
    pending = saved["meta"]["pending_interrupted_work"]
    assert pending["user"] == "stale build"
    assert pending["reason"] == "unfinished_tool_turn"


def test_session_quarantines_unanswered_user_tail():
    session = Session("system")
    session.turn_count = 1
    session.messages = [{"role": "user", "content": "add rewards to game"}]

    meta = session.quarantine_unfinished_tail()

    assert meta["changed"] is True
    assert meta["reason"] == "unanswered_user_turn"
    assert meta["user"] == "add rewards to game"
    assert session.messages == []


def test_session_load_strips_existing_saved_unfinished_tail(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "main.json").write_text(json.dumps({
        "name": "main",
        "session_id": "s1",
        "turn_count": 1,
        "messages": [
            {"role": "user", "content": "stale build"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"x"}'}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "file"},
        ],
    }), encoding="utf-8")
    manager = SessionManager(str(sessions_dir))

    loaded = manager.load("main")

    assert loaded is not None
    assert loaded["messages"] == []
    assert loaded["_unfinished_tail_meta"]["changed"] is True
    assert loaded["_unfinished_tail_meta"]["user"] == "stale build"


def test_agent_autosave_session_skips_empty_session():
    agent = Agent.__new__(Agent)
    agent._sessions = FakeSessionManager()
    agent.session = SimpleNamespace(messages=[])

    agent.autosave_session()

    assert agent._sessions.saved == []


def test_native_record_session_autosaves_conversation_snapshot():
    agent = Agent.__new__(Agent)
    agent._sessions = FakeSessionManager()
    agent.session = SimpleNamespace(
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        token_log=[],
        turn_count=1,
    )
    agent.profile = FakeProfile()
    agent.memory = None

    record_session(agent)

    assert agent._sessions.saved == [("main", 2)]



def test_record_session_autosaves_conversation_snapshot():
    agent = Agent.__new__(Agent)
    agent._sessions = FakeSessionManager()
    agent.session = SimpleNamespace(
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        token_log=[],
        turn_count=1,
    )
    agent.profile = FakeProfile()
    agent.memory = None

    _record_session(agent)

    assert agent._sessions.saved == [("main", 2)]


def test_autosave_includes_compression_metadata_when_active():
    """Session save includes compression stats when ops have occurred."""
    agent = Agent.__new__(Agent)
    agent._sessions = FakeSessionManager()
    agent.session = SimpleNamespace(messages=[{"role": "user", "content": "hi"}])
    agent.compression_total_ops = 8
    agent.compression_total_saved = 4200
    agent.compression_last_pct = 35
    agent.truncation_total_ops = 1
    agent.truncation_total_saved = 800
    agent.truncation_last_pct = 50

    agent.autosave_session()

    assert len(agent._sessions.saved_meta) == 1
    assert agent._sessions.saved_meta[0]["compression"]["total_ops"] == 8
    assert agent._sessions.saved_meta[0]["compression"]["total_saved"] == 4200
    assert agent._sessions.saved_meta[0]["compression"]["last_pct"] == 35
    assert agent._sessions.saved_meta[0]["compression"]["truncation_ops"] == 1
    assert agent._sessions.saved_meta[0]["compression"]["context_saved_chars"] == 5000


def test_session_save_includes_closeout_metadata_when_agent_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = Agent.__new__(Agent)
    agent.config = {"agent": {}}
    agent._sessions = FakeSessionManager()
    agent.session = SimpleNamespace(
        session_id="s1",
        messages=[{"role": "user", "content": "hi"}],
        token_log=[],
        turn_count=1,
        total_tokens=0,
        output_tokens=0,
        max_history=50,
        created_at=0,
        trimmed_messages_count=0,
    )
    agent.compression_total_ops = 2
    agent.compression_total_saved = 1200
    agent.compression_last_pct = 25
    agent.workers = None
    agent._goal_active = False

    result = agent._cmd_session("save checkpoint")

    assert "saved" in result
    assert "Session closeout:" in result
    assert agent._sessions.saved_meta[0]["closeout"]["path"].endswith(".md")
    assert agent._sessions.saved_meta[0]["compression"]["saved_tokens_est"] == 300


def test_autosave_persists_pending_interrupted_work_metadata():
    agent = Agent.__new__(Agent)
    agent._sessions = FakeSessionManager()
    agent.session = SimpleNamespace(messages=[{"role": "user", "content": "hi"}])
    agent.compression_total_ops = 0
    agent._pending_interrupted_work = {
        "changed": True,
        "reason": "stalled_work_after_return",
        "dropped_messages": 17,
        "user": "add zombie rewards and weapon upgrades",
    }

    agent.autosave_session()

    assert len(agent._sessions.saved_meta) == 1
    pending = agent._sessions.saved_meta[0]["pending_interrupted_work"]
    assert pending["user"] == "add zombie rewards and weapon upgrades"
    assert pending["reason"] == "stalled_work_after_return"
    assert pending["dropped_messages"] == 17


def test_restore_context_saving_metadata_includes_handoff_momentum():
    agent = Agent.__new__(Agent)

    agent._restore_context_saving_meta({
        "compression": {
            "total_ops": 2,
            "total_saved": 1200,
            "truncation_ops": 1,
            "truncation_saved": 300,
            "momentum_ops": 8,
            "momentum_saved": 4200,
            "momentum_truncation_ops": 1,
            "momentum_truncation_saved": 800,
            "session_compaction_ops": 3,
            "session_compaction_saved": 2400,
        }
    })

    assert agent.compression_total_ops == 2
    assert agent.context_momentum_compression_ops == 8
    assert agent._tool_context_saving_ops() == 12
    assert agent._tool_context_saved_chars() == 6500
    assert agent.session_compaction_total_saved == 2400


def test_autosave_omits_compression_metadata_when_no_ops():
    """Session save does not include compression when no ops occurred."""
    agent = Agent.__new__(Agent)
    agent._sessions = FakeSessionManager()
    agent.session = SimpleNamespace(messages=[{"role": "user", "content": "hi"}])
    agent.compression_total_ops = 0
    agent._pending_interrupted_work = {}

    agent.autosave_session()

    assert len(agent._sessions.saved_meta) == 0
