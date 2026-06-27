"""Provenance and lifecycle manifests for MO skill imports.

Pure-stdlib read/write/validate for the three import artifacts:

  - ``source_manifest.json``  — what external material was inspected (per import).
  - ``skill_manifest.json``   — provenance/lifecycle beside a promoted skill.
  - ``skill_evolution.json``  — approved local deltas. It REFERENCES profile
    learning, it does not re-store it (constraint C6).

No network, no execution. This module only reads/writes the path it is given;
callers resolve persistent locations through ``core.state.paths.resolve_state_path``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ...utils.atomic_write import atomic_write_json

SCHEMA_VERSION = "1"

_SOURCE_FIELDS = (
    "schema_version", "source_kind", "source_url", "source_ref", "source_hash",
    "fetched_at", "fetch_method", "origin_allowlist", "license", "files",
    "content_digests", "byte_count", "warnings", "trust_level",
    "operator_approval_required",
)
_SKILL_FIELDS = (
    "schema_version", "skill_name", "source_manifest_ref", "source_url",
    "source_kind", "source_hash", "generated_by", "generated_at", "approved_by",
    "approved_at", "last_checked_at", "last_source_hash", "update_available",
    "outcome_score", "last_used_at", "retire_after",
)
_EVOLUTION_FIELDS = (
    "schema_version", "skill_name", "approved_lessons", "rejected_lessons",
    "fixes", "operator_constraints", "last_updated_at", "base_source_hash",
)

# Kinds the stdlib-only pipeline can intake. PDF/video are out of scope until a
# dependency budget is approved, so they fall through to "unknown" here.
VALID_SOURCE_KINDS = frozenset({
    "github_repo", "github_tree", "docs_site", "llms_txt", "local_path", "unknown",
})


def new_source_manifest(
    *,
    source_kind: str,
    source_url: str = "",
    source_ref: str = "",
    source_hash: str = "",
    fetch_method: str = "",
    license: str = "",
    trust_level: str = "untrusted",
) -> dict[str, Any]:
    """Build a fresh source manifest. Imports are untrusted + approval-gated by default."""
    return {
        "schema_version": SCHEMA_VERSION,
        "source_kind": source_kind if source_kind in VALID_SOURCE_KINDS else "unknown",
        "source_url": str(source_url or ""),
        "source_ref": str(source_ref or ""),
        "source_hash": str(source_hash or ""),
        "fetched_at": time.time(),
        "fetch_method": str(fetch_method or ""),
        "origin_allowlist": [],
        "license": str(license or ""),
        "files": [],
        "content_digests": {},
        "byte_count": 0,
        "warnings": [],
        "trust_level": str(trust_level or "untrusted"),
        "operator_approval_required": True,
    }


def new_skill_manifest(
    *,
    skill_name: str,
    source_kind: str = "",
    source_url: str = "",
    source_hash: str = "",
    source_manifest_ref: str = "",
    generated_by: str = "mo-skill-import",
) -> dict[str, Any]:
    """Build the provenance/lifecycle sidecar for a generated/promoted skill."""
    now = time.time()
    return {
        "schema_version": SCHEMA_VERSION,
        "skill_name": str(skill_name or ""),
        "source_manifest_ref": str(source_manifest_ref or ""),
        "source_url": str(source_url or ""),
        "source_kind": str(source_kind or ""),
        "source_hash": str(source_hash or ""),
        "generated_by": str(generated_by or ""),
        "generated_at": now,
        "approved_by": "",
        "approved_at": 0.0,
        "last_checked_at": now,
        "last_source_hash": str(source_hash or ""),
        "update_available": False,
        "outcome_score": 0.0,
        "last_used_at": 0.0,
        "retire_after": 0.0,
    }


def new_skill_evolution(*, skill_name: str, base_source_hash: str = "") -> dict[str, Any]:
    """Build the approved-deltas sidecar. Lessons reference profile learning; this
    file never rewrites SKILL.md and never duplicates the profile-learning store."""
    return {
        "schema_version": SCHEMA_VERSION,
        "skill_name": str(skill_name or ""),
        "approved_lessons": [],
        "rejected_lessons": [],
        "fixes": [],
        "operator_constraints": [],
        "last_updated_at": time.time(),
        "base_source_hash": str(base_source_hash or ""),
    }


def _missing(data: Any, fields: tuple[str, ...]) -> list[str]:
    if not isinstance(data, dict):
        return ["manifest must be a JSON object"]
    return [f"missing field: {name}" for name in fields if name not in data]


def validate_source_manifest(data: Any) -> list[str]:
    issues = _missing(data, _SOURCE_FIELDS)
    if isinstance(data, dict):
        if data.get("source_kind") not in VALID_SOURCE_KINDS:
            issues.append(f"invalid source_kind: {data.get('source_kind')!r}")
        if data.get("operator_approval_required") is not True:
            issues.append("operator_approval_required must be true (imports are approval-gated)")
    return issues


def validate_skill_manifest(data: Any) -> list[str]:
    issues = _missing(data, _SKILL_FIELDS)
    if isinstance(data, dict) and not str(data.get("skill_name") or "").strip():
        issues.append("skill_name is required")
    return issues


def validate_skill_evolution(data: Any) -> list[str]:
    return _missing(data, _EVOLUTION_FIELDS)


def read_manifest(path: str | Path) -> dict[str, Any]:
    """Read a manifest JSON; return {} on any error (callers re-create if empty)."""
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def write_manifest(path: str | Path, data: dict[str, Any]) -> None:
    """Atomically write a manifest. ``path`` must already be a resolved location."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(path), data, indent=2, sort_keys=True)
