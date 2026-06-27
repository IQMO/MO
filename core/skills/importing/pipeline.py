"""Orchestrator for the skill-import pipeline — the entry points ``/learning`` calls.

Ties classify -> intake -> distill -> (temporary use | candidate) -> promote. All
state lands under profile state via the lower layers; nothing executes imported
content; promotion is always an explicit operator step.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...state.paths import resolve_state_path
from . import distill as _distill
from . import intake as _intake
from . import promote as _promote
from . import temporary as _temporary
from .sources import classify_source


def _imports_root(config: dict[str, Any] | None = None) -> Path:
    return Path(resolve_state_path("memory/skill_imports", config or {}))


def inspect(raw: str, *, profile: Any | None = None, config: dict[str, Any] | None = None,
            network_allowed: bool = False, opener=None) -> dict[str, Any]:
    """Classify + fetch + distill into an inert candidate bundle. No promotion."""
    ref = classify_source(raw)
    if not ref.ok:
        return {"ok": False, "error": ref.reason or f"unrecognized source: {raw}"}
    files, manifest = _intake.intake(ref, opener=opener, network_allowed=network_allowed)
    if not files:
        return {"ok": False, "error": "nothing fetched", "warnings": manifest.get("warnings", [])}
    result = _distill.distill(ref, files, manifest, config=config)
    return {
        "ok": True, "candidate_id": result["candidate_id"], "name": result["name"],
        "kind": ref.kind, "files": len(files), "warnings": manifest.get("warnings", []),
        "risk": result["risk"], "conflicts": result["conflicts"], "has_block": result["has_block"],
        "bundle_dir": result["bundle_dir"], "source_hash": manifest.get("source_hash", ""),
    }


def use(raw: str, *, profile: Any | None = None, config: dict[str, Any] | None = None,
        network_allowed: bool = False, opener=None) -> dict[str, Any]:
    """One-turn use-without-install: fetch + build untrusted temporary context."""
    ref = classify_source(raw)
    if not ref.ok:
        return {"ok": False, "error": ref.reason or f"unrecognized source: {raw}"}
    files, manifest = _intake.intake(ref, opener=opener, network_allowed=network_allowed)
    if not files:
        return {"ok": False, "error": "nothing fetched", "warnings": manifest.get("warnings", [])}
    label = ref.url or ref.local_path or ref.kind
    return {"ok": True, "context": _temporary.build_temporary_context(files, label=label), "files": len(files)}


def promote(candidate_id: str, *, profile: Any | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return _promote.promote(candidate_id, profile=profile, config=config)


def list_candidates(*, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    root = _imports_root(config)
    if not root.exists():
        return out
    for cand_json in sorted(root.glob("*/candidate_bundle/candidate.json")):
        try:
            cand = json.loads(cand_json.read_text(encoding="utf-8"))
            approval_path = cand_json.parent / "approval_record.json"
            approved = False
            if approval_path.exists():
                approved = bool(json.loads(approval_path.read_text(encoding="utf-8")).get("approved"))
            out.append({
                "candidate_id": cand.get("id"), "label": cand.get("source_label"),
                "kind": cand.get("source_kind"), "approved": approved,
            })
        except Exception:
            continue
    return out


def status(*, profile: Any | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "candidates": list_candidates(config=config),
        "imported_skills": _promote.list_imported_skills(profile, config=config),
    }
