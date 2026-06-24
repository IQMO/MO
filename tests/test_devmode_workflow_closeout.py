"""OWNER_MAINTENANCE workflow.md runtime closeout: a completed board stamps ONE authoritative
section so an unticked model checklist can't read as incomplete (live 2026-06-24T0404:
matrix/rotation/diff/catalog left `[ ]` while the board was completed/open 0). The
model's own checklist rows are NEVER rewritten; non-complete boards are not stamped."""
from core.tasking.agent_taskboard import AgentTaskBoard

WORKFLOW = (
    "# OWNER_MAINTENANCE Workflow\n\n## Phase Boot\n"
    "- [x] Read OWNER_MAINTENANCE.md\n"
    "- [ ] Build Capability Coverage Matrix\n"
    "- [ ] Diff audit\n"
    "- [ ] Catalog written\n"
)

COMPLETE_PROJ = {"state": "completed", "open_count": 0, "tasks": [{"status": "completed"}] * 6}


def _stamp(tmp_path, text, proj):
    p = tmp_path / "workflow.md"
    p.write_text(text, encoding="utf-8")
    AgentTaskBoard._append_workflow_runtime_closeout(p, proj)
    return p.read_text(encoding="utf-8")


def test_completed_board_appends_authoritative_section(tmp_path):
    out = _stamp(tmp_path, WORKFLOW, COMPLETE_PROJ)
    assert "## Runtime Closeout (authoritative)" in out
    assert "completed**, open=0, 6/6 phase rows done" in out
    # the model's own checklist rows are UNCHANGED — never blindly ticked
    assert "- [ ] Build Capability Coverage Matrix" in out
    assert "- [ ] Diff audit" in out
    assert "- [x] Read OWNER_MAINTENANCE.md" in out


def test_idempotent_stamps_once(tmp_path):
    p = tmp_path / "workflow.md"
    p.write_text(WORKFLOW, encoding="utf-8")
    AgentTaskBoard._append_workflow_runtime_closeout(p, COMPLETE_PROJ)
    AgentTaskBoard._append_workflow_runtime_closeout(p, COMPLETE_PROJ)
    assert p.read_text(encoding="utf-8").count("## Runtime Closeout (authoritative)") == 1


def test_open_board_not_stamped(tmp_path):
    proj = {"state": "active", "open_count": 2,
            "tasks": [{"status": "completed"}, {"status": "pending"}]}
    out = _stamp(tmp_path, WORKFLOW, proj)
    assert "Runtime Closeout" not in out
    assert out == WORKFLOW


def test_completed_state_but_open_rows_not_stamped(tmp_path):
    # defensive: state says completed but open_count disagrees -> do not stamp
    proj = {"state": "completed", "open_count": 1,
            "tasks": [{"status": "completed"}, {"status": "pending"}]}
    assert "Runtime Closeout" not in _stamp(tmp_path, WORKFLOW, proj)


def test_missing_file_is_noop(tmp_path):
    AgentTaskBoard._append_workflow_runtime_closeout(tmp_path / "nope.md", COMPLETE_PROJ)  # no raise
