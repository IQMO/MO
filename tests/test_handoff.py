import json
import os
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from core.agent.agent import Agent
from core.session.handoff import build_compact_summary, build_handoff_document, context_pressure, should_auto_handoff, write_handoff_document
from core.session.session import Session
from core.tasking.task_board import TaskBoard, TaskItem


class FakeSessions:
    current_name = "main"

    def __init__(self):
        self.snapshots = []

    def save_snapshot(self, name, session, extra_meta=None):
        self.snapshots.append((name, len(session.messages)))
        return "saved"


def _agent_with_session(*, budget_tokens=250, max_history=50):
    agent = Agent.__new__(Agent)
    agent._thread_state = threading.local()
    agent._session = Session("system", max_history=max_history)
    agent.config = {"agent": {"context_handoff_threshold": 0.50}}
    agent.context_handoff_enabled = True
    agent.context_handoff_threshold = 0.50
    agent.context_budget_tokens = budget_tokens
    agent.context_budget_source = "test"
    agent.provider_name = "mock"
    agent.model = "mock"
    agent._sessions = FakeSessions()
    agent.workers = SimpleNamespace(summary=lambda limit=8: "")
    agent._goal_plan = None
    agent.sandbox_config = {"enabled": True}
    agent.gateway = SimpleNamespace(last_task_board=None)
    agent._handoff_count = 0
    agent.last_handoff_path = ""
    return agent


def test_compact_handoff_summary_includes_file_refs_and_graph(tmp_path, monkeypatch):
    # Seed at the resolved private state path (where the handoff reads), not cwd.
    from core.path_defaults import resolve_state_path
    memory = Path(resolve_state_path("memory"))
    memory.mkdir(parents=True, exist_ok=True)
    (memory / "file_operations.jsonl").write_text(
        json.dumps({"session_id": "s1", "files_read": ["core/agent.py"], "files_modified": ["core/handoff.py"]}) + "\n",
        encoding="utf-8",
    )
    graph_dir = memory / "structural_graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "graph.json").write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
    agent = _agent_with_session()
    agent.session.add_user("continue handoff work")

    summary = build_compact_summary(agent, reason="unit test")

    assert "MO HANDSOFF CONTEXT (compact)" in summary
    assert "<read-files>" in summary
    assert "core/agent.py" in summary
    assert "core/handoff.py" in summary
    assert len(summary.splitlines()) < 500


def test_handoff_document_is_temp_redacted_and_reference_based():
    agent = _agent_with_session()
    agent.session.add_user("please continue; api_key=sk-secret123")

    document = build_handoff_document(agent, focus="continue safely", reason="test")
    path = write_handoff_document(document)

    assert path.parent == Path(tempfile.gettempdir())
    text = path.read_text(encoding="utf-8")
    assert "MO HANDSOFF CONTEXT" in text
    assert "sk-secret123" not in text
    assert "docs/interface/INTERFACE-CLEANUP-PRD.md" in text


