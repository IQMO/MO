from types import SimpleNamespace

from core.tasking.task_board import TaskBoard, TaskItem
from core.workspace_awareness import build_workspace_awareness, prt_safe_to_mutate, should_include_workspace_awareness


class FakeProc:
    returncode = 0
    stdout = "## main\n M core/agent.py\n?? core/workspace_awareness.py\n"
    stderr = ""


def test_workspace_awareness_summarizes_uncommitted_changes(monkeypatch):
    monkeypatch.setattr("core.workspace_awareness.subprocess.run", lambda *a, **k: FakeProc())

    text = build_workspace_awareness(SimpleNamespace(_goal_active=False, _goal_plan=None), cwd=".")

    assert "Workspace / worker awareness" in text
    assert "2 uncommitted file(s)" in text
    assert "M core/agent.py" in text
    assert "avoid conflicting edits" in text


def test_workspace_awareness_skips_simple_greetings_but_keeps_status_requests():
    assert should_include_workspace_awareness("hi mo") is False
    assert should_include_workspace_awareness("what are we cooking today?") is True
    assert should_include_workspace_awareness("fix the checkout bug") is True


def test_prt_safe_to_mutate_checks_project_cwd(monkeypatch, tmp_path):
    calls = []

    def fake_run(*_args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="## main\n", stderr="")

    monkeypatch.setattr("core.workspace_awareness.subprocess.run", fake_run)

    ok, reason = prt_safe_to_mutate(SimpleNamespace(project_cwd=str(tmp_path), workers=None, _goal_active=False))

    assert ok is True
    assert reason == ""
    assert calls[0]["cwd"] == str(tmp_path)


def test_workspace_awareness_includes_active_goal_and_task_board(monkeypatch):
    monkeypatch.setattr("core.workspace_awareness.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="## main\n", stderr=""))
    plan = SimpleNamespace(
        objective="review visuals",
        steps=[1, 2],
        completed_count=lambda: 1,
    )
    board = TaskBoard("turn-1", "deep_review", [TaskItem("1", "Inspect UI", "active")])
    gateway = SimpleNamespace(last_task_board=board)
    agent = SimpleNamespace(_goal_active=True, _goal_plan=plan, gateway=gateway)

    text = build_workspace_awareness(agent, cwd=".")

    assert "Git state: clean" in text
    assert "Background MO worker active: review visuals · 1/2 done" in text
    assert "Recent task board: 1 tasks (0 done, 1 open)" in text
