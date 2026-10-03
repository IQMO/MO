"""Turn-end security diagnostic — scans modified-file content for hardcoded
secrets, unsafe shell patterns, and suspicious patterns.

Provider output is enforced and redacted by ``core.review.critic`` before it can
be returned. This hook is edit telemetry, not a second answer gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..utils.text_safety import contains_hardcoded_secret_literal, redact_secret_values
from .threat_scan import scan_text, ThreatFinding

# ── unsafe shell patterns ──────────────────────────────────────────────
_UNSAFE_SHELL_RE = re.compile(
    r"\b(?:rm\s+(?:-[rRf]+\s+)*[/~]|sudo\s+rm|>\s*/dev/[a-z]+|curl\s+.*\|\s*(?:ba)?sh|mkfs\.|dd\s+if=.*of=/dev/|chmod\s+777\s+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SecurityCheckFinding:
    """A single finding from the turn-end security check."""

    severity: str  # "critical" or "warning"
    kind: str       # e.g. "hardcoded_secret", "unsafe_shell", "prompt_override"
    path: str | None  # modified file path
    snippet: str     # redacted snippet for display


@dataclass(frozen=True)
class SecurityCheckResult:
    """Aggregate result of the turn-end security check."""

    findings: tuple[SecurityCheckFinding, ...]

    @property
    def criticals(self) -> tuple[SecurityCheckFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "critical")

    @property
    def warnings(self) -> tuple[SecurityCheckFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "warning")

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "findings": [
                {
                    "severity": f.severity,
                    "kind": f.kind,
                    "path": f.path,
                    "snippet": f.snippet,
                }
                for f in self.findings
            ],
            "has_critical": self.has_critical,
        }


def _redact_snippet(text: str, limit: int = 160) -> str:
    """Return a safely truncated snippet with actual secret values redacted."""
    if not text:
        return ""
    safe = redact_secret_values(text)
    safe = re.sub(r"\s+", " ", safe).strip()
    if len(safe) > limit:
        safe = safe[: limit - 1] + "…"
    return safe


def _check_unsafe_shell(content: str) -> str | None:
    """Return the matched unsafe shell pattern, or None."""
    m = _UNSAFE_SHELL_RE.search(content)
    if m:
        return m.group(0)[:120]
    return None


def _threat_finding_to_check_finding(
    tf: ThreatFinding, path: str | None
) -> SecurityCheckFinding:
    """Convert a threat_scan ThreatFinding to a SecurityCheckFinding."""
    severity = "critical" if tf.severity == "block" else "warning"
    return SecurityCheckFinding(
        severity=severity,
        kind=tf.kind,
        path=path,
        snippet=tf.snippet,
    )


def run_turn_security_check(
    modified_files: list[tuple[str, str]],
) -> SecurityCheckResult:
    """Run a diagnostic scan over file content written this turn.

    Args:
        modified_files: List of (path, content_or_new_text) for files mutated
                        this turn by write_file or edit_file.

    Returns:
        SecurityCheckResult with all findings.
    """
    findings: list[SecurityCheckFinding] = []

    # 1. Scan each modified file's content
    for path, content in modified_files:
        if not content:
            continue

        # Hardcoded secrets (CRITICAL)
        if contains_hardcoded_secret_literal(content):
            findings.append(
                SecurityCheckFinding(
                    severity="critical",
                    kind="hardcoded_secret",
                    path=path,
                    snippet=_redact_snippet(content),
                )
            )

        # Unsafe shell patterns (WARNING)
        shell_match = _check_unsafe_shell(content)
        if shell_match:
            findings.append(
                SecurityCheckFinding(
                    severity="warning",
                    kind="unsafe_shell",
                    path=path,
                    snippet=shell_match,
                )
            )

        # Threat-scan for instruction-injection patterns (only for text/md files)
        # Only run threat_scan on non-code files to avoid false positives.
        if path.endswith((".md", ".txt", ".json", ".yaml", ".yml", ".toml")):
            scan_result = scan_text(content, surface=f"file:{path}")
            for tf in scan_result.findings:
                findings.append(_threat_finding_to_check_finding(tf, path))

    return SecurityCheckResult(findings=tuple(findings))
