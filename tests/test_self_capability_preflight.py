from types import SimpleNamespace

from core.agent.agent import Agent
from core.self_capability_preflight import (
    build_self_capability_preflight_context,
    devmode05_continuation_instruction,
    devmode05_final_allows_stop,
    devmode05_task_truth_continuation_instruction,
    is_devmode05_activation,
    is_vs05_activation,
    should_include_self_capability_preflight,
    vs05_continuation_instruction,
    vs05_final_allows_stop,
    vs05_readonly_source_roots,
)


def test_self_capability_preflight_detection_is_scoped():
    assert is_devmode05_activation("DEVMODE05") is True
    assert is_devmode05_activation("start DEVMODE05") is True
    assert is_vs05_activation("VS05") is True
    assert is_vs05_activation("/VS05 E:\\ref-a E:\\ref-b") is True
    assert is_vs05_activation("start VS05") is True
    assert should_include_self_capability_preflight("DEVMODE05") is True
    assert should_include_self_capability_preflight("start VS05") is True
    assert should_include_self_capability_preflight("audit your workflow against the codebase") is True
    assert should_include_self_capability_preflight("why did you skip the graph tool?") is True

    assert should_include_self_capability_preflight("hi mo") is False
    assert should_include_self_capability_preflight("can you fix this bug in parser.py") is False


def test_self_capability_preflight_ignores_incidental_mo_substrings():
    # Regression: the 2-char "mo" scope marker used to match inside ordinary
    # words (re-MO-ve, me-MO-ry, MO-dal), firing the self-preflight on plain work.
    assert should_include_self_capability_preflight("debug the memory leak") is False
    assert should_include_self_capability_preflight("audit and remove duplicate rows") is False
    assert should_include_self_capability_preflight("skip the modal animation") is False
    # Real whole-word "mo" self-scope with an action word still fires.
    assert should_include_self_capability_preflight("audit mo's own workflow") is True


def test_vs05_readonly_source_roots_extracts_existing_absolute_paths(tmp_path):
    current = tmp_path / "ref-a"
    reference = tmp_path / "ref-b"
    current.mkdir()
    reference.mkdir()

    roots = vs05_readonly_source_roots(f'start VS05 "{current}" {reference}')

    assert roots == [str(current.resolve()), str(reference.resolve())]


def test_self_capability_preflight_context_lists_existing_systems(tmp_path):
    text = build_self_capability_preflight_context("DEVMODE05 audit MO behavior", cwd=".")

    assert "Capability Coverage Matrix" in text
    assert "do not ask what to investigate" in text
    assert "STARTUP EVIDENCE ORDER" in text
    assert "bounded live-trace rewind" in text
    assert "structural graph summary/context before broad grep" in text
    assert "EXISTING with source evidence" in text
    assert "continue autonomously" in text
    assert "/structural-graph" in text
    assert "/learning" in text
    assert "/profile" in text
    assert "core/graph/code_graph.py" in text
    assert "core/graph/structural_graph.py" in text
    assert "core/graph/search.py" in text     # BM25 fuzzy search is discoverable
    assert "core/graph/callgraph.py" in text  # caller/callee walker is discoverable
    assert "core/learning/proactive_learning.py" in text
    # Guard against path rot: every capability file the preflight advertises must exist.
    from core.self_capability_preflight import _CAPABILITY_FILES
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    for _name, rel_path, _note in _CAPABILITY_FILES:
        assert (repo / rel_path).exists(), f"preflight advertises missing file: {rel_path}"
    assert "Required discovery areas" in text
    assert "Verifier checklist" in text
    assert "affected-method logic review" in text
    assert "smallest evidence" in text
    assert "model approval cannot override deterministic omissions" in text
    assert "we might need it later" in text
    assert "duplication/stale/legacy" in text
    assert "private runtime home" in text
    assert "repo-local fallback" in text
    assert "sandbox-blocked" in text
    assert "OS-SHELL RULE" in text
    assert "active shell" in text
    assert "python -c" in text
    assert "SHADOW SELF-AUDIT" in text
    assert "not raw tool telemetry" in text
    assert "Budget boundaries are continuity handoffs" in text
    assert "FINAL SELF-CLOSEOUT GATE" in text
    assert "BASELINE+DELTA" in text
    assert "taskboard must represent real protocol phases" in text
    assert "cost impact" in text
    assert "After open=0/completed task truth" in text
    assert "taskboard_done_claim_conflict" in text


