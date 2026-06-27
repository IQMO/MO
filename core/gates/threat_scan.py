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
    ("secret_bearing_text", SECRET_LINE_RE),
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


def scan_text(text: str, *, surface: str = "memory") -> ThreatScanResult:
    """Scan text for high-confidence durable-memory threats."""
    value = str(text or "")
    findings: list[ThreatFinding] = []

    invisible = INVISIBLE_UNICODE_RE.search(value)
    if invisible:
        findings.append(ThreatFinding("invisible_unicode", "block", _snippet(value, invisible.start(), invisible.end())))

    for kind, pattern in _BLOCK_PATTERNS:
        match = pattern.search(value)
        if match:
            # Skip secret_exfiltration when the action verb is clearly negated
            # (e.g., "Never print secrets" is a security instruction, not a threat)
            if kind == "secret_exfiltration":
                prefix = value[:match.start()]
                # Check if a negation word appears within 20 chars before the match
                pre_context = prefix[-40:] if len(prefix) > 40 else prefix
                if _NEGATION_RE.search(pre_context):
                    continue
            findings.append(ThreatFinding(kind, "block", _snippet(value, match.start(), match.end())))

    for kind, pattern in _WARN_PATTERNS:
        match = pattern.search(value)
        if match:
            findings.append(ThreatFinding(kind, "warn", _snippet(value, match.start(), match.end())))

    return ThreatScanResult(surface=surface, blocked=any(f.severity == "block" for f in findings), findings=tuple(findings))



