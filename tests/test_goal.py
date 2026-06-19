"""Tests for core/goal/goal.py — GoalRunner, decomposition, and iteration."""
import time
from pathlib import Path
from types import SimpleNamespace

from core.goal import GoalRunner, GoalPlan, GoalStep, decompose_goal, parse_goal_budget
from core.goal.goal_auditor import GoalAuditor
from core.workers import WorkerRegistry


class FakeProfile:
    _path = "memory/mo.db"
    user_name = "TestUser"


def make_fake_agent():
    agent = SimpleNamespace(
        _goal_plan=None,
        _goal_active=False,
        _goal_runner=None,
        profile=FakeProfile(),
        sandbox_config={"audit_log": None},
        session=SimpleNamespace(
            messages=[],
            get_messages=lambda **kw: [{"role": "system", "content": "test"}],
            sanitize_for_provider=lambda **kw: None,
            add_user=lambda text: None,
            add_assistant=lambda *a, **kw: None,
            add_message=lambda msg: None,
            add_tool_result=lambda tid, content: None,
            record_usage=lambda **kw: None,
            turn_count=0,
        ),
        critic=SimpleNamespace(review=lambda text: SimpleNamespace(text=text)),
        tool_definitions=[],
        _active_lane=None,
        _deep_review_analysis_rounds=0,
        _last_rendered_board=None,
        max_provider_requests=1,
        max_tool_rounds=1,
        tool_result_max_chars=6000,
        context_summary_enabled=False,
        provider_name="mock",
        model="mock-model",
    )

    def fake_run_turn(prompt, **kwargs):
        return "Done. Implemented the feature. Tests passed."

    agent.run_turn = fake_run_turn
    return agent


# ── Decomposition tests ───────────────────────────────────────────
# After simplification: decompose_goal() returns the operator's objective as-is.
# Ghost/provider plans the work — no regex title stamping.

def test_decompose_build_creates_one_step():
    steps = decompose_goal("build a CLI parser for config.yaml")
    assert len(steps) == 1
    assert steps[0].title == "build a CLI parser for config.yaml"


def test_decompose_generic_new_game_goal_uses_scoped_terminal_game_titles():
    steps = decompose_goal("build new game i would like quickly")
    titles = [step.title for step in steps]
    assert titles == ["build new game i would like quickly"]


def test_decompose_endless_runner_goal_uses_clean_game_titles():
    steps = decompose_goal("I want new running game that endless run")
    titles = [step.title for step in steps]
    assert len(steps) == 1
    assert titles[0] == "I want new running game that endless run"


def test_decompose_design_build_reuses_work_pattern_dna():
    steps = decompose_goal("build a responsive interface for the dashboard")
    titles = [step.title.lower() for step in steps]
    assert len(steps) == 1
    assert titles[0] == "build a responsive interface for the dashboard"


def test_decompose_write_story_html_gets_build_verify_shape():
    steps = decompose_goal("write me story into html")
    titles = [step.title.lower() for step in steps]
    assert len(steps) == 1
    assert titles[0] == "write me story into html"


def test_decompose_review_creates_one_step():
    steps = decompose_goal("deeply review E:\\my-project and report issues")
    assert len(steps) == 1
    assert steps[0].title == "deeply review E:\\my-project and report issues"


def test_decompose_token_savings_goal_gets_specific_reporting_steps():
    steps = decompose_goal("investigate token saving reporting always shows same savings")
    titles = [step.title for step in steps]
    assert titles == ["investigate token saving reporting always shows same savings"]


def test_decompose_multi_surface_mo_audit_goal_is_reflective_not_generic():
    steps = decompose_goal(
        "check MO session logs token reporting goal taskboard auditor workers PR complexity profile performance and fix docs"
    )
    titles = [step.title for step in steps]
    assert titles == ["check MO session logs token reporting goal taskboard auditor workers PR complexity profile performance and fix docs"]


def test_decompose_pr_review_goal_uses_pr_specific_readonly_shape():
    steps = decompose_goal("PR review for current changes")
    titles = [step.title for step in steps]
    assert titles == ["PR review for current changes"]


def test_decompose_fix_creates_one_step():
    steps = decompose_goal("fix the checkout bug in cart.py")
    assert len(steps) == 1
    assert steps[0].title == "fix the checkout bug in cart.py"


def test_decompose_simple_chat_creates_one_step():
    steps = decompose_goal("explain how Python generators work")
    assert len(steps) == 1
    assert steps[0].title == "explain how Python generators work"


# ── Budget parsing ────────────────────────────────────────────────

def test_parse_goal_budget_default():
    objective, budget = parse_goal_budget(["build", "a", "CLI"])
    assert objective == "build a CLI"
    assert budget.max_wall_seconds == 14400.0
    assert not hasattr(budget, "max_iterations")
    assert not hasattr(budget, "max_no_progress")


