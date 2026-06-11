from core.ghost.ghost_context import _task_board_text, build_ghost_context
from core.backend_monitor import BackendMonitor
from core.gateway import Gateway
from core.tasking.task_board import TaskBoard, TaskItem, record_snapshot, read_recent_snapshots
from core.goal import GoalPlan, GoalStep


class FakeProfile:
    def build_profile_context(self, max_chars=900):
        return "Operator prefers direct reports. api_key=SECRET123"


class FakeSession:
    messages = [
        {"role": "user", "content": "please inspect the checkout bug"},
        {"role": "assistant", "content": "main MO claimed it found cart.py"},
    ]


class FakeAgent:
    profile = FakeProfile()
    session = FakeSession()
    _goal_active = False
    _goal_plan = None


class NoopAgent(FakeAgent):
    def run_turn(self, *args, **kwargs):
        return "ok"


def test_ghost_context_includes_safe_profile_board_workers_and_redacts_secret(tmp_path):
    agent = NoopAgent()
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))
    gateway.last_task_board = TaskBoard(
        "turn-1",
        "deep_review",
        [TaskItem("1", "Inspect checkout files", "active")],
    )
    gateway.monitor.emit_text("provider request running with token=SECRET456")

    context = build_ghost_context(
        agent,
        gateway,
        question="should we run this in background?",
        ui_state={"main_busy": True, "activity": "thinking", "queued_count": 1},
    )

    assert "Workers / routing state" in context
    assert "Main MO: busy" in context
    assert "Queued user inputs: 1" in context
    assert "Inspect checkout files" in context
    assert "Operator prefers direct reports" in context
    assert "api_key=[redacted]" in context
    assert "token=[redacted]" in context
    assert "Ghost routing guidance" in context
    assert "Background MO worker/goal" in context


def test_ghost_context_warns_high_risk_routes_to_main_mo():
    context = build_ghost_context(
        FakeAgent(),
        None,
        question="can you deploy and git push this to production?",
    )

    assert "high-risk boundary" in context
    assert "main MO/Gateway" in context
    assert "not background work" in context


def test_ghost_status_question_uses_compact_visible_state_context():
    context = build_ghost_context(
        FakeAgent(),
        None,
        question="why is goal queued and what is MO doing?",
        ui_state={"main_busy": True, "activity": "thinking", "queued_count": 1, "goal_queued": True},
    )

    assert "Main MO: busy" in context
    assert "Queued user inputs: 1" in context
    assert "Background MO worker/goal: queued" in context
    assert "Safe operator profile" not in context
    assert "Recent visible chat" not in context
    assert "It is okay to mention busy, queued, running, or idle" in context
    assert "Never mention internal state" not in context


def test_ghost_context_includes_private_code_map_with_goal_and_main_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "ghost_routing.py").write_text("def recommend_ghost_route():\n    return None\n", encoding="utf-8")
    agent = FakeAgent()
    agent._goal_active = True
    agent._goal_plan = GoalPlan(objective="watch routing", steps=[GoalStep("1", "Inspect Ghost routing", "active")])

    context = build_ghost_context(
        agent,
        None,
        question="investigate ghost routing while goal is active",
        ui_state={"main_busy": True, "activity": "thinking", "goal_worker_active": True, "goal_stage": "iterating"},
    )

    assert "Private code map" in context
    assert "ghost_routing.py" in context
    assert "Main MO: busy" in context
    assert "Background MO worker/goal: active" in context
    assert "Objective: watch routing" in context


def test_ghost_task_board_text_prefers_live_ui_then_gateway_before_ledger(tmp_path, monkeypatch):
    path = tmp_path / "taskboards.jsonl"
    stale = TaskBoard(title="Ledger board", session_id="s1", tasks=[TaskItem("1", "Ledger stale", "active")])
    live = TaskBoard(title="Gateway board", session_id="s1", tasks=[TaskItem("1", "Gateway live", "active")])
    record_snapshot(stale, "updated", path=path)

    import core.ghost.ghost_context as ghost_context
    monkeypatch.setattr(ghost_context, "read_recent_snapshots", lambda limit=1, session_id="": read_recent_snapshots(limit=limit, path=path, session_id=session_id))

    gateway = type("GatewayStub", (), {"last_task_board": live})()

    assert "UI live" in _task_board_text(gateway, {"board_text": "1 tasks (0 done, 1 open)\n→ UI live"}, session_id="s1")
    gateway_text = _task_board_text(gateway, {}, session_id="s1")
    assert "Gateway live" in gateway_text
    assert "Ledger stale" not in gateway_text
    ledger_text = _task_board_text(None, {}, session_id="s1")
    assert "Ledger stale" in ledger_text


def test_ghost_context_includes_goal_plan_when_worker_active():
    agent = FakeAgent()
    agent._goal_active = True
    agent._goal_plan = GoalPlan(
        objective="review visuals",
        steps=[GoalStep("1", "Inspect TUI", "completed", ["read_file:interface/main_terminal.py"]), GoalStep("2", "Report findings", "active")],
    )

    context = build_ghost_context(agent, None, ui_state={"goal_worker_active": True, "goal_stage": "iterating"})

    assert "Background MO worker/goal: active" in context
    assert "Objective: review visuals" in context
    assert "completed: Inspect TUI" in context
    assert "active: Report findings" in context
