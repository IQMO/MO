"""Unit tests for the integrated turn health guard: compaction + handoff + warnings."""
from types import SimpleNamespace

from core.agent.agent import Agent
from core.self_capability_preflight import devmode05_final_allows_stop
from core.session.session import Session
from core.tasking.task_board import TaskBoard, TaskItem


def _agent(*, max_tool_rounds=80, context_handoff_enabled=True):
    """Minimal agent wired enough for _check_turn_health and _force_tool_budget_handoff."""
    agent = Agent.__new__(Agent)
    agent.max_tool_rounds = max_tool_rounds
    agent.context_handoff_enabled = context_handoff_enabled
    agent.config = {"agent": {}}
    agent._handoff_count = 0
    agent._turn_health_compacted = False
    agent._turn_health_handed_off = False
    return agent


class _Monitor:
    def __init__(self):
        self.events = []

    def emit(self, event_type, payload):
        self.events.append((event_type, payload))


# ── Warning-only tiers ──────────────────────────────────────────────

def test_no_warning_at_low_usage():
    agent = _agent(max_tool_rounds=80)
    result = agent._check_turn_health(0, None, monitor=None)
    assert result is None


def test_note_warning_at_60_percent():
    agent = _agent(max_tool_rounds=80)
    result = agent._check_turn_health(48, None, monitor=None)  # 60%
    assert result is not None
    assert "TURN HEALTH NOTE" in result
    assert "48/80" in result


def test_warning_after_compaction_used():
    """Once compaction fires, further calls only inject the note warning."""
    agent = _agent(max_tool_rounds=80)
    agent._turn_health_compacted = True  # compaction already happened
    agent._turn_health_handed_off = True  # not at handoff threshold
    result = agent._check_turn_health(68, None, monitor=None)  # 12 remaining, 85%
    assert result is not None
    assert "TURN HEALTH WARNING" in result
    assert "Wrap up" in result


def test_critical_warning_at_5_remaining_no_handoff_disabled():
    agent = _agent(max_tool_rounds=80, context_handoff_enabled=False)
    result = agent._check_turn_health(75, None, monitor=None)  # 5 remaining
    assert result is not None
    assert "TURN HEALTH CRITICAL" in result
    assert agent._turn_health_handed_off is False  # No handoff when disabled


# ── Compaction trigger ──────────────────────────────────────────────

def test_compaction_skip_is_not_reported_as_compacted():
    agent = _agent(max_tool_rounds=80)
    agent._session = Session("sys")
    agent.config["agent"]["context_momentum_compact_enabled"] = True
    monitor = _Monitor()

    result = agent._check_turn_health(48, None, monitor=monitor)  # 60%
    assert agent._turn_health_compacted is True
    assert "TURN HEALTH NOTE" in (result or "")
    assert "available for session compaction" in (result or "")
    assert not any(event == "session_compact" for event, _payload in monitor.events)
    turn_health = [payload for event, payload in monitor.events if event == "turn_health"]
    assert turn_health[-1]["action"] == "compact_skipped"

    # Second call with clean extra_context — should only warn, not compact
    result2 = agent._check_turn_health(49, None, monitor=None)
    assert "compacted" not in (result2 or "").lower()
    assert "Be mindful" in (result2 or "")


def test_compaction_reports_compacted_only_when_session_changes():
    agent = _agent(max_tool_rounds=80)
    agent._session = Session("sys", max_history=100)
    agent.session.add_user("inspect the diff")
    agent.session.add_message({"role": "assistant", "content": "", "tool_calls": [{
        "id": "c1",
        "function": {"name": "shell", "arguments": '{"command":"git diff"}'},
    }]})
    agent.session.add_tool_result("c1", "diff --git a/a.py b/a.py\n" + "+line\n" * 200)
    agent.session.add_assistant("Diff inspected.")
    for idx in range(20):
        agent.session.add_user(f"recent {idx}")
        agent.session.add_assistant(f"answer {idx}")
    agent.config["agent"]["context_momentum_compact_enabled"] = True
    monitor = _Monitor()

    result = agent._check_turn_health(48, None, monitor=monitor)

    assert "Context compacted" in (result or "")
    assert any(event == "session_compact" for event, _payload in monitor.events)
    turn_health = [payload for event, payload in monitor.events if event == "turn_health"]
    assert turn_health[-1]["action"] == "compact"
    assert turn_health[-1]["saved_chars"] > 0