def test_parse_goal_budget_timeout_override():
    objective, budget = parse_goal_budget(["--timeout", "600", "fix", "the", "bug"])
    assert objective == "fix the bug"
    assert budget.max_wall_seconds == 600.0


def test_parse_goal_budget_timeout_caps_at_four_hours():
    objective, budget = parse_goal_budget(["--timeout", "999999", "review", "code"])
    assert objective == "review code"
    assert budget.max_wall_seconds == 14400.0


# ── GoalPlan dataclass ────────────────────────────────────────────

def test_goal_plan_completed_count():
    plan = GoalPlan(
        objective="test",
        steps=[
            GoalStep("1", "step one", status="completed", evidence=["read_file:x"]),
            GoalStep("2", "step two", status="active"),
            GoalStep("3", "step three"),
        ],
    )
    assert plan.completed_count() == 1
    assert plan.open_count() == 2
    assert not plan.all_done()


def test_goal_plan_all_done():
    plan = GoalPlan(
        objective="test",
        steps=[
            GoalStep("1", "a", status="completed", evidence=["e1"]),
            GoalStep("2", "b", status="completed", evidence=["e2"]),
        ],
    )
    assert plan.all_done()


def test_goal_plan_next_open_step():
    plan = GoalPlan(
        objective="test",
        steps=[
            GoalStep("1", "done", status="completed", evidence=["e"]),
            GoalStep("2", "active", status="active"),
            GoalStep("3", "pending"),
        ],
    )
    assert plan.next_open_step().id == "2"


def test_goal_plan_serialization():
    plan = GoalPlan(
        objective="build X",
        steps=[GoalStep("1", "step one")],
        run_id="test-001",
        started_at=1000.0,
    )
    data = plan.as_dict()
    assert data["objective"] == "build X"
    assert data["run_id"] == "test-001"
    assert len(data["steps"]) == 1
    assert data["steps"][0]["title"] == "step one"


def test_goal_runner_to_task_board_uses_current_taskboard_signature():
    agent = make_fake_agent()
    runner = GoalRunner(agent)
    plan = GoalPlan(
        objective="build game",
        run_id="run-1",
        steps=[
            GoalStep("1", "Build", status="completed", evidence=["write_file:game.py"]),
            GoalStep("2", "Verify", status="active", blocker="needs tests"),
        ],
    )

    board = runner.to_task_board(plan)

    assert board.turn_id == "goal-run-1"
    assert board.title == "Goal progress"
    assert board.objective == "build game"
    assert board.done_count() == 1
    assert board.open_count() == 1
    assert board.tasks[0].kind == "edit"
    assert board.tasks[0].completion_gate == "tool"
    assert board.tasks[1].title == "Verify"
    assert board.tasks[1].kind == "verify"
    assert board.tasks[1].completion_gate == "verification"
    assert board.tasks[1].depends_on == ["1"]


# ── GoalRunner tests ──────────────────────────────────────────────

def test_goal_persist_assigns_run_id_instead_of_writing_dot_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = make_fake_agent()
    runner = GoalRunner(agent)
    plan = GoalPlan(objective="verify", steps=[GoalStep("1", "Verify", status="completed")])

    runner._persist(plan)

    assert plan.run_id
    assert not Path("memory/goal-runs/.json").exists()
    assert Path(f"memory/goal-runs/{plan.run_id}.json").exists()


def test_goal_runner_persists_active_plan_after_iteration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = make_fake_agent()
    runner = GoalRunner(agent)

    runner.start("review codebase and report")

    assert Path(f"memory/goal-runs/{agent._goal_plan.run_id}.json").exists()


def test_goal_runner_pauses_before_step_when_active_worker_claims_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = make_fake_agent()
    agent.workers = WorkerRegistry()
    agent.workers.create(kind="worker", source="ghost", route="background", objective="edit core/agent.py", state="running")
    runner = GoalRunner(agent)

    result = runner.start("fix core/agent.py bug")

    assert result.startswith("[PAUSED]")
    assert "workspace conflict" in result
    assert agent._goal_active is False


def test_goal_runner_pauses_review_goal_without_tool_backed_evidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = make_fake_agent()
    agent.run_turn = lambda prompt, **kwargs: "Found issue in core/goal/goal.py line 1. Recommend fix with evidence-style wording."
    runner = GoalRunner(agent)

    result = runner.start("review codebase and report")
    for _ in range(8):
        if result.startswith("[PAUSED]"):
            break
        result = runner.continue_goal()

    assert result.startswith("[PAUSED]")
    assert "no tool-backed evidence" in result
    assert agent._goal_active is False


def test_goal_runner_start_creates_plan():
    agent = make_fake_agent()
    runner = GoalRunner(agent)
    runner.start("build a hello world script")
    assert agent._goal_plan is not None
    assert agent._goal_plan.objective == "build a hello world script"
    assert agent.workers.recent()[-1].state in {"running", "completed"}
    assert agent._goal_plan.iterations_run >= 1


