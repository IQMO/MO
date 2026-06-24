from pathlib import Path
from types import SimpleNamespace

from core.agent.agent import Agent
from core.tasking.task_board import TaskBoard, TaskItem, clear_current_board_if_empty, read_recent_snapshots, record_snapshot
from core.tasking.task_manager import TaskManager
from core.session.session_closeout import _taskboard_state
from core.session.handoff import _task_board_summary
from core.ghost.ghost_context import _task_board_text
from core.tasking import task_evidence



def test_taskboard_snapshot_write_failure_is_non_fatal(tmp_path):
    board = TaskBoard(tasks=[TaskItem("1", "Inspect", "active")])

    assert record_snapshot(board, "updated", path=tmp_path) is None


def test_taskboard_default_ledger_does_not_write_during_pytest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    board = TaskBoard(tasks=[TaskItem("1", "Inspect", "active")])

    assert record_snapshot(board, "updated") is None
    assert not Path("memory/taskboards/taskboards.jsonl").exists()


def test_taskboard_explicit_ledger_keeps_current_json_beside_ledger(tmp_path, monkeypatch):
    """current.json must follow the explicit ledger path, never live memory at cwd."""
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / "isolated" / "taskboards.jsonl"
    board = TaskBoard(tasks=[TaskItem("1", "Inspect", "active")])

    assert record_snapshot(board, "updated", path=ledger) is not None
    assert (ledger.parent / "current.json").exists()
    assert not Path("memory/taskboards/current.json").exists()


def test_clear_current_board_if_empty_removes_boardless_working_copy(tmp_path):
    ledger = tmp_path / "isolated" / "taskboards.jsonl"
    tm = TaskManager(tmp_path, tasks_dir=ledger.parent)
    tm.save({"session_id": "s1", "state": "active", "tasks": []})

    assert (ledger.parent / "current.json").exists()
    assert clear_current_board_if_empty(path=ledger) is True
    assert not (ledger.parent / "current.json").exists()


def test_taskboard_snapshot_ledger_round_trip(tmp_path):
    path = tmp_path / "memory" / "taskboards" / "taskboards.jsonl"
    board = TaskBoard(
        turn_id="turn-1",
        title="Review code",
        objective="review code",
        session_id="session-1",
        tasks=[
            TaskItem("1", "Inspect", "completed", evidence=["read_file:core/a.py"], kind="inspect", completion_gate="tool"),
            TaskItem("2", "Verify", "active", kind="verify", completion_gate="verification", depends_on=["1"]),
        ],
    )

    record = record_snapshot(board, "updated", path=path)
    recent = read_recent_snapshots(limit=5, path=path)

    assert record is not None
    assert path.exists()
    assert len(recent) == 1
    assert recent[0]["turn_id"] == "turn-1"
    assert recent[0]["board_id"] == board.board_id
    assert recent[0]["session_id"] == "session-1"
    assert read_recent_snapshots(limit=5, path=path, session_id="other") == []
    assert recent[0]["tasks"][0]["kind"] == "inspect"
    assert recent[0]["tasks"][1]["completion_gate"] == "verification"
    assert recent[0]["tasks"][1]["depends_on"] == ["1"]


def test_taskboard_snapshot_dedupes_duplicate_terminal_rows(tmp_path):
    path = tmp_path / "taskboards.jsonl"
    board = TaskBoard(tasks=[TaskItem("1", "Report", "completed", kind="report", completion_gate="final")])

    first = record_snapshot(board, "completed", path=path)
    second = record_snapshot(board, "completed", path=path)
    board.task("1").evidence.append("final:sent")
    third = record_snapshot(board, "completed", path=path)

    assert first is not None and second is not None and third is not None
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_taskitem_metadata_from_rows():
    board = TaskBoard()
    board.set_rows("Build", [
        {"id": "1", "text": "Inspect files", "status": "active", "kind": "inspect", "completion_gate": "tool"},
        {"id": "2", "text": "Verify", "status": "pending", "kind": "verify", "completion_gate": "verification", "depends_on": "10"},
    ])

    assert board.task("1").kind == "inspect"
    assert board.task("2").completion_gate == "verification"
    assert board.task("2").depends_on == ["10"]


