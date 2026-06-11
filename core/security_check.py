"""Turn-end security check hook — scans modified-file content and response text
for hardcoded secrets, unsafe shell patterns, and suspicious patterns.

Reuses existing detection primitives (text_safety, threat_scan) so this module
adds only the hook invocation layer — no new scanner engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .text_safety import contains_secret_value
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
    path: str | None  # file path, or None for response-text findings
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
    # Redact common secret patterns in snippet before returning
    safe = re.sub(
        r"(?i)(?:bearer\s+)[a-z0-9._\-+/=]{8,}",
        "bearer [REDACTED]",
        text[:limit * 2],
    )
    safe = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|token|secret|password|private[_-]?key|session[_-]?cookie)\s*[:=]\s*)[^\s,;]{3,}",
        r"\1[REDACTED]",
        safe,
    )
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
    final_text: str = "",
) -> SecurityCheckResult:
    """Run security scan over file content written this turn and the response text.

    Args:
        modified_files: List of (path, content_or_new_text) for files mutated
                        this turn by write_file or edit_file.
        final_text: The final response text being returned to the operator.

    Returns:
        SecurityCheckResult with all findings.
    """
    findings: list[SecurityCheckFinding] = []

    # 1. Scan each modified file's content
    for path, content in modified_files:
        if not content:
            continue

        # Hardcoded secrets (CRITICAL)
        if contains_secret_value(content):
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

    # 2. Scan final response text for secrets (should never happen)
    if final_text and contains_secret_value(final_text):
        findings.append(
            SecurityCheckFinding(
                severity="critical",
                kind="hardcoded_secret_in_response",
                path=None,
                snippet=_redact_snippet(final_text),
            )
        )

    return SecurityCheckResult(findings=tuple(findings))
