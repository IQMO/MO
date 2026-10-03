"""Promotion + lifecycle for imported skill candidates (constraints C1, C4, C6).

Promotion converges on the EXISTING ``core.skills.write_skill_pack_from_candidate``
(which writes a contract-valid SKILL.md). A ``skill_manifest.json`` sidecar carries
provenance/lifecycle; aging/retirement and outcome scoring reuse the existing skills
runtime — no parallel store.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ...utils.atomic_write import atomic_write_json
from . import fetch, risk
from .manifest import new_skill_manifest, read_manifest, write_manifest


def _bundle_dir(candidate_id: str, *, config: dict[str, Any] | None = None) -> Path:
    # snapshot_root sanitizes the hash to alnum[:16]; candidate_id IS hash[:16].
    return fetch.snapshot_root(candidate_id, config=config) / "candidate_bundle"


def load_candidate(candidate_id: str, *, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    try:
        return json.loads((_bundle_dir(candidate_id, config=config) / "candidate.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def promote(candidate_id: str, *, profile: Any | None = None, config: dict[str, Any] | None = None,
            approved_by: str = "operator") -> dict[str, Any]:
    """Promote an approved candidate into a real local skill via the existing writer."""
    candidate = load_candidate(candidate_id, config=config)
    if not candidate:
        return {"ok": False, "error": f"candidate {candidate_id} not found"}
    risk_report = _promotion_risk_report(candidate_id, candidate, config=config)
    if risk_report is None:
        return {
            "ok": False,
            "error": "candidate has no complete source snapshot to re-scan; inspect the source again",
        }
    if risk_report.get("has_block") is True:
        return {
            "ok": False,
            "error": "candidate has unresolved BLOCK risk findings; redact or replace the source and inspect again",
        }

    # Lazy import keeps the subpackage free of any import-order coupling.
    from .. import write_skill_pack_from_candidate

    skill_path = write_skill_pack_from_candidate(candidate, profile=profile, config=config)

    bundle = _bundle_dir(candidate_id, config=config)
    src_manifest = read_manifest(bundle / "source_manifest.json")
    sidecar = new_skill_manifest(
        skill_name=str(candidate.get("source_label") or candidate_id),
        source_kind=str(candidate.get("source_kind") or ""),
        source_url=str(src_manifest.get("source_url") or ""),
        source_hash=str(src_manifest.get("source_hash") or ""),
        source_manifest_ref=str(bundle / "source_manifest.json"),
    )
    sidecar["approved_by"] = approved_by
    sidecar["approved_at"] = time.time()
    sidecar_path = Path(skill_path).parent / "skill_manifest.json"
    write_manifest(sidecar_path, sidecar)

    # Mark the approval record so /learning candidates reflects state.
    try:
        atomic_write_json(str(bundle / "approval_record.json"),
                          {"approved": True, "candidate_id": candidate_id, "approved_at": time.time()}, indent=2)
    except Exception:
        pass

    return {"ok": True, "skill_path": str(skill_path), "skill_manifest": str(sidecar_path)}


def _promotion_risk_report(
    candidate_id: str,
    candidate: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Re-scan the full captured source; stored summaries are not promotion gates."""
    source_root = _bundle_dir(candidate_id, config=config).parent / "source"
    if not source_root.is_dir():
        return None
    chunks: list[str] = []
    for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None
    if not chunks:
        return None
    # Re-scan the exact candidate fields consumed by the pack writer too. The
    # captured source may be clean while candidate.json was edited afterward;
    # promotion must fail closed on either representation.
    chunks.extend(
        str(candidate.get(key) or "")
        for key in (
            "source_label",
            "source_kind",
            "trigger",
            "behavior",
            "scope",
            "anti_pattern",
            "source_text",
        )
    )
    return risk.scan_source_text("\n".join(chunks), surface="skill_import_promotion").as_dict()


def refresh(skill_manifest_path: str | Path, new_source_hash: str) -> dict[str, Any]:
    """Re-check a promoted skill's source hash. Sets update_available without rewriting
    the skill — promotion of an update stays an explicit operator action."""
    path = Path(skill_manifest_path)
    data = read_manifest(path)
    if not data:
        return {"ok": False, "error": "skill_manifest not found"}
    changed = bool(new_source_hash) and new_source_hash != str(data.get("last_source_hash") or "")
    data["last_checked_at"] = time.time()
    data["update_available"] = changed
    if changed:
        data["last_source_hash"] = new_source_hash
    write_manifest(path, data)
    return {"ok": True, "update_available": changed}


def list_imported_skills(profile: Any | None = None, *, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Scan the skill root for promoted imports (those carrying a skill_manifest.json)."""
    from .. import skills_root
    root = Path(skills_root(profile, config=config))
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for sidecar in sorted(root.glob("*/skill_manifest.json")):
        data = read_manifest(sidecar)
        if data:
            out.append({
                "skill": sidecar.parent.name,
                "source_kind": data.get("source_kind", ""),
                "source_url": data.get("source_url", ""),
                "update_available": bool(data.get("update_available")),
                "approved_at": data.get("approved_at", 0.0),
            })
    return out
