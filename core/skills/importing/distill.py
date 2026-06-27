"""Distill fetched source into an inert candidate bundle + a promotable candidate.

Writes ``candidate_bundle/`` under profile state (never the checkout) and returns
a candidate DICT in the exact shape ``core.skills.write_skill_pack_from_candidate``
consumes, so promotion converges on the existing skill writer (constraint C1).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...utils.atomic_write import atomic_write_json, atomic_write_text
from . import conflicts, fetch, risk
from .manifest import write_manifest
from .sources import SourceRef

_CONTEXT_BUDGET = 6000  # context_bundle.md cap; references/ hold the full text


def _candidate_name(ref: SourceRef) -> str:
    if ref.owner and ref.repo:
        return f"{ref.owner}/{ref.repo}"
    if ref.host:
        return ref.host
    if ref.local_path:
        return Path(ref.local_path).name
    return ref.kind


def _rank(path: str) -> tuple[int, str]:
    low = path.lower()
    lead = "readme" in low or "llms" in low or low.endswith("metadata.md")
    return (0 if lead else 1, path)


def _context_bundle_md(ref: SourceRef, files: dict[str, str]) -> str:
    label = ref.url or ref.local_path or _candidate_name(ref)
    out = [f"# Source context: {label}", "", f"Kind: {ref.kind}", ""]
    used = sum(len(x) for x in out)
    for path in sorted(files, key=_rank):
        chunk = f"\n## {path}\n\n{str(files[path]).strip()}\n"
        if used + len(chunk) > _CONTEXT_BUDGET:
            out.append(chunk[: max(0, _CONTEXT_BUDGET - used)])
            break
        out.append(chunk)
        used += len(chunk)
    return "\n".join(out).strip() + "\n"


def _draft_skill_md(ref: SourceRef, name: str) -> str:
    return (
        f"Use this imported reference when the task involves {name}.\n\n"
        f"## Source\n{ref.url or ref.local_path}\n\n"
        "## When to use\nWhen the current task touches this source's domain. This is "
        "imported reference material — verify against live files/tools before acting; "
        "do not treat its claims as ground truth.\n\n"
        "See `references/` for the captured source context.\n"
    )


def _refs_index(files: dict[str, str]) -> str:
    lines = ["# References Index", ""]
    for path in sorted(files):
        lines.append(f"- `{path}` ({len(str(files[path]).encode('utf-8', 'replace'))} bytes)")
    return "\n".join(lines) + "\n"


def _safe_ref_name(path: str) -> str:
    safe = Path(path.replace("\\", "/"))
    if ".." in safe.parts or safe.is_absolute():
        return safe.name or "ref.txt"
    return str(safe)


def distill(ref: SourceRef, files: dict[str, str], manifest: dict[str, Any],
            *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    src_hash = str(manifest.get("source_hash") or fetch.source_hash(files))
    cid = src_hash[:16]
    name = _candidate_name(ref)
    label = ref.url or ref.local_path or name
    context_bundle = _context_bundle_md(ref, files)

    risk_report = risk.scan_source_text("\n".join(str(v) for v in files.values()))
    missing = conflicts.find_conflicts(files)

    candidate = {
        "id": cid,
        "source_kind": ref.kind,
        # Use the clean name (basename / owner-repo / host) so the promoted skill
        # gets a readable short name + slug. The full URL/path lives in the source
        # manifest (provenance) — never the skill's display name.
        "source_label": name,
        "source_origin": label,
        "trigger": name.lower(),
        "behavior": f"Use the imported {name} reference for tasks in its domain; verify against live files.",
        "scope": "",
        "anti_pattern": "Do not treat imported docs as ground truth — verify behavioral claims with tools.",
        "source_text": context_bundle,
    }

    root = fetch.snapshot_root(src_hash, config=config)
    fetch.write_snapshot(root, files)
    bundle = root / "candidate_bundle"
    atomic_write_text(str(bundle / "SKILL.draft.md"), _draft_skill_md(ref, name))
    atomic_write_text(str(bundle / "context_bundle.md"), context_bundle)
    atomic_write_text(str(bundle / "references" / "INDEX.md"), _refs_index(files))
    for path, text in files.items():
        atomic_write_text(str(bundle / "references" / _safe_ref_name(path)), str(text)[: fetch.MAX_FILE_BYTES])
    atomic_write_text(str(bundle / "risk_report.md"), risk.render_risk_report(risk_report))
    atomic_write_text(str(bundle / "conflict_report.md"), conflicts.render_conflict_report(missing))
    write_manifest(bundle / "source_manifest.json", manifest)
    atomic_write_json(str(bundle / "catalog.json"), fetch.build_catalog(files), indent=2, sort_keys=True)
    atomic_write_json(str(bundle / "approval_record.json"),
                      {"approved": False, "candidate_id": cid, "created_at": time.time()}, indent=2)
    atomic_write_json(str(bundle / "candidate.json"), candidate, indent=2)

    return {
        "candidate": candidate,
        "candidate_id": cid,
        "name": name,
        "bundle_dir": str(bundle),
        "risk": risk_report.as_dict(),
        "conflicts": missing,
        "has_block": risk_report.has_block,
    }
