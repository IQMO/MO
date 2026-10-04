"""Surface-neutral evidence evaluation for target-bound computer actuation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet

from ..tasking.results import ToolExecutionRecord, tool_execution_records


@dataclass(frozen=True)
class ActuationEvidence:
    complete: bool
    attempted: bool
    reason: str = ""
    # False when the action bound no target (a launch whose window has not
    # appeared): no later observation can match it, so asking for one is futile.
    observable: bool = True


def computer_no_progress_block_reason(
    tool_sequence: list[ToolExecutionRecord],
    *,
    action_tools: AbstractSet[str],
    unchanged_cycles: int = 3,
) -> str | None:
    """Stop another state-changing action after repeated unchanged observations.

    Native computer events carry only a bounded digest of observation state.
    Pair exact target revisions so one late observation cannot make several
    unobserved actions look like verified cycles.
    """
    events: list[dict] = []
    for record in tool_execution_records(tool_sequence):
        if not record.successful and record.tool not in action_tools:
            continue
        events.extend(item for item in record.computer_events if isinstance(item, dict))

    cycles: list[tuple[str, str]] = []
    for index, event in enumerate(events):
        if not (
            event.get("event") == "action"
            and event.get("status") == "executed"
            and event.get("state_changed") is True
            and str(event.get("tool") or "") in action_tools
        ):
            continue
        target_id = str(event.get("target_id") or "")
        revision = int(event.get("target_revision") or 0)
        if not target_id or revision <= 0:
            continue
        for later in events[index + 1 :]:
            if later.get("event") != "observation":
                continue
            if (
                str(later.get("target_id") or "") == target_id
                and int(later.get("target_revision") or 0) == revision
            ):
                signature = str(later.get("observation_signature") or "")
                if signature:
                    cycles.append((target_id, signature))
                break

    required = max(2, int(unchanged_cycles or 3))
    if len(cycles) < required:
        return None
    target_id, signature = cycles[-1]
    if all(item == (target_id, signature) for item in cycles[-required:]):
        return (
            "[COMPUTER NO-PROGRESS STOP] The last "
            f"{required} state-changing action/observation cycles left the same target "
            "visibly unchanged. Do not repeat or vary computer actions speculatively. "
            "Inspect the failure from non-actuating evidence, select a different exact target, "
            "or ask the operator for clarification."
        )
    return None


def evaluate_actuation_evidence(
    tool_sequence: list[ToolExecutionRecord],
    *,
    action_tools: AbstractSet[str],
    observation_tools: AbstractSet[str],
    action_label: str = "computer actuation",
) -> ActuationEvidence:
    """Require a successful action followed by observation of its target revision.

    Native adapters emit target-bound ``computer_events``. The compatibility
    branch accepts a later successful observation tool only when a third-party
    action adapter has not yet adopted that event contract.
    """
    records = tool_execution_records(tool_sequence)
    successful = [
        (index, event.tool, event)
        for index, event in enumerate(records)
        if event.successful
    ]
    actions = [
        (index, event.tool, event)
        for index, event in enumerate(records)
        if event.tool in action_tools
        and (
            event.successful
            or any(
                isinstance(item, dict)
                and item.get("event") == "action"
                and item.get("status") == "outcome_unknown"
                for item in event.computer_events
            )
        )
    ]
    if not actions:
        return ActuationEvidence(
            complete=False,
            attempted=False,
            reason=f"No successful {action_label} has been performed.",
        )

    last_action_index, _last_action_name, last_action_event = actions[-1]
    action_events = [
        item
        for item in last_action_event.computer_events
        if isinstance(item, dict)
        and item.get("event") == "action"
        and item.get("status") in {"executed", "outcome_unknown"}
    ]
    if action_events:
        action_event = action_events[-1]
        target_id = str(action_event.get("target_id") or "")
        if not target_id:
            return ActuationEvidence(
                complete=False,
                attempted=True,
                reason="The app window had not appeared when MO checked.",
                observable=False,
            )
        target_revision = int(action_event.get("target_revision") or 0)
        action_sequence = int(action_event.get("sequence") or 0)
        for index, name, event in successful:
            if index < last_action_index:
                continue
            if index == last_action_index:
                observations = [
                    item
                    for item in event.computer_events
                    if isinstance(item, dict)
                    and item.get("event") == "observation"
                    and int(item.get("sequence") or 0) > action_sequence
                ]
            elif name in observation_tools:
                observations = [
                    item
                    for item in event.computer_events
                    if isinstance(item, dict) and item.get("event") == "observation"
                ]
            else:
                continue
            if any(
                str(item.get("target_id") or "") == target_id
                and int(item.get("target_revision") or 0) >= target_revision
                for item in observations
            ):
                return ActuationEvidence(complete=True, attempted=True)
        return ActuationEvidence(
            complete=False,
            attempted=True,
            reason="The latest actuation has no fresh observation of the same target revision.",
        )

    if any(index > last_action_index and name in observation_tools for index, name, _ in successful):
        return ActuationEvidence(complete=True, attempted=True)
    return ActuationEvidence(
        complete=False,
        attempted=True,
        reason=f"The latest {action_label} has not been verified from fresh UI evidence.",
    )
