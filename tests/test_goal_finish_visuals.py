from types import SimpleNamespace

from core.goal import GoalPlan, GoalStep
from interface.main_terminal import MoTui


class _FakeOutput:
    def __init__(self, rows=20, columns=100):
        self._size = SimpleNamespace(rows=rows, columns=columns)

    def get_size(self):
        return self._size


class _FakeApp:
    def __init__(self, rows=20, columns=100):
        self.output = _FakeOutput(rows=rows, columns=columns)
        self.invalidated = 0

    def invalidate(self):
        self.invalidated += 1


def _plain(fragments):
    return "".join(text for _style, text in fragments)


def _make_tui(rows=20, columns=100):
    agent = SimpleNamespace(session=SimpleNamespace(token_log=[]), provider_name="mock", model="mock", config={})
    tui = MoTui(agent, SimpleNamespace())
    tui._app = _FakeApp(rows=rows, columns=columns)
    return tui


def test_goal_finish_hides_only_goal_board_and_preserves_main_board_cache():
    tui = _make_tui()
    tui.board_text = "2 tasks (1 done, 1 open)\n√ Main inspect\n→ Main verify"
    tui._goal_board_text = "1 tasks (1 done, 0 open)\n√ Goal task"
    tui.agent._goal_plan = GoalPlan(objective="small goal", steps=[GoalStep("1", "Goal task", "completed")])

    tui._goal_finish("[✓ DONE] Goal: 1/1 done · 1s\nall steps done")

    assert tui._goal_board_text == ""
    assert "Main inspect" in tui.board_text
    assert "Main verify" in _plain(tui._get_board_fragments())
    assert _plain(tui._get_goal_board_fragments()) == ""


def test_goal_finish_hides_live_board_and_reports_compact_details():
    tui = _make_tui()
    tui.agent.compression_total_saved = 4800
    tui.agent.compression_total_ops = 2
    tui.agent._compression_saved_tokens_estimate = lambda: 1200
    tui.agent._goal_plan = GoalPlan(
        objective="build a responsive interface for the dashboard",
        steps=[
            GoalStep("1", "Inspect local design system and context for dashboard", "completed", ["read_file:interface/layout.py"]),
            GoalStep("2", "Build scoped UI for dashboard", "completed", ["edit_file:interface/tui_goal.py"]),
            GoalStep("3", "Verify dashboard with local static design quality gate", "completed", ["shell:python -m pytest tests/test_goal_finish_visuals.py", "verification_result:passed"]),
        ],
    )
    tui._goal_board_text = "3 tasks (3 done, 0 open)\n√ Inspect\n√ Build\n√ Verify"

    tui._goal_finish("[✓ DONE] Goal: 3/3 done · 6.0m\nall steps done")
    rendered = _plain(tui._get_transcript())
    final_line = [line.strip() for line in rendered.splitlines() if line.strip()][-1]

    assert tui._goal_board_text == ""
    assert _plain(tui._get_goal_board_fragments()) == ""
    assert "Goal report" in rendered
    assert "Flow:" in rendered
    assert "Did:" in rendered
    assert "Build scoped UI for dashboard" in rendered
    assert "References:" in rendered
    assert "interface/tui_goal.py" in rendered
    assert "Checks:" in rendered
    assert "ran pytest" in rendered
    assert "3 tasks (3 done, 0 open)" not in rendered
    assert final_line == "Goal finished Goal: 3/3 done · 6.0m · saved ~1.2k tokens · complexity moderate"
    assert rendered.rstrip().endswith(final_line)


def test_goal_finish_uses_per_goal_context_savings_when_available():
    tui = _make_tui()
    tui.agent.compression_total_saved = 8000
    tui.agent._compression_saved_tokens_estimate = lambda: 2000
    tui.agent._goal_plan = GoalPlan(
        objective="audit token savings reporting",
        steps=[GoalStep("1", "Map token/context-savings reporting surfaces", "completed", ["read_file:core/agent.py"])],
        context_savings_chars=1600,
    )

    tui._goal_finish("[✓ DONE] Goal: 1/1 done · 1m\nall steps done")
    rendered = _plain(tui._get_transcript())
    final_line = [line.strip() for line in rendered.splitlines() if line.strip()][-1]

    assert "saved ~400 tokens" in final_line
    assert "saved ~2k tokens" not in final_line


def test_goal_finish_reports_caveats_for_paused_or_blocked_goal():
    tui = _make_tui()
    tui.agent._goal_plan = GoalPlan(
        objective="fix failing tests",
        steps=[
            GoalStep("1", "Inspect evidence", "completed", ["read_file:tests/test_goal.py"]),
            GoalStep("2", "Apply minimal fix", "active", [], "tests still fail"),
            GoalStep("3", "Verify resolution", "pending"),
        ],
        auditor_feedback="verification still failing",
    )

    tui._goal_finish("[PAUSED] Goal: 1/3 done · 5m\ntime budget reached")
    rendered = _plain(tui._get_transcript())
    final_line = [line.strip() for line in rendered.splitlines() if line.strip()][-1]

    assert "Goal report" in rendered
    assert "Caveats:" in rendered
    assert "time budget reached" in rendered
    assert "tests still fail" in rendered
    assert "verification still failing" in rendered
    assert final_line == "Goal paused Goal: 1/3 done · 5m · saved 0 tokens · complexity simple"
    assert rendered.rstrip().endswith(final_line)
