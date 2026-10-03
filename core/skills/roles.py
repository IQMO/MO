"""Roles are reusable skills that can govern conversations, workers, and schedules.

Their body supplies the role perspective and workflow. Optional MCP scoping and
lane declarations are applied here; ordinary Agent, sandbox, authority, and
confirmation rules remain the enforcement owners.
"""
from __future__ import annotations

import fnmatch
import os
import re
from typing import Any

from ._util import _normalize_trigger, _slug
from .loading import load_generated_learning_skills, load_skills
from .model import Skill, _dedupe_skills, skill_matches_project


_NEGATION = re.compile(r"\b(?:do not|don't|dont|never|no)\b(?:\s+\S+){0,5}\s*$")
_EXPLICIT_ROLE_ACTION_RE = re.compile(r"\b(?:activate|start|use|switch to|enter|call|invoke|ask)\b")
_RESTRICTED_ROLE_LANES = frozenset(
    {"report", "review-only", "investigate", "prt-review-only"}
)


def _has_positive_phrase(text: str, phrase: str) -> bool:
    """True when at least one occurrence is not locally negated."""
    start = 0
    while True:
        index = text.find(phrase, start)
        if index < 0:
            return False
        prefix = text[max(0, index - 100):index]
        if not _NEGATION.search(prefix):
            return True
        start = index + len(phrase)


def list_roles(
    roots: list[str | os.PathLike],
    *,
    profile: Any | None = None,
    project_cwd: str | None = None,
) -> list[Skill]:
    """Return global roles and roles bound to the current canonical project.

    A project-specific role takes precedence over a global pack with the same
    role/name, while roles bound to another project are never exposed here.
    """
    skills = _dedupe_skills([*load_skills(roots), *load_generated_learning_skills(profile)])
    roles = [
        skill for skill in skills
        if skill.role and skill_matches_project(skill, project_cwd)
    ]
    return sorted(roles, key=lambda skill: (not bool(skill.project_root), skill.name.casefold()))


def resolve_role(
    role_name: str,
    roots: list[str | os.PathLike],
    *,
    profile: Any | None = None,
    project_cwd: str | None = None,
) -> Skill | None:
    """Find the current-project role matching ``role_name`` (role name, then slug).

    Returns None when no matching role is available in this project — callers
    should fail loud rather than silently run an ungoverned worker.
    """
    target = _normalize_trigger(role_name)
    if not target:
        return None
    roles = list_roles(roots, profile=profile, project_cwd=project_cwd)
    for skill in roles:
        if _normalize_trigger(skill.role) == target:
            return skill
    target_slug = _slug(role_name)
    for skill in roles:
        if _slug(skill.name) == target_slug or _normalize_trigger(skill.name) == target:
            return skill
    return None


def resolve_role_for_text(
    user_input: str,
    roots: list[str | os.PathLike],
    *,
    profile: Any | None = None,
    project_cwd: str | None = None,
) -> Skill | None:
    """Resolve a conversational role from matching global/current-project metadata."""
    text = _normalize_trigger(user_input)
    if not text:
        return None
    candidates: list[tuple[int, Skill]] = []
    explicit_role_request = bool(_EXPLICIT_ROLE_ACTION_RE.search(text))
    for skill in list_roles(roots, profile=profile, project_cwd=project_cwd):
        if skill.project_root and not explicit_role_request:
            continue
        score = 0
        role = _normalize_trigger(skill.role)
        name = _normalize_trigger(skill.name)
        if role and _has_positive_phrase(text, role):
            score = max(score, 6)
        if name and _has_positive_phrase(text, name):
            score = max(score, 6)
        for trigger in skill.triggers:
            clean = _normalize_trigger(trigger)
            if clean and _has_positive_phrase(text, clean):
                score = max(score, 4 + min(2, len(clean.split()) // 3))
        if score:
            candidates.append((score, skill))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], not bool(item[1].project_root), item[1].name.casefold()))
    return candidates[0][1]


def role_overlay_text(skill: Skill) -> str:
    """Build the active role overlay that pins a skill as this run's perspective.

    It guides the workflow without replacing MO's current-turn authority,
    sandbox, evidence gates, or ordinary tools.
    """
    label = skill.role or skill.name
    lines = [
        "",
        f"## Active Role: {label}",
        f"You are running as the '{label}' role. This contract GOVERNS this run and "
        "overrides generic behavior. Obey it exactly; do not drift to generic advice.",
    ]
    if skill.role_tools:
        lines.append(
            "Tools this run: " + ", ".join(skill.role_tools)
            + " (plus core read/search/verify tools). Other MCP tools are unavailable — do not attempt them."
        )
    if skill.role_lane:
        lines.append(f"Lane: {skill.role_lane} — respect it.")
    if skill.role_verify:
        lines.append(
            f"Before claiming any result, satisfy the '{skill.role_verify}' evidence gate "
            "(use tools for proof, never assert without it)."
        )
    lines.extend(["", "### Role skill — follow exactly", skill.body.strip()])
    return "\n".join(lines)


def role_has_restricted_lane(skill: Skill | None) -> bool:
    """Whether a role's authored lane forbids general mutation/actuation."""
    lane = str(getattr(skill, "role_lane", "") or "").strip().lower().replace("_", "-")
    if lane in {"read-only", "readonly", "review"}:
        lane = "review-only"
    return lane in _RESTRICTED_ROLE_LANES


def _tool_name_matches_any(name: str, globs: tuple[str, ...]) -> bool:
    low = str(name or "").lower()
    return any(fnmatch.fnmatch(low, str(glob).lower()) for glob in globs)


def scope_definitions_for_role(definitions: list[dict], skill: Skill) -> list[dict]:
    """Hard-scope MCP tools to the role's declarations.

    Ordinary core tools remain available. MCP tools must match ``role_tools`` so
    the model cannot attempt undeclared work.
    """
    globs = tuple(skill.role_tools or ())
    out: list[dict] = []
    for definition in definitions:
        name = str((definition.get("function") or {}).get("name") or "")
        if not name.startswith("mcp__") or _tool_name_matches_any(name, globs):
            out.append(definition)
    return out
