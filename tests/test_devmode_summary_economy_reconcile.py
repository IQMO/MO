"""OWNER_MAINTENANCE summary economy reconciliation: ALL named economy counts (header, Economy
section, Closeout; both 'N metric' and 'metric: N' forms) are normalized to the
authoritative monitor figures, while narration and unnamed numbers are preserved.
Live 2026-06-24T0404: header read 90/119 while the Economy section still said 74/106."""
from core.tasking.agent_taskboard import AgentTaskBoard

AUTH = {
    "provider_requests": 90, "tool_calls": 119, "tool_errors": 6,
    "sandbox_blocked": 2, "compression_events": 6,
}


def _reconcile(tmp_path, text):
    p = tmp_path / "summary.md"
    p.write_text(text, encoding="utf-8")
    AgentTaskBoard._reconcile_summary_economy_counts(p, AUTH)
    return p.read_text(encoding="utf-8")


def test_header_number_before_form_normalized(tmp_path):
    out = _reconcile(
        tmp_path,
        "Economy: 87 provider requests, 117 tool calls, 5 tool errors, "
        "1 sandbox-blocked, 4 compression events",
    )
    assert "90 provider requests" in out
    assert "119 tool calls" in out
    assert "6 tool errors" in out
    assert "2 sandbox-blocked" in out
    assert "6 compression events" in out
    for stale in ("87 ", "117 ", "5 tool", "1 sandbox", "4 compression"):
        assert stale not in out


def test_colon_form_normalized(tmp_path):
    # The exact missed case (Economy section colon form) that caused the drift.
    out = _reconcile(
        tmp_path,
        "Provider requests: 74\nTool calls: 106\nSandbox-blocked: 9\n"
        "Compression events: 3\nTool errors: 4",
    )
    assert "Provider requests: 90" in out
    assert "Tool calls: 119" in out
    assert "Sandbox-blocked: 2" in out
    assert "Compression events: 6" in out
    assert "Tool errors: 6" in out


def test_unnamed_numbers_preserved(tmp_path):
    out = _reconcile(tmp_path, "Provider requests: 74, responses: 74, errors: 0")
    assert "Provider requests: 90" in out
    assert "responses: 74" in out   # provider_responses is not a reconciled metric
    assert "errors: 0" in out       # bare 'errors' (provider errors) is not touched


def test_header_and_section_agree_after(tmp_path):
    text = (
        "- Economy: 87 provider requests, 117 tool calls\n"
        "## Economy\n- Provider requests: 74\n- Tool calls: 106\n"
    )
    out = _reconcile(tmp_path, text)
    assert "90 provider requests" in out and "Provider requests: 90" in out
    assert "119 tool calls" in out and "Tool calls: 119" in out
    assert "117" not in out and "106" not in out and "87 " not in out


def test_economy_line_tool_calls_normalized_with_bare_errors(tmp_path):
    out = _reconcile(tmp_path, "[OWNER_MAINTENANCE COMPLETE] Economy: 106 tool calls, 5 errors (shell x4)")
    assert "119 tool calls" in out
    assert "6 errors (shell x4)" in out  # Economy + tool calls means runtime tool errors


def test_pure_narration_untouched(tmp_path):
    text = "350 focused tests pass. 37-commit diff. 0 P0/P1/P2 findings. 3 P3 observations."
    assert _reconcile(tmp_path, text) == text


def test_devmode_summary_gets_runtime_tool_error_ledger_when_missing(tmp_path):
    p = tmp_path / "summary.md"
    p.write_text(
        "# DEVMODE" "05 Session Summary\n"
        "## Verification\n"
        "- Git: clean\n"
        "## Closeout\n"
        "- **[DEVMODE" "05 COMPLETE]** clean.\n",
        encoding="utf-8",
    )
    summary = dict(AUTH, tool_errors=3, error_tools=["shell", "read_file"])
    AgentTaskBoard._reconcile_summary_economy_counts(p, summary)
    out = p.read_text(encoding="utf-8")
    assert "## Tool Error Ledger" in out
    assert "Tool errors: 3 errors recorded by the runtime monitor." in out
    assert "Error tools: read_file, shell." in out


def test_existing_tool_error_ledger_is_not_duplicated(tmp_path):
    p = tmp_path / "summary.md"
    p.write_text(
        "# DEVMODE" "05 Session Summary\n"
        "## Tool Error Ledger\n"
        "- Tool errors: 2 errors recorded.\n",
        encoding="utf-8",
    )
    summary = dict(AUTH, tool_errors=3, error_tools=["shell"])
    AgentTaskBoard._reconcile_summary_economy_counts(p, summary)
    out = p.read_text(encoding="utf-8")
    assert out.count("## Tool Error Ledger") == 1
    assert "Tool errors: 3 errors recorded by the runtime monitor." in out
    assert "Error tools: shell." in out


def test_existing_tool_error_ledger_tools_are_reconciled(tmp_path):
    p = tmp_path / "summary.md"
    p.write_text(
        "# OWNER_MAINTENANCE Summary\n"
        "## Tool Error Ledger\n"
        "- Tool errors: 2 shell errors -- recovered inline.\n"
        "## Closeout\n"
        "- Economy: 52 tool calls, 3 errors (shell), 28 provider requests, 4 compression events\n",
        encoding="utf-8",
    )
    summary = dict(AUTH, tool_errors=4, error_tools=["shell", "edit_file"])
    AgentTaskBoard._reconcile_summary_economy_counts(p, summary)
    out = p.read_text(encoding="utf-8")
    assert out.count("## Tool Error Ledger") == 1
    assert "Tool errors: 4 errors recorded by the runtime monitor." in out
    assert "Error tools: edit_file, shell." in out
    assert "119 tool calls, 4 errors" in out
    assert "90 provider requests" in out
    assert "6 compression events" in out


def test_idempotent_when_already_correct(tmp_path):
    text = ("Economy: 90 provider requests, 119 tool calls, 6 tool errors, "
            "2 sandbox-blocked, 6 compression events")
    assert _reconcile(tmp_path, text) == text


def test_missing_file_is_noop(tmp_path):
    AgentTaskBoard._reconcile_summary_economy_counts(tmp_path / "nope.md", AUTH)  # no raise