def test_vs05_preflight_context_points_to_vs05_protocol():
    text = build_self_capability_preflight_context("start VS05", cwd=".")

    assert "operator/devmode/VS05.md" in text
    assert "comparison/adoption mode" in text
    assert "current MO workspace as the default target" in text
    assert "operator-supplied paths/links as read-only references" in text
    assert "stay read-only until the operator approves" in text
    assert "operator/devmode/VS05/00-activation-and-boundaries.md" in text
    assert "VS05 TARGET RULE" in text
    assert "VS05 SEMANTIC DELTA RULE" in text
    assert "taskboard ledger/resume surfaces" in text
    assert "SQLite/profile/workflow learning surfaces" in text
    assert "structural/code graph caches" in text
    assert "VS05 BEHAVIOR ECONOMY RULE" in text
    assert "provider-first smoothness" in text
    assert "Ghost/taskboard owner split" in text
    assert "VS05 TERMINAL SHAPE" in text
    assert "Target, Matrix, Adoption, Reject, Defer/Recheck, Artifacts, Approval" in text


def test_vs05_final_stop_requires_terminal_closeout():
    assert vs05_final_allows_stop("start VS05 E:\\ref-a E:\\ref-b", "initial capture only") is False
    assert vs05_final_allows_stop("start VS05", "[VS05 BLOCKED] provider timeout") is True
    assert vs05_final_allows_stop("start VS05", "[VS05 BLOCKED] still comparing") is False
    assert (
        vs05_final_allows_stop(
            "start VS05",
            "[VS05 COMPLETE] Target: current MO. Matrix done; adoption: none; reject: duplicate",
        )
        is True
    )
    assert vs05_final_allows_stop(
        "start VS05",
        "[VS05 COMPLETE]\nTarget: current MO workspace.\nStatus: 7 MO-STRONGER | 10 REFERENCE-STRONGER | 3 MISSING.\nAdopt now: none.\nReject: duplicate.",
    ) is True
    assert vs05_final_allows_stop("normal request", "initial capture only") is True


def test_vs05_continuation_names_matrix_and_dispositions():
    instruction = vs05_continuation_instruction("start VS05", "initial capture only")

    assert "[VS05 CONTINUATION]" in instruction
    assert "comparison matrix" in instruction
    assert "adoption/reject/defer" in instruction
    assert "Target, Matrix, Adoption, Reject" in instruction


def test_vs05_complete_continuation_uses_terminal_template():
    instruction = vs05_continuation_instruction("start VS05", "[VS05 COMPLETE] adoption only")

    assert "missing required closeout terms" in instruction
    assert "Target, Matrix, Adoption, Reject, Defer/Recheck, Artifacts, Approval" in instruction


def test_devmode_final_stop_requires_terminal_boundary():
    assert devmode05_final_allows_stop("START DEVMODE05", "checkpoint report") is False
    assert devmode05_final_allows_stop("START DEVMODE05", "[DEVMODE05 COMPLETE] done") is True
    assert devmode05_final_allows_stop("START DEVMODE05", "[ABORTED] I should stop now") is False
    assert devmode05_final_allows_stop("START DEVMODE05", "[DEVMODE05 BLOCKED] provider timeout") is True
    assert devmode05_final_allows_stop("START DEVMODE05", "[DEVMODE05 BLOCKED] tool budget exhausted") is True
    assert devmode05_final_allows_stop(
        "START DEVMODE05",
        "[DEVMODE05 BLOCKED]\n\nContinuation capsule:\n- Completed: matrix/catalog/workflow created.\n- Dirty files: core/agent_turn.py.\n- Next: continue cleanup.",
    ) is False
    assert devmode05_final_allows_stop("normal request", "checkpoint report") is True


def test_devmode_final_stop_accepts_markdown_wrapped_terminal_boundary():
    assert devmode05_final_allows_stop("START DEVMODE05", "## [DEVMODE05 COMPLETE]\nsummary") is True
    assert devmode05_final_allows_stop("START DEVMODE05", "# [DEVMODE05 BLOCKED] tool budget exhausted") is True
    assert devmode05_final_allows_stop("START DEVMODE05", "**[DEVMODE05 BLOCKED] — Tool budget exhausted**") is True
    assert devmode05_final_allows_stop("START DEVMODE05", "---\n\n**[DEVMODE05 COMPLETE]** session clean") is True
    assert devmode05_final_allows_stop("START DEVMODE05", "Clean. **[DEVMODE05 COMPLETE]** — session closed") is True
    assert devmode05_final_allows_stop("START DEVMODE05", "All checks complete.\n\n---\n\n## [DEVMODE05 COMPLETE]\nsummary") is True

    assert devmode05_final_allows_stop("START DEVMODE05", "Summary: [DEVMODE05 COMPLETE] done") is False
    assert devmode05_final_allows_stop("START DEVMODE05", "I think [DEVMODE05 BLOCKED] maybe") is False


