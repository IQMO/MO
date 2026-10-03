"""Behavior gate pipeline — declarative input-phase gates.

The keystone of moving MO's scattered, imperative gate checks toward a single
declarative registry. This module owns the INPUT phase: deterministic checks that can
block a turn BEFORE any provider call (prompt-injection threat scan, malicious-code
refusal). Adding a new pre-provider rule is now one entry here, not an ad-hoc branch.

Scope note: turn-FINAL answer enforcement lives in ``core.gates.final_gates``. That
module owns the contract, task-truth, done-claim, verify-edits, and
claim/source gates that run immediately before the final answer is accepted.
Private extension gates, when present, are loaded through ``core.local_extensions``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .content_safety import classify_harmful_coding_request


@dataclass(frozen=True)
class GateOutcome:
    """A blocking input-gate result: the message to return + the turn 'kind' tag."""

    message: str
    kind: str
    monitor_event: tuple[str, dict] | None = None


def run_input_gates(agent: Any, text: str) -> tuple[GateOutcome | None, list[tuple[str, dict]]]:
    """Run input-phase gates in order.

    Returns ``(outcome, events)`` where *outcome* is the first blocking GateOutcome (or
    None to proceed), and *events* are non-blocking monitor events the caller emits
    (e.g. a threat-scan finding that did not rise to a block). Pure except for the
    threat-scan call delegated to the agent; the caller handles all monitor emission.
    """
    events: list[tuple[str, dict]] = []

    # Gate 1 — prompt-injection / exfiltration / operator-deception threat scan.
    threat = agent._scan_user_input(text)
    if threat:
        events.append(("threat_scan", threat))
        if threat.get("blocked"):
            reason = threat.get("reason") or "unsafe instruction pattern"
            return (
                GateOutcome(
                    message=(
                        f"Input blocked by local safety scan: {reason}. Rephrase without "
                        "prompt-override, deception, or secret-exfiltration content."
                    ),
                    kind="threat_blocked",
                ),
                events,
            )

    # Gate 2 — malicious-code / harmful-request refusal (dual-use-aware).
    harmful = classify_harmful_coding_request(text, getattr(agent, "config", {}))
    if harmful:
        return (
            GateOutcome(
                message=harmful,
                kind="content_blocked",
                monitor_event=("content_safety", {"blocked": True, "kind": "malicious_code"}),
            ),
            events,
        )

    return None, events