# ── Handoff trigger ─────────────────────────────────────────────────

def test_handoff_triggers_at_5_remaining():
    agent = _agent(max_tool_rounds=80)
    agent._session = Session("sys")
    agent._provider_surface = lambda: "main"
    handoff_calls = []

    def fake_handoff(**kwargs):
        handoff_calls.append(kwargs)
        agent._handoff_count += 1

    agent._perform_context_handoff = fake_handoff
    agent._sessions = SimpleNamespace()
    agent.config["agent"]["context_handoff_threshold"] = 0.50

    # First health check at 0 rounds — no handoff (tool_rounds > 0 guard)
    agent._check_turn_health(0, None, monitor=None)
    assert len(handoff_calls) == 0

    # At 75 rounds (5 remaining) — handoff should trigger
    agent._check_turn_health(75, None, monitor=None)
    assert len(handoff_calls) == 1
    assert "TOOL BUDGET CRITICAL" in handoff_calls[0]["focus"]
    assert agent._turn_health_handed_off is True
    assert agent._handoff_count == 1

    # Second call should NOT handoff again, but should warn (not compact)
    result2 = agent._check_turn_health(76, None, monitor=None)
    assert len(handoff_calls) == 1
    assert "TURN HEALTH CRITICAL" in (result2 or "")
    # Should NOT try to compact after handoff
    assert "compacted" not in (result2 or "").lower()


def test_devmode05_handoff_requests_continuation_capsule_not_final_answer():
    agent = _agent(max_tool_rounds=80)
    agent._session = Session("sys")
    agent.session.add_user("start DEVMODE05")
    agent._provider_surface = lambda: "main"
    handoff_calls = []

    def fake_handoff(**kwargs):
        handoff_calls.append(kwargs)
        agent._handoff_count += 1

    agent._perform_context_handoff = fake_handoff
    agent._sessions = SimpleNamespace()
    agent.config["agent"]["context_handoff_threshold"] = 0.50

    result = agent._check_turn_health(75, None, monitor=None)

    assert len(handoff_calls) == 1
    assert "DEVMODE05 is not complete" in handoff_calls[0]["focus"]
    assert "continuation capsule" in handoff_calls[0]["focus"]
    assert "provide your final answer NOW" not in handoff_calls[0]["focus"]
    assert "DEVMODE05 continuation capsule" in (result or "")
    assert "Produce your final answer now" not in (result or "")


def test_devmode05_completed_board_handoff_requests_complete_not_continuation():
    agent = _agent(max_tool_rounds=80)
    agent._session = Session("sys")
    agent.session.add_user("start DEVMODE05")
    agent.gateway = SimpleNamespace(last_task_board=TaskBoard(tasks=[TaskItem("1", "Closeout", "completed")]))
    agent._provider_surface = lambda: "main"
    handoff_calls = []

    def fake_handoff(**kwargs):
        handoff_calls.append(kwargs)
        agent._handoff_count += 1

    agent._perform_context_handoff = fake_handoff
    agent._sessions = SimpleNamespace()

    result = agent._check_turn_health(75, None, monitor=None)

    assert len(handoff_calls) == 1
    assert "taskboard is already complete/open=0" in handoff_calls[0]["focus"]
    assert "[DEVMODE05 COMPLETE]" in handoff_calls[0]["focus"]
    assert "DEVMODE05 is not complete" not in handoff_calls[0]["focus"]
    assert "Produce [DEVMODE05 COMPLETE]" in (result or "")
    assert "DEVMODE05 continuation capsule" not in (result or "")