def test_cross_gate_vs05_does_not_block_devmode05_completion():
    """VS05 gate must not block a valid DEVMODE05 completion when both protocols are mentioned."""
    # User input mentions both VS05 and DEVMODE05 — VS05 gate should yield to DEVMODE05 completion
    user_input = "Start DEVMODE05 to implement GAP-02. Commit T2005 VS05 closeout artifacts."
    # is_vs05_activation returns True (mentions VS05), is_devmode05_activation returns True
    assert vs05_final_allows_stop(user_input, "[DEVMODE05 COMPLETE] done") is True
    assert vs05_final_allows_stop(user_input, "[DEVMODE05 BLOCKED] provider timeout") is True
    # But VS05 gate still enforces its own completions
    assert vs05_final_allows_stop(user_input, "initial comparison draft") is False


def test_cross_gate_devmode05_does_not_block_vs05_completion():
    """DEVMODE05 gate must not block a valid VS05 completion when both protocols are mentioned."""
    user_input = "Start VS05 E:\\ref-a E:\\ref-b and also check DEVMODE05 status."
    # is_devmode05_activation returns True (mentions DEVMODE05), is_vs05_activation returns True
    assert devmode05_final_allows_stop(
        user_input,
        "[VS05 COMPLETE]\nTarget: current MO workspace.\nMatrix: done.\nAdoption: none.\nReject: duplicate.\nArtifacts: docs/comparisons/vs05/run.\nApproval: required.",
    ) is True
    assert devmode05_final_allows_stop(user_input, "[VS05 BLOCKED] sandbox blocked") is True
    # But DEVMODE05 gate still enforces its own completions
    assert devmode05_final_allows_stop(user_input, "mid-protocol report") is False


def test_vs05_final_stop_accepts_prefaced_markdown_terminal_boundary():
    text = """All artifacts are complete and verified. Producing the final VS05 closeout.

---

## [VS05 COMPLETE]

Target: current MO workspace.
Reference: `E:\\ref-a` vs `E:\\ref-b`.
Scope: read-only comparison.
Matrix: MO-STRONGER 7, REFERENCE-STRONGER 1, EQUIVALENT 2.
Adoption: none without operator approval.
Reject: duplicate/provider-owned items rejected.
Defer/Recheck: none active.
Artifacts: docs/comparisons/vs05/2026-06-07T2121/.
Approval: required before source edits.
"""
    assert vs05_final_allows_stop("START VS05 E:\\ref-a E:\\ref-b", text) is True
    assert vs05_final_allows_stop(
        "START VS05 E:\\ref-a E:\\ref-b",
        "Summary: [VS05 COMPLETE] Target current MO; Matrix done; adoption none; reject duplicate.",
    ) is False


def test_vs05_completion_rejects_external_target_drift():
    text = """[VS05 COMPLETE]
Target: E:\\ref-b.
Reference: E:\\ref-a.
Scope: source-pair comparison.
Matrix: MO-STRONGER 7, REFERENCE-STRONGER 1.
Adoption: six items scoped for ref-b.
Reject: duplicate legacy items.
Artifacts: docs/comparisons/vs05/run.
Approval: Operator approval required before source edits in E:\\ref-b.
"""
    assert vs05_final_allows_stop("START VS05 E:\\ref-a E:\\ref-b", text) is False

    instruction = vs05_continuation_instruction("START VS05 E:\\ref-a E:\\ref-b", text)
    assert "Current MO workspace is the adoption target" in instruction
    assert "not for a reference path" in instruction


def test_vs05_prefaced_complete_gets_specific_missing_terms_instruction():
    text = """All artifacts are complete.

## [VS05 COMPLETE]

Target: current MO workspace.
Matrix: MO-STRONGER 7.
Artifacts: docs/comparisons/vs05/run.
"""
    instruction = vs05_continuation_instruction("START VS05 E:\\ref-a E:\\ref-b", text)

    assert "missing required closeout terms" in instruction
    assert "adoption" in instruction
    assert "reject" in instruction


def test_devmode_complete_rejects_self_reported_open_work():
    text = """[DEVMODE05 COMPLETE]
Session report:
- Deferred: 9 findings carried forward.
- Next: TOOL-T2 shell drift follow-up.

============================================================
BEHAVIOR VALIDATION: 23/28 non-failing (5 fail, 0 warn, 9 info)
============================================================
  [FAIL] Provider errors        6 provider error(s)
  [FAIL] Anti-hallucination contract 10/13 handoff missing orientation label
============================================================
[ISSUES] 5 check(s) failed - review trace for details
"""
    assert devmode05_final_allows_stop("START DEVMODE05", text) is False


