"""Choosing which skills to inject into Agent context.

Two complementary paths: ``select_skills_context`` matches by user-input TEXT
(task triggers), and ``select_conventions_context`` / ``select_skills_by_location``
match by CODE LOCATION (a skill whose ``scope`` carries file-globs surfaces when MO
is working on files the graph selected). An independently opt-in semantic embedding
fallback can back the text path when no literal trigger matches.
Learned file-scoped conventions use location selection or an explicit name;
generated words from their rule body are not independent task triggers.
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import fnmatch
import os
from pathlib import Path
import re
from typing import Any

from ..runtime.turn_intent import TRIVIAL_GREETINGS, looks_like_trivial_greeting
from ._util import _scope_path_globs
from .loading import load_generated_learning_skills, load_skills
from .model import Skill, _dedupe_skills, _mastery_bonus, skill_matches_project

_MAX_CONTEXT_CHARS = 2600
_GREETINGS = TRIVIAL_GREETINGS


def _render_selected_skills(
    selected: list[Skill],
    *,
    header: str,
    max_chars: int,
    truncation_marker: str,
) -> str:
    """Render selected task skills and location conventions through one owner."""
    rendered, _included = _render_selected_skills_with_metadata(
        selected,
        header=header,
        max_chars=max_chars,
        truncation_marker=truncation_marker,
    )
    return rendered


def _render_selected_skills_with_metadata(
    selected: list[Skill],
    *,
    header: str,
    max_chars: int,
    truncation_marker: str,
) -> tuple[str, tuple[Skill, ...]]:
    """Render complete skill blocks; metadata never certifies partial guidance."""
    if max_chars <= 0:
        return "", ()
    output = str(header or "").strip()
    included: list[Skill] = []
    for skill in selected:
        head = f"\n**{skill.name}**"
        if skill.description:
            head += f" - {skill.description}"
        if skill.provenance != "authored":
            head += f" [{skill.provenance}]"
        parts = [head]
        if skill.scope:
            parts.append(f"Scope: {skill.scope}")
        parts.append(skill.body.strip())
        block = "\n".join(part for part in parts if part).strip()
        candidate = f"{output}\n{block}" if output else block
        if len(candidate) <= max_chars:
            output = candidate
            included.append(skill)
            continue
        marker = str(truncation_marker or "").strip()
        if marker and len(output) + 1 + len(marker) <= max_chars:
            output = f"{output}\n{marker}" if output else marker
        break
    if not included:
        return "", ()
    return output, tuple(included)


def should_include_skills(user_input: str) -> bool:
    text = str(user_input or "").strip()
    return bool(text) and not looks_like_trivial_greeting(text)


def select_skills_context(
    user_input: str,
    roots: list[str | os.PathLike],
    *,
    profile: Any | None = None,
    config: dict[str, Any] | None = None,
    max_skills: int = 3,
    max_chars: int = _MAX_CONTEXT_CHARS,
    project_cwd: str | None = None,
) -> str:
    """Return the relevant unified skill context block, or an empty string."""
    context, _selected = select_skills_context_with_metadata(
        user_input,
        roots,
        profile=profile,
        config=config,
        max_skills=max_skills,
        max_chars=max_chars,
        project_cwd=project_cwd,
    )
    return context


def select_skills_context_with_metadata(
    user_input: str,
    roots: list[str | os.PathLike],
    *,
    profile: Any | None = None,
    config: dict[str, Any] | None = None,
    max_skills: int = 3,
    max_chars: int = _MAX_CONTEXT_CHARS,
    authored_skills: list[Skill] | None = None,
    project_cwd: str | None = None,
) -> tuple[str, tuple[Skill, ...]]:
    """Return rendered context plus the exact skills that produced it.

    Runtime policy gates use the metadata instead of treating any non-empty
    skills block as proof that a specific governed method was selected.
    """
    if not should_include_skills(user_input):
        return "", ()
    authored = load_skills(roots) if authored_skills is None else authored_skills
    generated = load_generated_learning_skills(profile, config=config)
    skills = [skill for skill in _dedupe_skills([*authored, *generated])
              if skill_matches_project(skill, project_cwd)]
    if not skills:
        return "", ()
    text = str(user_input or "")
    text_lower = text.lower()
    scored = [(skill, _match_score(skill, text_lower)) for skill in skills]
    matched = [(skill, score) for skill, score in scored if score > 0]
    if not matched:
        matched = _semantic_matches(text, skills, config=config)
    if not matched:
        return "", ()
    requested = [(skill, score) for skill, score in matched if skill_is_requested(skill, text)]
    if requested:
        # Explicit packs own this request's guidance budget. Incidental trigger
        # and mastery scores must not crowd out the method the user named.
        matched = requested
    matched.sort(key=lambda item: (-item[1], item[0].name.casefold()))
    selected = matched[:max_skills]
    out, rendered_skills = _render_selected_skills_with_metadata(
        [skill for skill, _score in selected],
        header="### Relevant MO skills - follow before acting on this task",
        max_chars=max_chars,
        truncation_marker="[skills context truncated]",
    )
    return out, rendered_skills


# --- location-aware selection (conventions: rules surfaced by WHERE MO is working) ----
# select_skills_context above matches by user-input TEXT (task triggers). This path matches
# by CODE LOCATION: a skill whose `scope` carries file-globs surfaces when MO is working on
# files the graph selected (the node file-paths), so MO sees its conventions for THIS area
# without dumping every rule. Additive — does not change the text-match path.

def skill_matches_location(skill: Skill, file_paths: list[str], *, project_cwd: str | None = None) -> bool:
    """True when the skill's scope file-globs match any of the given code locations."""
    globs = _scope_path_globs(getattr(skill, "scope", "") or "")
    if not globs or not file_paths or not skill_matches_project(skill, project_cwd):
        return False
    norm = [str(p).replace("\\", "/").lstrip("./") for p in file_paths if p]
    for glob in globs:
        for fp in norm:
            if fnmatch.fnmatch(fp, glob) or fp == glob or fp.endswith("/" + glob):
                return True
    return False