def test_goal_runner_stop():
    agent = make_fake_agent()
    runner = GoalRunner(agent)
    runner.start("build something")
    result = runner.stop()
    assert "STOPPED" in result or "PAUSED" in result
    assert not agent._goal_active


def test_goal_runner_status_no_active():
    agent = make_fake_agent()
    runner = GoalRunner(agent)
    result = runner.status()
    assert "No active goal" in result


def test_goal_runner_empty_objective():
    agent = make_fake_agent()
    runner = GoalRunner(agent)
    result = runner.start("")
    assert "Usage" in result


def test_goal_repeated_same_step_rejection_triggers_replan_feedback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = make_fake_agent()
    agent.run_turn = lambda prompt, **kwargs: "Still blocked; current approach is not converging."
    calls = []

    def fake_replan(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content="Use the existing parser library and make the smallest wrapper."), "ghost"

    agent.complete_ghost_no_tools = fake_replan
    step = GoalStep("1", "Build markdown converter", status="active", reopened_count=3)
    plan = GoalPlan("build markdown converter", [step], run_id="replan-goal", started_at=time.time())
    agent._goal_plan = plan
    agent._goal_active = True

    GoalRunner(agent)._run_iteration()

    assert calls
    assert plan.replans_run == 1
    assert "Approach re-plan required" in plan.auditor_feedback
    assert "existing parser library" in plan.auditor_feedback
    assert step.status == "active"


