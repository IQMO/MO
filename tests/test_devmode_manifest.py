"""Tests for the runtime-owned DEVMODE manifest (core/tasking/devmode_manifest.py)."""
from __future__ import annotations

from core.tasking.devmode_manifest import (
    MANIFEST_NAME,
    build_devmode_manifest,
    load_devmode_manifest,
    write_devmode_manifest,
)
from core.tasking.task_board import TaskBoard, TaskItem


def test_manifest_artifact_entries_hash_present_and_record_missing(tmp_path):
    (tmp_path / "summary.md").write_text("hello", encoding="utf-8")
    m = build_devmode_manifest(tmp_path, economy={"tool_errors": 0})
    by_name = {a["name"]: a for a in m["artifacts"]}
    # present file → exists + bytes + sha256
    assert by_name["summary.md"]["exists"] is True
    assert by_name["summary.md"]["bytes"] == 5
    assert len(by_name["summary.md"]["sha256"]) == 64
    # missing file → EXPLICIT entry, never silent absence
    assert by_name["catalog.md"]["exists"] is False
    assert by_name["catalog.md"]["sha256"] is None
    # economy.md + manifest.json are runtime-owned
    assert by_name["economy.md"]["runtime_owned"] is True
    assert by_name[MANIFEST_NAME]["runtime_owned"] is True
    # acceptance criterion 5: the core SESSION artifacts are all indexed
    for required in ("summary.md", "workflow.md", "catalog.md", "capability-matrix.md", "economy.md", MANIFEST_NAME):
        assert required in by_name
    # longitudinal.md is a GLOBAL cross-session record (one level up), not a session
    # artifact — it must NOT be indexed here (else it reports a false "missing").
    assert "longitudinal.md" not in by_name


def test_manifest_records_taskboard_evidence_and_token_only_rows(tmp_path):
    board = TaskBoard(tasks=[
        TaskItem("1", "Gather", "completed", ["read_file:x", "shell:y"]),
        TaskItem("2", "Report", "completed", ["final:devmode05_protocol_closeout"]),
        TaskItem("3", "Verify", "pending"),
    ])
    m = build_devmode_manifest(tmp_path, task_board=board)
    tb = m["taskboard"]
    assert tb["open_count"] == 1  # task 3 still pending
    by_id = {t["id"]: t for t in tb["tasks"]}
    assert by_id["1"]["non_final_evidence_count"] == 2 and by_id["1"]["final_token_only"] is False
    assert by_id["2"]["evidence_count"] == 1 and by_id["2"]["non_final_evidence_count"] == 0
    assert by_id["2"]["final_token_only"] is True  # report row closed on a final token only


def test_manifest_economy_matches_and_freezes(tmp_path):
    eco = {"provider_requests": 48, "tool_calls": 79, "tool_errors": 4,
           "provider_errors": 1, "source": "mon.jsonl"}
    m = build_devmode_manifest(tmp_path, economy=eco, frozen_tool_errors=4)
    assert m["economy"]["tool_errors"] == 4
    assert m["economy"]["frozen_tool_errors"] == 4
    assert m["economy"]["provider_errors"] == 1  # provider retries are visible, not lost
    assert m["monitor"]["source"] == "mon.jsonl"


def test_manifest_projects_error_and_blocked_tool_names(tmp_path):
    """The manifest must carry the per-tool error/blocked NAMES (not just counts) so the
    authoritative error ledger names which tools failed — closing the T2206 mis-attribution
    gap. Names come straight from economy_summary; the manifest sorts/dedupes them."""
    eco = {
        "tool_errors": 3,
        "error_tools": ["test_runner", "edit_file"],
        "blocked_tools": ["read_file", "shell"],
        "source": "mon.jsonl",
    }
    m = build_devmode_manifest(tmp_path, economy=eco, frozen_tool_errors=3)
    assert m["economy"]["error_tools"] == ["edit_file", "test_runner"]   # sorted
    assert m["economy"]["blocked_tools"] == ["read_file", "shell"]
    # absent in economy -> empty lists, never missing keys
    m2 = build_devmode_manifest(tmp_path, economy={"tool_errors": 0})
    assert m2["economy"]["error_tools"] == [] and m2["economy"]["blocked_tools"] == []


def test_manifest_status_complete_vs_blocked(tmp_path):
    for status in ("active", "complete", "blocked"):
        assert build_devmode_manifest(tmp_path, status=status)["status"] == status
    # acceptance criterion 7: a blocked manifest can never read complete
    assert build_devmode_manifest(tmp_path, status="blocked")["status"] != "complete"


def test_manifest_write_and_load_roundtrip(tmp_path):
    m = build_devmode_manifest(tmp_path, economy={"tool_errors": 2}, status="complete",
                               run_session_ids={"mo-2", "mo-1"})
    assert write_devmode_manifest(tmp_path, m) is True
    assert (tmp_path / MANIFEST_NAME).is_file()
    loaded = load_devmode_manifest(tmp_path)
    assert loaded["status"] == "complete"
    assert loaded["economy"]["tool_errors"] == 2
    assert loaded["schema_version"] == 1
    assert loaded["run_session_ids"] == ["mo-1", "mo-2"]  # sorted, stable
    assert load_devmode_manifest(tmp_path / "nope") is None
