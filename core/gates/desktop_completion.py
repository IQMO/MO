"""Verify executed computer actions on Terminal and Desktop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..runtime.surface_identity import DESKTOP_SURFACES, normalize_runtime_surface
from ..tooling.tool_constants import ACTUATION_TOOLS
from ..tasking.results import ToolExecutionRecord, tool_execution_records
from .actuation_completion import evaluate_actuation_evidence

_VERIFY_TOOLS = frozenset({
    "computer_observe",
})


@dataclass(frozen=True)
class DesktopCompletionResult:
    count: int
    instruction: str = ""
    blocked_text: str = ""


def _emit_desktop_completion(
    monitor: Any,
    action: str,
    *,
    reason: str,
    tool_sequence: list[ToolExecutionRecord] | None,
) -> None:
    """Record an action whose result was left unconfirmed."""
    if monitor is None:
        return
    try:
        monitor.emit("desktop_completion", {
            "action": action,
            "reason": str(reason or "")[:200],
            "tools_run": [
                event.tool
                for event in tool_execution_records(tool_sequence)
            ][-12:],
        })
    except Exception:
        pass


def run_desktop_completion_gate(
    route_source: str,
    tool_sequence: list[ToolExecutionRecord],
    *,
    assistant_text: str = "",
    count: int,
    monitor: Any = None,
) -> DesktopCompletionResult:
    """State an unconfirmed action result instead of letting the reply imply success.

    Actions return their own fresh evidence, so this gate never asks for another
    provider round; ``count`` passes through the shared completion ledger.
    Routing hints never require an action.
    """
    desktop_surface = normalize_runtime_surface(route_source) in DESKTOP_SURFACES
    records = tool_execution_records(tool_sequence)

    approval_blocks = [
        event.block_reason
        for event in records
        if event.block_reason.startswith("[APPROVAL REQUIRED")
    ]
    if approval_blocks:
        approval_text = approval_blocks[-1]
        if not desktop_surface:
            return DesktopCompletionResult(count=count, blocked_text=approval_text)
        return DesktopCompletionResult(
            count=count,
            blocked_text=(
                approval_blocks[-1]
                + '\n__MO_OPTIONS__:{"mode":"single","options":['
                '{"label":"Approve once","detail":"Run only this exact action if its target and parameters are unchanged"},'
                '{"label":"Deny","detail":"Do not run this action"},'
                '{"label":"Cancel task","detail":"Stop this desktop task and invalidate its targets"}'
                "]}"
            ),
        )

    # A routing hint cannot require an action. Read-only answers, choices,
    # and honest failures may finish normally. Verify actual mutations only;
    # never ask the model to repeat an action to satisfy a completion gate.
    evidence = evaluate_actuation_evidence(
        [event for event in records if event.presentation_only is not True],
        action_tools=ACTUATION_TOOLS,
        observation_tools=_VERIFY_TOOLS,
    )
    if not evidence.attempted or evidence.complete:
        return DesktopCompletionResult(count=count)
    # Actions return their own fresh evidence and the model reads it, so there
    # is no forced verification round. An unknown result is stated, not implied.
    _emit_desktop_completion(
        monitor, "unverified",
        reason=evidence.reason, tool_sequence=tool_sequence,
    )
    return DesktopCompletionResult(
        count=count,
        blocked_text=f"{assistant_text.strip()}\n\nVerification unavailable: {evidence.reason}".strip(),
    )