def test_goal_replan_counter_caps_at_two(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = make_fake_agent()
    agent.run_turn = lambda prompt, **kwargs: "Still blocked; current approach is not converging."
    calls = []
    agent.complete_ghost_no_tools = lambda **kwargs: calls.append(kwargs) or (SimpleNamespace(content="new plan"), "ghost")
    step = GoalStep("1", "Build markdown converter", status="active", reopened_count=5)
    plan = GoalPlan("build markdown converter", [step], run_id="replan-cap", started_at=time.time(), replans_run=2)
    agent._goal_plan = plan
    agent._goal_active = True

    GoalRunner(agent)._run_iteration()

    assert calls == []
    assert plan.replans_run == 2


def test_goal_auditor_approval_completes_active_step_with_existing_evidence(tmp_path):
    agent = make_fake_agent()
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text("", encoding="utf-8")
    agent.sandbox_config = {"audit_log": str(audit_path)}
    agent.run_turn = lambda prompt, **kwargs: "All tests passed."
    step = GoalStep(
        "1",
        "Verify fix passes",
        status="active",
        evidence=["test_runner:pytest -q", "verification_result:passed"],
    )
    plan = GoalPlan("fix bug", [step], run_id="approve-existing", started_at=time.time())
    agent._goal_plan = plan
    agent._goal_active = True

    result = GoalRunner(agent)._run_iteration()

    assert step.status == "completed"
    assert "DONE" in result
    assert agent._goal_active is False


def test_goal_correct_progress_does_not_trigger_replan(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = make_fake_agent()
    audit_path = tmp_path / "audit.jsonl"
    agent.sandbox_config = {"audit_log": str(audit_path)}

    def fake_run_turn(prompt, **kwargs):
        audit_path.write_text(
            f'{{"ts": {time.time() + 1}, "surface":"goal", "tool":"read_file", "arguments":{{"path":"main.py"}}, "blocked":false}}\n',
            encoding="utf-8",
        )
        return "Inspected the requested implementation path with concrete file evidence."

    agent.run_turn = fake_run_turn
    calls = []
    agent.complete_ghost_no_tools = lambda **kwargs: calls.append(kwargs) or (SimpleNamespace(content="new plan"), "ghost")
    step = GoalStep("1", "Inspect implementation path", status="active")
    plan = GoalPlan("investigate implementation path", [step], run_id="replan-ok", started_at=time.time())
    agent._goal_plan = plan
    agent._goal_active = True

    GoalRunner(agent)._run_iteration()

    assert calls == []
    assert plan.replans_run == 0
    assert step.status == "completed"


def test_goal_replan_feedback_appears_in_next_turn_prompt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = make_fake_agent()
    agent.run_turn = lambda prompt, **kwargs: "Still blocked; current approach is not converging."
    agent.complete_ghost_no_tools = lambda **kwargs: (SimpleNamespace(content="Switch to the existing project helper before more edits."), "ghost")
    step = GoalStep("1", "Build markdown converter", status="active", reopened_count=3)
    plan = GoalPlan("build markdown converter", [step], run_id="replan-prompt", started_at=time.time())
    agent._goal_plan = plan
    agent._goal_active = True
    runner = GoalRunner(agent)

    runner._run_iteration()
    prompt = runner._build_turn_prompt(plan, step)

    assert "Approach re-plan required" in prompt
    assert "existing project helper" in prompt


def test_goal_replan_text_alone_does_not_complete_step():
    agent = make_fake_agent()
    runner = GoalRunner(agent)
    step = GoalStep("1", "Build markdown converter", status="active")
    plan = GoalPlan("build markdown converter", [step], started_at=time.time())

    runner._reopen_step(plan, step, "Approach re-plan required: choose a smaller/direct approach")

    assert step.status == "active"
    assert step.evidence == []
    assert plan.completed_count() == 0


# ── GoalAuditor tests ─────────────────────────────────────────────

def test_auditor_approves_step_with_evidence():
    auditor = GoalAuditor(FakeProfile())
    step = GoalStep("1", "Implement feature", status="completed", evidence=["write_file:main.py"])
    verdict = auditor.review_iteration(step, "Wrote main.py with the feature.")
    assert verdict.approved


def test_auditor_rejects_step_without_evidence():
    auditor = GoalAuditor(FakeProfile())
    step = GoalStep("1", "Implement feature", status="completed", evidence=[])
    verdict = auditor.review_iteration(step, "Done.")
    assert not verdict.approved
    assert any("without tool evidence" in f for f in verdict.findings)


def test_auditor_does_not_treat_write_tests_step_as_verification():
    auditor = GoalAuditor(FakeProfile())
    step = GoalStep("1", "Write code for tests for goal stale loop", status="completed", evidence=["write_file:tests/test_goal.py"])
    verdict = auditor.review_iteration(step, "Wrote tests/test_goal.py with focused regression coverage.")
    assert verdict.approved


def test_auditor_rejects_verify_without_test():
    auditor = GoalAuditor(FakeProfile())
    step = GoalStep("1", "Verify with tests", status="completed", evidence=["read_file:x.py"])
    verdict = auditor.review_iteration(step, "Looks good.")
    assert not verdict.approved
    assert any("test runner" in f for f in verdict.findings)


def test_auditor_approves_verify_with_test_runner():
    auditor = GoalAuditor(FakeProfile())
    step = GoalStep("1", "Verify with tests", status="completed", evidence=["test_runner:pytest -q"])
    verdict = auditor.review_iteration(step, "All tests passed.")
    assert verdict.approved


def test_auditor_uses_shared_lint_verification_evidence():
    auditor = GoalAuditor(FakeProfile())
    step = GoalStep("1", "Run primary lint/type checker", status="completed", evidence=["shell:python -m ruff check --output-format concise"])
    verdict = auditor.review_iteration(step, "All checks passed!")
    assert verdict.approved


def test_auditor_profile_rules_do_not_reopen_tool_backed_steps_for_generic_wording():
    auditor = GoalAuditor(FakeProfile())
    step = GoalStep("1", "Inspect design system", status="completed", evidence=["read_file:index.html"])
    content = "Done. " + ("This explains the implementation direction without paths. " * 20)

    verdict = auditor.review_iteration(step, content)

    assert verdict.approved


def test_auditor_completion_rejects_without_tool_evidence():
    auditor = GoalAuditor(FakeProfile())
    plan = GoalPlan(
        objective="build X",
        steps=[
            GoalStep("1", "step one", status="completed", evidence=["content:500chars"]),
            GoalStep("2", "step two", status="completed", evidence=["content:300chars"]),
        ],
    )
    verdict = auditor.review_completion(plan)
    assert not verdict.approved
    assert any("tool-backed" in f for f in verdict.findings)


def test_auditor_completion_approves_with_tool_evidence():
    auditor = GoalAuditor(FakeProfile())
    plan = GoalPlan(
        objective="build X",
        steps=[
            GoalStep("1", "inspect", status="completed", evidence=["read_file:main.py"]),
            GoalStep("2", "implement", status="completed", evidence=["write_file:main.py"]),
            GoalStep("3", "verify", status="completed", evidence=["test_runner:pytest", "verification_result:passed"]),
        ],
    )
    verdict = auditor.review_completion(plan)
    assert verdict.approved


def test_auditor_completion_rejects_blocked_steps():
    auditor = GoalAuditor(FakeProfile())
    plan = GoalPlan(
        objective="fix bug",
        steps=[
            GoalStep("1", "locate", status="completed", evidence=["grep:error"]),
            GoalStep("2", "fix", status="blocked", blocker="tests still fail"),
        ],
    )
    verdict = auditor.review_completion(plan)
    assert not verdict.approved
    assert any("blocked" in f for f in verdict.findings)


def test_auditor_completion_rejects_open_steps():
    auditor = GoalAuditor(FakeProfile())
    plan = GoalPlan(
        objective="build X",
        steps=[
            GoalStep("1", "inspect", status="completed", evidence=["read_file:main.py"]),
            GoalStep("2", "implement", status="active", evidence=[]),
            GoalStep("3", "verify", status="pending", evidence=[]),
        ],
    )
    verdict = auditor.review_completion(plan)
    assert not verdict.approved
    assert any("not completed" in f for f in verdict.findings)


def test_auditor_rejects_failing_verify_even_with_test_runner_evidence():
    auditor = GoalAuditor(FakeProfile())
    step = GoalStep("3", "Verify with tests", status="completed", evidence=["test_runner:pytest -q"])
    verdict = auditor.review_iteration(step, "pytest -q\n2 failed, 5 passed\n[exit code 1]")
    assert not verdict.approved
    assert any("failing tests" in f for f in verdict.findings)


# ── GoalAuditor.extract_learnings (profile learning derivation) ────

def test_extract_learnings_returns_empty_for_no_findings():
    auditor = GoalAuditor(FakeProfile())
    assert auditor.extract_learnings([]) == {}


def test_extract_learnings_noise_gate_drops_single_low_iteration_finding():
    auditor = GoalAuditor(FakeProfile())
    # One finding + few iterations = noise, not durable learning.
    insights = auditor.extract_learnings(
        ["step 'x' completed without tool evidence"],
        iterations_run=1,
    )
    assert insights == {}


def test_extract_learnings_records_evidence_gate_pattern():
    auditor = GoalAuditor(FakeProfile())
    insights = auditor.extract_learnings(
        [
            "step 'a' completed without tool evidence",
            "step 'b' completed without evidence",
        ],
        iterations_run=2,
    )
    assert any("tool evidence" in t for t in insights.get("evolution", []))
    assert any("provider prose alone is not proof" in t for t in insights.get("core_traits", []))


def test_extract_learnings_records_verification_pattern():
    auditor = GoalAuditor(FakeProfile())
    insights = auditor.extract_learnings(
        [
            "verification step lacks test_runner evidence",
            "verification shows failing tests",
        ],
        iterations_run=3,
    )
    assert any("verification" in t.lower() for t in insights.get("current_focus", []))
    assert any("test_runner tool evidence" in t for t in insights.get("core_traits", []))


def test_extract_learnings_records_staleness_pattern():
    auditor = GoalAuditor(FakeProfile())
    insights = auditor.extract_learnings(
        ["goal appears stale", "no tool-backed evidence after retries"],
        iterations_run=5,
    )
    assert any("staleness" in t.lower() for t in insights.get("current_focus", []))
    assert any("smaller sub-steps" in t for t in insights.get("evolution", []))


def test_extract_learnings_records_provider_error_pattern():
    auditor = GoalAuditor(FakeProfile())
    insights = auditor.extract_learnings(
        ["provider unavailable", "provider error blocked progress"],
        iterations_run=3,
    )
    assert any("provider errors" in t for t in insights.get("current_focus", []))


def test_extract_learnings_records_blocked_completion_pattern():
    auditor = GoalAuditor(FakeProfile())
    insights = auditor.extract_learnings(
        ["step 'fix' still blocked", "goal not completed"],
        iterations_run=3,
    )
    assert any("completed status with evidence" in t for t in insights.get("core_traits", []))


def test_extract_learnings_unrecognized_findings_yield_no_insights():
    auditor = GoalAuditor(FakeProfile())
    # Enough findings to pass the noise gate, but no known pattern markers.
    insights = auditor.extract_learnings(
        ["some neutral observation", "another neutral note"],
        iterations_run=3,
    )
    assert insights == {}


def test_goal_auditor_rejection_reopens_same_step_without_appending_task(tmp_path):
    agent = make_fake_agent()
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text('{"tool":"test_runner","arguments":{"command":"pytest -q"},"blocked":false}\n', encoding="utf-8")
    agent.sandbox_config = {"audit_log": str(audit_path)}
    agent.run_turn = lambda prompt, **kwargs: "pytest -q\n1 failed, 2 passed\n[exit code 1]"

    plan = GoalPlan(
        objective="fix failing tests",
        steps=[
            GoalStep("1", "Inspect evidence", status="completed", evidence=["read_file:test.py"]),
            GoalStep("2", "Apply minimal fix", status="completed", evidence=["edit_file:app.py"]),
            GoalStep("3", "Verify fix passes", status="active"),
        ],
        run_id="test-goal",
        started_at=time.time(),
    )
    agent._goal_plan = plan
    agent._goal_active = True

    result = GoalRunner(agent)._run_iteration()

    assert len(plan.steps) == 3
    assert plan.steps[2].status == "active"
    assert plan.completed_count() == 2
    assert "failed" in " ".join(plan.steps[2].evidence)
    assert "Auditor:" not in result


def test_goal_reopened_step_with_old_evidence_does_not_autocomplete_without_new_evidence(tmp_path):
    agent = make_fake_agent()
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text('', encoding="utf-8")
    agent.sandbox_config = {"audit_log": str(audit_path)}
    step = GoalStep("1", "Investigate stale loop", status="active", evidence=["read_file:core/goal/goal.py"])
    plan = GoalPlan("investigate", [step], started_at=time.time())

    GoalRunner(agent)._record_evidence(plan, step, "short", since_ts=time.time())

    assert step.status == "active"


def test_goal_verify_pass_after_failure_clears_failed_marker(tmp_path):
    agent = make_fake_agent()
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text('{"ts": 20, "tool":"shell", "arguments":{"command":"pytest -q"}, "blocked":false}\n', encoding="utf-8")
    agent.sandbox_config = {"audit_log": str(audit_path)}
    step = GoalStep("1", "Verify with tests", status="active", evidence=["verification_result:failed"])
    plan = GoalPlan("verify", [step], started_at=time.time())

    GoalRunner(agent)._record_evidence(plan, step, "1 passed [exit code 0]", since_ts=15)

    assert "verification_result:failed" not in step.evidence
    assert "verification_result:passed" in step.evidence
    assert step.status == "completed"


def test_goal_record_evidence_ignores_stale_audit_entries(tmp_path):
    agent = make_fake_agent()
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        '{"ts": 10, "tool":"read_file", "arguments":{"path":"old.py"}, "blocked":false}\n'
        '{"ts": 20, "tool":"shell", "arguments":{"command":"pytest -q"}, "blocked":false}\n',
        encoding="utf-8",
    )
    agent.sandbox_config = {"audit_log": str(audit_path)}
    step = GoalStep("1", "Verify with tests", status="active")
    plan = GoalPlan("verify", [step], started_at=time.time())

    GoalRunner(agent)._record_evidence(plan, step, "1 passed", since_ts=15)

    assert step.evidence == ["shell:pytest -q", "verification_result:passed"]


def test_goal_runner_passes_monitor_to_agent_turn_when_supported():
    agent = make_fake_agent()
    seen = {}

    class Monitor:
        def emit(self, *_args, **_kwargs):
            pass

    monitor = Monitor()
    def fake_run_turn(prompt, **kwargs):
        seen["kwargs"] = kwargs
        return "done"

    agent.run_turn = fake_run_turn

    result = GoalRunner(agent)._run_agent_turn("work", monitor)

    assert result == "done"
    assert seen["kwargs"]["monitor"] is monitor


def test_goal_record_evidence_filters_other_surface_and_worker_entries(tmp_path):
    agent = make_fake_agent()
    agent._goal_worker_id = "w-goal"
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        '{"ts": 20, "surface":"main", "worker_id":"", "tool":"read_file", "arguments":{"path":"main.py"}, "blocked":false}\n'
        '{"ts": 21, "surface":"goal", "worker_id":"w-other", "tool":"read_file", "arguments":{"path":"other.py"}, "blocked":false}\n'
        '{"ts": 22, "surface":"goal", "worker_id":"w-goal", "tool":"read_file", "arguments":{"path":"goal.py"}, "blocked":false}\n',
        encoding="utf-8",
    )
    agent.sandbox_config = {"audit_log": str(audit_path)}
    step = GoalStep("1", "Inspect repository state", status="active")
    plan = GoalPlan("review repo", [step], run_id="goal-run", started_at=time.time())

    GoalRunner(agent)._record_evidence(plan, step, "short", since_ts=15)

    assert step.evidence == ["read_file:goal.py"]
    assert step.status == "completed"


