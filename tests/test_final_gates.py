"""Final-phase answer-enforcement gates: ordering, counters, once-per-turn guards."""
from types import SimpleNamespace

import core.final_gates as fg
from core.final_gates import (
    CLAIM_GATES,
    run_claim_gates,
    run_contract_gate,
    run_done_claim_gate,
    run_iam05_reporting_gate,
    run_self_protocol_truth_gate,
    run_verify_edits_gate,
)


class _Monitor:
    def __init__(self):
        self.events = []

    def emit(self, event_type, payload):
        self.events.append((event_type, payload))


def _agent():
    # The registry calls instruction methods by name; stub them to echo the label.
    return SimpleNamespace(
        _unverified_completion_claim_instruction=lambda label: f"completion:{label}",
        _unverified_current_state_claim_instruction=lambda label: f"current:{label}",
        _unsourced_external_claim_instruction=lambda label: f"source:{label}",
    )


def test_registry_has_three_claim_gates_in_order():
    assert [g.name for g in CLAIM_GATES] == [
        "completion_claim", "current_state_claim", "unsourced_external_claim",
    ]


def test_no_claim_returns_none_and_no_event():
    monitor = _Monitor()
    out = run_claim_gates(_agent(), "I refactored the parser.", {}, fired=set(), monitor=monitor)
    assert out is None
    assert monitor.events == []


def test_completion_claim_fires_first_with_instruction_and_event():
    monitor = _Monitor()
    fired = set()
    out = run_claim_gates(_agent(), "All tests pass.", {}, fired=fired, monitor=monitor)
    assert out == "completion:tests-pass claim"
    assert ("unverified_completion_claim", {"label": "tests-pass claim"}) in monitor.events
    assert "completion_claim" in fired


def test_current_state_event_includes_tool_calls():
    monitor = _Monitor()
    out = run_claim_gates(_agent(), "The latest version is 8.2.", {"edit_file": 2}, fired=set(), monitor=monitor)
    assert out == "current:latest-version claim"
    event = next(p for e, p in monitor.events if e == "unverified_claim")
    assert event["label"] == "latest-version claim"
    assert event["tool_calls"] == 2  # preserved from the original inline gate


def test_unsourced_gate_fires_only_after_external_pull():
    # web_fetch used + current-state claim + no URL -> unsourced gate (current-state
    # gate is suppressed because web_fetch is a verifying tool).
    out = run_claim_gates(_agent(), "The latest version is 8.2.", {"web_fetch": 1}, fired=set())
    assert out == "source:latest-version claim"


def test_fired_gate_does_not_refire():
    fired = {"completion_claim"}
    # completion already fired this turn -> skip it; nothing else matches -> None.
    out = run_claim_gates(_agent(), "All tests pass.", {}, fired=fired)
    assert out is None


def test_one_gate_per_call_even_when_multiple_match():
    # A turn that both claims clean AND a latest version: completion wins this call,
    # current-state would fire on the next loop pass (after completion is in `fired`).
    fired = set()
    text = "All tests pass and the latest version is 8.2."
    first = run_claim_gates(_agent(), text, {}, fired=fired)
    assert first == "completion:tests-pass claim"
    second = run_claim_gates(_agent(), text, {}, fired=fired)
    assert second == "current:latest-version claim"
    third = run_claim_gates(_agent(), text, {}, fired=fired)
    assert third is None


# ── done-claim gate (Move 3 increment 2: boundary-driven) ──
def _done_claim_agent(conflict: bool):
    return SimpleNamespace(
        _boundary_has_done_claim_conflict=lambda report: conflict,
        _done_claim_task_truth_instruction=lambda: "DONE-CLAIM-INSTRUCTION",
    )


def test_done_claim_gate_fires_on_boundary_conflict():
    fired = set()
    out = run_done_claim_gate(_done_claim_agent(True), {"any": "report"}, fired=fired)
    assert out == "DONE-CLAIM-INSTRUCTION"
    assert "done_claim" in fired


def test_done_claim_gate_silent_without_conflict():
    assert run_done_claim_gate(_done_claim_agent(False), {}, fired=set()) is None


def test_done_claim_gate_once_per_turn():
    # Already fired this turn -> skip even if the conflict still holds.
    assert run_done_claim_gate(_done_claim_agent(True), {}, fired={"done_claim"}) is None


def test_done_claim_shares_fired_set_with_claim_gates_without_collision():
    # done-claim and claim gates share one turn-level `fired` set; keys never collide.
    fired = set()
    run_done_claim_gate(_done_claim_agent(True), {}, fired=fired)
    out = run_claim_gates(_agent(), "All tests pass.", {}, fired=fired)
    assert out == "completion:tests-pass claim"
    assert fired == {"done_claim", "completion_claim"}


