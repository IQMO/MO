"""Skill inventories for operator display and bounded model discovery."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .authoring import validate_skill_pack
from .loading import load_generated_learning_skills, load_skills
from .model import Skill, _dedupe_skills, _iter_skill_files, _parse_skill, skill_matches_project
from ..utils.text_utils import DEFAULT_CONTEXT_STOPWORDS, words


@dataclass(frozen=True)
class SkillPackStatus:
    """One loader-visible skill or one inspected unavailable pack."""

    name: str
    description: str = ""
    active: bool = False
    issues: tuple[str, ...] = ()
    generated: bool = False
    skill: Skill | None = None


def render_skill_directory(skills: list[Skill], *, query: str = "", max_chars: int = 2000, project_cwd: str | None = None) -> str:
    """Expose bounded capability metadata without selecting or applying a skill.

    Reuse the physical packs loaded for this turn. Relevant metadata precedes
    ambient packs; ranking never activates their instructions. Source paths
    remain complete for ``read_file``.
    """
    if max_chars <= 0:
        return ""
    header = (
        "Available local skills (descriptions only, not loaded instructions). "
        "Choose by the request and conversation. If the full guidance is not "
        "already included, read its source before using it. Preserve the "
        "operator's chosen method and approval scope."
    )
    lines = [header]
    included = 0
    from .selection import skill_is_requested

    terms = words(query) - DEFAULT_CONTEXT_STOPWORDS
    ordered = sorted(_dedupe_skills(skills), key=lambda skill: (
        not skill_is_requested(skill, query),
        -len(terms & words(f"{skill.name} {skill.description} {' '.join(skill.triggers)}")),
        skill.provenance != "seed", skill.name.casefold(),
    ))
    for skill in ordered:
        if not skill.source or not skill_matches_project(skill, project_cwd):
            continue
        name = " ".join(skill.name.split())
        description = " ".join(skill.description.split())
        row = f"- {name}: {description} | read_file path={json.dumps(skill.source, ensure_ascii=False)}"
        if len("\n".join([*lines, row])) <= max_chars:
            lines.append(row)
            included += 1
    if not included:
        return ""
    if included < len(ordered):
        note = "[Additional skill metadata omitted after budget.]"
        if len("\n".join([*lines, note])) <= max_chars:
            lines.append(note)
    return "\n".join(lines)


def inspect_skill_packs(roots: list[str | Path]) -> tuple[SkillPackStatus, ...]:
    """Inspect physical pack files without exposing their private paths."""
    rows: list[SkillPackStatus] = []
    for path in _iter_skill_files(roots):
        skill: Skill | None = _parse_skill(path)
        if skill is not None:
            rows.append(SkillPackStatus(skill.name, skill.description, True))
            continue
        issues = validate_skill_pack(path) if path.name.lower() == "skill.md" else []
        rows.append(
            SkillPackStatus(
                _display_name(path),
                active=False,
                issues=tuple(issues or ("missing or invalid activation triggers",)),
            )
        )
    return tuple(sorted(rows, key=lambda row: (not row.active, row.name.casefold())))


def visible_skill_packs(
    roots: list[str | Path],
    *,
    profile: Any | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[SkillPackStatus, ...]:
    """Return every loader-visible physical and generated pack once."""
    inspected = inspect_skill_packs(roots)
    physical = load_skills(roots)
    physical_objects = {id(skill) for skill in physical}
    unified = _dedupe_skills([
        *physical,
        *load_generated_learning_skills(profile, config=config),
    ])
    rows = [row for row in inspected if not row.active]
    for skill in unified:
        rows.append(
            SkillPackStatus(
                skill.name,
                skill.description,
                active=True,
                generated=id(skill) not in physical_objects,
                skill=skill,
            )
        )
    return tuple(sorted(rows, key=lambda row: (not row.active, row.name.casefold())))


def render_skill_inventory(
    roots: list[str | Path],
    *,
    profile: Any | None = None,
    config: dict[str, Any] | None = None,
    selected: str = "",
) -> str:
    """Render the exact loader-visible inventory for a user-facing response."""
    rows = list(visible_skill_packs(roots, profile=profile, config=config))
    query = str(selected or "").strip().casefold()
    if query:
        row = next((item for item in rows if item.name.casefold() == query), None)
        if row is None:
            return f"Unknown local skill pack: {selected.strip()}"
        state = "active learning adapter" if row.generated else "active physical pack"
        if not row.active:
            state = "unavailable"
        lines = [row.name, f"  state: {state}"]
        if row.description:
            lines.append(f"  purpose: {row.description}")
        if row.issues:
            lines.append(f"  issues: {'; '.join(row.issues)}")
        lines.append("Proof: loader-visible runtime state; private paths withheld; no provider call or profile write.")
        return "\n".join(lines)

    active = [row for row in rows if row.active]
    unavailable = [row for row in rows if not row.active]
    generated_count = sum(row.generated for row in active)
    lines = ["Local skill packs:"]
    if active:
        for row in active:
            suffix = f" — {row.description}" if row.description else ""
            lines.append(f"  - {row.name}{suffix}")
    else:
        lines.append("  (none active)")
    if unavailable:
        lines.append("Unavailable skill packs:")
        for row in unavailable:
            lines.append(f"  - {row.name} — {'; '.join(row.issues)}")
    lines.append(
        f"Proof: {len(active) - generated_count} active physical pack(s) and "
        f"{generated_count} active learning adapter(s) visible to this runtime; "
        "private paths withheld; no provider call or profile write."
    )
    return "\n".join(lines)


def _display_name(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip() or path.parent.name
    except OSError:
        pass
    return path.parent.name if path.name.lower() == "skill.md" else path.stem
