import json
from types import SimpleNamespace

from core.agent.agent import Agent
from core.tasking.task_board import TaskBoard, TaskItem


def _agent_with_boundary_capture(captured: dict) -> Agent:
    agent = object.__new__(Agent)
    agent.session = SimpleNamespace(
        messages=[],
        add_user=lambda _text: None,
        turn_count=0,
        sanitize_for_provider=lambda **_kwargs: {},
        get_messages=lambda extra_context=None: [{"role": "system", "content": extra_context or ""}],
        record_usage=lambda *a, **k: None,
        add_assistant=lambda *a, **k: None,
        add_message=lambda *a, **k: None,
        add_tool_result=lambda *a, **k: None,
    )
    agent.profile = None
    agent.memory = None
    agent.context_summary_enabled = False
    agent.context_handoff_enabled = True
    agent.max_provider_requests = 1
    agent.max_tool_rounds = 1
    agent.provider_name = "fake"
    agent.model = "fake"
    agent.tool_definitions = []
    agent.critic = SimpleNamespace(review=lambda text: SimpleNamespace(text=text))
    agent._pending_turn_proposal = ""
    agent._deep_review_analysis_rounds = 0
    agent._scan_user_input = lambda _text: None
    agent._quarantine_unfinished_tail_before_turn = lambda *_args, **_kwargs: {}
    agent._pause_interrupted_work_for_return = lambda *_args, **_kwargs: None
    agent._pre_turn_context_handoff = lambda _text: False
    agent._maybe_handle_init_turn = lambda _text: None
    agent._maybe_handle_workflow_control_turn = lambda _text: None
    agent._maybe_handle_identity_turn = lambda _text: None
    agent._build_extra_context = lambda _text: ""
    agent._maybe_context_handoff = lambda *_args, **_kwargs: False
    agent._emit_sanitize_event = lambda *_args, **_kwargs: None
    agent._record_turn_memory_and_learning = lambda *_args, **_kwargs: []
    agent._append_after_turn_notes = lambda text, _notes: text
    agent._run_consistency_boundary = lambda boundary, **kwargs: captured.update({"boundary": boundary, **kwargs})
    return agent