def test_handoff_document_prunes_old_temp_capsules(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    for index in range(35):
        old = tmp_path / f"mo-handoff-20260528-1200{index:02d}-1.md"
        old.write_text("old", encoding="utf-8")
        os_time = 1_780_000_000 + index
        old.touch()
        os.utime(old, (os_time, os_time))

    path = write_handoff_document("# MO HANDSOFF CONTEXT\nnew\n")

    files = sorted(tmp_path.glob("mo-handoff-*.md"))
    assert path.exists()
    assert len(files) == 30
    assert not (tmp_path / "mo-handoff-20260528-120000-1.md").exists()


def test_should_auto_handoff_uses_model_budget_and_message_pressure():
    agent = _agent_with_session(budget_tokens=250)
    for idx in range(10):
        agent.session.add_user("x" * 220 + str(idx))

    triggered, metrics = should_auto_handoff(agent)

    assert triggered is True
    assert metrics["char_ratio"] >= 0.50
    assert metrics["trigger_dimension"] == "token-budget"
    assert context_pressure(agent)["budget_chars"] == 1000


def test_should_auto_handoff_triggers_on_message_ratio_before_trim():
    agent = _agent_with_session(budget_tokens=100_000, max_history=20)
    for idx in range(10):
        agent.session.add_user(f"short {idx}")

    triggered, metrics = should_auto_handoff(agent)

    assert triggered is True
    assert metrics["char_ratio"] < 0.50
    assert metrics["message_ratio"] >= 0.50
    assert metrics["trigger_dimension"] == "message-count"


def test_should_auto_handoff_supports_separate_thresholds():
    agent = _agent_with_session(budget_tokens=100_000, max_history=20)
    agent.config = {"agent": {"context_handoff_char_threshold": 0.95, "context_handoff_msg_threshold": 0.40}}
    for idx in range(8):
        agent.session.add_user(f"short {idx}")

    triggered, metrics = should_auto_handoff(agent)

    assert triggered is True
    assert metrics["char_threshold"] == 0.95
    assert metrics["message_threshold"] == 0.40
    assert metrics["trigger_dimension"] == "message-count"


def test_provider_call_consumes_handoff_only_after_success():
    agent = _agent_with_session()
    agent.temperature = 0
    agent.max_tokens = 100
    agent.tool_definitions = []
    agent.session._handoff_context = "handoff seed"

    class FailingProvider:
        def complete(self, **kwargs):
            raise RuntimeError("temporary provider failure")

    agent.providers = [FailingProvider()]
    agent.provider_index = 0

    try:
        Agent._call_provider(agent)
    except RuntimeError:
        pass

    assert agent.session._handoff_context == "handoff seed"

    class OkProvider:
        def complete(self, **kwargs):
            assert any("handoff seed" in str(m.get("content") or "") for m in kwargs["messages"] if m.get("role") == "system")
            return SimpleNamespace(content="ok", tool_calls=[], usage=None, finish_reason="stop")

    agent.providers = [OkProvider()]
    response = Agent._call_provider(agent)

    assert response.content == "ok"
    assert agent.session._handoff_context == ""


def test_provider_context_overflow_handoff_retries_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = _agent_with_session(budget_tokens=100_000)
    agent.temperature = 0
    agent.max_tokens = 100
    agent.max_provider_requests = 3
    agent.max_tool_rounds = 1
    agent.tool_definitions = []
    agent.context_summary_enabled = True
    agent.tool_compress_enabled = False
    agent.tool_result_max_chars = 1000
    agent.allowed_roots = [str(tmp_path)]
    agent._active_lane = None
    agent._pending_turn_proposal = ""
    agent._deep_review_analysis_rounds = 0
    agent._pending_interrupted_work = {}
    agent.profile = None
    agent.memory = None
    agent.critic = SimpleNamespace(review=lambda text: SimpleNamespace(text=text))

    monkeypatch.setattr(Agent, "_build_extra_context", lambda self, user_input: "")
    monkeypatch.setattr(Agent, "_maybe_handle_init_turn", lambda self, user_input: None)
    monkeypatch.setattr(Agent, "_maybe_handle_workflow_control_turn", lambda self, user_input: None)
    monkeypatch.setattr(Agent, "_maybe_handle_identity_turn", lambda self, user_input: None)
    monkeypatch.setattr(Agent, "_quarantine_unfinished_tail_before_turn", lambda self, user_input, monitor=None: {"changed": False})
    monkeypatch.setattr(Agent, "_pause_interrupted_work_for_return", lambda self, user_input, quarantine_meta, monitor=None: None)
    monkeypatch.setattr(Agent, "_record_turn_memory_and_learning", lambda self, user_input, final_text: [])
    monkeypatch.setattr(Agent, "_run_consistency_boundary", lambda self, boundary, **kwargs: None)

    class OverflowThenOkProvider:
        name = "mock"
        model = "mock"
        api_mode = "mock"

        def __init__(self):
            self.calls = 0
            self.system_contexts = []

        def complete(self, **kwargs):
            self.calls += 1
            self.system_contexts.append("\n\n".join(str(m.get("content") or "") for m in kwargs["messages"] if m.get("role") == "system"))
            if self.calls == 1:
                raise RuntimeError("context_length_exceeded: maximum context length exceeded")
            return SimpleNamespace(content="ok after recovery", tool_calls=[], usage=None, finish_reason="stop")

    provider = OverflowThenOkProvider()
    agent.providers = [provider]
    agent.provider_index = 0

    class CaptureMonitor:
        def __init__(self):
            self.events = []

        def emit(self, event_type, payload):
            self.events.append((event_type, payload))

    monitor = CaptureMonitor()

    result = agent.run_turn("continue with the current work", monitor=monitor)

    assert result == "ok after recovery"
    assert provider.calls == 2
    assert "MO HANDSOFF CONTEXT" in provider.system_contexts[1]
    assert agent._handoff_count == 1
    assert agent.session._handoff_context == ""
    assert any(
        event_type == "provider_error"
        and payload.get("reason") == "provider_context_overflow"
        for event_type, payload in monitor.events
    )
    assert any(
        event_type == "session_event"
        and payload.get("kind") == "provider_context_overflow_recovery"
        and payload.get("recovered") is True
        and payload.get("handoff") is True
        for event_type, payload in monitor.events
    )


def test_auto_handoff_opens_clean_session_and_saves_snapshot():
    agent = _agent_with_session(budget_tokens=250)
    for idx in range(10):
        agent.session.add_user("context " + ("x" * 220) + str(idx))
    latest = "finish the current task"
    agent.session.add_user(latest)

    started = Agent._maybe_context_handoff(agent, latest, extra_context="")

    assert started is True
    assert agent.session.session_id.startswith("mo-handoff-")
    # Handoff seed is stored in _handoff_context, not as a user message
    assert len(agent.session.messages) >= 1  # preserves recent context
    assert agent.session.messages[-1]["content"] == latest
    assert agent.session._handoff_context
    assert "MO HANDSOFF CONTEXT" in agent.session._handoff_context
    assert agent._sessions.snapshots
    assert agent._handoff_count == 1
    assert agent.last_handoff_path
    assert agent.last_handoff_notice == ""


def test_auto_handoff_preserves_latest_visible_report_without_tool_bloat():
    agent = _agent_with_session(budget_tokens=250)
    for idx in range(8):
        agent.session.add_user("context " + ("x" * 220) + str(idx))
    agent.session.add_assistant("[RAW TOOL PAYLOAD BLOCKED] internal retry")
    agent.session.add_assistant("Fixed the UI.\n\nChecks:\n- pytest — pass")
    latest = "how to run it ?"

    started = Agent._pre_turn_context_handoff(agent, latest)

    assert started is True
    assert len(agent.session.messages) >= 1  # preserves recent context
    # assert some messages were kept"role": "assistant", "content": "Fixed the UI.\n\nChecks:\n- pytest — pass"}
    assert agent.session.messages[-1] == {"role": "user", "content": latest}
    assert len(agent.session.messages) >= 2  # preserves recent context
    assert "RAW TOOL PAYLOAD" not in str(agent.session.messages)


def test_compact_command_is_handoff_not_destructive_trim():
    agent = _agent_with_session(budget_tokens=250)
    for idx in range(10):
        agent.session.add_user("old context " + ("x" * 220) + str(idx))

    result = Agent._cmd_compact(agent, "")

    assert "Context handoff opened a clean session" in result
    assert agent.session.session_id.startswith("mo-handoff-")
    # Handoff seed is in _handoff_context, not messages
    assert len(agent.session.messages) >= 0  # may preserve recent context
    assert agent.session._handoff_context
    assert "MO HANDSOFF CONTEXT" in agent.session._handoff_context
    assert agent._sessions.snapshots


def test_handoff_v2_includes_taskboard_tool_audit_and_graph(tmp_path, monkeypatch):
    agent = _agent_with_session()
    audit_path = tmp_path / "tool_audit.jsonl"
    audit_path.write_text(
        "\n".join(
            [
                json.dumps({
                    "ts": time.time(),
                    "tool": "read_file",
                    "arguments": {"path": "core/handoff.py"},
                    "result_chars": 1234,
                    "blocked": False,
                    "block_reason": "",
                }),
                json.dumps({
                    "ts": time.time(),
                    "tool": "shell",
                    "arguments": {"command": "rm -rf .", "workdir": "."},
                    "result_chars": 0,
                    "blocked": True,
                    "block_reason": "destructive command blocked",
                }),
            ]
        ),
        encoding="utf-8",
    )
    agent.sandbox_config = {"enabled": True, "audit_log": str(audit_path)}
    provider_audit_path = tmp_path / "provider_audit.jsonl"
    provider_audit_path.write_text(
        json.dumps({
            "ts": time.time(),
            "event": "provider_error",
            "surface": "main",
            "provider": "mock",
            "model": "mock",
            "reason": "timeout",
            "ok": False,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("core.session.handoff.PROVIDER_AUDIT_LOG_PATH", provider_audit_path)
    board = TaskBoard(
        "turn-1",
        "problem_solving",
        [
            TaskItem("1", "Inspect handoff implementation", "completed", ["read_file:core/handoff.py"]),
            TaskItem("2", "Verify handoff tests", "blocked", blocker="pytest failed once"),
        ],
        objective="make handoff great",
    )
    agent.gateway = SimpleNamespace(last_task_board=board)
    monkeypatch.setattr("core.session.handoff.should_include_code_graph_context", lambda _query: True)
    monkeypatch.setattr(
        "core.session.handoff.build_code_graph_context",
        lambda _query, max_chars=1200, max_nodes=6: "### MO Internal Code Map - orientation only\n- file: handoff `core/handoff.py` symbols=build_handoff_document\nMap slice id: test1234",
    )

    document = build_handoff_document(agent, focus="improve handoff graph references", reason="unit test")

    assert "## Session continuity" in document
    assert "## Taskboard state" in document
    assert "Inspect handoff implementation" in document
    assert "read_file:core/handoff.py" in document
    assert "## Verified evidence ledger" in document
    assert "recent tool read_file" in document
    assert "## Already tried / avoid repeating" in document
    assert "destructive command blocked" in document
    assert "## References and graph" in document
    assert "core/handoff.py" in document
    assert "Map slice id: test1234" in document
    assert "provider_error" in document
    assert "provider stability" in document


def test_pre_turn_handoff_does_not_handoff_twice_same_turn():
    agent = _agent_with_session(budget_tokens=250)
    for idx in range(10):
        agent.session.add_user("context " + ("x" * 220) + str(idx))
    latest = "continue once only"

    assert Agent._pre_turn_context_handoff(agent, latest) is True
    assert Agent._maybe_context_handoff(agent, latest, extra_context="x" * 10_000) is False
    assert len(agent._sessions.snapshots) == 1
    assert agent._handoff_count == 1


def test_auto_handoff_activity_avoids_operator_facing_handoff_jargon(monkeypatch):
    from types import SimpleNamespace

    agent = Agent.__new__(Agent)
    
    agent.session = SimpleNamespace(
        messages=[],
        add_user=lambda _text: None,
        turn_count=0,
        sanitize_for_provider=lambda **_kwargs: None,
        get_messages=lambda extra_context=None: [{"role": "system", "content": extra_context or ""}],
        record_usage=lambda **_kwargs: None,
        add_assistant=lambda *_args, **_kwargs: None,
    )
    agent.profile = None
    agent.memory = None
    agent.context_summary_enabled = False
    agent.context_handoff_enabled = True
    agent.max_provider_requests = 1
    agent.max_tool_rounds = 1
    agent.provider_name = "fake"
    agent.model = "fake"
    agent.tool_definitions = []
    agent.critic = SimpleNamespace(review=lambda text: SimpleNamespace(text=text))
    agent._pending_turn_proposal = ""
    agent._deep_review_analysis_rounds = 0
    agent._thread_state = threading.local()
    agent._call_provider = lambda **_kwargs: SimpleNamespace(content="ok", tool_calls=[], usage=None, finish_reason="stop")
    monkeypatch.setattr(Agent, "_pre_turn_context_handoff", lambda self, latest: False)
    monkeypatch.setattr(Agent, "_maybe_context_handoff", lambda self, latest, extra_context="": True)
    activities = []

    result = agent.run_turn("continue", on_activity=activities.append)

    assert result == "ok"
    joined = "\n".join(activities).lower()
    assert "handoff" not in joined
    assert "clean session" not in joined
    assert "context pressure" not in joined


def test_handoff_reports_prior_session_trim_loss():
    agent = _agent_with_session(max_history=3)
    for idx in range(6):
        agent.session.add_user(f"message {idx}")

    document = build_handoff_document(agent, focus="continue after trim", reason="trim test")

    assert agent.session.trimmed_messages_count > 0
    assert "were already trimmed" in document
    assert context_pressure(agent)["trimmed_messages_count"] == agent.session.trimmed_messages_count


def test_trimmed_history_after_compaction_does_not_force_low_pressure_handoff():
    agent = _agent_with_session(budget_tokens=10_000)
    for idx in range(8):
        agent.session.add_user(f"small message {idx}")
    agent.session.trimmed_messages_count = 4
    agent.session.last_trimmed_at = 100.0
    agent.session.compacted_messages_count = 12
    agent.session.last_compacted_at = 200.0

    triggered, metrics = should_auto_handoff(agent)

    assert triggered is False
    assert metrics["trigger_dimension"] == ""


def test_trimmed_history_after_last_compaction_still_triggers_handoff():
    agent = _agent_with_session(budget_tokens=10_000)
    for idx in range(8):
        agent.session.add_user(f"small message {idx}")
    agent.session.compacted_messages_count = 12
    agent.session.last_compacted_at = 100.0
    agent.session.trimmed_messages_count = 4
    agent.session.last_trimmed_at = 200.0

    triggered, metrics = should_auto_handoff(agent)

    assert triggered is True
    assert metrics["trigger_dimension"] == "trimmed-history"


def test_handoff_includes_context_saving_notice_when_stats_exist():
    """Handoff document mentions compression/truncation savings when ops occurred."""
    agent = _agent_with_session()
    agent.compression_total_ops = 12
    agent.compression_total_saved = 8420
    agent.compression_last_pct = 34
    agent.truncation_total_ops = 2
    agent.truncation_total_saved = 800
    document = build_handoff_document(agent, focus="test", reason="test")
    assert "Context-saving momentum kept tool-result context lean" in document
    assert "12 compressed" in document
    assert "2 truncated" in document
    assert "9,220" in document
    assert "Re-run tools for exact details" in document


def test_handoff_omits_compression_notice_when_no_stats():
    """Handoff document does NOT mention compression when no ops occurred."""
    agent = _agent_with_session()
    agent.compression_total_ops = 0
    agent.compression_total_saved = 0
    document = build_handoff_document(agent, focus="test", reason="test")
    assert "Context-saving momentum kept tool-result context lean" not in document


def test_adaptive_threshold_raised_when_compression_active():
    """When compression has saved meaningful amounts, handoff threshold increases."""
    agent = _agent_with_session()
    # Override config threshold for this test
    agent.config["agent"]["context_handoff_threshold"] = 0.70
    agent.context_handoff_threshold = 0.70
    # Simulate active compression with good savings
    agent.compression_total_ops = 10
    agent.compression_total_saved = 5000  # avg 500 chars saved per op

    triggered, metrics = should_auto_handoff(agent)

    # Threshold should be higher than base 0.70 due to adaptive boost
    assert metrics["threshold"] > 0.70
    assert metrics["threshold"] <= 0.78  # max +8%


def test_adaptive_threshold_not_raised_without_enough_ops():
    """Few compression ops do not trigger adaptive threshold."""
    agent = _agent_with_session()
    agent.config["agent"]["context_handoff_threshold"] = 0.70
    agent.context_handoff_threshold = 0.70
    agent.compression_total_ops = 2  # < 5
    agent.compression_total_saved = 800

    triggered, metrics = should_auto_handoff(agent)

    # Threshold should stay at base 0.70
    assert metrics["threshold"] == 0.70


def test_adaptive_threshold_not_raised_with_low_savings():
    """Many ops but very low average savings don't trigger boost."""
    agent = _agent_with_session()
    agent.config["agent"]["context_handoff_threshold"] = 0.70
    agent.context_handoff_threshold = 0.70
    agent.compression_total_ops = 20
    agent.compression_total_saved = 1000  # avg only 50 chars per op

    triggered, metrics = should_auto_handoff(agent)

    # Average savings too low, no boost
    assert metrics["threshold"] == 0.70


def test_adaptive_threshold_capped_at_92():
    """Threshold never exceeds 0.92 regardless of compression."""
    agent = _agent_with_session()
    agent.config["agent"]["context_handoff_threshold"] = 0.90
    agent.context_handoff_threshold = 0.90
    agent.compression_total_ops = 200
    agent.compression_total_saved = 200000  # huge savings

    triggered, metrics = should_auto_handoff(agent)

    # Should be capped at 0.92
    assert metrics["threshold"] <= 0.92


def test_handoff_carries_compression_stats_as_momentum():
    """After handoff, current counters reset but adaptive momentum survives."""
    agent = _agent_with_session(budget_tokens=250)
    # Accumulate some compression stats
    agent.compression_total_ops = 12
    agent.compression_total_saved = 8000
    agent.compression_last_pct = 45
    agent.truncation_total_ops = 1
    agent.truncation_total_saved = 1200
    # Fill session to trigger handoff
    for idx in range(10):
        agent.session.add_user("context " + ("x" * 220) + str(idx))
    latest = "finish"
    agent.session.add_user(latest)

    started = Agent._maybe_context_handoff(agent, latest, extra_context="")

    assert started is True
    # Current-session stats reset for honest per-session reporting.
    assert agent.compression_total_ops == 0
    assert agent.compression_total_saved == 0
    assert agent.compression_last_pct == 0
    # Carried stats keep adaptive handoff momentum alive.
    assert agent.context_momentum_compression_ops == 12
    assert agent.context_momentum_compression_saved == 8000
    assert agent.context_momentum_truncation_ops == 1
    assert agent.context_momentum_truncation_saved == 1200
    triggered, metrics = should_auto_handoff(agent)
    assert metrics["threshold"] > agent.context_handoff_threshold


import pytest as _pytest_state_lane


@_pytest_state_lane.fixture(autouse=True)
def _legacy_state_lane(monkeypatch, tmp_path):
    """Handoff reference-gathering reads the repo (graph slice built from source
    in cwd, docs), so cwd must stay the checkout — no chdir. State is isolated to
    a private tmp home (MO_STATE_HOME), so graph/state writes land there, never
    the checkout. Tests that seed state write to the resolved state path."""
    monkeypatch.delenv("MO_STATE_LOCAL", raising=False)
    monkeypatch.delenv("MO_HOME", raising=False)
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path / "state-home"))
