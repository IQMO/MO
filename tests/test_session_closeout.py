import json
import os
import time
from types import SimpleNamespace

from core.learning.proactive_learning import read_learning_suggestions
from core.session.session_closeout import SessionCloseout, build_session_closeout, prune_session_closeouts, render_session_closeout, stage_session_closeout_feedback, write_session_closeout
from core.tasking.task_board import TaskBoard, TaskItem
from core.workers import WorkerRegistry


class ProfileStub:
    def __init__(self, path):
        self._path = str(path)

    def append_profile_learning(self, *_args, **_kwargs):
        raise AssertionError("closeout feedback must stage candidates, not profile learning")


def test_session_closeout_reports_unresolved_tasks_and_token_savings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    board = TaskBoard(
        "turn-1",
        "build_create",
        [
            TaskItem("1", "Inspect context", "completed", ["read_file:README.md"]),
            TaskItem("2", "Verify work", "active"),
        ],
    )
    workers = WorkerRegistry()
    workers.create(kind="goal", source="user", route="background", objective="review docs", state="running")
    agent = SimpleNamespace(
        session=SimpleNamespace(
            session_id="s1",
            turn_count=2,
            messages=[{"role": "user", "content": "build"}],
            token_log=[{"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}],
            max_history=50,
            created_at=0,
            trimmed_messages_count=0,
        ),
        _sessions=SimpleNamespace(current_name="main"),
        gateway=SimpleNamespace(last_task_board=board),
        workers=workers,
        _goal_active=False,
        compression_total_ops=3,
        compression_total_saved=4000,
        compression_last_pct=40,
    )

    closeout = build_session_closeout(agent, reason="unit test")
    rendered = render_session_closeout(closeout)
    path = write_session_closeout(closeout, root=tmp_path / "closeouts")

    assert closeout.clean is False
    assert closeout.compression_saved_tokens_est == 1000
    assert any("Verify work" in item for item in closeout.unresolved)
    assert any("worker" in item for item in closeout.unresolved)
    assert "~1,000 tokens" in rendered
    assert path.exists()
    assert "Gateway/taskboard evidence" in path.read_text(encoding="utf-8")


def test_session_closeout_writes_file_ops_under_runtime_home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    audit = home / "logs" / "tool_audit.jsonl"
    audit.parent.mkdir(parents=True)
    audit.write_text(json.dumps({
        "ts": time.time(),
        "tool": "read_file",
        "arguments": {"path": str(tmp_path / "project" / "README.md")},
        "blocked": False,
    }) + "\n", encoding="utf-8")
    agent = SimpleNamespace(
        config={"runtime": {"home": str(home), "state": "private"}},
        project_cwd=str(tmp_path / "project"),
        session=SimpleNamespace(
            session_id="s-files",
            turn_count=1,
            messages=[{"role": "user", "content": "read"}],
            token_log=[],
            total_tokens=0,
            output_tokens=0,
            max_history=50,
            created_at=0,
            trimmed_messages_count=0,
        ),
        _sessions=SimpleNamespace(current_name="main"),
        compression_total_ops=0,
        compression_total_saved=0,
        compression_last_pct=0,
        workers=WorkerRegistry(),
        _goal_active=False,
        provider_name="mock",
        model="mock-model",
    )

    build_session_closeout(agent)

    ops = home / "memory" / "file_operations.jsonl"
    assert ops.exists()
    row = json.loads(ops.read_text(encoding="utf-8").splitlines()[-1])
    assert row["session_id"] == "s-files"
    assert row["files_read"]


def test_session_closeout_prunes_old_artifacts(tmp_path):
    root = tmp_path / "closeouts"
    root.mkdir()
    for idx in range(5):
        path = root / f"old-{idx}.md"
        path.write_text(str(idx), encoding="utf-8")
        stamp = 1_700_000_000 + idx
        os.utime(path, (stamp, stamp))

    removed = prune_session_closeouts(root, keep=3)

    remaining = sorted(path.name for path in root.glob("*.md"))
    assert len(removed) == 2
    assert remaining == ["old-2.md", "old-3.md", "old-4.md"]


def test_write_session_closeout_prunes_after_write(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "closeouts"
    root.mkdir()
    for idx in range(3):
        path = root / f"old-{idx}.md"
        path.write_text(str(idx), encoding="utf-8")
        stamp = 1_700_000_000 + idx
        os.utime(path, (stamp, stamp))
    agent = SimpleNamespace(
        session=SimpleNamespace(
            session_id="s-new",
            turn_count=1,
            messages=[{"role": "user", "content": "hi"}],
            token_log=[],
            total_tokens=0,
            output_tokens=0,
            max_history=50,
            created_at=0,
            trimmed_messages_count=0,
        ),
        _sessions=SimpleNamespace(current_name="main"),
        compression_total_ops=0,
        compression_total_saved=0,
        compression_last_pct=0,
        workers=WorkerRegistry(),
        _goal_active=False,
    )

    new_path = write_session_closeout(build_session_closeout(agent), root=root, keep=2)

    remaining = {path.name for path in root.glob("*.md")}
    assert new_path.exists()
    assert new_path.name in remaining
    assert len(remaining) == 2
    assert "old-0.md" not in remaining


def test_session_closeout_includes_learning_delta(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (profile_dir / "learning.md").write_text(
        f"## {stamp} — profile learning\n- core_traits: verify first\n",
        encoding="utf-8",
    )
    agent = SimpleNamespace(
        session=SimpleNamespace(
            session_id="s-learn",
            turn_count=1,
            messages=[],
            token_log=[],
            total_tokens=0,
            output_tokens=0,
            max_history=50,
            created_at=1,
            trimmed_messages_count=0,
        ),
        profile=SimpleNamespace(_path=str(tmp_path / "mo.db")),
        _sessions=SimpleNamespace(current_name="main"),
        compression_total_ops=0,
        compression_total_saved=0,
        compression_last_pct=0,
        workers=WorkerRegistry(),
        _goal_active=False,
    )

    closeout = build_session_closeout(agent, reason="learned")

    assert closeout.learning_delta
    assert "core_traits" in closeout.learning_delta[0]


def test_session_closeout_can_be_clean(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = SimpleNamespace(
        session=SimpleNamespace(
            session_id="s-clean",
            turn_count=1,
            messages=[{"role": "user", "content": "hi"}],
            token_log=[],
            total_tokens=0,
            output_tokens=0,
            max_history=50,
            created_at=0,
            trimmed_messages_count=0,
        ),
        _sessions=SimpleNamespace(current_name="main"),
        compression_total_ops=0,
        compression_total_saved=0,
        compression_last_pct=0,
        workers=WorkerRegistry(),
        _goal_active=False,
    )

    closeout = build_session_closeout(agent, reason="clean")

    assert closeout.clean is True
    assert closeout.unresolved == ()


def test_session_closeout_writes_runtime_learning_suggestions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "tool_audit.jsonl").write_text(
        json.dumps({"ts": 20.0, "tool": "shell", "blocked": True}) + "\n",
        encoding="utf-8",
    )
    (logs / "provider_audit.jsonl").write_text(
        json.dumps({"ts": 21.0, "event": "provider_error", "reason": "timeout"}) + "\n",
        encoding="utf-8",
    )
    agent = SimpleNamespace(
        session=SimpleNamespace(
            session_id="s-runtime-learning",
            turn_count=1,
            messages=[],
            token_log=[],
            total_tokens=0,
            output_tokens=0,
            max_history=50,
            created_at=10.0,
            trimmed_messages_count=0,
        ),
        _sessions=SimpleNamespace(current_name="main"),
        compression_total_ops=0,
        compression_total_saved=0,
        compression_last_pct=0,
        workers=WorkerRegistry(),
        _goal_active=False,
        gateway=SimpleNamespace(last_task_board=TaskBoard("turn", "fix", [TaskItem("1", "Verify", "active")]))
    )

    build_session_closeout(agent, reason="runtime learning")

    suggestions = read_learning_suggestions(path=tmp_path / "memory" / "learning_suggestions.jsonl")
    kinds = {item.kind for item in suggestions}
    assert "closeout:open_taskboard" in kinds
    assert "closeout:tool_blocks" in kinds
    assert "closeout:provider_errors" in kinds


def test_session_closeout_feedback_ignores_single_session_noise(tmp_path):
    profile = ProfileStub(tmp_path / "mo.db")
    closeout = SessionCloseout(
        reason="unit",
        session_id="s1",
        slot="main",
        turn_count=1,
        message_count=2,
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
        unresolved=("workspace has 1 uncommitted file(s)",),
        dirty_files=("M core/x.py",),
        clean=False,
    )

    result = stage_session_closeout_feedback(profile, closeout, closeout_path="one.md")

    assert result["staged"] is False
    assert "dirty_workspace" in result["patterns"]
    assert not (tmp_path / "workflow_candidates.jsonl").exists()


def test_session_closeout_feedback_stages_repeated_pattern_candidate(tmp_path):
    profile = ProfileStub(tmp_path / "mo.db")
    first = SessionCloseout(
        reason="unit",
        session_id="s1",
        slot="main",
        turn_count=1,
        message_count=2,
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
        unresolved=("workspace has 1 uncommitted file(s)",),
        dirty_files=("M core/x.py",),
        clean=False,
    )
    second = SessionCloseout(
        reason="unit",
        session_id="s2",
        slot="main",
        turn_count=1,
        message_count=2,
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
        unresolved=("workspace has 2 uncommitted file(s)",),
        dirty_files=("M core/y.py", "M core/z.py"),
        clean=False,
    )

    assert stage_session_closeout_feedback(profile, first, closeout_path="one.md")["staged"] is False
    result = stage_session_closeout_feedback(profile, second, closeout_path="two.md")

    stored = (tmp_path / "workflow_candidates.jsonl").read_text(encoding="utf-8")
    assert result["staged"] is True
    assert "dirty_workspace" in result["repeated"]
    assert "session-closeout" in stored
    assert "requires explicit approval" in stored


def test_session_closeout_feedback_requires_consecutive_pattern(tmp_path):
    profile = ProfileStub(tmp_path / "mo.db")
    dirty = SessionCloseout(
        reason="unit",
        session_id="s1",
        slot="main",
        turn_count=1,
        message_count=2,
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
        unresolved=("workspace has 1 uncommitted file(s)",),
        dirty_files=("M core/x.py",),
        clean=False,
    )
    open_board = SessionCloseout(
        reason="unit",
        session_id="s2",
        slot="main",
        turn_count=1,
        message_count=2,
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
        task_total=2,
        task_open=1,
        unresolved=("task active: Verify work",),
        clean=False,
    )

    stage_session_closeout_feedback(profile, dirty, closeout_path="one.md")
    result = stage_session_closeout_feedback(profile, open_board, closeout_path="two.md")

    assert result["staged"] is False
    assert result["repeated"] == []


import pytest as _pytest_state_lane


@_pytest_state_lane.fixture(autouse=True)
def _legacy_state_lane(monkeypatch, tmp_path):
    """This module asserts legacy project-relative state behavior; opt out of
    the conftest MO_STATE_HOME isolation (tests here chdir to tmp paths)."""
    monkeypatch.delenv("MO_STATE_HOME", raising=False)
    monkeypatch.delenv("MO_HOME", raising=False)
    monkeypatch.setenv("MO_STATE_LOCAL", "1")  # explicit project-local opt-out (state is private-by-default)
    from core.path_defaults import repo_root as _rr
    monkeypatch.setenv("MO_PROJECT_CWD", str(_rr()))
    monkeypatch.chdir(tmp_path)  # project-local state -> tmp, never the repo root
