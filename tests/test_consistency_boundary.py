from types import SimpleNamespace

from core.consistency_boundary import (
    check_consistency_boundary,
    render_consistency_boundary,
)
from core.review.diff_review import ReviewReport
from core.goal import GoalPlan, GoalStep
from core.session.session_closeout import SessionCloseout
from core.tasking.task_board import TaskBoard, TaskItem


def test_prt_boundary_reports_unresolved_and_done_conflict():
    report = ReviewReport(
        diff_ref="HEAD",
        files_changed=1,
        additions=1,
        deletions=0,
        findings=[],
        score=4.0,
        unresolved_count=1,
        affected_tests=[],
        created_at=1.0,
    )

    boundary = check_consistency_boundary("prt", prt_report=report, final_text="PRT finished and ready")

    kinds = {finding.kind for finding in boundary.findings}
    assert boundary.clean is False
    assert "prt_unresolved" in kinds
    assert "prt_done_claim_conflict" in kinds
    assert "do not claim production-ready" in boundary.findings[0].message


def test_goal_boundary_blocks_completed_without_tool_evidence():
    plan = GoalPlan(
        objective="audit repo verification",
        steps=[GoalStep("1", "Execute goal", status="completed", evidence=["content:200chars"])],
        state="completed",
    )

    boundary = check_consistency_boundary("goal", goal_plan=plan)

    assert any(f.kind == "goal_missing_tool_evidence" for f in boundary.findings)


def test_session_boundary_reports_unresolved_closeout():
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

    boundary = check_consistency_boundary("session_closeout", session_closeout=closeout)

    assert boundary.clean is False
    assert boundary.findings[0].kind == "session_unresolved"
    assert "unresolved=1" in boundary.findings[0].evidence


def test_taskboard_boundary_detects_done_claim_with_open_rows():
    board = TaskBoard("turn", "build_create", [TaskItem("1", "Verify work", "active")])

    boundary = check_consistency_boundary("turn_final", task_board=board, final_text="Done, all fixed.")

    assert any(f.kind == "taskboard_done_claim_conflict" for f in boundary.findings)


def test_taskboard_boundary_empty_board_with_done_claim_is_clean():
    # Phase 3: a MO-owned board MO never populated via set_plan stays EMPTY (no
    # rows). An empty board + a done-claim must NOT false-trip the conflict gate.
    board = TaskBoard("turn", "build_create", [])

    boundary = check_consistency_boundary("turn_final", task_board=board, final_text="Done, all fixed.")

    assert not any(f.kind == "taskboard_done_claim_conflict" for f in boundary.findings)


def test_learning_promise_without_record_is_finding():
    boundary = check_consistency_boundary(
        "turn_final",
        final_text="I learned from this and will remember it.",
        learning_notes=[],
    )

    assert any(f.kind == "learning_promise_without_record" for f in boundary.findings)


def test_git_boundary_failed_push_is_finding():
    boundary = check_consistency_boundary(
        "commit_push",
        command="git push origin main",
        tool_result="remote rejected\n[exit code 1]",
    )

    assert any(f.kind == "git_push_failed" for f in boundary.findings)


def test_proposal_boundary_finds_complete_status_with_unchecked_items(tmp_path):
    path = tmp_path / "docs" / "proposals" / "demo.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Demo\n\n**Status:** Complete\n\n- [x] Done\n- [ ] Not done\n", encoding="utf-8")

    boundary = check_consistency_boundary("proposal_closeout", proposal_paths=[path])

    assert any(f.kind == "proposal_complete_with_unchecked_items" for f in boundary.findings)
    assert "unchecked=1" in boundary.findings[0].evidence


def test_clean_boundary_renders_clean():
    boundary = check_consistency_boundary("session_closeout", session_closeout=SimpleNamespace(clean=True, unresolved=(), dirty_files=()))

    assert boundary.clean is True
    assert render_consistency_boundary(boundary) == "Consistency boundary: clean (session_closeout)"
