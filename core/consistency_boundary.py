"""Deterministic consistency checks at MO natural boundaries.

These checks report drift; they never mutate taskboards, profile files, docs, or
source code. They are evidence/orientation guardrails for PRT, goals, session
closeout, proposal closeout, and commit/push flows.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .backend_monitor import BackendMonitor, get_monitor, redact_monitor_text
from .number_utils import as_int as _as_int


@dataclass(frozen=True)
class BoundaryFinding:
    kind: str
    severity: str
    message: str
    evidence: str = ""
    suggestion: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class BoundaryReport:
    boundary: str
    findings: tuple[BoundaryFinding, ...] = ()
    clean: bool = True
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "boundary": self.boundary,
            "clean": self.clean,
            "finding_count": len(self.findings),
            "findings": [finding.as_dict() for finding in self.findings],
            "created_at": self.created_at,
        }


_DONE_CLAIM_RE = re.compile(
    r"\b(done|complete|completed|finished|fixed|resolved|ready|production[- ]ready|pushed|committed)\b",
    re.I,
)
_OWNER_MAINTENANCE_COMPLETE_MARKER_RE = re.compile(r"\[OWNER_MAINTENANCE\s+COMPLETE\]", re.I)
_LEARNING_PROMISE_RE = re.compile(
    r"\b(i(?:'ll| will)? remember|i learned|learned from this|updated learning|saved to memory|noted:)\b",
    re.I,
)
_CHECKED_BOX_RE = re.compile(r"(?m)^\s*- \[x\]\s+", re.I)
_UNCHECKED_BOX_RE = re.compile(r"(?m)^\s*- \[ \]\s+")
_COMPLETE_STATUS_RE = re.compile(
    r"(?im)^\s*(?:>\s*)?(?:\*\*)?status(?:\*\*)?\s*[:：](?:\*\*)?\s*(?:\*\*)?(?:complete|completed|implemented|done|pushed-ready|production-ready)\b"
)
_EXIT_CODE_RE = re.compile(r"\[exit code\s+(-?\d+)\]", re.I)


def truth_boundary(
    *,
    deterministic: bool = True,
    labeled: bool = True,
    evidence_preserved: list[str] | None = None,
    loss_accounted: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return a truth-boundary assertion for a context transformation.

    Every compact/handoff/trim should call this and attach the result so
    downstream consumers can verify the anti-hallucination contract was
    satisfied: deterministic (no provider calls), labeled (orientation),
    evidence-preserving (tool/file references survive), loss-accounted
    (all omissions are named).
    """
    return {
        "deterministic": deterministic,
        "labeled": labeled,
        "evidence_preserved": list(evidence_preserved or []),
        "loss_accounted": dict(loss_accounted or {}),
    }


def check_consistency_boundary(
    boundary: str,
    *,
    agent: Any | None = None,
    final_text: str = "",
    user_text: str = "",
    prt_report: Any | None = None,
    goal_plan: Any | None = None,
    session_closeout: Any | None = None,
    task_board: Any | None = None,
    learning_notes: Iterable[str] | None = None,
    command: str = "",
    tool_result: str = "",
    proposal_paths: Iterable[str | Path] | None = None,
    truth_boundary: dict[str, Any] | None = None,
) -> BoundaryReport:
    """Return a compact report for a natural consistency boundary."""
    findings: list[BoundaryFinding] = []
    boundary_name = str(boundary or "boundary").strip() or "boundary"

    if prt_report is not None:
        findings.extend(_check_prt(prt_report, final_text=final_text))
    if goal_plan is not None:
        findings.extend(_check_goal(goal_plan))
    if session_closeout is not None:
        findings.extend(_check_session_closeout(session_closeout))
    if task_board is not None:
        findings.extend(_check_taskboard(task_board, final_text=final_text))
    findings.extend(_check_learning_promise(final_text=final_text, user_text=user_text, learning_notes=learning_notes))
    if command or tool_result:
        findings.extend(_check_commit_push(command=command, tool_result=tool_result))
    if proposal_paths:
        findings.extend(_check_proposals(proposal_paths))
    if truth_boundary is not None:
        findings.extend(_check_truth_boundary(truth_boundary))

    clean = not findings
    return BoundaryReport(
        boundary=redact_monitor_text(boundary_name, 80),
        findings=tuple(_dedupe_findings(findings)),
        clean=clean,
    )


