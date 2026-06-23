from types import SimpleNamespace

from core.agent.agent import Agent
from core.owner_protocols import (
    is_devmode05_activation,
    is_ifdev05_activation,
    is_vs05_activation,
    vs05_readonly_source_roots,
)
from core.self_maintenance.devmode_closeout import (
    devmode05_continuation_instruction,
    devmode05_final_allows_stop,
    devmode05_task_truth_continuation_instruction,
    ifdev05_continuation_instruction,
    ifdev05_final_allows_stop,
    vs05_continuation_instruction,
    vs05_final_allows_stop,
)
from core.self_maintenance.preflight import should_include_self_capability_preflight


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


def test_self_capability_preflight_catches_self_diagnosis_without_overfiring():
    # Regression: self-diagnosis phrasing (the turns where MO most needs to inventory
    # its own capabilities) was skipped because the verb/noun wasn't in the fixed
    # lists. These must fire — but ordinary work must NOT trip the heavy preflight.
    fires = (
        "why is MO guessing project facts it should know",
        "figure out why you cost so much per turn",
        "your profile gating is broken, investigate",
        "MO keeps drifting on self-work",
    )
    for text in fires:
        assert should_include_self_capability_preflight(text) is True, text
    quiet = (
        "fix the bug in the parser",
        "can you fix this bug",
        "investigate the crash and patch it",
        "reduce the cost of this query",
        "can you figure out the codebase",
        "add a retry to the poller",
    )
    for text in quiet:
        assert should_include_self_capability_preflight(text) is False, text


def test_ifdev05_activation_detection_and_scope():
    assert is_ifdev05_activation("IFDEV05") is True
    assert is_ifdev05_activation("start IFDEV05") is True
    assert is_ifdev05_activation("diagnose the interface") is False
    assert should_include_self_capability_preflight("IFDEV05") is True
    assert should_include_self_capability_preflight("start IFDEV05") is True


def test_ifdev05_final_stop_gate():
    # Clean completion is a terminal stop; open-work / mid-turn prose are not.
    assert ifdev05_final_allows_stop("IFDEV05", "[IFDEV05 COMPLETE] catalog closed; remaining: none") is True
    assert ifdev05_final_allows_stop("IFDEV05", "[IFDEV05 COMPLETE] remaining: 2 findings deferred") is False
    assert ifdev05_final_allows_stop("IFDEV05", "Here is my UX analysis so far") is False
    assert ifdev05_final_allows_stop("IFDEV05", "[IFDEV05 BLOCKED] more work to do") is False
    # Non-IFDEV05 turns are never gated by this function.
    assert ifdev05_final_allows_stop("normal request", "anything") is True


def test_ifdev05_cross_gate_defers_to_other_protocols():
    # An IFDEV05 turn must not block the other protocols' terminal markers.
    assert ifdev05_final_allows_stop("IFDEV05", "[DEVMODE05 COMPLETE] done") is True
    assert ifdev05_final_allows_stop("IFDEV05", "[VS05 COMPLETE] done") is True


def test_ifdev05_continuation_instruction_targets_open_work():
    msg = ifdev05_continuation_instruction("IFDEV05", "[IFDEV05 COMPLETE] remaining: 1 open finding")
    assert "[IFDEV05 CONTINUATION]" in msg
    assert "open" in msg.lower()


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