# ── verify-edits gate (Move 3 increment 3: side-effecting affected-test runner) ──
def _verify_agent(instruction, *, calls=None):
    """Agent whose affected-test runner returns `instruction` and records its calls."""
    def _run(modified):
        if calls is not None:
            calls.append(modified)
        return instruction
    return SimpleNamespace(_affected_test_failure_instruction=_run)


def test_verify_edits_gate_fires_when_tests_fail():
    fired = set()
    out = run_verify_edits_gate(_verify_agent("FIX-THE-TESTS"), ["a.py"], fired=fired)
    assert out == "FIX-THE-TESTS"
    assert "verify_edits" in fired


def test_verify_edits_passing_check_does_not_mark_fired():
    # Behavior-preserving subtlety: a passing check (falsy instruction) must NOT set the
    # guard, so the affected-test runner can run again later this turn.
    fired = set()
    out = run_verify_edits_gate(_verify_agent(""), ["a.py"], fired=fired)
    assert out is None
    assert "verify_edits" not in fired


def test_verify_edits_skips_and_does_not_rerun_after_a_failure_fired():
    # Once a failure fired this turn, the gate is skipped — the side-effecting runner is
    # NOT invoked again (matches the old `if not verify_edits_continued` guard).
    calls = []
    out = run_verify_edits_gate(_verify_agent("FIX", calls=calls), ["a.py"], fired={"verify_edits"})
    assert out is None
    assert calls == []


def test_verify_edits_runner_invoked_when_not_yet_fired():
    # A prior passing check left the guard unset; a later call re-runs the affected tests.
    calls = []
    run_verify_edits_gate(_verify_agent("", calls=calls), ["a.py"], fired=set())
    assert calls == [["a.py"]]


# ── IAM05 reporting truth gate (answer-time reconciliation) ──
def _iam05_text(*, calls=4, errors=0, corpus=10, ledger="~/.mo/memory/iam05/evidence_ledger_T123456.md"):
    return (
        f"IAM05 report. {calls} tool calls, {errors} tool errors. "
        f"Coverage: sampled 3 of {corpus}. Evidence ledger: {ledger}."
    )


def test_iam05_reporting_gate_silent_for_non_iam05(monkeypatch):
    monkeypatch.setattr(fg, "iam05_source_corpus_count", lambda cwd=None: 10)
    out = run_iam05_reporting_gate(
        "fix parser",
        _iam05_text(corpus=10),
        {"read_file": 4},
        {},
        fired=set(),
    )
    assert out is None


def test_iam05_reporting_gate_blocks_tool_count_mismatch(monkeypatch):
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setattr(fg, "iam05_source_corpus_count", lambda cwd=None: 369)
    out = run_iam05_reporting_gate(
        "start IAM05",
        _iam05_text(calls=18, errors=0, corpus=369),
        {"read_file": 33, "shell": 9, "grep": 6, "write_file": 1, "edit_file": 1, "test_runner": 4},
        {},
        fired=set(),
    )
    assert out is not None
    assert "exact tool calls = 54" in out


def test_iam05_reporting_gate_blocks_error_count_mismatch(monkeypatch):
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setattr(fg, "iam05_source_corpus_count", lambda cwd=None: 10)
    out = run_iam05_reporting_gate(
        "start IAM05",
        _iam05_text(calls=4, errors=0, corpus=10),
        {"read_file": 4},
        {"shell": 2},
        fired=set(),
    )
    assert out is not None
    assert "exact tool errors = 2" in out


def test_iam05_reporting_gate_blocks_wrong_scope_denominator(monkeypatch):
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setattr(fg, "iam05_source_corpus_count", lambda cwd=None: 369)
    out = run_iam05_reporting_gate(
        "start IAM05",
        _iam05_text(calls=4, errors=0, corpus=30),
        {"read_file": 4},
        {},
        fired=set(),
    )
    assert out is not None
    assert "sampled N of 369" in out


def test_iam05_reporting_gate_blocks_date_only_ledger(monkeypatch):
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setattr(fg, "iam05_source_corpus_count", lambda cwd=None: 10)
    out = run_iam05_reporting_gate(
        "start IAM05",
        _iam05_text(calls=4, errors=0, corpus=10, ledger="~/.mo/memory/iam05/evidence_ledger_20260624.md"),
        {"read_file": 4},
        {},
        fired=set(),
    )
    assert out is not None
    assert "session-unique" in out


