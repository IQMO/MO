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


def desktop_completion_satisfied(
    tool_sequence: list[ToolExecutionRecord],
) -> bool:
    """Return whether the latest computer action has fresh target evidence."""
    return evaluate_actuation_evidence(
        [
            event
            for event in tool_execution_records(tool_sequence)
            if event.presentation_only is not True
        ],
        action_tools=ACTUATION_TOOLS,
        observation_tools=_VERIFY_TOOLS,
    ).complete


def _emit_desktop_completion(
    monitor: Any,
    action: str,
    *,
    count: int,
    reason: str,
    tool_sequence: list[ToolExecutionRecord] | None,
) -> None:
    """Record which executed action is awaiting evidence."""
    if monitor is None:
        return
    try:
        monitor.emit("desktop_completion", {
            "action": action,
            "action_checkpoint": count,
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
    """Require fresh post-action evidence.

    Count is the last challenged action's one-based ledger position on every
    surface. Routing hints never require an action.
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
    action_checkpoint = max(
        index + 1 for index, event in enumerate(records)
        if event.tool in ACTUATION_TOOLS
        and event.presentation_only is not True
        and (
            event.successful
            or any(
                isinstance(item, dict)
                and item.get("event") == "action"
                and item.get("status") == "outcome_unknown"
                for item in event.computer_events
            )
        )
    )
    if count != action_checkpoint and evidence.observable:
        _emit_desktop_completion(
            monitor, "verification", count=action_checkpoint,
            reason=evidence.reason, tool_sequence=tool_sequence,
        )
        return DesktopCompletionResult(
            count=action_checkpoint,
            instruction=(
                "[COMPUTER VERIFICATION] The last computer action lacks fresh evidence. "
                "Observe the same target revision to check the result. Do not repeat the action. "
                "After it is verified, continue any unfinished part of the operator's request. "
                "If verification is unavailable, report that limitation without claiming success."
            ),
        )
    _emit_desktop_completion(
        monitor, "unverified", count=action_checkpoint,
        reason=evidence.reason, tool_sequence=tool_sequence,
    )
    return DesktopCompletionResult(
        count=count,
        blocked_text=(
            f"{assistant_text.strip()}\n\nVerification unavailable: {evidence.reason} "
            "The action's outcome is not confirmed."
        ).strip(),
    )