def test_devmode_complete_allows_explicit_no_open_work_summary():
    text = """[DEVMODE05 COMPLETE]
Session report:
- Deferred: none.
- Remaining: 0.
- Next: none.
"""
    assert devmode05_final_allows_stop("START DEVMODE05", text) is True


def test_devmode_rejected_complete_gets_open_work_continuation_instruction():
    text = """[DEVMODE05 COMPLETE]
Session report:
- Deferred: 7 items stable from prior sessions.
- Next: review the deferred findings.
"""

    instruction = devmode05_continuation_instruction("START DEVMODE05", text)

    assert "claimed [DEVMODE05 COMPLETE]" in instruction
    assert "Do not repeat the same completion report" in instruction
    assert "Deferred active work: none" in instruction


def test_devmode_task_truth_continuation_instruction_names_complete_task():
    instruction = devmode05_task_truth_continuation_instruction()

    assert "task/protocol truth" in instruction
    assert "Do not repeat the same completion report" in instruction
    assert "complete_task" in instruction
    assert "open task count is zero" in instruction
    assert "taskboard_done_claim_conflict" in instruction
    assert "do not inspect taskboard source" in instruction
    assert "only if `complete_task` is unavailable or fails" in instruction


def test_agent_injects_self_capability_preflight_for_devmode(monkeypatch, tmp_path):
    agent = Agent.__new__(Agent)
    agent.session = SimpleNamespace(created_at=0)
    agent.profile = None
    agent.memory = None
    agent.workers = None
    agent.config = {}
    agent.project_cwd = str(tmp_path)
    agent.reasoning = ""
    agent._pending_turn_proposal = ""
    agent._goal_active = False
    agent._thread_state = SimpleNamespace()

    monkeypatch.setattr("core.agent.agent_turn.should_include_workspace_awareness", lambda _text: False)
    monkeypatch.setattr("core.agent.agent_turn.should_include_code_graph_context", lambda _text: False)

    context = agent._build_extra_context("DEVMODE05 audit MO behavior")

    assert "MO Self-Capability Preflight" in context
    assert "Capability Coverage Matrix" in context
    assert "hard gate for MO self/DEVMODE05 work" in context
    assert "code_graph" not in getattr(agent, "_last_turn_context_flags", {}) or not agent._last_turn_context_flags["code_graph"]
    assert agent._last_turn_context_flags["self_capability"] is True