def test_iam05_reporting_gate_blocks_stale_function_span(monkeypatch):
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setattr(fg, "iam05_source_corpus_count", lambda cwd=None: 10)
    monkeypatch.setattr(fg, "iam05_function_span_index", lambda cwd=None: {"_run_turn_impl": {239}})
    text = _iam05_text(calls=4, errors=0, corpus=10) + " _run_turn_impl is 812 lines."
    out = run_iam05_reporting_gate("start IAM05", text, {"read_file": 4}, {}, fired=set())
    assert out is not None
    assert "line-count mismatch" in out


def test_iam05_reporting_gate_blocks_ambiguous_bare_span(monkeypatch):
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setattr(fg, "iam05_source_corpus_count", lambda cwd=None: 10)
    monkeypatch.setattr(fg, "iam05_function_span_index", lambda cwd=None: {"run_turn": set()})
    text = _iam05_text(calls=4, errors=0, corpus=10) + " run_turn is 746 lines."
    out = run_iam05_reporting_gate("start IAM05", text, {"read_file": 4}, {}, fired=set())
    assert out is not None
    assert "ambiguous line-count claim" in out


def test_iam05_reporting_gate_accepts_exact_report(monkeypatch):
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setattr(fg, "iam05_source_corpus_count", lambda cwd=None: 10)
    monkeypatch.setattr(fg, "iam05_function_span_index", lambda cwd=None: {"_run_turn_impl": {239}})
    text = _iam05_text(calls=4, errors=0, corpus=10) + " _run_turn_impl is 239 lines."
    fired = set()
    out = run_iam05_reporting_gate("start IAM05", text, {"read_file": 4}, {}, fired=fired)
    assert out is None
    assert fired == set()


# ── contract gate (Move 3 increment 4: closing-board, counter, devmode branch) ──
class _Task:
    def __init__(self, id, status):
        self.id = id
        self.status = status


class _Board:
    def __init__(self, tasks, open_count=0):
        self.tasks = tasks
        self._open = open_count

    def open_count(self):
        return self._open


def _patch_contract(monkeypatch, result, capture=None):
    """Stub enforce_contract_gate (-> result) and load_persisted (-> []); record kwargs."""
    monkeypatch.setattr(fg, "load_persisted_tasks_for_contract", lambda board: [])

    def _enforce(board, *, persisted_tasks, board_closing, task_ids):
        if capture is not None:
            capture["task_ids"] = task_ids
            capture["board_closing"] = board_closing
        return result

    monkeypatch.setattr(fg, "enforce_contract_gate", _enforce)


def test_contract_gate_silent_when_board_not_closing(monkeypatch):
    _patch_contract(monkeypatch, (False, ["r"], "FIX"))
    # open rows remain -> not a closing board -> no enforcement, count untouched.
    board = _Board([_Task("1", "active")], open_count=1)
    assert run_contract_gate(object(), board, "do x", set(), count=0, max_continuations=2) == (None, 0)
    # empty / no board -> silent too.
    assert run_contract_gate(object(), None, "do x", set(), count=0, max_continuations=2) == (None, 0)


def test_contract_gate_passes_when_contract_ok(monkeypatch):
    _patch_contract(monkeypatch, (True, [], ""))
    board = _Board([_Task("1", "completed")], open_count=0)
    assert run_contract_gate(object(), board, "do x", set(), count=0, max_continuations=2) == (None, 0)


def test_contract_gate_fires_and_increments(monkeypatch):
    _patch_contract(monkeypatch, (False, ["row 1 lacks evidence"], "CLOSE-WITH-EVIDENCE"))
    board = _Board([_Task("1", "completed")], open_count=0)
    out, count = run_contract_gate(object(), board, "do x", set(), count=0, max_continuations=2)
    assert out == "CLOSE-WITH-EVIDENCE"
    assert count == 1


def test_contract_gate_counter_bounds_and_does_not_loop(monkeypatch):
    _patch_contract(monkeypatch, (False, ["r"], "FIX"))
    board = _Board([_Task("1", "completed")], open_count=0)
    count = 0
    # Fires while count < max (2): 0 -> 1 -> 2.
    out, count = run_contract_gate(object(), board, "do x", set(), count=count, max_continuations=2)
    assert (out, count) == ("FIX", 1)
    out, count = run_contract_gate(object(), board, "do x", set(), count=count, max_continuations=2)
    assert (out, count) == ("FIX", 2)
    # At the cap: disagreement, allow close (None), count unchanged -> no infinite loop.
    out, count = run_contract_gate(object(), board, "do x", set(), count=count, max_continuations=2)
    assert (out, count) == (None, 2)


