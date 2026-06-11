"""Gateway board creation and Ghost proposal parsing tests."""
from __future__ import annotations

from core.gateway import _new_gateway_board, _parse_ghost_proposal


def test_parse_ghost_proposal_extracts_text_and_rows():
    """Ghost proposal format: text --- JSON tasks."""
    raw = """Intent: Fix the login bug in auth.py.
Scope: auth.py only.
---
{"tasks": [
  {"title": "Inspect auth.py", "kind": "inspect", "completion_gate": "tool", "depends_on": []},
  {"title": "Fix null check", "kind": "edit", "completion_gate": "tool", "depends_on": ["1"]},
  {"title": "Verify fix", "kind": "verify", "completion_gate": "verification", "depends_on": ["2"]}
]}"""
    text, rows = _parse_ghost_proposal(raw)
    assert "Intent: Fix the login bug" in text
    assert "---" not in text
    assert len(rows) == 3
    assert rows[0]["title"] == "Inspect auth.py"
    assert rows[2]["completion_gate"] == "verification"


def test_parse_ghost_proposal_no_json_fallback():
    """Returns empty rows list when no valid JSON found."""
    text, rows = _parse_ghost_proposal("Just some text without JSON")
    assert text == "Just some text without JSON"
    assert rows == []


def test_new_gateway_board_with_ghost_rows():
    """Board created from Ghost's structured rows."""
    rows = [
        {"id": "1", "text": "Inspect auth.py", "status": "active", "kind": "inspect", "completion_gate": "tool", "depends_on": []},
        {"id": "2", "text": "Fix null check", "status": "pending", "kind": "edit", "completion_gate": "tool", "depends_on": ["1"]},
        {"id": "3", "text": "Verify fix", "status": "pending", "kind": "verify", "completion_gate": "verification", "depends_on": ["2"]},
    ]
    board = _new_gateway_board("t1", "s1", "fix login bug", title="login bug", rows=rows)
    assert board.task("1").title == "Inspect auth.py"
    assert board.task("1").kind == "inspect"
    assert board.task("2").depends_on == ["1"]
    assert board.task("3").completion_gate == "verification"
    # Task 2 depends on 1; 1 is active (not completed) so deps not satisfied
    assert board.dependencies_satisfied("2") is False
    assert board.dependencies_satisfied("1") is True


def test_new_gateway_board_no_rows_uses_fallback():
    """When Ghost provides no rows, a single-row fallback board is created."""
    board = _new_gateway_board("t1", "s1", "hi mo", rows=None)
    assert board is not None
    assert len(board.tasks) == 1
    assert board.task("1").title == "Work on hi mo"
    assert board.task("1").kind == "edit"


def test_new_gateway_board_devmode05_no_rows_uses_protocol_phases():
    """DEVMODE05 fallback rows reflect real protocol phases, not one generic wrapper."""
    board = _new_gateway_board("t1", "s1", "Start DEVMODE05", rows=None)

    assert board is not None
    assert len(board.tasks) == 6
    assert board.task("1").title == "Boot protocol and load prior session context"
    assert "baseline-plus-delta capability matrix" in board.task("2").title
    assert board.task("6").kind == "report"
    assert board.task("6").completion_gate == "final"
