"""Deterministic threat scanning for durable MO memory surfaces.

Local learning artifacts can still become instruction-smuggling channels. This
scanner blocks high-confidence prompt override, role hijack, hidden Unicode,
secret exfiltration, and operator-deception patterns; callers decide how to
handle warnings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

INVISIBLE_UNICODE_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")

_NEGATION_RE = re.compile(
    r"\b(never|don'?t|do not|should not|must not|won'?t|shall not)\b",
    re.IGNORECASE,
)
SECRET_LINE_RE = re.compile(
    r"(bearer\s+|authorization:|api[_-]?key|access[_-]?token|secret|password|private[_-]?key|session[_-]?cookie)",
    re.IGNORECASE,
)

_BLOCK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "prompt_override",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b.{0,80}\b(previous|prior|system|developer|instruction|rules?)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "role_hijack",
        re.compile(
            r"\b(you are now|act as|become)\b.{0,80}\b(system|developer|root|admin|unfiltered|jailbreak|dan)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "memory_persistence_attack",
        re.compile(
            r"\b(save|store|remember|persist)\b.{0,80}\b(always|from now on|forever)\b.{0,120}\b(ignore|bypass|override|hide|exfiltrate|reveal)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "secret_exfiltration",
        re.compile(
            r"\b(reveal|print|show|send|upload|exfiltrate|steal|dump)\b.{0,120}\b(api[_-]?key|token|password|secret|\.env|credential|private[_-]?key|cookie)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "operator_deception",
        re.compile(
            r"\b(do not|don't|never)\b.{0,60}\b(tell|show|report|mention)\b.{0,60}\b(operator|user)\b|\bhide this from\b.{0,40}\b(operator|user)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)

_WARN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("secret_bearing_text", re.compile(
        r"\b(?:[\w-]*(?:api[_-]?key|access[_-]?token|secret|password|private[_-]?key|session[_-]?cookie))"
        r"[\"']?\s*[:=]\s*[\"']?[^\s\"']+|\bbearer\s+\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----",
        re.IGNORECASE,
    )),
    (
        "instruction_like_memory",
        re.compile(r"\b(always|never|must|must not)\b.{0,80}\b(answer|obey|refuse|tool|shell|memory)\b", re.IGNORECASE | re.DOTALL),
    ),
)


@dataclass(frozen=True)
class ThreatFinding:
    kind: str
    severity: str
    snippet: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "severity": self.severity, "snippet": self.snippet}


@dataclass(frozen=True)
class ThreatScanResult:
    surface: str
    blocked: bool
    findings: tuple[ThreatFinding, ...]

    @property
    def warnings(self) -> tuple[ThreatFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "warn")

    @property
    def blocks(self) -> tuple[ThreatFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "block")

    def reason(self) -> str:
        return ", ".join(f.kind for f in self.blocks) or "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "blocked": self.blocked,
            "findings": [finding.as_dict() for finding in self.findings],
        }


def _snippet(text: str, start: int = 0, end: int = 0, limit: int = 160) -> str:
    raw = str(text or "")
    if not raw:
        return ""
    left = max(0, start - 40)
    right = min(len(raw), max(end + 40, left + limit))
    value = re.sub(r"\s+", " ", raw[left:right]).strip()
    if SECRET_LINE_RE.search(value):
        return "[redacted secret-bearing snippet]"
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return value


def _block_match_is_exempt(value: str, kind: str, match: re.Match[str]) -> bool:
    """Return whether a block-pattern match is a clearly safe negated rule."""
    if kind != "secret_exfiltration":
        return False
    prefix = value[:match.start()]
    pre_context = prefix[-40:] if len(prefix) > 40 else prefix
    return bool(_NEGATION_RE.search(pre_context))


def _iter_block_matches(value: str):
    """Yield every non-exempt high-confidence match with its threat kind."""
    for kind, pattern in _BLOCK_PATTERNS:
        for match in pattern.finditer(value):
            if _block_match_is_exempt(value, kind, match):
                continue
            yield kind, match


def _whole_line_span(value: str, start: int, end: int) -> tuple[int, int]:
    """Expand an instruction match so no trailing line fragment survives."""
    line_start = value.rfind("\n", 0, max(0, start)) + 1
    next_newline = value.find("\n", max(start, end))
    line_end = len(value) if next_newline < 0 else next_newline + 1
    return line_start, line_end


def _redaction_marker(removed: str, kinds: set[str], *, at_line_start: bool) -> str:
    label = ",".join(sorted(kinds))
    marker = f"[REDACTED instruction-like content: {label}]"
    if not at_line_start:
        return marker

    first_line = removed.split("\n", 1)[0]
    diff_prefix = first_line[:1] if first_line[:1] in {"+", "-", " "} else ""
    body = first_line[len(diff_prefix):]
    comment = re.match(r"^(\s*)(#|//|/\*|\*|<!--)\s*", body)
    if comment:
        return f"{diff_prefix}{comment.group(1)}{comment.group(2)} {marker}"
    return f"{diff_prefix}{marker}"


def neutralize_blocked_content(text: str) -> str:
    """Remove instruction-bearing spans before untrusted text reaches a model.

    Callers that still need exact bytes for deterministic parsing should retain
    their own raw copy. Newline counts are preserved so model-facing diffs keep
    useful file/hunk shape without carrying the flagged instructions themselves.
    """
    value = str(text or "")
    spans: list[tuple[int, int, set[str]]] = []
    for kind, match in _iter_block_matches(value):
        start, end = _whole_line_span(value, match.start(), match.end())
        spans.append((start, end, {kind}))
    spans.extend(
        (match.start(), match.end(), {"invisible_unicode"})
        for match in INVISIBLE_UNICODE_RE.finditer(value)
    )
    if not spans:
        return value

    merged: list[tuple[int, int, set[str]]] = []
    for start, end, kinds in sorted(spans, key=lambda item: (item[0], item[1])):
        if merged and start < merged[-1][1]:
            previous_start, previous_end, previous_kinds = merged[-1]
            merged[-1] = (
                previous_start,
                max(previous_end, end),
                previous_kinds | kinds,
            )
        else:
            merged.append((start, end, set(kinds)))

    parts: list[str] = []
    cursor = 0
    for start, end, kinds in merged:
        parts.append(value[cursor:start])
        removed = value[start:end]
        parts.append(_redaction_marker(
            removed,
            kinds,
            at_line_start=(start == 0 or value[start - 1] == "\n"),
        ))
        parts.append("\n" * removed.count("\n"))
        cursor = end
    parts.append(value[cursor:])
    return "".join(parts)


def scan_text(text: str, *, surface: str = "memory") -> ThreatScanResult:
    """Scan text for high-confidence durable-memory threats."""
    value = str(text or "")
    findings: list[ThreatFinding] = []

    invisible = INVISIBLE_UNICODE_RE.search(value)
    if invisible:
        findings.append(ThreatFinding("invisible_unicode", "block", _snippet(value, invisible.start(), invisible.end())))

    seen_block_kinds: set[str] = set()
    for kind, match in _iter_block_matches(value):
        if kind in seen_block_kinds:
            continue
        seen_block_kinds.add(kind)
        findings.append(ThreatFinding(kind, "block", _snippet(value, match.start(), match.end())))

    for kind, pattern in _WARN_PATTERNS:
        match = pattern.search(value)
        if match:
            findings.append(ThreatFinding(kind, "warn", _snippet(value, match.start(), match.end())))

    return ThreatScanResult(surface=surface, blocked=any(f.severity == "block" for f in findings), findings=tuple(findings))