def test_goal_record_evidence_accepts_shared_read_tools(tmp_path):
    agent = make_fake_agent()
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text('{"ts": 20, "tool":"git_status", "arguments":{}, "blocked":false}\n', encoding="utf-8")
    agent.sandbox_config = {"audit_log": str(audit_path)}
    step = GoalStep("1", "Inspect repository state", status="active")
    plan = GoalPlan("review repo", [step], started_at=time.time())

    GoalRunner(agent)._record_evidence(plan, step, "short", since_ts=15)

    assert step.evidence == ["git_status"]
    assert step.status == "completed"


def test_goal_broad_repair_step_ignores_support_artifact_write(tmp_path):
    agent = make_fake_agent()
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        '{"ts": 20, "tool":"write_file", "arguments":{"path":"_check_compile.py"}, "blocked":false}\n',
        encoding="utf-8",
    )
    agent.sandbox_config = {"audit_log": str(audit_path)}
    step = GoalStep("2", "Fix confirmed broken/incomplete examples", status="active")
    plan = GoalPlan("fix examples", [GoalStep("1", "Inspect", "completed", ["find_files:examples"]), step], started_at=time.time())

    GoalRunner(agent)._record_evidence(plan, step, "wrote helper", since_ts=15)

    assert step.evidence == ["write_file:_check_compile.py"]
    assert step.status == "active"


