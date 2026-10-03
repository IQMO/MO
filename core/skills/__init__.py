"""Unified local skill packs for task-triggered MO learning.

Skills are local markdown packs, not a marketplace or public slash-command
surface. The active runtime root is profile-owned (``~/.mo/skills`` in normal
use); shipped seed packs are reconciled there while local mastery is retained.
Promoted workflow
candidates and confirmed learning suggestions are adapted into this same
selection path so Agent context injection has one "skills" source.

This package is split into focused modules; this file is a stable re-export
facade so ``from core.skills import <name>`` keeps working unchanged:

- ``_util``      leaf text/format/path helpers (no ``Skill`` dependency)
- ``model``      the ``Skill`` dataclass, parsing, frontmatter, activation contract
- ``mastery``    outcome counters, decay retirement, correction recording
- ``loading``    roots, seeding, and loading skills (incl. learning/mining adapters)
- ``inventory``  loader-visible active/unavailable pack status without private paths
- ``selection``  text-match, location, and semantic selection into Agent context
- ``authoring``  writing packs / conventions / promotions to disk
- ``roles``      role binding for interactive conversations and background workers
"""
# This package is the stable import facade; internal module splits stay invisible
# to callers, including names intentionally omitted from public __all__.
# ruff: noqa: F401
from __future__ import annotations

from ._util import (
    _as_int,
    _backtick_values,
    _candidate_skill_body,
    _candidate_triggers,
    _coerce_triggers,
    _dedupe_paths,
    _first_heading,
    _is_profile_skill_source,
    _memory_root,
    _meaningful_words,
    _normalize_trigger,
    _one_line,
    _parse_scalar,
    _safe_support_path,
    _scope_path_globs,
    _skill_name_from_candidate,
    _slug,
    _title,
    _unquote,
    _yaml_quote,
    skills_root,
)
from .model import (
    Skill,
    _activation_contract_triggers,
    _bump_frontmatter_int,
    _dedupe_skills,
    _frontmatter_int,
    _iter_skill_files,
    _mastery_bonus,
    _parse_frontmatter,
    _parse_skill,
    _set_frontmatter_int,
    _skill_contract_issues,
    _split_forbidden_activation,
    _split_frontmatter,
)
from .mastery import (
    _append_skill_evolution_fix,
    record_selected_skill_outcomes,
    record_skill_outcome,
    retire_skill_packs_by_candidate_ids,
    retire_stale_generated_skills,
)
from .loading import (
    _SEED_ROOT,
    _UNIVERSAL_LEARNING_KINDS,
    _retire_generated_skills_best_effort,
    default_skill_roots,
    load_confirmed_suggestion_skills,
    load_generated_learning_skills,
    load_skills,
    materialized_learning_authority,
    materialized_learning_ids,
    retired_learning_authority,
    retired_learning_candidate_ids,
    seed_profile_skills,
)
from .inventory import (
    SkillPackStatus,
    inspect_skill_packs,
    render_skill_inventory,
    visible_skill_packs,
)
from .selection import (
    _GREETINGS,
    _MAX_CONTEXT_CHARS,
    _match_score,
    _semantic_matches,
    select_conventions_context,
    select_skills_by_location,
    select_skills_context,
    select_skills_context_with_metadata,
    should_include_skills,
    skill_matches_location,
)
from .authoring import (
    _MAX_SOURCE_TEXT_CHARS,
    validate_skill_pack,
    write_convention,
    write_skill_pack,
    write_skill_pack_from_candidate,
    write_skill_pack_from_suggestion,
)
from .roles import (
    _tool_name_matches_any,
    list_roles,
    resolve_role,
    resolve_role_for_text,
    role_has_restricted_lane,
    role_overlay_text,
    scope_definitions_for_role,
)

__all__ = [
    "Skill",
    "skills_root",
    "seed_profile_skills",
    "default_skill_roots",
    "load_skills",
    "load_generated_learning_skills",
    "load_confirmed_suggestion_skills",
    "materialized_learning_authority",
    "materialized_learning_ids",
    "retired_learning_authority",
    "retired_learning_candidate_ids",
    "should_include_skills",
    "select_skills_context",
    "select_skills_context_with_metadata",
    "SkillPackStatus",
    "inspect_skill_packs",
    "visible_skill_packs",
    "render_skill_inventory",
    "skill_matches_location",
    "select_skills_by_location",
    "select_conventions_context",
    "list_roles",
    "resolve_role",
    "resolve_role_for_text",
    "role_has_restricted_lane",
    "role_overlay_text",
    "scope_definitions_for_role",
    "write_skill_pack",
    "validate_skill_pack",
    "write_convention",
    "write_skill_pack_from_candidate",
    "write_skill_pack_from_suggestion",
    "record_skill_outcome",
    "retire_skill_packs_by_candidate_ids",
    "retire_stale_generated_skills",
    "record_selected_skill_outcomes",
]