def test_agent_blocked_first_tool_does_not_materialize_task_board():
    captured = {}
    agent = _agent_with_boundary_capture(captured)
    agent.max_provider_requests = 2
    agent.tool_definitions = [{"name": "edit_file"}]
    agent.allowed_roots = ["."]
    agent.sandbox_config = {"enabled": False}
    agent._active_lane = None
    agent.tool_compress_enabled = False
    agent._project_scoped_tool_arguments = lambda _name, arguments: arguments
    agent._self_mutation_block_reason = lambda *_args, **_kwargs: "[blocked] needs approval"
    agent._operator_approved = lambda *_args, **_kwargs: False
    agent._dispatch_tool = lambda *_args, **_kwargs: "should not run"
    agent._write_tool_audit = lambda *_args, **_kwargs: None
    agent._cap_tool_result_for_context = lambda result, **_kwargs: result
    boards = []
    responses = iter([
        SimpleNamespace(
            content="",
            tool_calls=[{"id": "call-1", "function": {"name": "edit_file", "arguments": '{"path":"app.py","old_text":"a","new_text":"b"}'}}],
            usage=None,
            finish_reason="tool_calls",
        ),
        SimpleNamespace(content="Blocked before editing.", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **_kwargs: next(responses)

    result = agent.run_turn("do it", on_first_tool=lambda *_args: boards.append(True) or TaskBoard(tasks=[TaskItem("1", "Edit", "active", kind="edit", completion_gate="tool")]))

    assert result == "Blocked before editing."
    assert boards == []


def test_agent_normal_final_boundary_receives_current_task_board():
    captured = {}
    agent = _agent_with_boundary_capture(captured)
    agent._call_provider = lambda **_kwargs: SimpleNamespace(content="Done, all fixed.", tool_calls=[], usage=None, finish_reason="stop")
    board = TaskBoard(tasks=[
        TaskItem("1", "Inspect", "completed", kind="inspect", completion_gate="tool"),
        TaskItem("2", "Report", "active", kind="report", completion_gate="final", depends_on=["1"]),
    ])

    events = []

    result = agent.run_turn("finish", task_board=board, on_board_event=events.append)

    assert result == "Done, all fixed."
    assert captured["boundary"] == "turn_final"
    assert captured["task_board"] is board
    assert board.task("2").status == "completed"
    assert events[-1]["type"] == "taskboard_update"
    assert events[-1]["update"] == "completed"
    assert events[-1]["board_id"] == board.board_id


def test_agent_final_answer_leaves_unfinished_non_final_task_active_before_boundary():
    captured = {}
    agent = _agent_with_boundary_capture(captured)
    agent._call_provider = lambda **_kwargs: SimpleNamespace(content="Here is the summary.", tool_calls=[], usage=None, finish_reason="stop")
    board = TaskBoard(tasks=[
        TaskItem("1", "Inspect", "completed", kind="inspect", completion_gate="tool"),
        TaskItem("2", "Verify findings", "active", kind="verify", completion_gate="verification", depends_on=["1"]),
        TaskItem("3", "Report", "pending", kind="report", completion_gate="final", depends_on=["2"]),
    ])

    result = agent.run_turn("summarize", task_board=board)

    assert result == "Here is the summary."
    assert captured["task_board"] is board
    assert board.task("2").status == "active"
    assert board.task("2").blocker == ""
    assert board.task("3").status == "pending"


def test_owner_maintenance_completion_conflict_continues_until_boundary_clean():
    captured = {}
    agent = _agent_with_boundary_capture(captured)
    agent.max_provider_requests = 4
    agent.max_tool_rounds = 4
    agent.tool_definitions = [{"name": "grep"}]
    agent.allowed_roots = ["."]
    agent.sandbox_config = {"enabled": False}
    agent._active_lane = None
    agent.tool_compress_enabled = False
    agent._project_scoped_tool_arguments = lambda _name, arguments: arguments
    agent._self_mutation_block_reason = lambda *_args, **_kwargs: None
    agent._operator_approved = lambda *_args, **_kwargs: False
    agent._dispatch_tool = lambda *_args, **_kwargs: "grep:evidence"
    agent._write_tool_audit = lambda *_args, **_kwargs: None
    agent._cap_tool_result_for_context = lambda result, **_kwargs: result
    agent._tool_result_is_error = lambda _result: False

    assistant_messages = []
    agent.session.add_assistant = lambda text, *a, **k: assistant_messages.append(text)
    boundary_calls = []

    def fake_boundary(boundary, **kwargs):
        boundary_calls.append((boundary, kwargs.get("final_text", "")))
        if len(boundary_calls) == 1:
            return SimpleNamespace(
                findings=(SimpleNamespace(kind="taskboard_done_claim_conflict", severity="major"),),
                clean=False,
            )
        return SimpleNamespace(findings=(), clean=True)

    agent._run_consistency_boundary = fake_boundary
    responses = iter([
        SimpleNamespace(
            content="",
            tool_calls=[{"id": "call-1", "function": {"name": "grep", "arguments": '{"pattern":"x","path":"core"}'}}],
            usage=None,
            finish_reason="tool_calls",
        ),
        SimpleNamespace(content="[OWNER_MAINTENANCE COMPLETE] premature", tool_calls=[], usage=None, finish_reason="stop"),
        SimpleNamespace(content="[OWNER_MAINTENANCE COMPLETE] settled", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **_kwargs: next(responses)
    board = TaskBoard(tasks=[
        TaskItem("1", "Verify unresolved finding", "active", kind="verify", completion_gate="verification"),
    ])

    result = agent.run_turn("start OWNER_MAINTENANCE", task_board=board)

    assert result == "[OWNER_MAINTENANCE COMPLETE] settled"
    assert len(boundary_calls) == 2
    assert any("[OWNER_MAINTENANCE AUTONOMY] Completion is not allowed" in msg for msg in assistant_messages)


def test_owner_maintenance_validated_closeout_closes_protocol_rows_after_notes(monkeypatch):
    """A stop-gate-approved OWNER_MAINTENANCE terminal report must close the fixed
    protocol board before later answer notes can make the finalizer re-read a
    different closeout view."""
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    captured = {}
    agent = _agent_with_boundary_capture(captured)
    agent.max_provider_requests = 4
    agent.max_tool_rounds = 4
    agent.tool_definitions = [{"name": "shell"}]
    agent.allowed_roots = ["."]
    agent.sandbox_config = {"enabled": False}
    agent._active_lane = None
    agent.tool_compress_enabled = False
    agent._project_scoped_tool_arguments = lambda _name, arguments: arguments
    agent._self_mutation_block_reason = lambda *_args, **_kwargs: None
    agent._operator_approved = lambda *_args, **_kwargs: False
    agent._dispatch_tool = lambda *_args, **_kwargs: "shell:evidence"
    agent._write_tool_audit = lambda *_args, **_kwargs: None
    agent._cap_tool_result_for_context = lambda result, **_kwargs: result
    agent._tool_result_is_error = lambda _result: False
    agent._append_after_turn_notes = lambda text, _notes: text + "\nRemaining: audit note"
    agent._devmode_run_session_ids = set()
    agent._devmode_closeout_frozen_errors = 0
    agent._active_devmode_session_dir = None
    calls: list[str] = []
    agent._write_devmode_economy_record = lambda: calls.append("economy")
    agent._reconcile_devmode_summary_marker = lambda _text: False
    agent._reconcile_devmode_workflow_closeout = lambda _board: calls.append("workflow")
    agent._write_devmode_manifest_record = lambda **kwargs: calls.append(f"manifest:{kwargs.get('status')}")
    agent._track_devmode_run_session_id = lambda: agent._devmode_run_session_ids.add("mo-test")

    responses = iter([
        SimpleNamespace(
            content="",
            tool_calls=[{"id": "call-1", "function": {"name": "shell", "arguments": '{"command":"git status"}'}}],
            usage=None,
            finish_reason="tool_calls",
        ),
        SimpleNamespace(
            content="[OWNER_MAINTENANCE COMPLETE] healthy; no open work; 0 tool errors",
            tool_calls=[],
            usage=None,
            finish_reason="stop",
        ),
    ])
    agent._call_provider = lambda **_kwargs: next(responses)
    board = TaskBoard(tasks=[
        TaskItem("1", "Boot protocol", "completed", kind="inspect", completion_gate="tool",
                 evidence=["read_file:OWNER_MAINTENANCE.md"]),
        TaskItem("2", "Capability matrix", "completed", kind="verify", completion_gate="verification",
                 depends_on=["1"], evidence=["read_file:summary.md"]),
        TaskItem("3", "Catalog findings", "completed", kind="verify", completion_gate="verification",
                 depends_on=["2"], evidence=["shell:rg findings"]),
        TaskItem("4", "Fix validated findings", "active", kind="edit", completion_gate="verification",
                 depends_on=["3"]),
        TaskItem("5", "Verify behavior", "pending", kind="verify", completion_gate="verification",
                 depends_on=["4"]),
        TaskItem("6", "Write final report", "pending", kind="report", completion_gate="final",
                 depends_on=["5"]),
    ])

    result = agent.run_turn("start OWNER_MAINTENANCE", task_board=board)

    assert result == "[OWNER_MAINTENANCE COMPLETE] healthy; no open work; 0 tool errors\nRemaining: audit note"
    assert board.open_count() == 0
    assert all(row.status == "completed" for row in board.tasks)
    assert calls == ["economy", "economy", "workflow", "manifest:complete"]
    assert captured["task_board"] is board


def test_owner_comparison_preliminary_answer_continues_until_terminal_closeout():
    captured = {}
    agent = _agent_with_boundary_capture(captured)
    agent.max_provider_requests = 4
    agent.max_tool_rounds = 4
    agent.tool_definitions = [{"name": "grep"}]
    agent.allowed_roots = ["."]
    agent.sandbox_config = {"enabled": False}
    agent._active_lane = None
    agent.tool_compress_enabled = False
    agent._project_scoped_tool_arguments = lambda _name, arguments: arguments
    agent._self_mutation_block_reason = lambda *_args, **_kwargs: None
    agent._operator_approved = lambda *_args, **_kwargs: False
    agent._dispatch_tool = lambda *_args, **_kwargs: "grep:evidence"
    agent._write_tool_audit = lambda *_args, **_kwargs: None
    agent._cap_tool_result_for_context = lambda result, **_kwargs: result
    agent._tool_result_is_error = lambda _result: False

    assistant_messages = []
    agent.session.add_assistant = lambda text, *a, **k: assistant_messages.append(text)
    responses = iter([
        SimpleNamespace(
            content="",
            tool_calls=[{"id": "call-1", "function": {"name": "grep", "arguments": '{"pattern":"x","path":"core"}'}}],
            usage=None,
            finish_reason="tool_calls",
        ),
        SimpleNamespace(content="OWNER_COMPARISON activated. Initial capture only.", tool_calls=[], usage=None, finish_reason="stop"),
        SimpleNamespace(content="[OWNER_COMPARISON COMPLETE] Target: current MO workspace. Matrix done; implementation: none; reject: duplicate", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **_kwargs: next(responses)
    board = TaskBoard(tasks=[
        TaskItem("1", "Capture current-MO target, reference roles, scope, and read-only boundary", "active", kind="inspect", completion_gate="tool"),
        TaskItem("2", "Build current-MO baseline from structured evidence before broad reads", "pending", kind="inspect", completion_gate="tool", depends_on=["1"]),
        TaskItem("3", "Build comparison matrix against current MO with reference evidence", "pending", kind="verify", completion_gate="verification", depends_on=["2"]),
        TaskItem("4", "Classify implement, reject, defer, by-design, and unknown items", "pending", kind="verify", completion_gate="verification", depends_on=["3"]),
        TaskItem("5", "Write OWNER_COMPARISON artifacts and approval-ready closeout", "pending", kind="report", completion_gate="final", depends_on=["4"]),
    ])

    result = agent.run_turn("start OWNER_COMPARISON E:\\ref-a E:\\ref-b", task_board=board)

    assert result == "[OWNER_COMPARISON COMPLETE] Target: current MO workspace. Matrix done; implementation: none; reject: duplicate"
    assert sum("[OWNER_COMPARISON CONTINUATION]" in msg for msg in assistant_messages) == 1
    assert board.open_count() == 0
    assert captured["task_board"] is board


def test_owner_maintenance_blocked_handoff_not_forced_to_continue_by_completion_gate():
    report = SimpleNamespace(
        findings=(SimpleNamespace(kind="taskboard_done_claim_conflict", severity="major"),),
        clean=False,
    )

    assert Agent._owner_maintenance_completion_boundary_requires_continuation(
        "start OWNER_MAINTENANCE",
        "[OWNER_MAINTENANCE BLOCKED]\n\nContinuation capsule:\n- Completed work: catalog.\n- Next: resume.",
        report,
    ) is False


def test_owner_comparison_completion_with_open_taskboard_forces_continuation():
    report = SimpleNamespace(
        findings=(SimpleNamespace(kind="taskboard_done_claim_conflict", severity="major"),),
        clean=False,
    )

    assert Agent._owner_maintenance_completion_boundary_requires_continuation(
        "start OWNER_COMPARISON E:\\ref-a E:\\ref-b",
        "[OWNER_COMPARISON COMPLETE]\n\nTarget: current MO workspace.\nMatrix done; implementation: none; reject: duplicate.",
        report,
    ) is True


def test_ordinary_done_claim_with_open_board_continues_once():
    """An ordinary (non-protocol) turn that claims done while rows are open must
    be forced to continue exactly once — not silently accepted (M3), and not
    looped forever even if the conflict persists."""
    captured = {}
    agent = _agent_with_boundary_capture(captured)
    agent.max_provider_requests = 5
    agent.max_tool_rounds = 5
    agent.tool_definitions = [{"name": "grep"}]
    agent.allowed_roots = ["."]
    agent.sandbox_config = {"enabled": False}
    agent._active_lane = None
    agent.tool_compress_enabled = False
    agent._project_scoped_tool_arguments = lambda _name, arguments: arguments
    agent._self_mutation_block_reason = lambda *_args, **_kwargs: None
    agent._operator_approved = lambda *_args, **_kwargs: False
    agent._dispatch_tool = lambda *_args, **_kwargs: "grep:evidence"
    agent._write_tool_audit = lambda *_args, **_kwargs: None
    agent._cap_tool_result_for_context = lambda result, **_kwargs: result
    agent._tool_result_is_error = lambda _result: False

    assistant_messages = []
    agent.session.add_assistant = lambda text, *a, **k: assistant_messages.append(text)
    # Always flag the conflict — the once-per-turn guard must still terminate.
    agent._run_consistency_boundary = lambda boundary, **kwargs: SimpleNamespace(
        findings=(SimpleNamespace(kind="taskboard_done_claim_conflict", severity="major"),),
        clean=False,
    )
    responses = iter([
        SimpleNamespace(content="All done!", tool_calls=[], usage=None, finish_reason="stop"),
        SimpleNamespace(content="Done, with the open item noted.", tool_calls=[], usage=None, finish_reason="stop"),
        SimpleNamespace(content="should not be reached", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **_kwargs: next(responses)
    board = TaskBoard(tasks=[
        TaskItem("1", "Verify the fix", "active", kind="verify", completion_gate="verification"),
    ])

    result = agent.run_turn("fix the failing test", task_board=board)

    assert result == "Done, with the open item noted."
    assert sum("[TASK TRUTH]" in msg for msg in assistant_messages) == 1


def test_self_protocol_truth_continuation_is_bounded():
    """The self-protocol completion-truth gate must fire a BOUNDED number of times
    (PROTOCOL_STOP_GATE_MAX), not loop to max_provider_requests when a completion
    keeps conflicting — mirroring the ordinary done-claim once-per-turn guard. Before
    the fix this gate had no per-turn cap and could re-inject ~max_provider_requests
    times (the historical self-check-fight loop)."""
    captured = {}
    agent = _agent_with_boundary_capture(captured)
    agent.max_provider_requests = 8
    agent.max_tool_rounds = 8
    agent.tool_definitions = [{"name": "grep"}]
    agent.allowed_roots = ["."]
    agent.sandbox_config = {"enabled": False}
    agent._active_lane = None
    agent.tool_compress_enabled = False
    agent._project_scoped_tool_arguments = lambda _name, arguments: arguments
    agent._self_mutation_block_reason = lambda *_args, **_kwargs: None
    agent._operator_approved = lambda *_args, **_kwargs: False
    agent._dispatch_tool = lambda *_args, **_kwargs: "grep:evidence"
    agent._write_tool_audit = lambda *_args, **_kwargs: None
    agent._cap_tool_result_for_context = lambda result, **_kwargs: result
    agent._tool_result_is_error = lambda _result: False

    assistant_messages = []
    agent.session.add_assistant = lambda text, *a, **k: assistant_messages.append(text)
    # Clean boundary so only the self-protocol gate (forced below) is exercised.
    agent._run_consistency_boundary = lambda boundary, **kwargs: SimpleNamespace(findings=(), clean=True)
    # Force the gate CONDITION true every iteration; the BOUND must still terminate it.
    agent._self_protocol_completion_boundary_requires_continuation = lambda *a, **k: True
    agent._self_protocol_task_truth_continuation_instruction = lambda _u: "[SELF-PROTO TRUTH]"

    responses = iter([
        SimpleNamespace(content=f"answer {i}", tool_calls=[], usage=None, finish_reason="stop")
        for i in range(8)
    ])
    agent._call_provider = lambda **_kwargs: next(responses)
    board = TaskBoard(tasks=[
        TaskItem("1", "Verify the fix", "active", kind="verify", completion_gate="verification"),
    ])

    result = agent.run_turn("fix the failing test", task_board=board)

    # Fired at most twice (bounded), then returned blocked instead of cleanly
    # closing through an unresolved conflict.
    assert sum("[SELF-PROTO TRUTH]" in msg for msg in assistant_messages) == 2
    assert result.startswith("[SELF PROTOCOL TRUTH BLOCKED]")


def test_owner_maintenance_completed_taskboard_rejects_post_completion_tool_calls():
    captured = {}
    agent = _agent_with_boundary_capture(captured)
    agent.max_provider_requests = 4
    agent.max_tool_rounds = 4
    agent.tool_definitions = [{"name": "shell"}]
    agent.allowed_roots = ["."]
    agent.sandbox_config = {"enabled": False}
    agent._active_lane = None
    agent.tool_compress_enabled = False
    agent._project_scoped_tool_arguments = lambda _name, arguments: arguments
    agent._self_mutation_block_reason = lambda *_args, **_kwargs: None
    agent._operator_approved = lambda *_args, **_kwargs: False
    agent._dispatch_tool = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("post-completion tool dispatched"))
    agent._write_tool_audit = lambda *_args, **_kwargs: None
    agent._cap_tool_result_for_context = lambda result, **_kwargs: result
    agent._tool_result_is_error = lambda _result: False

    assistant_messages = []
    agent.session.add_assistant = lambda text, *a, **k: assistant_messages.append(text)
    responses = iter([
        SimpleNamespace(
            content="",
            tool_calls=[{"id": "call-1", "function": {"name": "shell", "arguments": '{"command":"git status"}'}}],
            usage=None,
            finish_reason="tool_calls",
        ),
        SimpleNamespace(content="[OWNER_MAINTENANCE COMPLETE] closed from existing evidence", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **_kwargs: next(responses)
    board = TaskBoard(tasks=[
        TaskItem("1", "Close OWNER_MAINTENANCE", "completed", kind="report", completion_gate="final"),
    ])

    result = agent.run_turn("start OWNER_MAINTENANCE", task_board=board)

    assert result == "[OWNER_MAINTENANCE COMPLETE] closed from existing evidence"
    assert any("[OWNER_MAINTENANCE CLOSEOUT]" in msg for msg in assistant_messages)


def test_generic_first_task_does_not_complete_on_read_only_probe():
    agent = object.__new__(Agent)
    board = TaskBoard(tasks=[
        TaskItem("1", "Understand the request", "active"),
        TaskItem("2", "Report result", "pending"),
    ])

    advanced = agent._advance_task_board_after_tool(board, "read_file")

    assert advanced is False
    assert board.task("1").status == "active"
    assert board.task("2").status == "pending"


def test_taskboard_does_not_complete_final_report_row_before_final_answer():
    agent = object.__new__(Agent)
    board = TaskBoard(tasks=[
        TaskItem("1", "Locate PRT output file", "active"),
        TaskItem("2", "Read and summarize results", "pending"),
        TaskItem("3", "Deliver report to user", "pending", kind="report", completion_gate="final"),
    ])

    assert agent._advance_task_board_after_tool(board, "complete_task") is True
    assert board.task("1").status == "completed"
    assert board.task("2").status == "active"

    assert agent._advance_task_board_after_tool(board, "complete_task") is True
    assert board.task("2").status == "completed"
    assert board.task("3").status == "active"

    assert agent._advance_task_board_after_tool(board, "complete_task") is False
    assert board.task("3").status == "active"
    assert board.open_count() == 1

    assert agent._finalize_task_board_for_answer(board) is True
    assert board.task("3").status == "completed"
    assert board.open_count() == 0


def test_terminal_closeout_carries_real_evidence_not_hollow_token(monkeypatch):
    """C1: the terminal marker may close phase rows, but each closed row must carry
    the turn's real gathered evidence — not only a hollow `final:` token."""
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    board = TaskBoard(tasks=[
        TaskItem("1", "Boot", "completed", kind="inspect", completion_gate="tool",
                 evidence=["read_file:OWNER_MAINTENANCE.md", "shell:git rev-parse HEAD"]),
        TaskItem("2", "Matrix", "active", kind="verify", completion_gate="verification", depends_on=["1"]),
        TaskItem("3", "Catalog", "pending", kind="verify", completion_gate="verification", depends_on=["2"]),
        TaskItem("4", "Report", "pending", kind="report", completion_gate="final", depends_on=["3"]),
    ])
    agent = object.__new__(Agent)
    changed = agent._finalize_self_protocol_task_board_for_answer(
        "start OWNER_MAINTENANCE", "[OWNER_MAINTENANCE COMPLETE] catalog written; diagnostic-only", board)
    assert changed
    assert board.open_count() == 0
    for tid in ("2", "3"):
        row = next(t for t in board.tasks if t.id == tid)
        nonfinal = [e for e in row.evidence if not str(e).startswith("final:")]
        assert nonfinal, f"task {tid} bulk-closed on a final: token only (C1 regression)"
        assert any("read_file" in e or "git" in e for e in nonfinal)


def test_owner_maintenance_closeout_updates_workflow_before_final_manifest(monkeypatch):
    """The final manifest must index the final workflow.md, including the runtime
    closeout section. A complete manifest written before the final workflow update
    leaves stale workflow bytes/hash in manifest.json."""
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    calls: list[str] = []

    def fake_economy(self):
        calls.append("economy")

    def fake_workflow(self, task_board):
        calls.append("workflow")

    def fake_manifest(self, **kwargs):
        calls.append(f"manifest:{kwargs.get('status')}")

    monkeypatch.setattr(Agent, "_write_devmode_economy_record", fake_economy)
    monkeypatch.setattr(Agent, "_reconcile_devmode_workflow_closeout", fake_workflow)
    monkeypatch.setattr(Agent, "_write_devmode_manifest_record", fake_manifest)

    board = TaskBoard(tasks=[
        TaskItem("1", "Boot", "completed", kind="inspect", completion_gate="tool",
                 evidence=["read_file:OWNER_MAINTENANCE.md"]),
        TaskItem("2", "Report", "active", kind="report", completion_gate="final", depends_on=["1"]),
    ])
    agent = object.__new__(Agent)
    assert agent._finalize_self_protocol_task_board_for_answer(
        "start OWNER_MAINTENANCE",
        "[OWNER_MAINTENANCE COMPLETE] healthy; no open work",
        board,
    )
    assert calls == ["economy", "workflow", "manifest:complete"]


def test_self_completed_empty_phase_row_is_backfilled_at_closeout(monkeypatch):
    """A diagnostic/reasoning row the model closed itself via complete_task with NO
    real evidence must be backfilled with the session's gathered evidence at closeout —
    otherwise the whole-board contract gate (no circuit breaker) rejects it every turn
    and loops unbounded (observed live mo-1782079519: CONTRACT GATE loop, 69+ requests)."""
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    board = TaskBoard(tasks=[
        TaskItem("1", "Boot", "completed", kind="inspect", completion_gate="tool",
                 evidence=["read_file:OWNER_MAINTENANCE.md", "shell:git rev-parse HEAD"]),
        # rows the model self-completed via complete_task with only a final: token / empty
        TaskItem("2", "Catalog findings and choose lane", "completed", kind="verify",
                 completion_gate="verification", evidence=["final:assistant_response"]),
        TaskItem("3", "Verify behavior/cost/handoff", "completed", kind="verify",
                 completion_gate="verification", evidence=[]),
        TaskItem("4", "Report", "completed", kind="report", completion_gate="final",
                 evidence=["final:owner_maintenance_protocol_closeout"]),
    ])
    agent = object.__new__(Agent)
    agent._finalize_self_protocol_task_board_for_answer(
        "start OWNER_MAINTENANCE", "[OWNER_MAINTENANCE COMPLETE] HEALTHY; diagnostic-only; 0 tool errors", board)
    for tid in ("2", "3"):
        row = next(t for t in board.tasks if t.id == tid)
        nonfinal = [e for e in row.evidence if not str(e).startswith("final:")]
        assert nonfinal, f"task {tid} left empty — contract gate would loop unbounded"
    # whole-board contract gate now passes instead of looping
    from core.tasking.contract import enforce_contract_gate
    ok, reasons, _ = enforce_contract_gate(board, board_closing=True)
    assert ok, f"closeout still blocked after backfill: {reasons}"


def test_blocked_run_reconciles_summary_complete_marker_to_blocked(tmp_path):
    """A run that ends [OWNER_MAINTENANCE BLOCKED] must not leave a [OWNER_MAINTENANCE COMPLETE] in
    summary.md (the T0403 lie). The marker is reconciled deterministically."""
    from core.tasking.agent_taskboard import AgentTaskBoard
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary\n## Closeout\n- [OWNER_MAINTENANCE COMPLETE]\n", encoding="utf-8")
    # Not blocked -> untouched.
    assert AgentTaskBoard._reconcile_summary_terminal_marker(summary, blocked=False) is False
    assert "[OWNER_MAINTENANCE COMPLETE]" in summary.read_text(encoding="utf-8")
    # Blocked -> rewritten.
    assert AgentTaskBoard._reconcile_summary_terminal_marker(summary, blocked=True) is True
    out = summary.read_text(encoding="utf-8")
    assert "[OWNER_MAINTENANCE COMPLETE]" not in out
    assert "[OWNER_MAINTENANCE BLOCKED]" in out


def test_reconcile_devmode_summary_marker_fires_only_on_blocked_terminal(tmp_path):
    """The agent hook reconciles summary.md ONLY when the terminal answer is BLOCKED —
    a COMPLETE terminal leaves the summary's COMPLETE intact. Returns bool."""
    from core.tasking.agent_taskboard import AgentTaskBoard
    agent = AgentTaskBoard.__new__(AgentTaskBoard)
    active = tmp_path / "2026-01-06T0000"
    active.mkdir()
    (active / "summary.md").write_text("## Closeout\n- [OWNER_MAINTENANCE COMPLETE]\n", encoding="utf-8")
    agent._active_devmode_session_dir = active
    result = agent._reconcile_devmode_summary_marker("[OWNER_MAINTENANCE COMPLETE] HEALTHY.")
    assert result is False  # non-blocked text → no-op
    assert "[OWNER_MAINTENANCE COMPLETE]" in (active / "summary.md").read_text(encoding="utf-8")
    result = agent._reconcile_devmode_summary_marker("[OWNER_MAINTENANCE BLOCKED] turn budget exhausted; continuation capsule")
    assert result is True  # blocked text with session dir → reconciled
    out = (active / "summary.md").read_text(encoding="utf-8")
    assert "[OWNER_MAINTENANCE COMPLETE]" not in out and "[OWNER_MAINTENANCE BLOCKED]" in out


def test_blocked_terminal_reconciliation_projects_blocked_manifest(tmp_path):
    """A gateway-blocked OWNER_MAINTENANCE terminal marker must rewrite the private
    artifacts too: no summary COMPLETE, no active/complete manifest with open rows."""
    from core.tasking.agent_taskboard import AgentTaskBoard
    agent = AgentTaskBoard.__new__(AgentTaskBoard)
    active = tmp_path / "2026-01-06T0000"
    active.mkdir()
    (active / "summary.md").write_text("## Closeout\n- [OWNER_MAINTENANCE COMPLETE]\n", encoding="utf-8")
    board = TaskBoard(tasks=[
        TaskItem("1", "Boot", "completed", evidence=["read_file:protocol"]),
        TaskItem("2", "Matrix", "blocked", evidence=["shell:git status"], blocker="open rows"),
        TaskItem("3", "Catalog", "pending"),
    ])
    agent._active_devmode_session_dir = active
    agent._devmode_run_session_ids = {"session-blocked"}
    agent._devmode_closeout_frozen_errors = 0
    agent._current_route_source = "user"
    agent.instance_id = "instance-test"
    agent.session = SimpleNamespace(session_id="session-blocked")
    agent.gateway = SimpleNamespace(last_task_board=board)

    result = agent._reconcile_devmode_summary_marker("[OWNER_MAINTENANCE BLOCKED] open taskboard rows")
    assert result is True

    summary = (active / "summary.md").read_text(encoding="utf-8")
    assert "[OWNER_MAINTENANCE COMPLETE]" not in summary
    manifest = json.loads((active / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "blocked"
    assert manifest["taskboard"]["state"] == "blocked"
    assert manifest["taskboard"]["open_count"] == 2
    assert manifest["reconciliations"]["summary_terminal_marker"] == "changed"


def test_complete_task_never_closes_a_row_with_zero_evidence():
    """A phase row the model completes via complete_task WITHOUT running any tool of its
    own must not close evidence-empty (observed live mo-1782177115: DEVMODE tasks 5-6
    closed with evidence_count=0 yet passed the contract gate). The runtime attaches the
    session's gathered evidence at the moment of completion — upstream of any gate, so it
    can never loop. This covers the direct complete_task path (not just closeout finalize)."""
    board = TaskBoard(tasks=[
        TaskItem("1", "Gather", "active", kind="inspect", completion_gate="tool"),
        TaskItem("2", "Verify behavior/cost/handoff", "pending", kind="verify",
                 completion_gate="verification", depends_on=["1"]),
        TaskItem("3", "Write summary and close", "pending", kind="report",
                 completion_gate="final", depends_on=["2"]),
    ])
    agent = object.__new__(Agent)
    # Task 1 runs a real tool (accrues its own evidence), then completes → task 2 active.
    agent._advance_task_board_after_tool(board, "read_file", {"path": "core/atomic_write.py"})
    agent._advance_task_board_after_tool(board, "complete_task", {})
    # Task 2 completes via complete_task with NO tool of its own (the exact live pattern).
    # It must carry session evidence. The final/report row must not close via complete_task;
    # it closes only at final-answer boundary.
    agent._advance_task_board_after_tool(board, "complete_task", {})
    assert agent._advance_task_board_after_tool(board, "complete_task", {}) is False
    row = board.task("2")
    assert row.status == "completed"
    nonfinal = [e for e in (row.evidence or []) if not str(e).startswith("final:")]
    assert nonfinal, f"task 2 closed with zero real evidence: {row.evidence!r}"
    assert board.task("3").status == "active"