def test_metadata_gate_advances_verify_only_on_verification_tool():
    agent = object.__new__(Agent)
    board = TaskBoard(tasks=[
        TaskItem("1", "Verify locally", "active", kind="verify", completion_gate="verification"),
        TaskItem("2", "Report", "pending", kind="report", completion_gate="final", depends_on=["1"]),
    ])

    assert agent._advance_task_board_after_tool(board, "read_file") is False
    assert board.task("1").status == "active"

    assert agent._advance_task_board_after_tool(board, "shell", {"command": "python -m pytest -q"}) is False
    assert agent._advance_task_board_after_tool(board, "complete_task", {}) is True
    assert board.task("1").status == "completed"
    assert board.task("2").status == "active"


def test_metadata_final_gate_waits_for_final_answer_not_tool():
    agent = object.__new__(Agent)
    report = TaskItem("2", "Report result", "active", kind="report", completion_gate="final")

    assert task_evidence.tool_should_advance_task("shell", report, 1, 2, arguments={"command": "echo done"}) is False
    assert agent._final_should_complete_task(report) is True


def test_final_report_task_id_uses_metadata_not_numeric_position():
    board = TaskBoard(tasks=[
        TaskItem("inspect", "Inspect", "completed", kind="inspect", completion_gate="tool"),
        TaskItem("reply", "Report result", "pending", kind="report", completion_gate="final", depends_on=["inspect"]),
    ])

    assert Agent._final_report_task_id(board) == "reply"
    assert board.activate(Agent._final_report_task_id(board)) is True
    assert board.task("reply").status == "active"


def test_metadata_shell_matching_respects_kind_intent():
    object.__new__(Agent)
    inspect = TaskItem("1", "Inspect", "active", kind="inspect", completion_gate="tool")
    edit = TaskItem("2", "Edit", "active", kind="edit", completion_gate="tool")

    assert task_evidence.tool_should_advance_task("shell", inspect, 0, 2, arguments={"command": "rg taskboard core"}) is True
    assert task_evidence.tool_should_advance_task("shell", edit, 1, 2, arguments={"command": "python -m pytest -q"}) is False
    assert task_evidence.tool_should_advance_task("shell", edit, 1, 2, arguments={"command": "python - <<'PY'\nfrom pathlib import Path\nPath('x').write_text('x')\nPY"}) is True


def test_set_rows_metadata_preserved_in_taskboard():
    """Rows set via set_rows() preserve kind, completion_gate, depends_on."""
    board = TaskBoard()
    board.set_rows("review", [
        {"id": "1", "text": "Inspect code", "status": "active", "kind": "inspect", "completion_gate": "tool", "depends_on": []},
        {"id": "2", "text": "Report findings", "status": "pending", "kind": "report", "completion_gate": "final", "depends_on": ["1"]},
    ])
    assert board.task("1").kind == "inspect"
    assert board.task("1").completion_gate == "tool"
    assert board.task("2").depends_on == ["1"]
    assert board.task("2").completion_gate == "final"


def test_depends_on_blocks_activation_until_dependency_completed():
    board = TaskBoard(tasks=[
        TaskItem("1", "Build", "active", kind="edit", completion_gate="tool"),
        TaskItem("2", "Verify", "pending", kind="verify", completion_gate="verification", depends_on=["1", "3"]),
        TaskItem("3", "Prepare fixture", "pending", kind="edit", completion_gate="tool"),
    ])
    agent = object.__new__(Agent)

    assert agent._advance_task_board_after_tool(board, "edit_file") is False
    assert agent._advance_task_board_after_tool(board, "complete_task") is True
    assert board.task("1").status == "completed"
    assert board.task("2").status == "pending"
    assert board.task("3").status == "active"

    assert agent._advance_task_board_after_tool(board, "edit_file") is False
    assert agent._advance_task_board_after_tool(board, "complete_task") is True
    assert board.task("3").status == "completed"
    assert board.task("2").status == "active"


