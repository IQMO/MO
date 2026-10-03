"""Mastery signals for physical skill packs: outcome counters, decay-based
retirement of unproven generated packs, and operator-correction recording (which
un-blinds retirement and feeds the inert ``skill_evolution.json`` sidecar).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_text
from ._util import _as_int, skill_root_lock, skills_root
from .model import _bump_frontmatter_int, _parse_skill, _set_frontmatter_int


def record_skill_outcome(path: str | Path, outcome: str, *, now: float | None = None) -> bool:
    """Update simple mastery counters on a physical SKILL.md pack.

    ``opportunity`` means the skill was selected for a turn. ``success`` and
    ``correction`` are explicit outcome signals used by generated packs and
    future feedback hooks. Non-SKILL.md/generated JSONL adapters are ignored.
    """
    source = Path(path)
    if source.name.lower() != "skill.md" or not source.exists():
        return False
    clean = str(outcome or "").strip().lower()
    field = {
        "opportunity": "mastery_uses",
        "use": "mastery_uses",
        "success": "mastery_successes",
        "correction": "mastery_corrections",
        "failure": "mastery_corrections",
    }.get(clean)
    if not field:
        return False
    # Normal packs live at ``<root>/<slug>/SKILL.md``. Legacy flat markdown
    # packs are not eligible above, so the tree root is always the parent of
    # the slug directory and matches every other skill writer's lock.
    with skill_root_lock(source.parent.parent):
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        updated = _bump_frontmatter_int(text, field)
        updated = _set_frontmatter_int(updated, "last_used_at", int(now if now is not None else time.time()))
        if updated == text:
            return False
        try:
            atomic_write_text(source, updated, encoding="utf-8")
            return True
        except OSError:
            return False


def retire_stale_generated_skills(
    root: str | Path,
    *,
    decay_days: int = 60,
    now: float | None = None,
) -> list[Path]:
    """Move stale generated packs aside when opportunities produced no success."""
    base = Path(root).expanduser()
    if not base.exists():
        return []
    current = float(now if now is not None else time.time())
    cutoff = current - max(1, int(decay_days or 60)) * 86400
    retired: list[Path] = []
    with skill_root_lock(base):
        for path in base.glob("*/SKILL.md"):
            if path.parent.name.casefold().endswith(".retired"):
                continue
            skill = _parse_skill(path)
            if not skill or not skill.generated:
                continue
            uses = _as_int(skill.mastery.get("mastery_uses"))
            successes = _as_int(skill.mastery.get("mastery_successes"))
            corrections = _as_int(skill.mastery.get("mastery_corrections"))
            last_used = _as_int(skill.mastery.get("last_used_at") or skill.mastery.get("created_at"))
            if uses >= 3 and successes <= 0 and (corrections or last_used < cutoff):
                dest = _retire_skill_dir(path)
                if dest is not None:
                    retired.append(dest)
    return retired


def retire_skill_packs_by_candidate_ids(
    profile: Any | None,
    candidate_ids: Any,
    *,
    config: dict[str, Any] | None = None,
) -> list[Path]:
    """Recoverably retire generated packs derived from inactive records."""
    wanted = {str(item or "").strip() for item in (candidate_ids or ()) if str(item or "").strip()}
    if not wanted:
        return []
    root = Path(skills_root(profile, config=config))
    if not root.exists():
        return []
    retired: list[Path] = []
    with skill_root_lock(root):
        for path in root.glob("*/SKILL.md"):
            skill = _parse_skill(path)
            if not skill or not skill.generated:
                continue
            if skill.candidate_id not in wanted:
                continue
            destination = _retire_skill_dir(path)
            if destination is not None:
                retired.append(destination)
    return retired


def _retire_skill_dir(path: Path) -> Path | None:
    destination = path.parent.with_name(path.parent.name + ".retired")
    if destination.exists():
        destination = path.parent.with_name(f"{path.parent.name}.{time.time_ns()}.retired")
    try:
        path.parent.rename(destination)
        return destination
    except OSError:
        return None


def record_selected_skill_outcomes(
    profile: Any | None,
    sources: Any,
    outcome: str,
    *,
    config: dict[str, Any] | None = None,
    correction_text: str = "",
    now: float | None = None,
) -> list[str]:
    """Record an outcome on exact profile-owned generated packs.

    The caller supplies the skill sources selected for one accepted turn. This
    keeps a correction on that turn's governing packs instead of touching every
    pack used recently by another session or surface.
    """
    root = skills_root(profile, config=config)
    if not root or not Path(root).exists():
        return []
    try:
        base = Path(root).resolve()
    except OSError:
        return []
    current = float(now if now is not None else time.time())
    fix_note = _safe_fix_note(correction_text)
    seen: set[str] = set()
    eligible: list[tuple[Path, Any]] = []
    for raw in sources or ():
        try:
            path = Path(str(raw)).expanduser().resolve()
            path.relative_to(base)
        except (OSError, ValueError):
            continue
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        skill = _parse_skill(path)
        if not skill or not skill.generated:
            continue
        eligible.append((path, skill))
    # Generic praise cannot identify which of several simultaneously injected
    # skills caused the outcome. Do not give all of them duplicate success credit.
    if str(outcome or "").strip().lower() == "success" and len(eligible) != 1:
        return []
    touched: list[str] = []
    for path, skill in eligible:
        if record_skill_outcome(path, outcome, now=current):
            touched.append(str(path))
            if str(outcome or "").strip().lower() in {"correction", "failure"}:
                _append_skill_evolution_fix(path.parent, skill.name, fix_note, when=current)
    return touched


def _safe_fix_note(value: str) -> str:
    fix_note = " ".join(str(value or "").split())[:200]
    try:
        from ..utils.text_safety import contains_secret_value
        if fix_note and contains_secret_value(fix_note):
            return ""
    except Exception:
        return ""
    return fix_note


def _append_skill_evolution_fix(skill_dir: Path, skill_name: str, fix_note: str, *, when: float) -> None:
    """Append a correction to the skill's inert evolution sidecar (creating it on
    first use). Records WHAT was corrected for later manual promotion; it never
    rewrites SKILL.md and never duplicates the profile-learning store."""
    try:
        from .importing.manifest import new_skill_evolution, read_manifest, write_manifest
        root = Path(skill_dir).parent
        with skill_root_lock(root):
            evo_path = Path(skill_dir) / "skill_evolution.json"
            data = read_manifest(evo_path) or new_skill_evolution(skill_name=skill_name)
            fixes = data.get("fixes")
            if not isinstance(fixes, list):
                fixes = []
            fixes.append({"at": when, "correction": fix_note} if fix_note else {"at": when})
            data["fixes"] = fixes[-50:]
            data["last_updated_at"] = when
            if not data.get("skill_name"):
                data["skill_name"] = skill_name
            write_manifest(evo_path, data)
    except Exception:
        return
