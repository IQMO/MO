"""MO feature procedures: proven, evidence-gated protocol templates.

A ``WorkProcedure`` represents a stable feature protocol whose ordered steps are
part of the product behavior, such as clone/adopt or platform migration. Ordinary
build, fix, review, audit, comparison, and planning requests stay provider-authored
instead of receiving a generic seeded taskboard. When a procedure does apply, the
model still fills in each step's content and must satisfy its evidence gate before
completion.

This module owns no task truth. It only produces seed rows in the exact dict shape
``TaskBoard.set_rows`` already consumes; Gateway/Agent and the TaskBoard evidence gates stay the single
source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkStep:
    """One evidence-gated step of a WorkProcedure."""

    text: str
    kind: str  # inspect | edit | execute | verify | ask
    completion_gate: str = "tool"  # tool | verification | manual
    expected_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkProcedure:
    """An ordered, evidence-gated procedure for a stable feature protocol."""

    name: str
    steps: tuple[WorkStep, ...]


def procedure_rows(procedure: WorkProcedure) -> list[dict[str, object]]:
    """Serialize a WorkProcedure into ``TaskBoard.set_rows`` row dicts.

    Rows are strictly sequential (each depends on the previous) so the evidence
    gate on one step must clear before the next becomes ready. The first row is
    active; the rest are pending. The runtime — not model prose — mints
    completions, and ``set_rows``/the board contract coerce any evidence-gated row
    that arrives "completed" without evidence back to pending, so seeding a
    procedure cannot bypass verification.

    The board owns the exact operator objective separately. Procedure rows stay
    concise and reusable instead of duplicating raw, typo-bearing request text in
    the first visible row.
    """
    rows: list[dict[str, object]] = []
    for idx, step in enumerate(procedure.steps, start=1):
        rows.append(
            {
                "id": str(idx),
                "text": step.text,
                "status": "active" if idx == 1 else "pending",
                "kind": step.kind,
                "completion_gate": step.completion_gate,
                "depends_on": [str(idx - 1)] if idx > 1 else [],
                "expected_evidence": list(step.expected_evidence),
            }
        )
    return rows


# Fixed procedures exist only where the step sequence is itself reusable product
# behavior. Ordinary work remains provider-authored so any rows match the request.
# Rows describe work only. The ordinary assistant response is not a task row.
_PROCEDURES: dict[str, WorkProcedure] = {
    "clone_adopt": WorkProcedure(
        "clone_adopt",
        (
            WorkStep("Recon the reference in the browser: page screenshot + DOM snapshot + responsive breakpoints from its CSS", "inspect",
                     expected_evidence=("page screenshot, DOM snapshot, and CSS breakpoints captured",)),
            WorkStep("Extract the visual system (computed styles -> tokens), write a per-section spec, and download assets", "verify",
                     expected_evidence=("computed-style tokens + per-section spec",
                                        "referenced assets downloaded",)),
            WorkStep("Detect the output stack and record the adopt-delta (kept 1:1 vs. adapted to the request)", "inspect",
                     expected_evidence=("stack choice and adopt-delta recorded",)),
            WorkStep("Build the reconstruction against the spec (faithful first, then the requested adaptations)", "edit",
                     expected_evidence=("edits applied to the reconstruction target",)),
            WorkStep("Verify fidelity: render, screenshot, compare to the original, and close the gaps", "verify", "verification",
                     expected_evidence=("visual comparison against the original with the delta noted",
                                        "design_check advisory output for frontend files")),
        ),
    ),
    "platform_migration": WorkProcedure(
        "platform_migration",
        (
            WorkStep("Inspect the named source agent and connect prerequisites (paths, provider keys, re-auth)", "inspect",
                     expected_evidence=("source located and its assets read via migrate inspect / plan actions",)),
            WorkStep("Show the full migration plan and get the operator's explicit approval before any write", "ask", "manual",
                     expected_evidence=("operator approved the plan",)),
            WorkStep("Apply the approved assets into MO (profile / skills / rules / provider)", "edit",
                     expected_evidence=("migrate apply results for the approved assets",)),
            WorkStep("Verify each moved asset landed (files/facts present; provider/Telegram authenticates)", "verify",
                     expected_evidence=("post-move verification evidence per asset",)),
        ),
    ),
}


def work_procedure_for(pattern_name: str) -> WorkProcedure | None:
    """Return the WorkProcedure for a WorkPattern name, or None."""
    return _PROCEDURES.get(str(pattern_name or "").strip())