def test_goal_broad_repair_step_completes_after_scoped_edit(tmp_path):
    agent = make_fake_agent()
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        '{"ts": 20, "tool":"edit_file", "arguments":{"path":"examples/menu.py"}, "blocked":false}\n',
        encoding="utf-8",
    )
    agent.sandbox_config = {"audit_log": str(audit_path)}
    step = GoalStep("2", "Fix confirmed broken/incomplete examples", status="active")
    verify = GoalStep("3", "Verify all examples and tests", status="pending")
    plan = GoalPlan("fix examples", [GoalStep("1", "Inspect", "completed", ["find_files:examples"]), step, verify], started_at=time.time())

    GoalRunner(agent)._record_evidence(plan, step, "edited examples/menu.py", since_ts=15)

    assert step.evidence == ["edit_file:examples/menu.py"]
    assert step.status == "completed"
    assert verify.status == "active"


def test_goal_finish_records_per_goal_context_savings_delta():
    agent = make_fake_agent()
    agent.compression_total_saved = 1200
    agent.compression_total_ops = 2
    agent.truncation_total_saved = 800
    agent.truncation_total_ops = 1
    plan = GoalPlan(
        "review token savings",
        [GoalStep("1", "Map token/context-savings reporting surfaces", status="completed", evidence=["read_file:core/agent.py"])],
        started_at=time.time(),
        context_savings_start_chars=500,
        context_savings_start_ops=1,
    )

    GoalRunner(agent)._finish(plan, "completed", "done")

    assert plan.context_savings_chars == 1500
    assert plan.context_savings_ops == 2


