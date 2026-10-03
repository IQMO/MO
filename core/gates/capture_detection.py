"""Detect durable operator knowledge shared but not captured (input-side capture gate).

MO is DESIGNED to autonomously record durable operator facts (servers, repos, deploy
methods, project paths, preferences, credential LOCATIONS — never values) via
``record_profile_fact``, and location-scoped code conventions via ``record_convention``.
But unlike behavioral-rule learning — which has ``proactive_learning._PATTERNS``
detectors — fact/convention capture had NO detector, so it relied on the model
spontaneously calling the tool, which it almost never does (audit: record_profile_fact 2
calls, record_convention 0 across 14k tool calls).
This is the input-side twin of ``claim_verification``: detect when the operator SHARED durable
knowledge this turn and the model did NOT record it, so the turn loop injects one bounded
nudge to capture it — the same driven-enforcement pattern MO already trusts for claims.

Deliberately HIGH-PRECISION: requires a concrete environment/convention signal with assertive
framing, so ordinary coding chat and questions do not fire. Gated by ``learning.capture_nudge``
(default on) so it can be disabled if it ever over-fires.
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

from dataclasses import dataclass
from collections.abc import Iterator
import re
from typing import Any

# Tools that constitute "I captured durable knowledge this turn."
CAPTURE_TOOLS = frozenset({"record_profile_fact", "record_convention"})


@dataclass(frozen=True)
class CaptureGateResult:
    instruction: str = ""
    blocked_text: str = ""

# Operator ASSERTS a durable environment/personal fact worth remembering across sessions.
# Assertive framing required ("my server IS", "we deploy TO") so questions/mentions don't fire.
_FACT_PATTERNS = (
    (r"\b(?:my|our|the)\s+(?:server|host|vps|box|instance|machine)\s+(?:is|runs|lives|sits)\b(?!\s+(?:down|up|slow|broken|failing|crashing|fine|ok|okay|responding|acting))", "server fact"),
    (r"\bwe\s+(?:deploy|ship|release)\s+(?:to|via|through|by|with)\b", "deploy fact"),
    (r"\b(?:deploy|deployment)\s+(?:method|process)\s+(?:is|works)\b", "deploy fact"),
    (r"\b(?:the\s+)?(?:repo|repository|project)\s+(?:is\s+(?:at|on)|lives\s+(?:at|on|in)|url\s+is)\b", "repo fact"),
    (r"\b(?:credentials?|api\s+keys?|secrets?|tokens?|\.env)\s+(?:are|is|live|sit|located|stored|kept)(?:\s+\w+)?\s+(?:in|at|under|on)\b", "credential-location fact"),
    (r"\b(?:ssh|log\s*in|connect)\s+(?:in)?to\s+\S+\s+(?:with|using|via|as)\b", "access fact"),
    (r"\b(?:remember|note|fyi|for\s+the\s+record)\b[:,\s].{0,80}\b(?:my|our|the)\s+(?:server|repo|deploy|project|path|env|host|vps)\b", "durable-context fact"),
)
# Operator states a code CONVENTION for an area (should become a record_convention: rule + scope).
_CONVENTION_PATTERNS = (
    (r"\b(?:always|never)\b.{0,90}\b(?:in|for|under|when\s+(?:editing|touching|working\s+(?:on|in)))\b.{0,50}(?:\.py|\.ts|\.js|\.tsx|module|package|folder|director|dir\b|files?)", "code convention"),
    (r"\b(?:our|the|team)\s+(?:convention|standard|rule)\s+(?:for|in|is\s+to|is\s+that)\b", "stated convention"),
)
# Only an explicit request to remember a preference enters the mandatory capture
# path. A contextual "I prefer/dislike" remains guidance for the current task;
# deciding it is a durable operator rule would otherwise turn an inference into
# profile truth.
_PREFERENCE_PATTERN = re.compile(
    r"\b"
    r"(?:(?:can|could|would)\s+you\s+|please\s+)?"
    r"(?:remember|record|save|note)(?:\s+(?:this|my))?\s+"
    r"(?:(?:durable|permanent|long[-\s]?term|cross[-\s]?session)\s+)?preference"
    r"\b(?P<tail>[^.!?;\n]{2,200})",
    re.IGNORECASE,
)
_TEMPORARY_PREFERENCE = re.compile(
    r"\b(?:for|in|on|during)\s+(?:this|the\s+(?:current|next))\s+"
    r"(?:task|turn|request|reply|response|message|change|patch|file|session|case)\b|"
    r"\buntil\s+(?:this|the\s+current)\s+"
    r"(?:task|turn|request|reply|response|message|change|patch|file|session|case)"
    r"(?:\s+(?:ends?|is\s+over))?\b|"
    r"\b(?:for\s+now|right\s+now|just\s+this\s+once|just\s+for\s+this|"
    r"for\s+this\s+one|this\s+time|today|temporarily)\b",
    re.IGNORECASE,
)
_QUOTED_TEXT = re.compile(
    r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|(?<!\w)\'[^\'\n]+\'(?!\w)'
)
_FENCED_TEXT = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_THIRD_PARTY_ATTRIBUTION = re.compile(
    r"(?:\b(?:says?|said|writes?|wrote|claims?|claimed|reports?|reported|quoted?|"
    r"(?:tells?|told)\s+me)\s*(?:that\s*)?[:,-]?|"
    r"\baccording\s+to\s+[^,]{1,80},)\s*$",
    re.IGNORECASE,
)
_FACT_COMPILED = tuple((re.compile(p, re.IGNORECASE), label) for p, label in _FACT_PATTERNS)
_CONVENTION_COMPILED = tuple((re.compile(p, re.IGNORECASE), label) for p, label in _CONVENTION_PATTERNS)


def direct_operator_directives(
    text: str, pattern: re.Pattern[str],
) -> Iterator[tuple[str, re.Match[str]]]:
    """Yield matching operator statements outside quotations and attribution."""
    unquoted = _FENCED_TEXT.sub("", text)
    for original_line in unquoted.splitlines():
        if original_line.lstrip().startswith(">"):
            continue
        # Mask quoted signals while preserving literal arguments in a direct
        # operator instruction, such as the test command they want reused.
        line = _QUOTED_TEXT.sub(lambda match: " " * len(match.group()), original_line)
        start = 0
        for boundary in re.finditer(r"(?<=[.!?;])\s+|$", line):
            statement = line[start:boundary.start()]
            original_statement = original_line[start:boundary.start()]
            start = boundary.end()
            for match in pattern.finditer(statement):
                if _THIRD_PARTY_ATTRIBUTION.search(statement[:match.start()]):
                    # Later signal words in the same attributed statement do
                    # not turn the quoted speaker into the operator.
                    break
                yield original_statement, match


def _has_durable_operator_preference(text: str) -> bool:
    """Recognize direct operator preferences without learning quoted or one-turn text."""
    for statement, match in direct_operator_directives(text, _PREFERENCE_PATTERN):
        if _TEMPORARY_PREFERENCE.search(statement):
            continue
        tail = match.group("tail").strip(" \t,:-\u2014")
        if tail:
            return True
    return False


def used_capture_tools(tool_call_counts: dict | None) -> bool:
    if not tool_call_counts:
        return False
    return any(tool_call_counts.get(name) for name in CAPTURE_TOOLS)


def uncaptured_operator_knowledge_signal(user_input: str, tool_call_counts: dict | None) -> str | None:
    """Return a label if the operator shared durable knowledge this turn AND no capture tool
    was used, else None. High-precision — ordinary chat never fires."""
    text = str(user_input or "")
    if not text.strip() or used_capture_tools(tool_call_counts):
        return None
    for rx, label in _FACT_COMPILED:
        if rx.search(text):
            return label
    if _has_durable_operator_preference(text):
        return "preference fact"
    for rx, label in _CONVENTION_COMPILED:
        if rx.search(text):
            return label
    return None


def run_capture_gate(
    agent: Any,
    user_input: str,
    tool_call_counts: dict | None,
    *,
    fired: set,
    monitor: Any = None,
    on_activity: Any = None,
) -> CaptureGateResult:
    """Return one capture nudge without blocking inferred operational facts.

    Gated by ``learning.capture_nudge`` (default on). The provider remains the
    author of any capture; this gate never writes operator input directly.
    """
    cfg = getattr(agent, "config", {})
    cfg = cfg if isinstance(cfg, dict) else {}
    learn_cfg = cfg.get("learning") if isinstance(cfg.get("learning"), dict) else {}
    if not learn_cfg.get("capture_nudge", DEFAULT_PREFERENCES["learning.capture_nudge"]):
        return CaptureGateResult()
    label = uncaptured_operator_knowledge_signal(user_input, tool_call_counts)
    if not label:
        return CaptureGateResult()
    if "capture_gate" in fired:
        if label == "preference fact":
            return CaptureGateResult(
                blocked_text=(
                    "I could not record the explicit durable preference in this turn. "
                    "It has not been saved for future sessions."
                )
            )
        return CaptureGateResult()
    fired.add("capture_gate")
    if monitor:
        try:
            monitor.emit("uncaptured_operator_knowledge", {"label": label})
        except Exception:
            pass
    if on_activity:
        on_activity(f"operator shared {label} - nudging capture...")
    return CaptureGateResult(
        instruction=agent._uncaptured_operator_knowledge_instruction(label)
    )
