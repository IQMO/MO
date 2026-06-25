"""Learning bundle export/import — move MO's learned state between instances.

The operator runs more than one MO (e.g. local + server). Profile prose,
confirmed learning suggestions, and promoted workflows should travel between
them as one reviewed bundle instead of hand-copied files (adopted from the ECC
second-pass comparison, 2026-06-10T1737).

Safety contract:
- Export refuses when any bundled text trips the secret detector.
- Import is dry-run by default; ``confirm=True`` applies.
- Applied imports are append-only with id/fingerprint dedup — never overwrite
  the receiving instance's curated profile prose. Bundle profile files land in
  ``imports/<stamp>/`` for manual review instead of being auto-merged.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..atomic_write import atomic_write_json, atomic_write_text
from ..jsonl_utils import read_jsonl
from ..path_defaults import resolve_state_path
from ..skills import skills_root
from ..text_safety import contains_secret_value

BUNDLE_VERSION = "mo-learning-bundle-v1"
PROFILE_FILES = ("operator.md", "thinking_model.md", "behavior.md", "learning.md", "terms.md", "identity.md")


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
        "promoted_workflows": [],
        "skills": {},
    }
    for name in PROFILE_FILES:
        file_path = profile_dir / name
        if file_path.exists():
            bundle["profile_files"][name] = file_path.read_text(encoding="utf-8", errors="replace")
    bundle["confirmed_suggestions"] = [
        row for row in read_jsonl(memory / "learning_suggestions.jsonl")
        if str(row.get("status") or "").lower() == "confirmed"
    ]
    bundle["promoted_workflows"] = read_jsonl(memory / "workflow_promoted.jsonl")
    bundle["skills"] = _read_skill_tree(skills_root(profile))

    flat = json.dumps(bundle, ensure_ascii=False)
    if contains_secret_value(flat):
        return {"exported": False, "reason": "bundle text trips the secret detector; clean the offending profile/learning line first"}

    stamp = datetime.now().strftime("%Y-%m-%dT%H%M")
    out = Path(path) if path else memory / "exports" / f"mo-learning-bundle-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out, bundle, ensure_ascii=False, indent=2)
    return {
        "exported": True,
        "path": str(out),
        "counts": {
            "profile_files": len(bundle["profile_files"]),
            "confirmed_suggestions": len(bundle["confirmed_suggestions"]),
            "promoted_workflows": len(bundle["promoted_workflows"]),
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
    if contains_secret_value(json.dumps(bundle, ensure_ascii=False)):
        return {"imported": False, "reason": "bundle text trips the secret detector; refusing import"}

    memory = _memory_dir(profile)
    suggestions_path = memory / "learning_suggestions.jsonl"
    promoted_path = memory / "workflow_promoted.jsonl"
    existing_suggestions = {str(row.get("id") or "") for row in read_jsonl(suggestions_path)}
    existing_promoted = {str(row.get("id") or "") for row in read_jsonl(promoted_path)}
    skill_root = skills_root(profile)
    existing_skill_files = _existing_skill_files(skill_root)

    new_suggestions = [
        row for row in bundle.get("confirmed_suggestions") or []
        if isinstance(row, dict) and row.get("id") and str(row["id"]) not in existing_suggestions
    ]
    new_promoted = [
        row for row in bundle.get("promoted_workflows") or []
        if isinstance(row, dict) and row.get("id") and str(row["id"]) not in existing_promoted
    ]
    profile_files = {k: v for k, v in (bundle.get("profile_files") or {}).items() if k in PROFILE_FILES}
    skill_files = {
        k: v for k, v in (bundle.get("skills") or {}).items()
        if isinstance(k, str) and isinstance(v, str) and _safe_bundle_skill_path(k) and k not in existing_skill_files
    }

    plan = {
        "imported": False,
        "dry_run": not confirm,
        "new_confirmed_suggestions": len(new_suggestions),
        "new_promoted_workflows": len(new_promoted),
        "new_skill_files": len(skill_files),
        "profile_files_for_review": sorted(profile_files),
        "note": "profile prose is never auto-merged; review the staged copies and merge by hand",
    }
    if not confirm:
        return plan

    _append_jsonl(suggestions_path, new_suggestions)
    _append_jsonl(promoted_path, new_promoted)
    for rel, content in skill_files.items():
        atomic_write_text(skill_root / rel, content, encoding="utf-8")
    review_dir = memory / "imports" / datetime.now().strftime("%Y-%m-%dT%H%M")
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


def _read_skill_tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    out: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.suffix.lower() in {".md", ".txt"}):
        try:
            rel = str(path.relative_to(root)).replace("\\", "/")
            if _safe_bundle_skill_path(rel):
                out[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return out


def _existing_skill_files(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*")
        if path.is_file()
    }


def _safe_bundle_skill_path(value: str) -> bool:
    text = str(value or "").replace("\\", "/").strip("/")
    if not text or ".." in text.split("/"):
        return False
    path = Path(text)
    if path.is_absolute() or path.suffix.lower() not in {".md", ".txt"}:
        return False
    parts = text.split("/")
    if len(parts) < 2 or not parts[0]:
        return False
    if parts[-1] == "SKILL.md":
        return True
    return "references" in parts[1:] and path.suffix.lower() in {".md", ".txt"}
