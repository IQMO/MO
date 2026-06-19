import json

from core.file_operations import _read_tool_audit_files, accumulated_files, read_file_ops, write_file_ops


def test_read_tool_audit_files_tracks_reads_and_writes_since_timestamp(tmp_path):
    audit = tmp_path / "tool_audit.jsonl"
    audit.write_text(
        "\n".join(
            [
                json.dumps({"ts": 1, "tool": "read_file", "arguments": {"path": "old.py"}}),
                json.dumps({"ts": 10, "tool": "read_file", "arguments": {"path": "core/agent.py"}}),
                json.dumps({"ts": 11, "tool": "edit_file", "arguments": {"path": "core/handoff.py"}}),
                "not json",
            ]
        ),
        encoding="utf-8",
    )

    read_files, modified_files = _read_tool_audit_files(5, audit_path=audit)

    assert read_files == ["core/agent.py", "core/handoff.py"]
    assert modified_files == ["core/handoff.py"]


def test_write_and_accumulate_file_ops(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "tool_audit.jsonl").write_text(
        json.dumps({"ts": 10, "tool": "write_file", "arguments": {"path": "tests/test_handoff.py"}}) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "memory" / "file_operations.jsonl"

    write_file_ops("session-1", "run-1", 1, provider="mock", model="m", turn_count=3, path=out)

    records = read_file_ops(path=out)
    files = accumulated_files()
    assert records[0]["session_id"] == "session-1"
    assert records[0]["files_modified"] == ["tests/test_handoff.py"]
    assert files["tests/test_handoff.py"]["modifies"] == 1
    assert files["tests/test_handoff.py"]["reads"] == 1


def test_write_file_ops_skips_empty_records(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "memory" / "file_operations.jsonl"

    write_file_ops("session-empty", "", 1, path=out)

    assert not out.exists()


import pytest as _pytest_state_lane


@_pytest_state_lane.fixture(autouse=True)
def _legacy_state_lane(monkeypatch):
    """This module asserts legacy project-relative state behavior; opt out of
    the conftest MO_STATE_HOME isolation (tests here chdir to tmp paths)."""
    monkeypatch.delenv("MO_STATE_HOME", raising=False)
    monkeypatch.delenv("MO_HOME", raising=False)
    monkeypatch.setenv("MO_STATE_LOCAL", "1")  # explicit project-local opt-out (state is private-by-default)