def test_devmode05_tool_blocked_instruction_requires_terminal_marker():
    instruction = Agent._turn_health_tool_blocked_instruction("start DEVMODE05")

    assert "must start exactly" in instruction
    assert "[DEVMODE05 BLOCKED]" in instruction
    assert "Produce your final answer now" not in instruction


def test_devmode05_persistent_tool_block_text_is_terminal_blocked():
    text = Agent._turn_health_persistent_block_text("start DEVMODE05")

    assert text.startswith("[DEVMODE05 BLOCKED]")
    assert devmode05_final_allows_stop("start DEVMODE05", text)


def test_open_work_handoff_requests_work_continuation_capsule_not_final_answer():
    agent = _agent(max_tool_rounds=80)
    agent._session = Session("sys")
    agent.session.add_user("fix the broken workflow")
    agent.gateway = SimpleNamespace(last_task_board=TaskBoard(tasks=[TaskItem("1", "Fix", "active")]))
    agent._provider_surface = lambda: "main"
    handoff_calls = []

    def fake_handoff(**kwargs):
        handoff_calls.append(kwargs)
        agent._handoff_count += 1

    agent._perform_context_handoff = fake_handoff
    agent._sessions = SimpleNamespace()

    result = agent._check_turn_health(75, None, monitor=None)

    assert len(handoff_calls) == 1
    assert "Active work is not complete" in handoff_calls[0]["focus"]
    assert "[WORK BLOCKED]" in handoff_calls[0]["focus"]
    assert "provide your final answer NOW" not in handoff_calls[0]["focus"]
    assert "work continuation capsule" in (result or "")
    assert "Produce your final answer now" not in (result or "")


def test_open_work_tool_blocked_instruction_requires_work_marker():
    agent = _agent(max_tool_rounds=80)
    agent.gateway = SimpleNamespace(last_task_board=TaskBoard(tasks=[TaskItem("1", "Fix", "active")]))

    instruction = agent._turn_health_tool_blocked_instruction("fix the broken workflow")

    assert "must start exactly" in instruction
    assert "[WORK BLOCKED]" in instruction
    assert "Produce your final answer now" not in instruction


def test_open_work_persistent_tool_block_text_is_recoverable_boundary():
    agent = _agent(max_tool_rounds=80)
    agent.gateway = SimpleNamespace(last_task_board=TaskBoard(tasks=[TaskItem("1", "Fix", "active")]))

    text = agent._turn_health_persistent_block_text("fix the broken workflow")

    assert text.startswith("[WORK BLOCKED]")
    assert "Continuation required" in text


def test_handoff_not_triggered_when_disabled():
    agent = _agent(max_tool_rounds=80, context_handoff_enabled=False)
    result = agent._check_turn_health(75, None, monitor=None)  # 5 remaining
    assert agent._turn_health_handed_off is False
    assert "TURN HEALTH CRITICAL" in result


def test_handoff_not_triggered_at_zero_rounds():
    """Even with tiny max_tool_rounds, the tool_rounds>0 guard prevents instant handoff."""
    agent = _agent(max_tool_rounds=1)
    agent._session = Session("sys")
    agent._provider_surface = lambda: "main"
    handoff_calls = []

    def fake_handoff(**kwargs):
        handoff_calls.append(kwargs)

    agent._perform_context_handoff = fake_handoff
    agent._sessions = SimpleNamespace()

    result = agent._check_turn_health(0, None, monitor=None)
    assert len(handoff_calls) == 0
    # With max_tools=1 and tool_rounds=0, remaining=1 ≤ 5 — should not handoff
    # because tool_rounds=0. Critical warning still injected.
    assert result is not None


# ── Turn-level flag reset ───────────────────────────────────────────

def test_flags_reset_at_turn_start():
    """run_turn resets health flags each turn."""
    agent = _agent(max_tool_rounds=80)
    agent._turn_health_compacted = True
    agent._turn_health_handed_off = True

    # Simulate what run_turn does at the start
    agent._turn_health_compacted = False
    agent._turn_health_handed_off = False

    agent._check_turn_health(48, None, monitor=None)
    assert agent._turn_health_compacted is True  # Should compact now