def test_goal_provider_error_retries_same_step_without_fake_test_failure():
    agent = make_fake_agent()
    agent.run_turn = lambda prompt, **kwargs: "Provider error: unavailable"
    plan = GoalPlan(
        objective="build a CLI parser",
        steps=[GoalStep("1", "Inspect context", status="active")],
        run_id="test-goal",
        started_at=time.time(),
    )
    agent._goal_plan = plan
    agent._goal_active = True

    result = GoalRunner(agent)._run_iteration()

    assert len(plan.steps) == 1
    assert plan.steps[0].status == "active"
    assert "provider error" in plan.auditor_feedback
    assert "failing tests" not in result.lower()


def test_goal_provider_errors_pause_after_three_without_progress_claim():
    agent = make_fake_agent()
    agent.run_turn = lambda prompt, **kwargs: "Provider error: timeout"
    runner = GoalRunner(agent)
    runner._persist = lambda plan: None

    first = runner.start("build parser")
    second = runner.continue_goal()
    third = runner.continue_goal()

    assert first.startswith("[GOAL]")
    assert second.startswith("[GOAL]")
    assert third.startswith("[PAUSED]")
    assert "provider unavailable after 3 consecutive errors" in third
    assert agent._goal_plan.completed_count() == 0
    assert agent._goal_active is False


def test_goal_stop_sets_finished_at():
    agent = make_fake_agent()
    runner = GoalRunner(agent)
    runner._persist = lambda plan: None
    runner.start("build parser")

    result = runner.stop()

    assert result.startswith("[GOAL STOPPED]")
    assert agent._goal_plan.finished_at is not None


def test_goal_finish_updates_worker_result_summary_and_evidence():
    agent = make_fake_agent()
    agent.workers = WorkerRegistry()
    record = agent.workers.create(kind="goal", source="user", route="background", objective="verify", state="running", worker_id="goal-1")
    agent._goal_worker_id = record.id
    step = GoalStep("1", "Inspect context", status="completed", evidence=["read_file:README.md"])
    plan = GoalPlan("verify", [step], run_id="goal-1", started_at=time.time())

    GoalRunner(agent)._finish(plan, "completed", "done")

    updated = agent.workers.get("goal-1")
    assert updated is not None
    assert updated.result_summary.startswith("Goal completed: 1/1 done")
    assert updated.evidence == ["read_file:README.md"]


def test_goal_finish_clears_stale_blockers_and_sets_finished_at():
    agent = make_fake_agent()
    step = GoalStep("1", "Verify with tests", status="completed", blocker="old failure")
    plan = GoalPlan("verify", [step], started_at=time.time())

    result = GoalRunner(agent)._finish(plan, "completed", "done")

    assert "DONE" in result
    assert plan.finished_at is not None
    assert step.blocker == ""
    assert plan.as_dict()["finished_at"] == plan.finished_at


def test_goal_final_auditor_adds_missing_verify_step_for_legacy_misclassified_plan():
    agent = make_fake_agent()
    plan = GoalPlan(
        objective="write me story into html",
        steps=[
            GoalStep("1", "Investigate story request", status="completed", evidence=["read_file:examples/story_fixture.html"]),
            GoalStep("2", "Produce evidence-backed answer", status="completed", evidence=["content:1200chars"]),
        ],
        run_id="legacy-goal",
        started_at=time.time(),
    )
    agent._goal_plan = plan
    agent._goal_active = True

    result = GoalRunner(agent)._try_complete(plan)

    assert len(plan.steps) == 3
    assert plan.steps[2].status == "active"
    assert "verify" in plan.steps[2].title.lower()
    assert "verification step" in plan.steps[2].blocker
    assert "Auditor:" not in result


