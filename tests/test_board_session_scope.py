"""current.json is session-scoped: a new session must not inherit a prior session's
board (live mo-1782304565 — an IAM05 turn showed mo-1782300201's stale DEVMODE05 board).
clear_current_board_if_foreign_session clears it only when the session differs; same-
session state is preserved and the ledger stays authoritative."""
from core.tasking.task_board import clear_current_board_if_foreign_session
from core.tasking.task_manager import TaskManager


def _write_current(tmp_path, session_id):
    tm = TaskManager(tmp_path, tasks_dir=tmp_path)
    tm.save({
        "board_id": "b1", "session_id": session_id, "state": "active",
        "tasks": [{"id": "1", "title": "Boot protocol", "status": "active"}],
    })
    return str(tmp_path / "taskboards.jsonl"), tmp_path / "current.json"


def test_clears_foreign_session_board(tmp_path):
    ledger, current = _write_current(tmp_path, "mo-OLD")
    assert current.exists()
    assert clear_current_board_if_foreign_session("mo-NEW", path=ledger) is True
    assert not current.exists()  # archived + removed


def test_preserves_same_session_board(tmp_path):
    ledger, current = _write_current(tmp_path, "mo-SAME")
    assert clear_current_board_if_foreign_session("mo-SAME", path=ledger) is False
    assert current.exists()  # untouched — same session keeps its board for resume


def test_noop_when_no_current_json(tmp_path):
    ledger = str(tmp_path / "taskboards.jsonl")
    assert clear_current_board_if_foreign_session("mo-NEW", path=ledger) is False


def test_noop_on_empty_active_session(tmp_path):
    ledger, current = _write_current(tmp_path, "mo-OLD")
    assert clear_current_board_if_foreign_session("", path=ledger) is False
    assert current.exists()  # without a known active session, never clear


def test_noop_when_current_has_no_session_id(tmp_path):
    # A legacy current.json with no session_id is left alone (can't prove it's foreign).
    tm = TaskManager(tmp_path, tasks_dir=tmp_path)
    tm.save({"board_id": "b1", "session_id": "", "tasks": [{"id": "1", "title": "x", "status": "active"}]})
    assert clear_current_board_if_foreign_session("mo-NEW", path=str(tmp_path / "taskboards.jsonl")) is False
    assert (tmp_path / "current.json").exists()
