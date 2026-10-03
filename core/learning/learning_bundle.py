"""Learning bundle export/import — move MO's learned state between instances.

The operator runs more than one MO (e.g. local + server). Profile prose,
confirmed learning suggestions, and authoritative physical skill packs travel as
one reviewed bundle. A generated pack is omitted only when an exported confirmed
suggestion can recreate it; approved workflow and orphaned packs remain included
because the physical pack is then the only portable authority.

Safety contract:
- Export refuses when any bundled text trips the secret detector.
- Import is dry-run by default; ``confirm=True`` applies.
- Applied imports are append-only with id/fingerprint dedup — never overwrite
  the receiving instance's curated profile prose. Bundle profile files land in
  ``imports/<stamp>/`` for manual review instead of being auto-merged.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_json, atomic_write_text
from ..utils.jsonl_utils import read_jsonl
from ..state.paths import PROFILE_PROSE_FILES, resolve_state_path
from ..skills import retired_learning_authority, skills_root
from ..skills._util import skill_root_lock
from ..skills.importing.manifest import validate_skill_evolution, validate_skill_manifest
from ..utils.text_safety import contains_secret_value

BUNDLE_VERSION = "mo-learning-bundle-v3"
PROFILE_FILES = PROFILE_PROSE_FILES


def _recommendation_signature(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _skill_recommendation_signature(content: str) -> str:
    """Return the learned-behavior signature carried by one skill document."""
    lines = str(content or "").splitlines()
    for index, line in enumerate(lines):
        if line.strip().casefold() != "## learned behavior":
            continue
        for recommendation in lines[index + 1:]:
            if recommendation.strip():
                return _recommendation_signature(recommendation)
    return ""


def _memory_dir(profile: Any) -> Path:
    profile_path = getattr(profile, "_path", None)
    return Path(profile_path).parent if profile_path else Path(resolve_state_path("memory"))


def export_learning_bundle(profile: Any, *, path: str | Path | None = None) -> dict[str, Any]:
    """Write a learning bundle JSON; return {exported, path, counts} or a refusal."""
    memory = _memory_dir(profile)
    profile_dir = memory / "profile"
    bundle: dict[str, Any] = {
        "version": BUNDLE_VERSION,
        "exported_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "profile_files": {},
        "confirmed_suggestions": [],
        "skills": {},
    }
    for name in PROFILE_FILES:
        file_path = profile_dir / name
        if file_path.exists():
            bundle["profile_files"][name] = file_path.read_text(encoding="utf-8", errors="replace")
    learning = memory / "learning"
    skill_root = skills_root(profile)
    retired_ids, retired_recommendations = retired_learning_authority(skill_root)
    confirmed_suggestions = [
        row for row in read_jsonl(learning / "suggestions.jsonl")
        if str(row.get("status") or "").lower() == "confirmed"
        and str(row.get("id") or "") not in retired_ids
        and _recommendation_signature(row.get("recommendation")) not in retired_recommendations
    ]
    bundle["confirmed_suggestions"] = confirmed_suggestions
    bundle["skills"] = _read_skill_tree(
        skill_root,
        replaceable_candidate_ids={
            str(row.get("id") or "").strip()
            for row in confirmed_suggestions
            if str(row.get("id") or "").strip()
        },
    )

    flat = json.dumps(bundle, ensure_ascii=False)
    if contains_secret_value(flat):
        return {"exported": False, "reason": "bundle text trips the secret detector; clean the offending profile/learning line first"}

    stamp = datetime.now().strftime("%Y-%m-%dT%H%M")
    out = Path(path) if path else learning / "bundles" / "exports" / f"mo-learning-bundle-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out, bundle, ensure_ascii=False, indent=2)
    return {
        "exported": True,
        "path": str(out),
        "counts": {
            "profile_files": len(bundle["profile_files"]),
            "confirmed_suggestions": len(bundle["confirmed_suggestions"]),
            "skills": len(bundle["skills"]),
        },
    }


def import_learning_bundle(profile: Any, path: str | Path, *, confirm: bool = False) -> dict[str, Any]:
    """Import a bundle. Dry-run by default; ``confirm=True`` applies append-only."""
    src = Path(path)
    if not src.exists():
        return {"imported": False, "reason": f"bundle not found: {src}"}
    try:
        bundle = json.loads(src.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {"imported": False, "reason": "bundle is not valid JSON"}
    if not isinstance(bundle, dict) or bundle.get("version") != BUNDLE_VERSION:
        return {"imported": False, "reason": f"unsupported bundle version: {bundle.get('version') if isinstance(bundle, dict) else '?'}"}
    fields = {"confirmed_suggestions": list, "profile_files": dict, "skills": dict}
    invalid = next((name for name, kind in fields.items() if name in bundle and not isinstance(bundle[name], kind)), "")
    if invalid:
        return {"imported": False, "reason": f"bundle field {invalid!r} has the wrong JSON type"}
    if contains_secret_value(json.dumps(bundle, ensure_ascii=False)):
        return {"imported": False, "reason": "bundle text trips the secret detector; refusing import"}

    memory = _memory_dir(profile)
    learning = memory / "learning"
    suggestions_path = learning / "suggestions.jsonl"
    existing_rows = read_jsonl(suggestions_path)
    existing_suggestions = {str(row.get("id") or "") for row in existing_rows}
    existing_recommendations = {
        _recommendation_signature(row.get("recommendation")) for row in existing_rows
        if str(row.get("status") or "").casefold() == "confirmed"
    }
    skill_root = skills_root(profile)
    retired_ids, retired_recommendations = retired_learning_authority(skill_root)
    incoming_suggestions = [
        row for row in bundle.get("confirmed_suggestions") or []
        if isinstance(row, dict) and row.get("id")
    ]
    retired_imports = sum(
        1 for row in incoming_suggestions
        if str(row["id"]) in retired_ids
        or _recommendation_signature(row.get("recommendation")) in retired_recommendations
    )
    new_suggestions: list[dict[str, Any]] = []
    seen_ids = existing_suggestions.union(retired_ids)
    seen_recommendations = existing_recommendations.union(retired_recommendations)
    for row in incoming_suggestions:
        row_id = str(row["id"])
        recommendation = _recommendation_signature(row.get("recommendation"))
        if row_id in seen_ids or recommendation in seen_recommendations:
            continue
        new_suggestions.append(row)
        seen_ids.add(row_id)
        seen_recommendations.add(recommendation)
    profile_files = {k: v for k, v in (bundle.get("profile_files") or {}).items() if k in PROFILE_FILES}
    skill_files: dict[str, str] = {}
    for relative_path, content in (bundle.get("skills") or {}).items():
        if not isinstance(relative_path, str) or not isinstance(content, str):
            return {"imported": False, "reason": "bundle skill entries must map paths to text"}
        if not _valid_bundle_skill_file(relative_path, content):
            return {"imported": False, "reason": f"invalid bundle skill file: {relative_path!r}"}
        candidate = re.search(r"(?m)^candidate_id:\s*[\"']?([^\"'\s]+)", content)
        confirmed_learning = bool(
            re.search(r"(?m)^provenance:\s*[\"']?confirmed-learning[\"']?\s*$", content)
        )
        if (
            (candidate and candidate.group(1) in retired_ids)
            or (
                confirmed_learning
                and _skill_recommendation_signature(content) in retired_recommendations
            )
        ):
            retired_imports += 1
            continue
        destination = _bundle_skill_destination(skill_root, relative_path)
        if destination is None:
            return {"imported": False, "reason": f"bundle skill path escapes the skill root: {relative_path!r}"}
        if not destination.exists():
            skill_files[relative_path] = content

    plan = {
        "imported": False,
        "dry_run": not confirm,
        "new_confirmed_suggestions": len(new_suggestions),
        "new_skill_files": len(skill_files),
        "retired_candidates_skipped": retired_imports,
        "profile_files_for_review": sorted(profile_files),
        "note": "profile prose is never auto-merged; review the staged copies and merge by hand",
    }
    if not confirm:
        return plan

    with skill_root_lock(skill_root):
        destinations: dict[str, Path] = {}
        for relative_path in skill_files:
            destination = _bundle_skill_destination(skill_root, relative_path)
            if destination is None:
                return {
                    "imported": False,
                    "reason": f"bundle skill path escapes the skill root: {relative_path!r}",
                }
            destinations[relative_path] = destination
        _append_jsonl(suggestions_path, new_suggestions)
        for relative_path, content in skill_files.items():
            atomic_write_text(destinations[relative_path], content, encoding="utf-8")
    review_dir = learning / "bundles" / "imports" / datetime.now().strftime("%Y-%m-%dT%H%M")
    if profile_files:
        review_dir.mkdir(parents=True, exist_ok=True)
        for name, content in profile_files.items():
            atomic_write_text(review_dir / name, str(content), encoding="utf-8")
    plan.update({"imported": True, "dry_run": False, "review_dir": str(review_dir) if profile_files else ""})
    return plan


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_skill_tree(
    root: Path,
    *,
    replaceable_candidate_ids: set[str] | None = None,
) -> dict[str, str]:
    if not root.exists():
        return {}
    replaceable = replaceable_candidate_ids or set()
    derived_dirs: set[Path] = set()
    for skill_path in root.glob("*/SKILL.md"):
        try:
            text = skill_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        candidate = re.search(r"(?m)^candidate_id:\s*[\"']?([^\"'\s]+)", text)
        if (
            candidate
            and candidate.group(1) in replaceable
            and re.search(r"(?m)^provenance:\s*[\"']?confirmed-learning[\"']?\s*$", text)
        ):
            derived_dirs.add(skill_path.parent)
    out: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        try:
            if any(parent in derived_dirs for parent in (path, *path.parents)):
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            if any(part.casefold().endswith(".retired") for part in Path(rel).parts):
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            if _valid_bundle_skill_file(rel, content):
                out[rel] = content
        except OSError:
            continue
    return out


def _safe_bundle_skill_path(value: str) -> bool:
    text = str(value or "").replace("\\", "/").strip("/")
    if not text or ".." in text.split("/"):
        return False
    path = Path(text)
    if path.drive or path.is_absolute() or path.suffix.lower() not in {".md", ".txt", ".json"}:
        return False
    parts = text.split("/")
    if len(parts) < 2 or not parts[0]:
        return False
    if parts[-1] == "SKILL.md":
        return True
    if len(parts) == 2 and parts[-1] in {"skill_manifest.json", "skill_evolution.json"}:
        return True
    return "references" in parts[1:] and path.suffix.lower() in {".md", ".txt"}


def _bundle_skill_destination(root: Path, relative_path: str) -> Path | None:
    """Resolve one already-validated bundle path and prove root containment."""
    resolved_root = root.resolve(strict=False)
    destination = (resolved_root / Path(relative_path)).resolve(strict=False)
    try:
        destination.relative_to(resolved_root)
    except ValueError:
        return None
    return destination


def _valid_bundle_skill_file(relative_path: str, content: str) -> bool:
    if not _safe_bundle_skill_path(relative_path):
        return False
    name = Path(relative_path).name
    if name not in {"skill_manifest.json", "skill_evolution.json"}:
        return True
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return False
    if name == "skill_manifest.json":
        return not validate_skill_manifest(data)
    return not validate_skill_evolution(data)
