"""Final-phase claim-gate registry: dispatch order, once-per-turn, payloads."""
from types import SimpleNamespace

from core.final_gates import CLAIM_GATES, run_claim_gates


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