def test_preflight_context_user_clone_has_no_protocol_recipe(monkeypatch):
    """RC2-lite: the detailed DEVMODE05/VS05 protocol rules live in the operator
    pack (``~/.mo/operator/devmode/preflight-rules.json`` or MO_OPERATOR_PACK),
    not in public code. A user clone (no pack) gets only a generic self-review
    reminder — no protocol shape — plus generic capability orientation. The
    detailed owner-path assertions live in the private pack tests so the recipe is
    not published in the public test suite either."""
    import core.self_maintenance.preflight as scp

    monkeypatch.setattr(scp, "_load_owner_preflight_rules", lambda: [])
    text = scp.build_self_capability_preflight_context(
        "audit your workflow against the codebase", cwd="."
    )
    # generic reminder, never the protocol recipe
    assert "inventory the capabilities MO already has" in text
    assert "DEVMODE05" not in text
    assert "VS05" not in text
    assert "Capability Coverage Matrix" not in text
    assert "STARTUP EVIDENCE ORDER" not in text
    # still gives generic, non-recipe capability orientation (real public files)
    assert "Relevant code-backed capabilities to check:" in text
    assert "core/graph/code_graph.py" in text


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
    user_input = "Start DEVMODE05 to implement a finding. Commit T2005 VS05 closeout artifacts."
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
        "[VS05 COMPLETE]\nTarget: current MO workspace.\nMatrix: done.\nAdoption: none.\nReject: duplicate.\nArtifacts: ~/.mo/memory/comparisons/vs05/run.\nApproval: required.",
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
Artifacts: ~/.mo/memory/comparisons/vs05/2026-06-07T2121/.
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
Artifacts: ~/.mo/memory/comparisons/vs05/run.
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
Artifacts: ~/.mo/memory/comparisons/vs05/run.
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
    # These are UN-owned (no operator-owned classification) → actionable → must continue.
    text = """[DEVMODE05 COMPLETE]
Session report:
- Deferred: 7 items stable from prior sessions.
- Next: review the deferred findings.
"""

    instruction = devmode05_continuation_instruction("START DEVMODE05", text)

    assert "claimed [DEVMODE05 COMPLETE]" in instruction
    assert "Do not repeat the same completion report" in instruction
    # New contract: resolve actionable work OR classify operator-owned items explicitly —
    # no longer a blanket "Deferred active work: none" demand.
    assert "operator-decision items remain" in instruction
    assert "do NOT rewrite a real deferred item as RESOLVED" in instruction


def test_closeout_requires_session_artifacts_exist(tmp_path):
    """A [DEVMODE05 COMPLETE] with a bound session dir missing summary/economy/manifest is
    an INCOMPLETE closeout and must be blocked (live mo-1782208099: the completed-board guard
    ended the turn before they were written). All three present → no violation; no dir → pass."""
    import core.self_maintenance.devmode_closeout as scp
    text = "[DEVMODE05 COMPLETE] HEALTHY. 0 tool errors."
    sd = tmp_path / "2026-01-11T0000"
    sd.mkdir()
    # none present → blocked
    assert scp._devmode05_closeout_evidence_violation(text, frozen_error_count=0, session_dir=sd) is not None
    # 2 of 3 → still blocked, naming the missing one
    (sd / "summary.md").write_text("x", encoding="utf-8")
    (sd / "economy.md").write_text("x", encoding="utf-8")
    v = scp._devmode05_closeout_evidence_violation(text, frozen_error_count=0, session_dir=sd)
    assert v is not None and "manifest.json" in v
    # all three → no artifact violation; and no bound dir → not enforced
    (sd / "manifest.json").write_text("{}", encoding="utf-8")
    assert scp._devmode05_closeout_evidence_violation(text, frozen_error_count=0, session_dir=sd) is None
    assert scp._devmode05_closeout_evidence_violation(text, frozen_error_count=0, session_dir=None) is None


def test_closeout_gate_uses_frozen_error_count_not_moving_live():
    """Freeze: the terminal gate owns the FROZEN closeout error count, not the live
    (moving) monitor — so post-freeze closeout-edit errors can't shift the target and loop
    the gate forever (the mo-1782179985 N->N+1 loop that exhausted the turn budget)."""
    import core.self_maintenance.devmode_closeout as scp
    text = "[DEVMODE05 COMPLETE] HEALTHY. 8 tool errors (all recovered); see economy.md."
    # Owns the frozen 8 -> no violation, regardless of whatever the live monitor now says.
    assert scp._devmode05_closeout_evidence_violation(text, frozen_error_count=8) is None
    # A frozen count the text does NOT own -> still flagged (must own the frozen number).
    assert scp._devmode05_closeout_evidence_violation(text, frozen_error_count=10) is not None
    # Frozen 0 -> nothing to own -> no violation.
    assert scp._devmode05_closeout_evidence_violation("[DEVMODE05 COMPLETE] clean.", frozen_error_count=0) is None
    # devmode05_final_allows_stop threads the frozen count through.
    ui = "start DEVMODE05"
    assert scp.devmode05_final_allows_stop(ui, text, frozen_error_count=8) is True
    assert scp.devmode05_final_allows_stop(ui, text, frozen_error_count=10) is False


