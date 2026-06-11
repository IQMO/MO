from types import SimpleNamespace

from core.coordination_state import build_main_coordination_context, goal_summary_lines, worker_summary_lines
from core.goal import GoalPlan, GoalStep
from core.workers import WorkerRegistry


def test_main_coordination_context_warns_on_active_worker_path_conflict():
    registry = WorkerRegistry()
    worker = registry.create(
        kind="worker",
        source="ghost",
        route="background",
        objective="edit core/agent.py",
        state="running",
        claimed_paths=["core/agent.py"],
    )
    agent = SimpleNamespace(workers=registry)

    context = build_main_coordination_context(agent, "fix `core/agent.py` now")

    assert "Coordination conflict" in context
    assert worker.id in context
    assert "priority 1" in context.lower()
    assert "do not overwrite" in context


def test_main_coordination_context_is_empty_without_explicit_path_conflict():
    registry = WorkerRegistry()
    registry.create(kind="worker", source="ghost", route="background", objective="edit core/agent.py", state="running")
    agent = SimpleNamespace(workers=registry)

    assert build_main_coordination_context(agent, "say hi") == ""


def test_shared_worker_and_goal_summary_lines():
    registry = WorkerRegistry()
    registry.create(kind="ghost", source="user", route="main", objective="side question", state="running")
    plan = GoalPlan(
        objective="review docs",
        steps=[GoalStep("1", "Inspect docs", status="active", evidence=["read_file:docs/x.md"])],
        state="running",
        iterations_run=2,
    )
    agent = SimpleNamespace(workers=registry, _goal_plan=plan)

    assert any("ghost/main" in row for row in worker_summary_lines(agent))
    goal_rows = goal_summary_lines(agent, include_evidence=True)
    assert any("review docs" in row for row in goal_rows)
    assert any("read_file:docs/x.md" in row for row in goal_rows)