def test_goal_final_auditor_reopens_existing_step_without_appending_task():
    agent = make_fake_agent()
    plan = GoalPlan(
        objective="build a CLI parser",
        steps=[
            GoalStep("1", "Inspect context", status="completed", evidence=["read_file:main.py"]),
            GoalStep("2", "Write code", status="completed", evidence=["write_file:main.py"]),
            GoalStep("3", "Verify locally", status="completed", evidence=["content:looks good"]),
        ],
        run_id="test-goal",
        started_at=time.time(),
    )
    agent._goal_plan = plan
    agent._goal_active = True

    result = GoalRunner(agent)._try_complete(plan)

    assert len(plan.steps) == 3
    assert plan.steps[2].status == "active"
    assert plan.auditor_feedback
    assert "Auditor:" not in result


# ── _record_goal_learning persistence path ───────────────────────────

class CapturingProfile:
    """Profile that records append_profile_learning calls for assertions."""
    _path = "memory/mo.db"
    user_name = "TestUser"

    def __init__(self):
        self.calls = []

    def append_profile_learning(self, source, insights):
        self.calls.append((source, insights))


def _learning_plan(iterations_run=4, run_id="goal-xyz"):
    plan = GoalPlan(
        objective="harden the auditor pipeline",
        steps=[GoalStep("1", "step", status="completed", evidence=["e"])],
        run_id=run_id,
    )
    plan.iterations_run = iterations_run
    return plan


def test_record_goal_learning_no_findings_is_noop():
    agent = make_fake_agent()
    agent.profile = CapturingProfile()
    runner = GoalRunner(agent)
    runner._record_goal_learning(_learning_plan(), GoalAuditor(agent.profile), [])
    assert agent.profile.calls == []


def test_record_goal_learning_skips_when_profile_lacks_method():
    agent = make_fake_agent()
    agent.profile = SimpleNamespace()  # no append_profile_learning
    runner = GoalRunner(agent)
    # Findings present, but no writable profile → must not raise.
    runner._record_goal_learning(
        _learning_plan(), GoalAuditor(FakeProfile()),
        ["unverified completion", "tests not run"],
    )


def test_record_goal_learning_noise_gate_no_write():
    agent = make_fake_agent()
    agent.profile = CapturingProfile()
    runner = GoalRunner(agent)
    # Single finding + few iterations → extract_learnings returns empty → no write.
    runner._record_goal_learning(
        _learning_plan(iterations_run=1), GoalAuditor(agent.profile),
        ["one stray finding"],
    )
    assert agent.profile.calls == []


def test_record_goal_learning_writes_high_signal_insights():
    agent = make_fake_agent()
    agent.profile = CapturingProfile()
    runner = GoalRunner(agent)
    runner._record_goal_learning(
        _learning_plan(iterations_run=4, run_id="goal-xyz"),
        GoalAuditor(agent.profile),
        ["claimed done but tests not run", "unverified completion without evidence"],
        reason="auditor rejected unverified work",
    )
    assert len(agent.profile.calls) == 1
    source, insights = agent.profile.calls[0]
    assert source == "goal-auditor:goal-xyz"
    assert isinstance(insights, dict) and insights


def test_record_goal_learning_swallows_write_errors():
    agent = make_fake_agent()

    class ExplodingProfile(CapturingProfile):
        def append_profile_learning(self, source, insights):
            raise RuntimeError("disk full")

    agent.profile = ExplodingProfile()
    runner = GoalRunner(agent)
    # Must not propagate — persistence is best-effort.
    runner._record_goal_learning(
        _learning_plan(iterations_run=4),
        GoalAuditor(agent.profile),
        ["claimed done but tests not run", "unverified completion without evidence"],
    )


import pytest as _pytest_state_lane


@_pytest_state_lane.fixture(autouse=True)
def _legacy_state_lane(monkeypatch, tmp_path):
    """This module asserts legacy project-relative state behavior; opt out of
    the conftest MO_STATE_HOME isolation. chdir to a tmp so 'project-local'
    state lands there, never the repo root; MO_PROJECT_CWD still points at the
    real checkout for any code that reads project source."""
    from core.path_defaults import repo_root
    monkeypatch.delenv("MO_STATE_HOME", raising=False)
    monkeypatch.delenv("MO_HOME", raising=False)
    monkeypatch.setenv("MO_STATE_LOCAL", "1")  # explicit project-local opt-out (state is private-by-default)
    monkeypatch.setenv("MO_PROJECT_CWD", str(repo_root()))
    monkeypatch.chdir(tmp_path)
