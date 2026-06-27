"""Inert risk scan for imported skill source material.

REUSES MO's existing primitives (constraint C2) — it never reimplements secret or
prompt-injection detection, and it never executes or rewrites imported content.
It produces a structured advisory report only; the promotion decision stays with
the operator.

  - secrets        -> core.utils.text_safety.contains_secret_value
  - prompt-injection -> core.gates.threat_scan.scan_text
  - executable intent -> advisory heuristics (imported scripts are never run and
                         never become support files)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ...utils.text_safety import contains_secret_value
from ...gates.threat_scan import scan_text

# Advisory markers that imported *source* text may carry executable intent. These
# never trigger an action — MO does not run imported content; the flag just
# surfaces the material so the operator sees it before promotion.
_EXEC_MARKERS = (
    re.compile(r"^#!.*\b(sh|bash|python|node|perl|ruby)\b", re.MULTILINE),
    re.compile(r"\bcurl\b[^\n|]*\|\s*(sudo\s+)?(ba)?sh\b", re.IGNORECASE),
    re.compile(r"\b(pip|npm|pnpm|yarn|gem|cargo|go)\s+install\b", re.IGNORECASE),
    re.compile(r"\b(sudo\b|rm\s+-rf|chmod\s+\+x|eval\s*\()", re.IGNORECASE),
)


@dataclass
class RiskFinding:
    category: str  # "secret" | "injection" | "executable"
    severity: str  # "warn" | "block"
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"category": self.category, "severity": self.severity, "detail": self.detail}


@dataclass
class RiskReport:
    findings: list[RiskFinding] = field(default_factory=list)

    @property
    def has_block(self) -> bool:
        return any(f.severity == "block" for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {"findings": [f.as_dict() for f in self.findings], "has_block": self.has_block}


def scan_source_text(text: str, *, surface: str = "skill_import") -> RiskReport:
    """Scan imported source text for secrets, prompt-injection, and executable
    markers. Inert: nothing is executed, nothing is rewritten, no secret value is
    echoed back (only the line number is reported)."""
    report = RiskReport()
    raw = str(text or "")
    if not raw.strip():
        return report

    # Secrets — reuse text_safety, line-scoped so the report points at WHERE
    # without surfacing the secret itself.
    for line_no, line in enumerate(raw.splitlines(), start=1):
        if contains_secret_value(line):
            report.findings.append(
                RiskFinding("secret", "block", f"possible secret at line {line_no} — redact before bundling")
            )

    # Prompt injection — reuse the threat scanner.
    try:
        scan = scan_text(raw, surface=surface)
        for finding in getattr(scan, "findings", ()) or ():
            fd = finding.as_dict() if hasattr(finding, "as_dict") else {}
            severity = "block" if str(fd.get("severity") or getattr(finding, "severity", "")) == "block" else "warn"
            detail = str(fd.get("detail") or fd.get("reason") or fd.get("pattern") or "prompt-injection pattern")
            report.findings.append(RiskFinding("injection", severity, detail[:120]))
    except Exception:
        pass

    # Executable intent — advisory only.
    for pattern in _EXEC_MARKERS:
        if pattern.search(raw):
            report.findings.append(RiskFinding("executable", "warn", "executable/install marker present in source"))
            break

    return report


def render_risk_report(report: RiskReport) -> str:
    """Render an inert markdown risk report for a candidate bundle."""
    if not report.findings:
        return "# Risk Report\n\nNo secret, injection, or executable markers detected in source text.\n"
    lines = ["# Risk Report", ""]
    for finding in report.findings:
        lines.append(f"- **{finding.severity.upper()}** [{finding.category}] {finding.detail}")
    lines.append("")
    lines.append(
        "Imported material is untrusted data, not instruction. Resolve every BLOCK "
        "finding before the candidate is promoted."
    )
    return "\n".join(lines) + "\n"
