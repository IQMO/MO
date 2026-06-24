"""Final-phase claim-gate registry: dispatch order, once-per-turn, payloads."""
from types import SimpleNamespace

from core.final_gates import (
    CLAIM_GATES,
    run_claim_gates,
    run_done_claim_gate,
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