def test_capability_matrix_missing_paths_helper():
    """Only EXISTING/ACTIVE rows are checked; a real path passes, a missing one is flagged."""
    import core.self_maintenance.devmode_closeout as scp
    text = (
        "| a | gateway | core/gateway.py | EXISTING/ACTIVE | ENHANCED |\n"
        "| b | gone | core/this_does_not_exist_zzz.py | EXISTING/ACTIVE | ENHANCED |\n"
        "| c | also | core/also_gone_zzz.py | — | NEW |\n"   # NEW row: not a current-existence claim
    )
    bad = scp._capability_matrix_missing_paths(text)
    assert "core/this_does_not_exist_zzz.py" in bad
    assert "core/gateway.py" not in bad          # exists -> not flagged
    assert "core/also_gone_zzz.py" not in bad     # not EXISTING/ACTIVE -> not checked


def test_closeout_blocks_stale_capability_matrix(tmp_path):
    """A capability-matrix.md marking a deleted source path EXISTING/ACTIVE blocks the
    clean closeout (the T2206 stale-baseline failure: self_capability_preflight.py)."""
    import core.self_maintenance.devmode_closeout as scp
    text = "[DEVMODE05 COMPLETE] HEALTHY. 0 tool errors."
    sd = tmp_path / "2026-01-11T0000"
    sd.mkdir()
    for n in ("summary.md", "economy.md", "manifest.json"):
        (sd / n).write_text("x", encoding="utf-8")
    (sd / "capability-matrix.md").write_text(
        "| 1 | preflight | core/this_does_not_exist_zzz.py | EXISTING/ACTIVE | ENHANCED |\n",
        encoding="utf-8")
    v = scp._devmode05_closeout_evidence_violation(text, frozen_error_count=0, session_dir=sd)
    assert v is not None and "core/this_does_not_exist_zzz.py" in v
    # matrix that only cites a real path -> no matrix violation
    (sd / "capability-matrix.md").write_text(
        "| 1 | gateway | core/gateway.py | EXISTING/ACTIVE | ENHANCED |\n", encoding="utf-8")
    assert scp._devmode05_closeout_evidence_violation(text, frozen_error_count=0, session_dir=sd) is None


def test_closeout_blocks_mis_attributed_error_ledger(tmp_path):
    """The error ledger must name the ACTUAL erroring tools from the monitor — the T2206
    failure where the ledger blamed read_file while test_runner/edit_file actually errored."""
    import json
    import core.self_maintenance.devmode_closeout as scp
    mon = tmp_path / "backend_monitor-20260101-000000-test.jsonl"
    mon.write_text(
        json.dumps({"type": "tool_result", "payload": {"tool": "test_runner", "error": True, "route_source": "user", "session_id": "s1"}}) + "\n",
        encoding="utf-8")
    # closeout names the WRONG tool -> blocked (monitor truth is test_runner)
    wrong = "[DEVMODE05 COMPLETE] HEALTHY. 1 tool error: read_file missing path param."
    v = scp._devmode05_closeout_evidence_violation(wrong, monitor_path=str(mon), session_ids={"s1"})
    assert v is not None and "test_runner" in v
    # closeout that names the real erroring tool -> attribution passes
    right = "[DEVMODE05 COMPLETE] HEALTHY. 1 tool error: test_runner (recovered)."
    assert scp._devmode05_closeout_evidence_violation(right, monitor_path=str(mon), session_ids={"s1"}) is None


def test_closeout_blocks_t2206_summary_shape_wrong_error_tools_and_stale_matrix(tmp_path):
    """T2206 regression: the persisted summary marker appears late, the error ledger
    blamed read_file, and the matrix marked a deleted source path EXISTING/ACTIVE."""
    import json
    import core.self_maintenance.devmode_closeout as scp

    mon = tmp_path / "backend_monitor-20260623-220617-test.jsonl"
    mon.write_text(
        "\n".join([
            json.dumps({"type": "tool_result", "payload": {"tool": "test_runner", "error": True, "route_source": "user", "session_id": "s1"}}),
            json.dumps({"type": "tool_result", "payload": {"tool": "test_runner", "error": True, "route_source": "user", "session_id": "s1"}}),
            json.dumps({"type": "tool_result", "payload": {"tool": "edit_file", "error": True, "route_source": "user", "session_id": "s1"}}),
        ]) + "\n",
        encoding="utf-8",
    )
    sd = tmp_path / "2026-06-23T2206"
    sd.mkdir()
    for n in ("summary.md", "economy.md", "manifest.json"):
        (sd / n).write_text("x", encoding="utf-8")
    (sd / "capability-matrix.md").write_text(
        "| 23 | preflight | core/this_does_not_exist_zzz.py | EXISTING/ACTIVE | ENHANCED |\n",
        encoding="utf-8",
    )
    summary = """# DEVMODE05 Session Summary

## Tool Error Ledger
| # | Tool | Root Cause | Recovery |
| 1 | read_file | Missing path | Self-corrected |
| 2 | read_file | Missing path | Self-corrected |

## Tests
The test_runner docs and examples were reviewed outside the error ledger.

- **[DEVMODE05 COMPLETE]** — 2 tool errors, both read_file, recovered.
"""

    v = scp._devmode05_closeout_evidence_violation(
        summary, monitor_path=str(mon), session_ids={"s1"}, frozen_error_count=2, session_dir=sd
    )
    assert v is not None
    assert "test_runner" in v
    assert "edit_file" in v


