"""Locating skill roots, seeding shipped packs, and loading skills into memory.

Confirmed suggestions may remain virtual until materialized. Explicit workflow
promotion always writes a physical pack, which is its only runtime authority.
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import os
import time
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_text
from ..utils.jsonl_utils import read_jsonl
from ._util import (
    _dedupe_paths,
    _memory_root,
    _meaningful_words,
    _one_line,
    _stable_id_suffix,
    skill_root_lock,
    skills_root,
)
from .mastery import retire_stale_generated_skills
from .model import (
    Skill,
    _dedupe_skills,
    _iter_skill_files,
    _parse_frontmatter,
    _parse_skill,
    _set_frontmatter_int,
    _split_frontmatter,
)

_UNIVERSAL_LEARNING_KINDS = frozenset({"evidence_first", "clean_finish", "communication_concise"})
_SEED_ROOT = Path(__file__).resolve().parent / "seeds"
_RETIRED_SEED_SLUGS = frozenset({"project-mapper", "desktop-conventions"})
_MIGRATED_LEARNED_SEED_SLUGS = frozenset({"native-desktop-action-owns-its-route"})
_MASTERY_FIELDS = (
    "mastery_uses",
    "mastery_successes",
    "mastery_corrections",
    "last_used_at",
    "created_at",
)


def seed_profile_skills(
    profile: Any | None = None,
    *,
    runtime_home: str | None = None,
    config: dict[str, Any] | None = None,
) -> list[Path]:
    """Reconcile product-owned seed packs into the profile skill root.

    Packs that still declare ``provenance: seed`` follow the shipped source while
    retaining their local mastery counters. Explicitly migrated learned packs are
    promoted to their shipped authority with mastery preserved; other user-owned
    replacements remain untouched. Removed shipped seeds are retired recoverably.
    """
    target_root = skills_root(profile, runtime_home=runtime_home, config=config)
    changed: list[Path] = []
    if not _SEED_ROOT.is_dir():
        return changed
    with skill_root_lock(target_root):
        for slug in sorted(_RETIRED_SEED_SLUGS):
            retired = _retire_removed_seed(target_root, slug)
            if retired is not None:
                changed.append(retired)
        for seed_dir in sorted(path for path in _SEED_ROOT.iterdir() if path.is_dir()):
            seed_main = seed_dir / "SKILL.md"
            if not seed_main.exists():
                continue
            dest_main = target_root / seed_dir.name / "SKILL.md"
            try:
                shipped = seed_main.read_text(encoding="utf-8")
            except OSError:
                continue
            if dest_main.exists():
                provenance = _frontmatter_provenance(dest_main)
                migrates_learned_pack = (
                    seed_dir.name in _MIGRATED_LEARNED_SEED_SLUGS
                    and provenance == "learned-convention"
                )
                if provenance != "seed" and not migrates_learned_pack:
                    continue
                try:
                    existing = dest_main.read_text(encoding="utf-8")
                except OSError:
                    continue
                shipped = _preserve_seed_mastery(shipped, existing)
                if shipped == existing:
                    continue
            try:
                atomic_write_text(dest_main, shipped, encoding="utf-8")
                changed.append(dest_main)
            except OSError:
                continue
    return changed


def _preserve_seed_mastery(shipped: str, existing: str) -> str:
    meta_text, _body = _split_frontmatter(existing)
    meta = _parse_frontmatter(meta_text)
    merged = shipped
    for field in _MASTERY_FIELDS:
        if field in meta:
            try:
                merged = _set_frontmatter_int(merged, field, int(meta[field]))
            except (TypeError, ValueError):
                continue
    return merged


def _retire_removed_seed(root: Path, slug: str) -> Path | None:
    source = root / slug
    if _frontmatter_provenance(source / "SKILL.md") != "seed":
        return None
    destination = source.with_name(source.name + ".retired")
    if destination.exists():
        destination = source.with_name(f"{source.name}.{time.time_ns()}.retired")
    try:
        source.rename(destination)
        return destination
    except OSError:
        return None


def _frontmatter_provenance(path: Path) -> str:
    """Read ownership metadata even when the old pack is otherwise invalid."""
    try:
        meta_text, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    return str(_parse_frontmatter(meta_text).get("provenance") or "").strip()


def default_skill_roots(
    project_cwd: str | None = None,
    runtime_home: str | None = None,
    *,
    profile: Any | None = None,
    config: dict[str, Any] | None = None,
    maintain: bool = True,
) -> list[str]:
    """Return active skill roots in precedence order.

    Profile skills are always first. Project-local skill roots are opt-in via
    ``skills.project_local: true``.
    ``maintain=False`` resolves the same roots without seeding or retirement,
    for read-only inventory/status surfaces.
    """
    if maintain:
        seed_profile_skills(profile, runtime_home=runtime_home, config=config)
    profile_root = skills_root(profile, runtime_home=runtime_home, config=config)
    if maintain:
        _retire_generated_skills_best_effort(
            profile_root,
            profile=profile,
            runtime_home=runtime_home,
            config=config,
        )
    roots: list[Path] = [profile_root]
    cfg = config if isinstance(config, dict) else {}
    skills_cfg = cfg.get("skills", {}) if isinstance(cfg.get("skills", {}), dict) else {}
    include_project = skills_cfg.get("project_local", DEFAULT_PREFERENCES["skills.project_local"]) is True
    if project_cwd and include_project:
        roots.append(Path(project_cwd).expanduser() / "skills")
    return [str(path) for path in _dedupe_paths(roots)]


def _retire_generated_skills_best_effort(
    root: Path,
    *,
    profile: Any | None = None,
    runtime_home: str | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    cfg = config if isinstance(config, dict) else {}
    skills_cfg = cfg.get("skills", {}) if isinstance(cfg.get("skills", {}), dict) else {}
    raw_days = skills_cfg.get("decay_days") or os.environ.get("MO_SKILL_DECAY_DAYS") or DEFAULT_PREFERENCES["skills.decay_days"]
    try:
        decay_days = int(raw_days)
    except (TypeError, ValueError):
        decay_days = DEFAULT_PREFERENCES["skills.decay_days"]
    try:
        retire_stale_generated_skills(root, decay_days=decay_days)
        retired_ids = retired_learning_candidate_ids(root)
        if retired_ids:
            from ..learning.proactive_learning import retire_learning_suggestions

            suggestions = _memory_root(
                profile,
                runtime_home=runtime_home,
                config=config,
            ) / "learning" / "suggestions.jsonl"
            retire_learning_suggestions(retired_ids, path=suggestions)
    except Exception:
        return


def load_skills(roots: list[str | os.PathLike]) -> list[Skill]:
    """Load authored and generated skill packs from the given roots."""
    skills: list[Skill] = []
    for path in _iter_skill_files(roots):
        skill = _parse_skill(path)
        if not skill:
            continue
        skills.append(skill)
    return _dedupe_skills(skills)


def load_generated_learning_skills(
    profile: Any | None = None,
    *,
    runtime_home: str | None = None,
    config: dict[str, Any] | None = None,
) -> list[Skill]:
    """Adapt confirmed suggestions without physical packs into selectable skills."""
    return load_confirmed_suggestion_skills(
        profile,
        runtime_home=runtime_home,
        config=config,
    )


def materialized_learning_authority(
    profile: Any | None = None,
    *,
    runtime_home: str | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[set[str], set[str]]:
    """Return ids and recommendation signatures owned by physical learning packs.

    Candidate ids suppress either confirmed-suggestion or promoted-workflow
    adapters once a physical pack owns that candidate. Recommendation signatures
    additionally collapse legacy repeated confirmations that created new ids for
    the same learned rule; without both, one physical pack and several virtual
    adapters can inject identical behavior repeatedly.
    """
    ids: set[str] = set()
    recommendations: set[str] = set()
    try:
        root = skills_root(profile, runtime_home=runtime_home, config=config)
    except Exception:
        return ids, recommendations
    for path in _iter_skill_files([str(root)]):
        skill = _parse_skill(path)
        if skill is None:
            continue
        if skill.candidate_id:
            ids.add(skill.candidate_id)
        if skill.provenance == "confirmed-learning":
            recommendation = _confirmed_learning_signature(skill)
            if recommendation:
                recommendations.add(recommendation)
    return ids, recommendations


def materialized_learning_ids(
    profile: Any | None = None,
    *,
    runtime_home: str | None = None,
    config: dict[str, Any] | None = None,
) -> set[str]:
    """Candidate ids of confirmed learnings already owned by physical packs."""
    return materialized_learning_authority(
        profile,
        runtime_home=runtime_home,
        config=config,
    )[0]


def _confirmed_learning_signature(skill: Skill) -> str:
    """Return the full learned recommendation represented by one physical pack."""
    lines = skill.body.splitlines()
    for index, line in enumerate(lines):
        if line.strip().casefold() != "## learned behavior":
            continue
        for recommendation in lines[index + 1:]:
            if recommendation.strip():
                return _one_line(recommendation, 500).casefold()
    return _one_line(skill.description, 500).casefold()


def retired_learning_authority(root: str | Path) -> tuple[set[str], set[str]]:
    """Return ids retired by generated packs and confirmed-rule signatures.

    Every generated pack leaves a candidate-id tombstone so neither suggestion
    nor workflow staging can reactivate it. Recommendation signatures apply only
    to confirmed-learning packs because workflow bodies use a different schema.
    """
    base = Path(root).expanduser()
    ids: set[str] = set()
    recommendations: set[str] = set()
    if not base.exists():
        return ids, recommendations
    for path in base.glob("*.retired/SKILL.md"):
        skill = _parse_skill(path)
        if skill is None or not skill.generated:
            continue
        if skill.candidate_id:
            ids.add(skill.candidate_id)
        if skill.provenance == "confirmed-learning":
            signature = _confirmed_learning_signature(skill)
            if signature:
                recommendations.add(signature)
    return ids, recommendations


def retired_learning_candidate_ids(root: str | Path) -> set[str]:
    """Return candidate ids whose physical learning authority was retired."""
    return retired_learning_authority(root)[0]


def load_confirmed_suggestion_skills(
    profile: Any | None = None,
    *,
    runtime_home: str | None = None,
    config: dict[str, Any] | None = None,
) -> list[Skill]:
    path = _memory_root(profile, runtime_home=runtime_home, config=config) / "learning" / "suggestions.jsonl"
    materialized, materialized_recommendations = materialized_learning_authority(
        profile,
        runtime_home=runtime_home,
        config=config,
    )
    retired, retired_recommendations = retired_learning_authority(
        skills_root(profile, runtime_home=runtime_home, config=config)
    )
    materialized.update(retired)
    seen_recommendations: set[str] = set()
    out: list[Skill] = []
    for row in read_jsonl(path):
        if str(row.get("status") or "").lower() != "confirmed":
            continue
        recommendation = _one_line(row.get("recommendation", ""), 500)
        signature = recommendation.casefold()
        # The physical pack is the active authority. Signature suppression also
        # covers legacy repeated confirmations that have distinct candidate ids.
        if (
            str(row.get("id") or "").strip() in materialized
            or signature in materialized_recommendations
            or signature in retired_recommendations
            or signature in seen_recommendations
        ):
            continue
        if not recommendation:
            continue
        seen_recommendations.add(signature)
        kind = _one_line(row.get("kind", "learning"), 80)
        triggers = tuple(sorted(_meaningful_words(f"{kind} {recommendation}")))[:12]
        scope = "universal" if kind in _UNIVERSAL_LEARNING_KINDS else "matching turns"
        row_id = str(row.get("id") or "").strip()
        suffix = f" [{_stable_id_suffix(row_id)}]" if row_id else ""
        out.append(Skill(
            name=f"Learned: {kind.replace('_', ' ')}{suffix}",
            description=recommendation[:180],
            triggers=triggers,
            body=f"- {recommendation}",
            source=str(path),
            provenance="confirmed-learning",
            scope=scope,
            approval="confirmed",
            generated=True,
        ))
    return out
