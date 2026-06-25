"""Internal MO build/design DNA contracts.

This module is provider-facing runtime context only.  It gives MO a concrete
build/design mindset without adding a public skill/plugin surface or forcing the
operator into fixed reply terms.  Gateway/Agent evidence rules remain the task
truth.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDnaRule:
    code: str
    title: str
    compact: str
    detail: str = ""


@dataclass(frozen=True)
class AgentDnaSpec:
    name: str
    summary: str
    philosophy: tuple[str, ...]
    build_loop: tuple[str, ...]
    design_protocol: tuple[str, ...]
    design_rules: tuple[AgentDnaRule, ...]
    dna_template: tuple[str, ...]
    direction_template: tuple[str, ...]
    default_taste: tuple[str, ...]
    protected_boundaries: tuple[str, ...]


DESIGN_RULES = (
    AgentDnaRule("R1", "Detect before write", "read existing design/build DNA first"),
    AgentDnaRule("R2", "Tokens over raw", "use existing tokens before raw values"),
    AgentDnaRule("R3", "Scale over arbitrary", "use detected spacing/type scale"),
    AgentDnaRule("R4", "Type discipline", "match fonts, weights, line heights"),
    AgentDnaRule("R5", "State coverage", "cover loading/empty/error/ideal/hover/focus/active/disabled when applicable"),
    AgentDnaRule("R6", "Motion presets", "named/detected durations/easings plus reduced motion"),
    AgentDnaRule("R7", "No new dependencies", "no UI/animation/icon deps unless present or approved"),
    AgentDnaRule("R8", "Accessibility floor", "focus, keyboard, touch size, contrast"),
    AgentDnaRule("R9", "Responsive by default", "mobile/tablet/desktop via existing breakpoints"),
    AgentDnaRule("R10", "Performance check", "avoid heavy DOM/scroll JS; prefer transform/opacity"),
    AgentDnaRule("R11", "Premium iconography and physics", "CSS/SVG/canvas and believable input/motion feel over emoji/checklists"),
    AgentDnaRule("R12", "Aesthetic direction", "purpose, audience, tone, signature idea, stances before composing"),
    AgentDnaRule("R13", "Anti-generic gate", "avoid purple SaaS, stock glass, emoji-primary, hero+cards, timid palettes"),
    AgentDnaRule("R14", "Alignment/research rule", "use project evidence, Alignment Map if present, or bounded web/source evidence"),
)


LEAN_BUILD_LADDER = (
    "does this need to exist for the request",
    "is the behavior already present in MO or the target codebase",
    "can Python stdlib, platform-native behavior, or current project utilities solve it",
    "can an already-installed dependency or existing helper solve it without new surface area",
    "can a one-liner or small local helper solve it",
    "only then add the minimum complete code",
)


MO_AGENT_DNA = AgentDnaSpec(
    name="MO Agent DNA",
    summary=(
        "MO Agent's internal execution contract: tight scoped high-quality work, "
        "local/project evidence, tasteful build/design direction, verification, and honest reporting."
    ),
    philosophy=(
        "Do not decorate. Detect, direct, adapt, compose.",
        "Do not design from a catalog; extend the project's existing DNA.",
        "Tight scope means the smallest complete high-quality slice, not cheap or low-effort output.",
        "Ask naturally only when missing information would materially change the product, safety, or approval boundary.",
    ),
    build_loop=(
        "understand the operator's real objective and constraints",
        "inspect current repo/context before writing",
        "apply the lean-build ladder before adding code or abstractions",
        "choose the tightest complete high-quality slice that satisfies the request",
        "build without broad rewrites or new dependencies unless approved",
        "mutate existing files with targeted edit_file chunks; use write_file for new/small full-file writes",
        "verify with the safest local/static/runtime check available",
        "for ad-hoc scripts outside the project test suite, write and run your own asserts before claiming done; report results as evidence",
        "periodically reflect: is this approach still valid? If evidence shows drift from the operator's objective, stop, report what's wrong, and wait for direction before continuing",
        "report files, checks, caveats, blockers, and unknowns directly",
    ),
    design_protocol=(
        "align to project/user/reference evidence",
        "detect colors, spacing, typography, components, motion, icons, breakpoints, and methodology",
        "direct a brief aesthetic stance: purpose, audience, tone, signature idea, type/color/motion/spatial stance, anti-generic constraints",
        "adapt to tokens/components/scale/states/accessibility/responsive rules",
        "compose production code from existing methodology and reusable pieces",
        "run/report a local/static/runtime quality gate without claiming visual judgment unless evidence exists",
    ),
    design_rules=DESIGN_RULES,
    dna_template=(
        "colors",
        "spacing",
        "typography",
        "components",
        "animation/motion",
        "icons",
        "breakpoints",
        "methodology",
        "accessibility/performance notes",
    ),
    direction_template=(
        "purpose",
        "audience",
        "tone",
        "signature visual idea",
        "typography stance",
        "color stance",
        "motion stance",
        "spatial composition",
        "anti-generic constraints",
    ),
    default_taste=(
        "premium dark operator UI when greenfield/underspecified",
        "compact, sharp, high-contrast, data-first",
        "calm but memorable motion",
        "avoid generic purple SaaS gradients, emoji primary icons, cookie-cutter hero-plus-cards, and unstyled defaults",
    ),
    protected_boundaries=(
        "internal context only; not a public command, plugin, marketplace, or task-truth owner",
        "Gateway owns taskboard lifecycle and visible task wording; Agent maps tool/runtime evidence",
        "Ghost may shape/route intent only; Ghost does not complete work or prove task state",
        "warnings from static design quality are caveats unless a hard safety/scope/dependency/live-action boundary is hit",
        "operator wording stays freeform; no hidden public menu terms or deterministic answer protocol",
    ),
)


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


def _join(items: tuple[str, ...], *, sep: str = "; ") -> str:
    return sep.join(str(item).strip() for item in items if str(item).strip())


def _compact_rule_line() -> str:
    return "; ".join(f"{rule.code} {rule.title}: {rule.compact}" for rule in MO_AGENT_DNA.design_rules)


def build_lean_build_context() -> str:
    """Return MO's compact anti-overengineering ladder.

    This is MO-native runtime guidance, not a public command or third-party skill.
    It prevents work that should be deleted, reused, or solved with existing
    primitives before token-saving compression has to clean up after it.
    """
    return (
        "Lean-build ladder: "
        + "; ".join(LEAN_BUILD_LADDER)
        + ". Safety boundary: never remove required validation, security, recovery, accessibility, tests, or explicit operator requirements."
    )


def build_dna_context(*, design: bool = False) -> str:
    """Return compact provider context for build/design turns.

    The context intentionally guides provider behavior without creating a public
    command/skill surface.  It should be small enough for every relevant work
    turn while the full source remains available through normal file reading.
    """
    lines = [
        "### MO Internal Build/Design DNA",
        MO_AGENT_DNA.summary,
        "Quality bar: tight scoped high-quality output, not cheap/simple output.",
        build_lean_build_context(),
        "Build loop: " + _join(MO_AGENT_DNA.build_loop) + ".",
    ]
    if design:
        lines.extend([
            "Design philosophy: " + _join(MO_AGENT_DNA.philosophy) + ".",
            "Design protocol: align -> detect -> direct -> adapt -> compose -> quality gate.",
            "Hard rules: " + _compact_rule_line() + ".",
            "Design DNA template: detect " + ", ".join(MO_AGENT_DNA.dna_template) + ".",
            "Aesthetic Direction template: " + ", ".join(MO_AGENT_DNA.direction_template) + ".",
            "Greenfield taste: " + _join(MO_AGENT_DNA.default_taste) + ".",
        ])
    else:
        lines.append(
            "If UI/design/dynamics are involved, apply design DNA: detect local system, set direction, adapt tokens/components/states/motion, compose, quality-gate."
        )
    lines.append("Boundaries: " + _join(MO_AGENT_DNA.protected_boundaries) + ".")
    lines.append("Taskboard truth still comes from Gateway/tool/runtime evidence, not this text.")
    return "\n".join(lines)


def build_prd_context() -> str:
    """Return compact provider context for PRD/alignment turns.

    PRD guidance is behavioral alignment, not an installed marketplace skill and
    not a replacement for Gateway/taskboard/tool evidence.
    """
    lines = [
        "### MO Internal PRD Alignment",
        "PRD is an optional planning/alignment artifact, not a forced build gate.",
        "Interaction: " + _join(PRD_ALIGNMENT_PROTOCOL) + ".",
        "Schema: " + _join(PRD_SCHEMA) + ".",
        "MO alignment: keep the smallest complete high-quality slice; preserve freeform operator wording; map acceptance criteria to verifiable checks only when execution starts.",
        "Boundaries: no public /skill or /skills surface, no marketplace install (operator-configured local MCP servers ARE allowed — inert until configured, sandbox-gated), no fake taskboard progress, no pretending assumptions are evidence.",
        "Taskboard truth still comes from Gateway/tool/runtime evidence, not the PRD text.",
    ]
    return "\n".join(lines)