def test_closeout_attribution_ignores_incidental_names_after_leading_marker(tmp_path):
    """Residual hole the prior fix missed: with the COMPLETE marker at the START of the
    text, the broad ownership window let a tool named only in a later '## Tests' section
    satisfy the ledger. Ownership is now scoped to the ledger + the marker's OWN paragraph,
    so an incidental mention can't excuse a ledger that blames the wrong tool."""
    import json
    import core.self_maintenance.devmode_closeout as scp
    mon = tmp_path / "backend_monitor-20260101-000000-test.jsonl"
    mon.write_text("\n".join([
        json.dumps({"type": "tool_result", "payload": {"tool": "test_runner", "error": True, "route_source": "user", "session_id": "s1"}}),
        json.dumps({"type": "tool_result", "payload": {"tool": "edit_file", "error": True, "route_source": "user", "session_id": "s1"}}),
    ]) + "\n", encoding="utf-8")
    incidental = ("**[DEVMODE05 COMPLETE]** HEALTHY. 2 tool errors.\n\n"
                  "## Tests\nRan test_runner, edit_file: all pass.\n\n"
                  "## Tool Error Ledger\n| 1 | read_file | missing path | benign |\n")
    v = scp._devmode05_closeout_evidence_violation(incidental, monitor_path=str(mon), session_ids={"s1"}, frozen_error_count=2)
    assert v is not None and "test_runner" in v and "edit_file" in v
    # honest: the ledger itself names the real tools -> passes
    honest = ("**[DEVMODE05 COMPLETE]** HEALTHY. 2 tool errors.\n\n"
              "## Tool Error Ledger\n| 1 | test_runner | bad | recovered |\n| 2 | edit_file | bad | recovered |\n")
    assert scp._devmode05_closeout_evidence_violation(honest, monitor_path=str(mon), session_ids={"s1"}, frozen_error_count=2) is None


def test_closeout_blocks_late_marker_stale_matrix_even_without_tool_errors(tmp_path):
    """Artifact validation must still run when the COMPLETE marker is in a closeout
    section instead of at the very beginning of the persisted summary."""
    import core.self_maintenance.devmode_closeout as scp

    sd = tmp_path / "2026-06-23T2206"
    sd.mkdir()
    for n in ("summary.md", "economy.md", "manifest.json"):
        (sd / n).write_text("x", encoding="utf-8")
    (sd / "capability-matrix.md").write_text(
        "| 23 | preflight | core/this_does_not_exist_zzz.py | EXISTING/ACTIVE | ENHANCED |\n",
        encoding="utf-8",
    )
    summary = "# DEVMODE05 Session Summary\n\n## Closeout\n- **[DEVMODE05 COMPLETE]** — clean.\n"

    v = scp._devmode05_closeout_evidence_violation(summary, frozen_error_count=0, session_dir=sd)
    assert v is not None
    assert "core/this_does_not_exist_zzz.py" in v