def test_session_closeout_can_use_ledger_when_live_board_missing(tmp_path):
    path = tmp_path / "taskboards.jsonl"
    board = TaskBoard(session_id="current", tasks=[TaskItem("1", "Verify", "active", evidence=["test_runner:pending"])])
    stale = TaskBoard(session_id="old", tasks=[TaskItem("1", "Old stale task", "active")])
    record_snapshot(stale, "updated", path=path)
    record_snapshot(board, "updated", path=path)

    import core.session.session_closeout as sc
    original = sc.read_recent_snapshots
    sc.read_recent_snapshots = lambda limit=1, session_id="": read_recent_snapshots(limit=limit, path=path, session_id=session_id)
    try:
        state = _taskboard_state(SimpleNamespace(
            session=SimpleNamespace(session_id="current"),
            gateway=SimpleNamespace(last_task_board=None),
        ))
    finally:
        sc.read_recent_snapshots = original

    assert state["total"] == 1
    assert state["open"] == 1
    assert state["unresolved"] == ["task active: Verify"]
    assert state["evidence"] == ["test_runner:pending"]


def test_handoff_and_ghost_can_use_ledger_snapshot_when_no_live_board(tmp_path, monkeypatch):
    path = tmp_path / "taskboards.jsonl"
    board = TaskBoard(title="Fix bug", session_id="current", tasks=[TaskItem("1", "Inspect", "completed"), TaskItem("2", "Report", "active")])
    stale = TaskBoard(title="Stale bug", session_id="old", tasks=[TaskItem("1", "Stale", "active")])
    record_snapshot(stale, "updated", path=path)
    record_snapshot(board, "updated", path=path)

    import core.session.handoff as handoff
    import core.ghost.ghost_context as ghost_context
    monkeypatch.setattr(handoff, "read_recent_snapshots", lambda limit=1, session_id="": read_recent_snapshots(limit=limit, path=path, session_id=session_id))
    monkeypatch.setattr(ghost_context, "read_recent_snapshots", lambda limit=1, session_id="": read_recent_snapshots(limit=limit, path=path, session_id=session_id))

    rows = _task_board_summary(None, session_id="current")
    ghost_text = _task_board_text(None, {}, session_id="current")

    assert "Last recorded board" in rows[0]
    assert "Fix bug" in rows[0]
    assert "Stale bug" not in rows[0]
    assert "2 tasks (1 done, 1 open)" in ghost_text
    assert "Report" in ghost_text


def test_user_facing_ledger_fallback_requires_session_id(tmp_path, monkeypatch):
    path = tmp_path / "taskboards.jsonl"
    board = TaskBoard(title="Old board", session_id="old", tasks=[TaskItem("1", "Stale", "active")])
    record_snapshot(board, "updated", path=path)

    import core.session.handoff as handoff
    import core.ghost.ghost_context as ghost_context
    import core.session.session_closeout as sc
    monkeypatch.setattr(handoff, "read_recent_snapshots", lambda limit=1, session_id="": read_recent_snapshots(limit=limit, path=path, session_id=session_id))
    monkeypatch.setattr(ghost_context, "read_recent_snapshots", lambda limit=1, session_id="": read_recent_snapshots(limit=limit, path=path, session_id=session_id))
    monkeypatch.setattr(sc, "read_recent_snapshots", lambda limit=1, session_id="": read_recent_snapshots(limit=limit, path=path, session_id=session_id))

    assert _task_board_summary(None) == []
    assert _task_board_text(None, {}) == ""
    state = _taskboard_state(SimpleNamespace(session=SimpleNamespace(session_id=""), gateway=SimpleNamespace(last_task_board=None)))
    assert state["total"] == 0
