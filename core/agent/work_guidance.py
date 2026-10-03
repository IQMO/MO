"""Compact, provider-neutral guidance for scoped engineering work.

The selected provider remains the primary reasoner and author.  This module adds
only repository execution constraints and evidence-based visual implementation
guardrails; it does not prescribe an MO aesthetic, palette, layout, or persona.
"""
from __future__ import annotations


PRD_ALIGNMENT_PROTOCOL = (
    "use a PRD only when the operator asks for planning/requirements or when a complex build needs a lightweight alignment artifact",
    "never force PRD as a gate before ordinary build/create/design work",
    "ask one natural question at a time only when missing information would materially change users, scope, constraints, safety, or approval",
    "if enough context exists, draft with explicit assumptions, TBDs, anti-goals, and open questions instead of stalling",
    "write for both humans and AI builders: clear why, bounded what, concrete how, and verifiable done criteria",
)

PRD_SCHEMA = (
    "overview/problem/proposed solution",
    "goals, success metrics, and anti-goals",
    "scope, constraints, and assumptions/TBDs",
    "JTBD and user stories when product/user context matters",
    "experience model: screens/states/interactions/accessibility/design rationale",
    "component inventory plus data/API/state/file-structure notes when relevant",
    "binary acceptance criteria mapped to testable checks",
    "risks, rollout/MVP slice, sign-off/open questions, and next steps",
)


def _join(items: tuple[str, ...]) -> str:
    return "; ".join(str(item).strip() for item in items if str(item).strip())


def build_lean_build_context() -> str:
    """Return the request-scoped implementation rule."""
    return (
        "Stay inside the current request. Treat the selected provider as the primary reasoner and author. "
        "Reuse or simplify the existing owner before adding code; add only what is actually missing, "
        "preserve required behavior, and verify the changed path proportionally. "
        "Use existing project analyzers before custom scans; a custom scan needs a specific unanswered question. "
        "Save large scan results once and query bounded slices instead of rerunning discovery to reshape output. "
        "Distinguish inventoried files, automated checks, and source actually reviewed; report coverage gaps by surface/language."
    )


def build_design_work_context() -> str:
    """Return neutral, evidence-led guidance for visual implementation work."""
    return (
        "### MO Scoped Design Work\n"
        + build_lean_build_context()
        + " For an existing product, inspect and preserve its verified visual system, components, behavior, "
        "accessibility, responsive rules, and source ownership unless the operator explicitly requests a change. "
        "For a new concept, derive the visual direction from the current request and approved operator preferences; "
        "do not impose a fixed MO palette, layout, shape language, or aesthetic. Cover relevant interaction states, "
        "keyboard/focus behavior, reduced motion, and responsive rendering. Project evidence and the current request "
        "override learned preferences."
    )


def build_prd_context() -> str:
    """Return compact provider context for optional PRD/alignment turns."""
    lines = [
        "### MO Internal PRD Alignment",
        "PRD is an optional planning/alignment artifact, not a forced build gate.",
        "Interaction: " + _join(PRD_ALIGNMENT_PROTOCOL) + ".",
        "Schema: " + _join(PRD_SCHEMA) + ".",
        "Keep the smallest complete high-quality slice; preserve freeform operator wording; map acceptance criteria to verifiable checks only when execution starts.",
        "Boundaries: /skills is read-only local profile inventory; no /skill mutation, discovery, marketplace install, or plugin execution (operator-configured local MCP servers are allowed when configured and sandbox-gated), no fake taskboard progress, and no pretending assumptions are evidence.",
        "Taskboard truth still comes from Gateway/tool/runtime evidence, not the PRD text.",
    ]
    return "\n".join(lines)