def test_devmode05_operator_owned_deferred_is_valid_terminal(tmp_path, monkeypatch):
    """External-watcher governance fix (2026-06-23): a DEVMODE05 closeout may report
    OPERATOR-OWNED remainders (operator-decision pending / supervised fix-lane / recorded
    observation / accepted deferred) without being forced to a false "Remaining: none".
    The model must NOT have to rewrite them to RESOLVED to pass the gate — the exact T0000
    case (B2 supervised fix-lane + OBS-PERF-1 recorded observation)."""
    import core.self_maintenance.devmode_closeout as scp
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "nomon"))  # no tool errors
    ui = "start DEVMODE05"

    # The honest T0000 closeout wording — a valid terminal state.
    valid = (
        "[DEVMODE05 COMPLETE] HEALTHY. No actionable product work remains; operator-decision "
        "items remain: B2 (supervised fix-lane), OBS-PERF-1 (recorded observation).\n"
        "- Remaining: 2 inherited P3 items — B2 (supervised fix-lane, awaiting operator "
        "design decision), OBS-PERF-1 (recorded observation)."
    )
    assert scp._devmode05_completion_reports_open_work(valid) is False
    assert scp.devmode05_final_allows_stop(ui, valid) is True

    # Un-owned deferral is still actionable → must continue (NOT auto-accepted).
    unowned = "[DEVMODE05 COMPLETE] done.\n- Remaining: 2 findings deferred to next session."
    assert scp._devmode05_completion_reports_open_work(unowned) is True
    assert scp.devmode05_final_allows_stop(ui, unowned) is False

    # Operator-owned wording can NEVER mask a real actionable failure.
    failing = "[DEVMODE05 COMPLETE] 3 unresolved findings. operator-decision items remain: none."
    assert scp._devmode05_completion_reports_open_work(failing) is True


def test_devmode_task_truth_continuation_instruction_names_complete_task():
    instruction = devmode05_task_truth_continuation_instruction()

    assert "task/protocol truth" in instruction
    assert "Do not repeat the same completion report" in instruction
    assert "complete_task" in instruction
    assert "open task count is zero" in instruction
    assert "taskboard_done_claim_conflict" in instruction
    assert "do not inspect taskboard source" in instruction
    assert "only if `complete_task` is unavailable or fails" in instruction


def test_profile_loads_on_operator_runtime_turn_not_just_greeting(monkeypatch, tmp_path):
    # RC-A regression: an operator/project/runtime task (mo_control signals fire on
    # generic words like "deploy"/"keys") must load the profile (the sole
    # operator-data home) so MO uses its configured knowledge instead of guessing.
    # Greetings must still skip the profile to save tokens.
    agent = Agent.__new__(Agent)
    agent.session = SimpleNamespace(created_at=0)
    agent.profile = SimpleNamespace(build_profile_context=lambda: "[OPERATOR PROFILE BLOCK]")
    agent.memory = None
    agent.workers = None
    agent.config = {"mo_control": {"workspace_path": "", "trigger_terms": []}}
    agent.project_cwd = str(tmp_path)
    agent.reasoning = ""
    agent._pending_turn_proposal = ""
    agent._goal_active = False
    agent._thread_state = SimpleNamespace()

    monkeypatch.setattr("core.agent.agent_turn.should_include_workspace_awareness", lambda _text: False)
    monkeypatch.setattr("core.agent.agent_turn.should_include_code_graph_context", lambda _text: False)
    monkeypatch.setattr("core.agent.agent_turn.build_workflow_learning_context", lambda *_a, **_k: "")
    monkeypatch.setattr("core.agent.agent_turn.build_learning_context", lambda *_a, **_k: "")

    agent._build_extra_context("check the production deploy keys and report")
    assert agent._last_turn_context_flags["profile"] is True

    agent._build_extra_context("hi mo")
    assert agent._last_turn_context_flags["profile"] is False


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
    from core.self_maintenance.devmode_closeout import (
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
        "Reject: duplicate.\nArtifacts: ~/.mo/memory/comparisons/vs05/run.\nApproval: required.",
    ) is True


def test_operator_mode_requires_owner_token(monkeypatch, tmp_path):
    """RC1: the copyable protocol pack alone must NOT unlock operator mode — a
    private ~/.mo/operator.token (which a user clone never has) is also required."""
    import core.owner_protocols as scp

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
    from core.owner_protocols import (
        is_devmode05_activation,
        operator_protocols_installed,
    )

    # Suite-wide env forces installed: terms work
    assert operator_protocols_installed() is True
    assert is_devmode05_activation("start DEVMODE05") is True

    # Without env: falls back to the real file check (true on the operator
    # checkout, false on a user clone) — simulate the user clone explicitly.
    monkeypatch.delenv("MO_OPERATOR_PROTOCOLS", raising=False)
    import core.owner_protocols as scp
    monkeypatch.setattr(scp.Path, "exists", lambda self: False)
    assert scp.operator_protocols_installed() is False
    assert scp.is_devmode05_activation("start DEVMODE05") is False
    assert scp.is_vs05_activation("VS05 https://github.com/some/repo") is False


