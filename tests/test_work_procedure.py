"""Tests for crystallized work procedures and their evidence-gated seeding.

Covers the bridge from a build/reasoning WorkPattern to the taskboard's existing
evidence-gate engine, and the critical no-bypass invariant: a seeded procedure
step cannot be completed without evidence.
"""
from __future__ import annotations

from core.gateway import _new_gateway_board, _work_procedure_rows
from core.tasking.procedure import (
    procedure_rows,
    work_procedure_for,
)
from core.tasking.task_board import TaskBoard, check_task_board_contract
from core.work_patterns import procedure_for, select_work_pattern


def test_every_work_pattern_has_a_procedure():
    # Each build/reasoning pattern the classifier can emit must crystallize.
    for name in (
        "build_verify", "design_build", "fix_verify", "review_repair",
        "review_evidence", "project_audit", "reference_comparison", "prd_planning",
    ):
        proc = work_procedure_for(name)
        assert proc is not None, f"missing procedure for {name}"
        assert proc.steps, f"empty procedure for {name}"


def test_procedure_rows_are_sequential_and_gated():
    proc = work_procedure_for("fix_verify")
    rows = procedure_rows(proc)
    assert rows[0]["status"] == "active"
    assert all(r["status"] == "pending" for r in rows[1:])
    # strictly sequential dependency chain
    assert rows[0]["depends_on"] == []
    for idx, row in enumerate(rows[1:], start=2):
        assert row["depends_on"] == [str(idx - 1)]
    # closes on a final report row
    assert rows[-1]["kind"] == "report"
    assert rows[-1]["completion_gate"] == "final"


def test_work_procedures_seed_lean_build_checks_before_edits():
    build_rows = procedure_rows(work_procedure_for("build_verify"))
    assert "lean-build" in build_rows[0]["text"]
    assert "existing utilities" in build_rows[0]["expected_evidence"][0]
    assert "smallest complete" in build_rows[1]["text"]

    fix_rows = procedure_rows(work_procedure_for("fix_verify"))
    assert "existing fix surface" in fix_rows[0]["text"]
    assert "smallest safe fix" in fix_rows[1]["text"]


def test_classifier_selects_matching_procedure():
    proc = procedure_for("fix the broken login bug")
    assert proc is not None
    assert proc.name == select_work_pattern("fix the broken login bug").name


def test_chat_turn_has_no_procedure():
    assert procedure_for("hi mo") is None
    assert _work_procedure_rows("hi mo") is None


def test_no_bypass_seeded_step_cannot_complete_without_evidence():
    # The core invariant: a procedure-seeded, evidence-gated step that arrives
    # "completed" with no evidence is coerced back to pending by set_rows.
    proc = work_procedure_for("project_audit")
    rows = procedure_rows(proc)
    rows[0]["status"] = "completed"  # model prose tries to pre-close step 1
    rows[0]["evidence"] = []
    board = TaskBoard(turn_id="t", session_id="s")
    board.set_rows("audit", rows, objective="audit the repo")
    # Coerced out of "completed" — verification was not bypassed.
    assert board.task("1").status != "completed"
    assert board.task("1").is_open
    # And the board contract reports no completed-without-evidence gated row.
    result = check_task_board_contract(board, require_evidence=True)
    assert not any(r.startswith("evidence_missing") for r in result.reasons)


def test_step_completes_with_evidence():
    proc = work_procedure_for("fix_verify")
    rows = procedure_rows(proc)
    board = TaskBoard(turn_id="t", session_id="s")
    board.set_rows("fix", rows, objective="fix it")
    board.complete("1", evidence="grep: reproduced the failure at app.py:42")
    assert board.task("1").status == "completed"


def test_gateway_board_seeds_procedure_for_work_turn():
    # A real build/reasoning turn (Ghost gave no rows) seeds the gated phases
    # instead of a single generic "Work on ..." row.
    board = _new_gateway_board("t1", "s1", "audit the entire project for bugs", rows=None)
    assert len(board.tasks) > 1
    assert board.task("1").title != "Work on audit the entire project for bugs"
    assert board.tasks[-1].kind == "report"


def test_gateway_chat_turn_keeps_single_row_fallback():
    board = _new_gateway_board("t1", "s1", "hi mo", rows=None)
    assert len(board.tasks) == 1
    assert board.task("1").title == "Work on hi mo"