def emit_consistency_boundary(report: BoundaryReport, monitor: BackendMonitor | None = None) -> None:
    """Emit a safe monitor event for a boundary report."""
    mon = monitor or get_monitor()
    if not mon:
        return
    mon.emit("consistency_boundary", report.as_dict())


def render_consistency_boundary(report: BoundaryReport, *, include_clean: bool = True) -> str:
    """Render a compact user-readable boundary report."""
    if report.clean:
        return f"Consistency boundary: clean ({report.boundary})" if include_clean else ""
    lines = [f"Consistency boundary: {len(report.findings)} finding(s) ({report.boundary})"]
    for finding in report.findings[:5]:
        detail = f" — {finding.evidence}" if finding.evidence else ""
        lines.append(f"- {finding.severity}: {finding.message}{detail}")
    if len(report.findings) > 5:
        lines.append(f"- +{len(report.findings) - 5} more")
    return "\n".join(lines)


def changed_proposal_paths_for_last_commit(cwd: str | Path | None = None) -> list[Path]:
    """Return docs/proposals files changed by HEAD, best-effort."""
    try:
        proc = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            cwd=str(cwd or Path.cwd()),
            text=True,
            capture_output=True,
            timeout=3,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    paths = []
    for raw in proc.stdout.splitlines():
        rel = raw.strip().replace("\\", "/")
        if rel.startswith("docs/proposals/") and rel.endswith(".md"):
            paths.append(Path(cwd or Path.cwd()) / rel)
    return paths


def _check_prt(report: Any, *, final_text: str = "") -> list[BoundaryFinding]:
    findings: list[BoundaryFinding] = []
    unresolved = _as_int(getattr(report, "unresolved_count", 0))
    score = getattr(report, "score", "")
    diff_ref = str(getattr(report, "diff_ref", "") or "diff")
    target_met = bool(getattr(report, "is_target_met", False))
    if unresolved:
        findings.append(BoundaryFinding(
            kind="prt_unresolved",
            severity="major",
            message=f"PRT finished with {unresolved} unresolved finding(s); do not claim production-ready.",
            evidence=f"{diff_ref} score={score}",
            suggestion="Resolve or explicitly defer findings before claiming readiness.",
        ))
    if not target_met:
        findings.append(BoundaryFinding(
            kind="prt_target_not_met",
            severity="track",
            message="PRT target was not met at this boundary.",
            evidence=f"{diff_ref} score={score}, unresolved={unresolved}",
            suggestion="Report the PRT status as unresolved or follow up with fixes.",
        ))
    if unresolved and _DONE_CLAIM_RE.search(str(final_text or "")):
        findings.append(BoundaryFinding(
            kind="prt_done_claim_conflict",
            severity="major",
            message="Completion wording conflicts with unresolved PRT findings.",
            evidence=redact_monitor_text(final_text, 180),
            suggestion="Say PRT finished with unresolved findings, not done/ready.",
        ))
    return findings