def test_devmode05_closeout_gate_blocks_unowned_tool_errors(tmp_path, monkeypatch):
    """Runtime refuses a clean DEVMODE05 closeout that denies/omits real tool errors
    (the internalized watcher) — but never false-blocks a no-error session, and a
    closeout that owns the error finishes."""
    import json
    import core.self_maintenance.devmode_closeout as scp
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path))
    (tmp_path / "backend_monitor-1.jsonl").write_text(
        json.dumps({"type": "tool_result", "payload": {"error": True}}) + "\n", encoding="utf-8"
    )
    ui = "start DEVMODE05"
    # faked clean closeout while a tool error happened -> blocked
    assert scp.devmode05_final_allows_stop(ui, "[DEVMODE05 COMPLETE] HEALTHY, zero findings. No tool errors.") is False
    assert "tool error" in scp.devmode05_continuation_instruction(ui, "[DEVMODE05 COMPLETE] HEALTHY.").lower()
    # closeout that owns the error -> allowed
    assert scp.devmode05_final_allows_stop(ui, "[DEVMODE05 COMPLETE] 1 tool error (recovered); see economy.md.") is True
    # the exact T1930 escape: a stray "1" ("12 areas") + "error handling" must NOT count
    # as owning the error, and a bare "economy.md" mention is not ownership either.
    assert scp.devmode05_final_allows_stop(ui, "[DEVMODE05 COMPLETE] HEALTHY across 12 areas, proper error handling. Zero findings. See economy.md.") is False
    # a session with no tool errors is never blocked
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    assert scp.devmode05_final_allows_stop(ui, "[DEVMODE05 COMPLETE] HEALTHY.") is True


def test_devmode05_closeout_gate_blocks_future_session_stamp(tmp_path, monkeypatch):
    """A session dir stamped in the FUTURE (hand-typed, session_stamp.py skipped — the
    T1930 bug) blocks the closeout; a normal past-dated stamp does not."""
    import shutil
    from datetime import datetime, timedelta
    import core.self_maintenance.devmode_closeout as scp
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "nomon"))  # no tool errors
    devmode = tmp_path / "memory" / "devmode"
    future = (datetime.now() + timedelta(minutes=40)).strftime("%Y-%m-%dT%H%M")
    (devmode / future).mkdir(parents=True)
    ui = "start DEVMODE05"
    assert scp.devmode05_final_allows_stop(ui, "[DEVMODE05 COMPLETE] HEALTHY.") is False
    assert "future" in scp.devmode05_continuation_instruction(ui, "[DEVMODE05 COMPLETE] HEALTHY.").lower()
    shutil.rmtree(devmode / future)
    past = (datetime.now() - timedelta(minutes=40)).strftime("%Y-%m-%dT%H%M")
    (devmode / past).mkdir(parents=True)
    assert scp.devmode05_final_allows_stop(ui, "[DEVMODE05 COMPLETE] HEALTHY.") is True


def test_devmode05_closeout_gate_blocks_past_skewed_stamp(tmp_path, monkeypatch):
    """A dir stamped well BEFORE the session actually started (hand-typed, session_stamp.py
    skipped — the mo-1782177115 bug: a `T0112` dir created during an ~0311 session) blocks
    the closeout. Measured against the live monitor's start time, not `now`, so a long-but-
    legitimate run is never flagged."""
    import shutil
    from datetime import datetime, timedelta
    import core.self_maintenance.devmode_closeout as scp
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    mondir = tmp_path / "mon"
    mondir.mkdir()
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(mondir))
    now = datetime.now()
    # Live monitor: this session started ~now.
    (mondir / f"backend_monitor-{now:%Y%m%d-%H%M%S}-abcd1234.jsonl").write_text("", encoding="utf-8")
    devmode = tmp_path / "memory" / "devmode"
    skewed = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H%M")  # ~2h before session start
    (devmode / skewed).mkdir(parents=True)
    ui = "start DEVMODE05"
    assert scp.devmode05_final_allows_stop(ui, "[DEVMODE05 COMPLETE] HEALTHY.") is False
    assert "before this session" in scp.devmode05_continuation_instruction(ui, "[DEVMODE05 COMPLETE] HEALTHY.").lower()
    # A correctly-stamped dir (≈ session start) passes.
    shutil.rmtree(devmode / skewed)
    (devmode / now.strftime("%Y-%m-%dT%H%M")).mkdir(parents=True)
    assert scp.devmode05_final_allows_stop(ui, "[DEVMODE05 COMPLETE] HEALTHY.") is True