def test_devmode_activation_continues_past_checkpoint_final(monkeypatch):
    agent = Agent.__new__(Agent)
    assistant_messages = []
    agent.session = SimpleNamespace(
        messages=[],
        session_id="test-session",
        add_user=lambda text: agent.session.messages.append({"role": "user", "content": text}),
        turn_count=0,
        sanitize_for_provider=lambda **_kwargs: None,
        get_messages=lambda extra_context=None, **_kwargs: [{"role": "system", "content": extra_context or ""}] + agent.session.messages,
        record_usage=lambda *a, **k: None,
        add_assistant=lambda text, **_kwargs: (assistant_messages.append(text), agent.session.messages.append({"role": "assistant", "content": text})),
    )
    agent.profile = None
    agent.memory = None
    agent.workers = None
    agent.config = {}
    agent.project_cwd = "."
    agent.context_summary_enabled = False
    agent.context_handoff_enabled = False
    agent.max_provider_requests = 3
    agent.max_tool_rounds = 1
    agent.provider_name = "fake"
    agent.model = "fake"
    agent.tool_definitions = []
    agent.critic = SimpleNamespace(review=lambda text: SimpleNamespace(text=text))
    agent._pending_turn_proposal = ""
    agent._goal_active = False
    agent._thread_state = SimpleNamespace()
    # Both responses have zero tool calls — the evidence gate rejects the
    # completion claim even though it has the correct prefix, because no
    # tools were called (fabrication guard).
    responses = iter([
        SimpleNamespace(content="checkpoint report", tool_calls=[], usage=None, finish_reason="stop"),
        SimpleNamespace(content="[DEVMODE05 COMPLETE] done", tool_calls=[], usage=None, finish_reason="stop"),
        SimpleNamespace(content="[DEVMODE05 COMPLETE] final after max", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    calls = []

    def fake_provider(**_kwargs):
        calls.append(True)
        return next(responses)

    agent._call_provider = fake_provider
    monkeypatch.setattr("core.agent.agent_turn.should_include_workspace_awareness", lambda _text: False)
    monkeypatch.setattr("core.agent.agent_turn.should_include_code_graph_context", lambda _text: False)

    agent.run_turn("START DEVMODE05")

    # Without any tool evidence, both completion attempts are rejected:
    # response 1: prefix fails → autonomy injected
    # response 2: prefix passes but zero tool calls → evidence gate rejects
    # response 3: max provider requests hit after 2 retries; final answer accepted
    assert len(calls) == 3
    # At least one autonomy injection for the tool-evidence gate
    assert any("No tool evidence" in str(message) for message in assistant_messages)


def test_devmode_activation_is_current_turn_self_change_approval(tmp_path):
    agent = Agent.__new__(Agent)
    agent.config = {"agent": {"self_protection": True}}
    agent.agent_root = str(tmp_path)

    source_path = tmp_path / "interface" / "panel.py"
    blocked_without_devmode = agent._self_mutation_block_reason("fix a project file", "write_file", {"path": str(source_path)})
    allowed_with_devmode = agent._self_mutation_block_reason("DEVMODE05", "write_file", {"path": str(source_path)})

    assert "SELF-PROTECTION" in blocked_without_devmode
    assert allowed_with_devmode is None


def test_clean_complete_stops_without_committing_artifacts(tmp_path, monkeypatch):
    """Session artifacts live under gitignored docs/ — they are local-only and
    must NOT be committed. A clean COMPLETE (no open work) is a valid stop; the
    old 'commit docs/ artifacts before stopping' gate was removed (it forced the
    machinery leak into the product repo and could never fire post-gitignore)."""
    from core.self_capability_preflight import (
        devmode05_final_allows_stop,
        vs05_final_allows_stop,
    )
    assert devmode05_final_allows_stop(
        "start DEVMODE05",
        "[DEVMODE05 COMPLETE]\nSession report:\n- Deferred: none.\n- Remaining: 0.\n- Next: none.\n",
    ) is True
    assert vs05_final_allows_stop(
        "start VS05",
        "[VS05 COMPLETE]\nTarget: current MO workspace.\nMatrix: done.\nAdoption: none.\n"
        "Reject: duplicate.\nArtifacts: docs/comparisons/vs05/run.\nApproval: required.",
    ) is True


def test_operator_mode_requires_owner_token(monkeypatch, tmp_path):
    """RC1: the copyable protocol pack alone must NOT unlock operator mode — a
    private ~/.mo/operator.token (which a user clone never has) is also required."""
    import core.self_capability_preflight as scp

    monkeypatch.delenv("MO_OPERATOR_PROTOCOLS", raising=False)
    monkeypatch.setattr(scp, "_pack_present", lambda: True)
    monkeypatch.setattr(scp, "mo_home", lambda *a, **k: tmp_path)

    # pack present but no owner token -> inert (pack files alone can't fake it)
    assert scp.operator_protocols_installed() is False
    assert scp.is_devmode05_activation("start DEVMODE05") is False

    # owner token present -> operator mode active
    (tmp_path / "operator.token").write_text("owner-secret\n", encoding="utf-8")
    assert scp.operator_protocols_installed() is True
    assert scp.is_devmode05_activation("start DEVMODE05") is True

    # an empty token does not count
    (tmp_path / "operator.token").write_text("   \n", encoding="utf-8")
    assert scp.operator_protocols_installed() is False


def test_protocol_activation_requires_operator_pack(monkeypatch):
    """User clones have no devmode/ pack — the personal protocol terms are
    inert by absence; MO_OPERATOR_PROTOCOLS=1 (set suite-wide in conftest)
    or the real files restore them for the operator."""
    from core.self_capability_preflight import (
        is_devmode05_activation,
        operator_protocols_installed,
    )

    # Suite-wide env forces installed: terms work
    assert operator_protocols_installed() is True
    assert is_devmode05_activation("start DEVMODE05") is True

    # Without env: falls back to the real file check (true on the operator
    # checkout, false on a user clone) — simulate the user clone explicitly.
    monkeypatch.delenv("MO_OPERATOR_PROTOCOLS", raising=False)
    import core.self_capability_preflight as scp
    monkeypatch.setattr(scp.Path, "exists", lambda self: False)
    assert scp.operator_protocols_installed() is False
    assert scp.is_devmode05_activation("start DEVMODE05") is False
    assert scp.is_vs05_activation("VS05 https://github.com/some/repo") is False