def _check_goal(plan: Any) -> list[BoundaryFinding]:
    findings: list[BoundaryFinding] = []
    state = str(getattr(plan, "state", "") or "").lower()
    steps = list(getattr(plan, "steps", []) or [])
    total = len(steps)
    completed = sum(1 for step in steps if str(getattr(step, "status", "") or "") == "completed")
    open_steps = [step for step in steps if str(getattr(step, "status", "") or "") in {"pending", "active", "blocked"}]
    objective = str(getattr(plan, "objective", "") or "")
    if state == "completed" and total and completed < total:
        findings.append(BoundaryFinding(
            kind="goal_incomplete_steps",
            severity="major",
            message="Goal is marked completed while steps remain incomplete.",
            evidence=f"{completed}/{total} completed",
            suggestion="Reopen or pause the goal instead of reporting completion.",
        ))
    if state in {"paused", "blocked"}:
        findings.append(BoundaryFinding(
            kind=f"goal_{state}",
            severity="track",
            message=f"Goal ended {state}; unresolved work must remain visible.",
            evidence=redact_monitor_text(str(getattr(plan, "stop_reason", "") or ""), 200),
            suggestion="Report blocker/next step; do not claim the goal is done.",
        ))
    elif open_steps and state != "completed":
        findings.append(BoundaryFinding(
            kind="goal_open_steps",
            severity="track",
            message="Goal boundary has open steps.",
            evidence=f"{len(open_steps)} open of {total}",
            suggestion="Continue, pause with reason, or report the open work honestly.",
        ))
    if state == "completed" and _requires_tool_backed_goal(objective) and not _goal_has_tool_backed_evidence(steps):
        findings.append(BoundaryFinding(
            kind="goal_missing_tool_evidence",
            severity="major",
            message="Goal completed without tool-backed evidence for work that requires verification.",
            evidence=redact_monitor_text(objective, 180),
            suggestion="Use/read tools or verification evidence before completing the goal.",
        ))
    feedback = str(getattr(plan, "auditor_feedback", "") or "").strip()
    if state == "completed" and feedback:
        findings.append(BoundaryFinding(
            kind="goal_auditor_feedback_leftover",
            severity="minor",
            message="Goal completed while auditor feedback remains attached.",
            evidence=redact_monitor_text(feedback, 180),
            suggestion="Clear feedback only after the underlying issue is resolved.",
        ))
    return findings


def _check_session_closeout(closeout: Any) -> list[BoundaryFinding]:
    unresolved = list(getattr(closeout, "unresolved", []) or [])
    dirty = list(getattr(closeout, "dirty_files", []) or [])
    if bool(getattr(closeout, "clean", True)) and not unresolved and not dirty:
        return []
    message = "Session closeout has unresolved state."
    evidence = f"unresolved={len(unresolved)}, dirty={len(dirty)}"
    return [BoundaryFinding(
        kind="session_unresolved",
        severity="track",
        message=message,
        evidence=evidence,
        suggestion="Carry unresolved items forward as orientation; re-verify before future claims.",
    )]


def _check_taskboard(task_board: Any, *, final_text: str = "") -> list[BoundaryFinding]:
    tasks = list(getattr(task_board, "tasks", []) or [])
    text = str(final_text or "")
    claims_done = bool(_DONE_CLAIM_RE.search(text) or _OWNER_MAINTENANCE_COMPLETE_MARKER_RE.search(text))
    if not tasks or not claims_done:
        return []
    open_rows = [task for task in tasks if str(getattr(task, "status", "") or "") in {"pending", "active", "blocked"}]
    if not open_rows:
        return []
    titles = ", ".join(str(getattr(task, "title", "") or "task")[:80] for task in open_rows[:3])
    return [BoundaryFinding(
        kind="taskboard_done_claim_conflict",
        severity="major",
        message="Final answer claims completion while taskboard rows remain open.",
        evidence=f"{len(open_rows)} open: {titles}",
        suggestion="Do not mark/report final completion until Gateway/Agent task truth is closed.",
    )]


def _check_learning_promise(*, final_text: str, user_text: str, learning_notes: Iterable[str] | None) -> list[BoundaryFinding]:
    notes = [str(note or "").strip() for note in (learning_notes or []) if str(note or "").strip()]
    if notes:
        return []
    text = str(final_text or "")
    if not _LEARNING_PROMISE_RE.search(text):
        return []
    return [BoundaryFinding(
        kind="learning_promise_without_record",
        severity="minor",
        message="Assistant promised learning/memory but no learning note was recorded for the turn.",
        evidence=redact_monitor_text(text, 180),
        suggestion="Either record/stage durable learning or avoid claiming memory was updated.",
    )]


