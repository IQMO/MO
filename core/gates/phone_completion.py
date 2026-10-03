"""Completion evidence gate for authenticated API-to-origin-phone actions."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from ..runtime.surface_identity import normalize_runtime_surface
from ..tooling.tool_constants import PHONE_ACTUATION_TOOLS, PHONE_OBSERVATION_TOOLS
from ..tasking.results import ToolExecutionRecord, tool_execution_records
from .actuation_completion import evaluate_actuation_evidence


_PHONE_CONTEXT_RE = re.compile(
    r"\b(?:android|phone|mobile|this\s+(?:app|screen)|my\s+(?:app|screen|phone)|"
    r"button|field|menu|notification|settings)\b",
    re.I,
)
_PHONE_ACTION_RE = re.compile(
    r"\b(?:tap|click|press|type|enter|fill|scroll|swipe|select|toggle|"
    r"open|close|submit|send|delete|remove|back|home)\b",
    re.I,
)
_DIRECT_PHONE_ACTION_RE = re.compile(
    r"^\s*(?:please\s+)?(?:tap|click|press|type|enter|fill|scroll|swipe|"
    r"select|toggle|delete|remove|go\s+back|go\s+home)\b",
    re.I,
)
_EXPLAIN_RE = re.compile(r"^\s*(?:what|why|where|which|how|should|could|would|can)\b", re.I)
_PHONE_INSPECTION_RE = re.compile(
    r"\b(?:check|inspect|scan|analy[sz]e|review|show|list|find|report|look\s+(?:at|for))\b"
    r"[\s\S]{0,100}\b(?:android|phone|mobile|system|storage|cache|files?|folders?|"
    r"packages?|apps?|battery)\b|"
    r"\b(?:android|phone|mobile|system|storage|cache|files?|folders?|packages?|apps?|battery)\b"
    r"[\s\S]{0,100}\b(?:check|inspect|scan|analy[sz]e|review|show|list|find|report|"
    r"look\s+(?:at|for))\b",
    re.I,
)

_PHONE_APPROVAL_OPTIONS = (
    '\n__MO_OPTIONS__:{"mode":"single","options":['
    '{"label":"Allow","detail":"Run only this exact action if its target and parameters are unchanged"},'
    '{"label":"Not now","detail":"Do not run this action"}'
    "]}"
)


@dataclass(frozen=True)
class PhoneCompletionResult:
    count: int
    instruction: str = ""
    blocked_text: str = ""


def run_phone_completion_gate(
    agent: Any,
    user_input: str,
    route_source: str,
    tool_sequence: list[ToolExecutionRecord],
    *,
    count: int,
    max_continuations: int = 1,
    on_activity: Any = None,
) -> PhoneCompletionResult:
    if normalize_runtime_surface(route_source) != "api":
        return PhoneCompletionResult(count=count)
    records = tool_execution_records(tool_sequence)
    raw = str(getattr(agent, "_conversation_user_input", lambda value: value)(user_input) or "")
    attempted = any(
        event.tool in PHONE_ACTUATION_TOOLS
        for event in records
    )
    observation_attempted = any(
        event.tool in PHONE_OBSERVATION_TOOLS
        for event in records
    )
    inspection_explicit = bool(_PHONE_INSPECTION_RE.search(raw) and _PHONE_CONTEXT_RE.search(raw))
    explicit = bool(
        _DIRECT_PHONE_ACTION_RE.search(raw)
        or (_PHONE_CONTEXT_RE.search(raw) and _PHONE_ACTION_RE.search(raw) and not _EXPLAIN_RE.search(raw))
    )
    if inspection_explicit and not explicit and observation_attempted:
        # A failed tool attempt is still evidence of the exact live boundary and
        # must be reportable without forcing a second identical call.
        return PhoneCompletionResult(count=count)
    if inspection_explicit and not attempted and not observation_attempted:
        if count < max_continuations:
            if on_activity:
                on_activity("inspecting phone capability…")
            return PhoneCompletionResult(
                count=count + 1,
                instruction=(
                    "[PHONE INSPECTION GATE] The operator asked for live Android inspection, "
                    "but no origin-phone observation tool was attempted. Use phone_capabilities "
                    "first when access is uncertain, then the appropriate bounded phone tool "
                    "(phone_system_status, phone_cache_report, phone_packages, phone_files, "
                    "phone_storage_report, or phone_context). Report an exact unavailable/permission result if the live "
                    "host rejects the probe; do not claim that MO has no access without testing it."
                ),
            )
        return PhoneCompletionResult(
            count=count,
            blocked_text=(
                "I did not inspect the live origin phone in this turn, so I cannot verify its "
                "current Android capability or state."
            ),
        )
    if not attempted and not explicit:
        return PhoneCompletionResult(count=count)

    approval_blocks = [
        event.block_reason
        for event in records
        if event.tool in PHONE_ACTUATION_TOOLS
        and event.block_reason.startswith("[APPROVAL REQUIRED")
    ]
    if approval_blocks:
        return PhoneCompletionResult(
            count=count,
            blocked_text=approval_blocks[-1] + _PHONE_APPROVAL_OPTIONS,
        )

    evidence = evaluate_actuation_evidence(
        tool_sequence,
        action_tools=PHONE_ACTUATION_TOOLS,
        observation_tools=PHONE_OBSERVATION_TOOLS,
        action_label="phone actuation",
    )
    if evidence.complete:
        return PhoneCompletionResult(count=count)
    if count < max_continuations:
        if on_activity:
            on_activity("completing phone action…")
        return PhoneCompletionResult(
            count=count + 1,
            instruction=(
                "[PHONE COMPLETION GATE] The requested on-device action is not complete. "
                f"{evidence.reason} Continue in this same turn with the bounded phone tools, "
                "then obtain fresh post-action phone_context evidence for UI actuation or "
                "phone_files, phone_cache_report, or phone_packages evidence for storage, cache, "
                "or package actuation on the "
                "same origin phone. Do not claim success or ask the operator to perform the "
                "action when the authenticated semantic host is available."
            ),
        )
    return PhoneCompletionResult(
        count=count,
        blocked_text=(
            "I could not complete and verify the requested on-device phone action in this turn. "
            "I have not marked it as done."
        ),
    )