def select_skills_by_location(
    skills: list[Skill],
    file_paths: list[str],
    *,
    max_skills: int = 3,
    max_chars: int = _MAX_CONTEXT_CHARS,
    project_cwd: str | None = None,
) -> str:
    """Return a compact block of conventions whose scope governs the code in scope.

    `file_paths` = the file-paths of the graph nodes the current turn is working on
    (already relevance-selected by the code graph). Empty when no location context.
    """
    if not file_paths or not skills:
        return ""
    matched = [skill for skill in skills if skill_matches_location(skill, file_paths, project_cwd=project_cwd)]
    if not matched:
        return ""
    selected = matched[:max_skills]
    return _render_selected_skills(
        selected,
        header="### MO conventions for the code in scope - follow these where they apply",
        max_chars=max_chars,
        truncation_marker="[conventions truncated]",
    )


def select_conventions_context(
    user_input: str,
    roots: list[str | os.PathLike],
    file_paths: list[str],
    *,
    profile: Any | None = None,
    config: dict[str, Any] | None = None,
    max_skills: int = 3,
    max_chars: int = _MAX_CONTEXT_CHARS,
    project_cwd: str | None = None,
) -> str:
    """Location-triggered conventions for the code in scope this turn.

    Complements ``select_skills_context`` (text/task-triggered): loads the same unified
    skill set and surfaces the ones whose ``scope`` file-globs govern ``file_paths``.
    Empty when there is no location context or no scoped rule applies."""
    if not file_paths:
        return ""
    skills = _dedupe_skills([
        *load_skills(roots),
        *load_generated_learning_skills(profile, config=config),
    ])
    if not skills:
        return ""
    return select_skills_by_location(skills, file_paths, max_skills=max_skills, max_chars=max_chars,
                                    project_cwd=project_cwd)


def skill_is_requested(skill: Skill, user_input: str) -> bool:
    """Match an explicit display name or installed pack directory name."""
    names = [skill.name]
    source = Path(skill.source)
    if source.name.casefold() == "skill.md":
        names.append(source.parent.name)
    text = str(user_input or "").casefold()
    return any(
        name and re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", text)
        for name in names
    )


def _match_score(skill: Skill, text_lower: str) -> int:
    score = 0
    if skill_is_requested(skill, text_lower):
        score += 3
    if skill.provenance == "learned-convention" and _scope_path_globs(skill.scope):
        # Older generated packs contain arbitrary words from their rule body.
        # Their declared location remains the activation authority; a direct
        # name request also works without allowing those words to widen scope.
        return score
    score += sum(
        2
        for trigger in skill.triggers
        if trigger
        and re.search(rf"(?<!\w){re.escape(trigger)}(?!\w)", text_lower)
    )
    if skill.scope == "universal" and any(word in text_lower for word in ("fix", "build", "review", "test", "verify", "implement", "debug", "audit")):
        score += 1
    # Mastery ranks relevant candidates; it must never invent relevance on an
    # unrelated turn. Corrections may lower rank but must not erase a real
    # lexical/scope match entirely.
    return max(1, score + _mastery_bonus(skill)) if score > 0 else 0


def _semantic_matches(text: str, skills: list[Skill], *, config: dict[str, Any] | None = None) -> list[tuple[Skill, int]]:
    cfg = config if isinstance(config, dict) else {}
    skills_cfg = cfg.get("skills", {}) if isinstance(cfg.get("skills", {}), dict) else {}
    # Embeddings are primarily a memory-recall feature. Reusing them implicitly
    # for every unmatched skill query can cold-start a local ONNX model (or make
    # one API request per pack) during ordinary conversation. Semantic skill
    # selection therefore has its own explicit opt-in; authored triggers remain
    # the fast/default activation contract.
    if skills_cfg.get("semantic_match", DEFAULT_PREFERENCES["skills.semantic_match"]) is not True:
        return []
    # Similar wording cannot authorize a file-scoped convention elsewhere.
    # Its explicit name was already checked by the literal selector.
    skills = [
        skill for skill in skills
        if not (skill.provenance == "learned-convention" and _scope_path_globs(skill.scope))
    ]
    if not skills:
        return []
    try:
        from ..learning.embeddings import build_embedder, cosine

        embed = build_embedder(cfg)
        if not embed:
            return []
        user_vec = embed(text[:4000])
        scored: list[tuple[Skill, int]] = []
        for skill in skills:
            material = f"{skill.name}\n{skill.description}\n{' '.join(skill.triggers)}\n{skill.body}"
            score = cosine(user_vec, embed(material[:4000]))
            if score >= 0.42:
                scored.append((skill, int(score * 100)))
        return sorted(scored, key=lambda item: item[1], reverse=True)[:3]
    except Exception:
        return []