def test_contract_gate_normal_turn_is_turn_scoped(monkeypatch):
    capture = {}
    _patch_contract(monkeypatch, (True, [], ""), capture=capture)
    # A row completed THIS turn (not in turn_initial) -> task_ids scoped to it.
    board = _Board([_Task("1", "completed"), _Task("2", "completed")], open_count=0)
    run_contract_gate(object(), board, "ordinary work", {"2"}, count=0, max_continuations=2)
    assert capture["task_ids"] == {"1"}  # only the row newly completed this turn
    assert capture["board_closing"] is True


def test_contract_gate_devmode_enforces_whole_board(monkeypatch):
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")  # force operator-protocols installed
    capture = {}
    _patch_contract(monkeypatch, (True, [], ""), capture=capture)
    board = _Board([_Task("1", "completed"), _Task("2", "completed")], open_count=0)
    run_contract_gate(object(), board, "start devmode05", {"2"}, count=0, max_continuations=2)
    assert capture["task_ids"] is None  # whole board, not turn-scoped


# ── self-protocol truth gate (Move 3 increment 5: counter, short-circuit) ──
def _sp_agent(requires, instruction="SP-INSTRUCTION", *, calls=None):
    """Agent stub: boundary predicate -> `requires`, dispatcher -> `instruction`."""
    def _requires(user_input, final_text, boundary_report):
        if calls is not None:
            calls.append((user_input, final_text, boundary_report))
        return requires
    return SimpleNamespace(
        _self_protocol_completion_boundary_requires_continuation=_requires,
        _self_protocol_task_truth_continuation_instruction=lambda ui: instruction,
    )


def test_self_protocol_gate_silent_when_no_conflict():
    out, count = run_self_protocol_truth_gate(_sp_agent(False), "u", "t", object(), count=0, max_continuations=2)
    assert (out, count) == (None, 0)


def test_self_protocol_gate_fires_and_increments():
    out, count = run_self_protocol_truth_gate(_sp_agent(True, "DO-TASK-TRUTH"), "u", "t", object(), count=0, max_continuations=2)
    assert out == "DO-TASK-TRUTH"
    assert count == 1


def test_self_protocol_gate_caps_and_short_circuits_boundary_check():
    # At the cap, the boundary predicate must NOT be called (mirrors the original
    # `count < MAX and requires(...)` short-circuit) and the gate falls through.
    calls = []
    out, count = run_self_protocol_truth_gate(_sp_agent(True, calls=calls), "u", "t", object(), count=2, max_continuations=2)
    assert (out, count) == (None, 2)
    assert calls == []


def test_self_protocol_gate_counter_bounds_no_loop():
    agent = _sp_agent(True, "FIX")
    count = 0
    out, count = run_self_protocol_truth_gate(agent, "u", "t", object(), count=count, max_continuations=2)
    assert (out, count) == ("FIX", 1)
    out, count = run_self_protocol_truth_gate(agent, "u", "t", object(), count=count, max_continuations=2)
    assert (out, count) == ("FIX", 2)
    out, count = run_self_protocol_truth_gate(agent, "u", "t", object(), count=count, max_continuations=2)
    assert (out, count) == (None, 2)  # capped -> no further fire -> no loop


def test_self_protocol_gate_instruction_comes_from_dispatcher():
    # The returned text is exactly what the agent's protocol dispatcher produced.
    agent = _sp_agent(True, "PROTOCOL-SPECIFIC-TEXT")
    out, _ = run_self_protocol_truth_gate(agent, "start devmode05", "t", object(), count=0, max_continuations=2)
    assert out == "PROTOCOL-SPECIFIC-TEXT"


def test_self_protocol_gate_is_counter_based_independent_of_fired_set():
    # No interaction with the other registry gates: the self-protocol gate is
    # counter-threaded and never touches the shared `fired` set used by the
    # done-claim / claim gates.
    fired = set()
    _, c = run_self_protocol_truth_gate(_sp_agent(True), "u", "t", object(), count=0, max_continuations=2)
    _, c = run_self_protocol_truth_gate(_sp_agent(True), "u", "t", object(), count=c, max_continuations=2)
    assert fired == set()  # untouched by the counter-based gate
    # fired-based gates still operate independently afterward:
    assert run_done_claim_gate(_done_claim_agent(True), {}, fired=fired) == "DONE-CLAIM-INSTRUCTION"
    assert fired == {"done_claim"}
