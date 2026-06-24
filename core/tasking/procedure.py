"""MO work procedures: crystallized, evidence-gated step templates.

A ``WorkProcedure`` is the structured form of a ``core.work_patterns.WorkPattern``:
the proven step sequence for a build/reasoning turn, expressed as ordered,
dependency-linked, evidence-gated rows that seed a ``TaskBoard``. The model still
fills in the *content* of each step and must satisfy each step's evidence gate
before it completes — the procedure makes the *structure* cheap to replay, never
the *verification* optional.

This module owns no task truth. It only produces seed rows in the exact dict shape
``TaskBoard.set_rows`` already consumes (the same shape the OWNER_MAINTENANCE/OWNER_COMPARISON phase
fallbacks use); Gateway/Agent and the TaskBoard evidence gates stay the single
source of truth. Step text is distilled from the matching ``work_patterns`` prose
so no guidance is lost — only structured and gated.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkStep:
    """One evidence-gated step of a WorkProcedure."""

    text: str
    kind: str  # inspect | verify | edit | report
    completion_gate: str = "tool"  # tool | verification | final | manual
    expected_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkProcedure:
    """An ordered, evidence-gated procedure for a build/reasoning work pattern."""

    name: str
    steps: tuple[WorkStep, ...]


def procedure_rows(procedure: WorkProcedure, objective: str = "") -> list[dict[str, object]]:
    """Serialize a WorkProcedure into ``TaskBoard.set_rows`` row dicts.

    Rows are strictly sequential (each depends on the previous) so the evidence
    gate on one step must clear before the next becomes ready. The first row is
    active; the rest are pending. The runtime — not model prose — mints
    completions, and ``set_rows``/the board contract coerce any evidence-gated row
    that arrives "completed" without evidence back to pending, so seeding a
    procedure cannot bypass verification.

    When *objective* is given it is anchored onto the active first step so the
    board still shows *what* is being worked on (the proven structure replaces the
    generic single row, it must not erase the target).
    """
    target = " ".join(str(objective or "").split()).strip()
    rows: list[dict[str, object]] = []
    for idx, step in enumerate(procedure.steps, start=1):
        text = f"{step.text}: {target}" if idx == 1 and target else step.text
        rows.append(
            {
                "id": str(idx),
                "text": text,
                "status": "active" if idx == 1 else "pending",
                "kind": step.kind,
                "completion_gate": step.completion_gate,
                "depends_on": [str(idx - 1)] if idx > 1 else [],
                "expected_evidence": list(step.expected_evidence),
            }
        )
    return rows


# One procedure per build/reasoning WorkPattern. Conventions mirror the proven
# OWNER_MAINTENANCE/OWNER_COMPARISON gateway phase rows: kind ∈ inspect/verify/edit/report, gate
# "tool" for work steps and "final" for the closing report.
_PROCEDURES: dict[str, WorkProcedure] = {
    "build_verify": WorkProcedure(
        "build_verify",
        (
            WorkStep("Inspect context and lean-build options", "inspect",
                     expected_evidence=("read/search of relevant files and existing utilities",)),
            WorkStep("Implement the smallest complete working version", "edit",
                     expected_evidence=("edit applied to the target file(s)",)),
            WorkStep("Verify with a local/static/runtime check", "verify",
                     expected_evidence=("test/check command output",)),
            WorkStep("Report outcome against the request", "report", "final"),
        ),
    ),
    "design_build": WorkProcedure(
        "design_build",
        (
            WorkStep("Inspect design system and lean-build options", "inspect",
                     expected_evidence=("read of existing design tokens/components",)),
            WorkStep("Set direction and adapt existing tokens, components, states, and motion", "edit",
                     expected_evidence=("edit applied to the design/build target",)),
            WorkStep("Verify the result against real evidence", "verify",
                     expected_evidence=("render/test/check evidence",)),
            WorkStep("Report outcome against the request", "report", "final"),
        ),
    ),
    "fix_verify": WorkProcedure(
        "fix_verify",
        (
            WorkStep("Reproduce or inspect the failure and existing fix surface first", "inspect",
                     expected_evidence=("reproduction/root-cause evidence and reuse target",)),
            WorkStep("Apply the smallest safe fix", "edit",
                     expected_evidence=("edit applied to the fix target",)),
            WorkStep("Verify resolution with a local/static/runtime check", "verify",
                     expected_evidence=("passing check after the fix",)),
            WorkStep("Report the fix and its verification", "report", "final"),
        ),
    ),
    "review_repair": WorkProcedure(
        "review_repair",
        (
            WorkStep("Inventory the scoped files and tests", "inspect",
                     expected_evidence=("listing of scoped targets",)),
            WorkStep("Run focused checks to find confirmed issues", "verify",
                     expected_evidence=("check output identifying confirmed issues",)),
            WorkStep("Apply the smallest safe fixes for confirmed issues; delete/reuse before adding", "edit",
                     expected_evidence=("edits applied to confirmed issues",)),
            WorkStep("Rerun the relevant checks", "verify",
                     expected_evidence=("rerun output after fixes",)),
            WorkStep("Report findings and remaining open items", "report", "final"),
        ),
    ),
    "review_evidence": WorkProcedure(
        "review_evidence",
        (
            WorkStep("Inspect the scoped target/diff before reporting", "inspect",
                     expected_evidence=("read of the scoped target/diff",)),
            WorkStep("Gather read/search/git/test evidence for findings", "verify",
                     expected_evidence=("evidence backing each finding",)),
            WorkStep("Classify findings (verified/inferred/absent/uncertain) and report", "report", "final"),
        ),
    ),
    "project_audit": WorkProcedure(
        "project_audit",
        (
            WorkStep("Orient first (structural graph / fuzzy search / tree)", "inspect",
                     expected_evidence=("orientation evidence from graph/search/tree",)),
            WorkStep("Do targeted reads and scoped checks", "verify",
                     expected_evidence=("targeted read/check evidence",)),
            WorkStep("Catalog confirmed findings with file:line and severity", "verify",
                     expected_evidence=("findings catalog with file:line + severity",)),
            WorkStep("Fix one finding class at a time with lean-build reuse/deletion checks", "edit",
                     expected_evidence=("per-class fix with verification",)),
            WorkStep("Report confirmed/suspected/clean honestly", "report", "final"),
        ),
    ),
    "reference_comparison": WorkProcedure(
        "reference_comparison",
        (
            WorkStep("Gather real reference and current-project evidence (read-only)", "inspect",
                     expected_evidence=("reference + current evidence captured",)),
            WorkStep("Build the comparison matrix with per-row evidence", "verify",
                     expected_evidence=("matrix with evidence per row",)),
            WorkStep("Classify adopt/reject/defer/by-design and report MO-native minimal adoptions", "report", "final"),
        ),
    ),
    "prd_planning": WorkProcedure(
        "prd_planning",
        (
            WorkStep("Clarify material questions and state assumptions", "inspect",
                     expected_evidence=("assumptions and open questions captured",)),
            WorkStep("Draft PRD: goals, anti-goals, acceptance criteria, risks, open questions", "report", "final"),
        ),
    ),
}


def work_procedure_for(pattern_name: str) -> WorkProcedure | None:
    """Return the WorkProcedure for a WorkPattern name, or None."""
    return _PROCEDURES.get(str(pattern_name or "").strip())