def _check_commit_push(*, command: str, tool_result: str) -> list[BoundaryFinding]:
    cmd = str(command or "")
    low = cmd.lower()
    if "git commit" not in low and "git push" not in low:
        return []
    result = str(tool_result or "")
    failed = result.lower().startswith("error") or "[shell blocked]" in result.lower()
    match = _EXIT_CODE_RE.search(result)
    if match and int(match.group(1)) != 0:
        failed = True
    if not failed:
        return []
    kind = "git_push_failed" if "git push" in low else "git_commit_failed"
    return [BoundaryFinding(
        kind=kind,
        severity="major",
        message="Git boundary command failed; do not claim commit/push completion.",
        evidence=redact_monitor_text(result, 220),
        suggestion="Report the failure and rerun only after addressing the blocker.",
    )]


def _check_proposals(paths: Iterable[str | Path]) -> list[BoundaryFinding]:
    findings: list[BoundaryFinding] = []
    for raw in paths:
        path = Path(raw)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        unchecked = len(_UNCHECKED_BOX_RE.findall(text))
        checked = len(_CHECKED_BOX_RE.findall(text))
        if unchecked and _COMPLETE_STATUS_RE.search(text):
            findings.append(BoundaryFinding(
                kind="proposal_complete_with_unchecked_items",
                severity="major",
                message="Proposal/status says complete while checklist still has unchecked items.",
                evidence=f"{path.as_posix()} unchecked={unchecked}, checked={checked}",
                suggestion="Mark status partial or complete the unchecked work before claiming the proposal is done.",
            ))
    return findings


def _goal_has_tool_backed_evidence(steps: list[Any]) -> bool:
    for step in steps:
        for item in list(getattr(step, "evidence", []) or []):
            text = str(item or "")
            if re.match(r"^(read_file|write_file|edit_file|grep|find_files|git_status|shell|test_runner|project_bridge|web_fetch|web_snapshot):", text):
                return True
            if text.startswith("verification_result:passed"):
                return True
    return False


def _requires_tool_backed_goal(objective: str) -> bool:
    text = str(objective or "").lower()
    return any(word in text for word in ("codebase", "repo", "repository", "review", "audit", "investigate", "analyze", "inspect", "build", "fix", "debug", "verify", "test"))


def _check_truth_boundary(tb: dict[str, Any]) -> list[BoundaryFinding]:
    """Validate a truth-boundary assertion from a context transformation."""
    findings: list[BoundaryFinding] = []

    if not tb.get("deterministic"):
        findings.append(BoundaryFinding(
            kind="truth_boundary_nondeterministic",
            severity="major",
            message="Context transformation called a provider; must be deterministic.",
            suggestion="Replace provider call with deterministic data-only logic.",
        ))
    if not tb.get("labeled"):
        findings.append(BoundaryFinding(
            kind="truth_boundary_unlabeled",
            severity="major",
            message="Compacted/synthetic content is not labeled as orientation-only.",
            suggestion="Add explicit 'orientation only, not proof' label.",
        ))
    preserved = tb.get("evidence_preserved", [])
    loss = tb.get("loss_accounted", {})
    if not preserved and not loss:
        findings.append(BoundaryFinding(
            kind="truth_boundary_no_accounting",
            severity="minor",
            message="No evidence-preserved paths or loss-accounting provided.",
            suggestion="Attach preserved file/tool paths and name all loss.",
        ))
    return findings


def _dedupe_findings(findings: list[BoundaryFinding]) -> list[BoundaryFinding]:
    out: list[BoundaryFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for finding in findings:
        key = (finding.kind, finding.message, finding.evidence)
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out
