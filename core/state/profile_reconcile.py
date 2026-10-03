"""Dry-run-first reconciliation for curated MO profile state."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..utils.text_safety import contains_secret_value
from ..utils.atomic_write import atomic_create_bytes
from .paths import PROFILE_PROSE_FILES, mo_home, runtime_config_path
from .sync import GitStateSync, _candidate_files, _inside, _path_is_link, _safe_binary_candidate, _secret_shaped_name, _text_candidate


CURATED_PATHS = tuple(f"memory/profile/{name}" for name in PROFILE_PROSE_FILES) + ("skills", "skin")
MAX_PROFILE_FILES = 10_000
MAX_PROFILE_BYTES = 50_000_000


@dataclass(frozen=True)
class ProfileFile:
    relpath: str
    size: int
    digest: str


@dataclass
class ProfileSnapshot:
    root: Path
    files: dict[str, ProfileFile] = field(default_factory=dict)
    invalid: list[str] = field(default_factory=list)


@dataclass
class ReconcilePlan:
    local: ProfileSnapshot
    peer: ProfileSnapshot
    same: list[str]
    local_only: list[str]
    peer_only: list[str]
    different: list[str]

    @property
    def safe_union(self) -> bool:
        return not self.local.invalid and not self.peer.invalid and not self.different


def snapshot_profile(
    root: str | Path,
    *,
    max_file_bytes: int = 2_000_000,
    max_files: int = MAX_PROFILE_FILES,
    max_total_bytes: int = MAX_PROFILE_BYTES,
) -> ProfileSnapshot:
    base = Path(root).expanduser().resolve(strict=False)
    result = ProfileSnapshot(base)
    count = 0
    total_bytes = 0
    for relroot in CURATED_PATHS:
        target = base / relroot
        if not target.exists():
            continue
        paths = (target,) if target.is_file() else _candidate_files(target)
        for path in paths:
            try:
                count += 1
                if count > max(1, min(MAX_PROFILE_FILES, int(max_files))):
                    result.invalid.append(f"profile snapshot exceeds {max_files} files")
                    return result
                resolved = path.resolve(strict=False)
                relpath = resolved.relative_to(base).as_posix()
                if _path_is_link(path) or not _inside(resolved, base):
                    result.invalid.append(f"{relpath}: linked or escaping path")
                    continue
                if not path.is_file():
                    result.invalid.append(f"{relpath}: unsupported special entry")
                    continue
                size = path.stat().st_size
                if size > max_file_bytes:
                    result.invalid.append(f"{relpath}: exceeds {max_file_bytes} bytes")
                    continue
                total_bytes += size
                if total_bytes > max(1_024, min(MAX_PROFILE_BYTES, int(max_total_bytes))):
                    result.invalid.append(f"profile snapshot exceeds {max_total_bytes} bytes")
                    return result
                if _secret_shaped_name(path.name):
                    result.invalid.append(f"{relpath}: secret-shaped path")
                    continue
                data = path.read_bytes()
                if _text_candidate(path):
                    try:
                        text = data.decode("utf-8", errors="strict")
                    except UnicodeError:
                        result.invalid.append(f"{relpath}: invalid UTF-8 text")
                        continue
                    if contains_secret_value(text):
                        result.invalid.append(f"{relpath}: secret-shaped content")
                        continue
                elif not _safe_binary_candidate(path):
                    result.invalid.append(f"{relpath}: unsupported file type")
                    continue
                result.files[relpath] = ProfileFile(relpath, size, hashlib.sha256(data).hexdigest())
            except (OSError, ValueError):
                result.invalid.append(f"{path.name}: unreadable")
    result.invalid.sort()
    return result


def compare_profiles(local_root: str | Path, peer_root: str | Path) -> ReconcilePlan:
    local = snapshot_profile(local_root)
    peer = snapshot_profile(peer_root)
    names = sorted(set(local.files) | set(peer.files))
    same: list[str] = []
    local_only: list[str] = []
    peer_only: list[str] = []
    different: list[str] = []
    for name in names:
        left = local.files.get(name)
        right = peer.files.get(name)
        if left and right:
            (same if left.digest == right.digest else different).append(name)
        elif left:
            local_only.append(name)
        else:
            peer_only.append(name)
    return ReconcilePlan(local, peer, same, local_only, peer_only, different)


def apply_safe_union(plan: ReconcilePlan, *, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {"applied": False, "reason": "confirmation required", "copied": 0}
    if not plan.safe_union:
        return {"applied": False, "reason": "invalid paths or same-file conflicts require review", "copied": 0}
    payloads: list[tuple[Path, bytes]] = []
    for relpath in plan.local_only:
        payloads.append(_validated_copy(plan.local.root, plan.peer.root, plan.local.files[relpath]))
    for relpath in plan.peer_only:
        payloads.append(_validated_copy(plan.peer.root, plan.local.root, plan.peer.files[relpath]))
    for destination, data in payloads:
        atomic_create_bytes(destination, data)
    return {"applied": True, "reason": "safe one-sided union completed", "copied": len(payloads)}


def configured_peer(config: dict[str, Any] | None = None) -> Path | None:
    block = (config or {}).get("consistent_everywhere")
    block = block if isinstance(block, dict) else {}
    sync = block.get("sync") if isinstance(block.get("sync"), dict) else {}
    raw = str(sync.get("reconcile_peer_path") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve(strict=False)
    config_path = runtime_config_path(config)
    base = Path(config_path).expanduser().resolve(strict=False).parent if config_path else mo_home(config)
    return (base / path).resolve(strict=False)


def render_reconcile_plan(config: dict[str, Any] | None = None, *, confirm: bool = False) -> str:
    cfg = config or {}
    if GitStateSync(cfg).baseline_established():
        return "\n".join([
            "MO Everywhere profile reconciliation:",
            "  baseline:      committed",
            "  routine sync: exact private Git/OpenSSH lane",
            "  peer snapshot: first-attach history only",
            "  applied:       False (no first-attach reconciliation needed)",
        ])
    local = mo_home(cfg)
    peer = configured_peer(cfg)
    if peer is None:
        current = snapshot_profile(local)
        lines = [
            "MO Everywhere profile reconciliation (dry run):",
            f"  local curated files: {len(current.files)}",
            f"  invalid local paths: {len(current.invalid)}",
            "  peer snapshot: not configured",
            "  no files changed",
            "Set consistent_everywhere.sync.reconcile_peer_path to a trusted, private peer snapshot, then run /everywhere reconcile again.",
        ]
        if current.invalid:
            lines.extend(f"  blocked: {item}" for item in current.invalid[:12])
        return "\n".join(lines)
    if peer == local:
        return "\n".join([
            "MO Everywhere profile reconciliation (dry run):",
            "  blocked: reconcile_peer_path must be a distinct trusted profile snapshot",
            "  no files changed",
        ])
    plan = compare_profiles(local, peer)
    result = apply_safe_union(plan, confirm=confirm)
    lines = [
        "MO Everywhere profile reconciliation:",
        f"  exact matches: {len(plan.same)}",
        f"  local only:   {len(plan.local_only)}",
        f"  peer only:    {len(plan.peer_only)}",
        f"  conflicts:    {len(plan.different)}",
        f"  invalid:      {len(plan.local.invalid) + len(plan.peer.invalid)}",
        f"  safe union:   {plan.safe_union}",
        f"  applied:      {result['applied']} ({result['reason']})",
    ]
    if plan.different:
        lines.append("Same-file conflicts (preserved on both sides):")
        lines.extend(f"- {item}" for item in plan.different[:20])
    invalid = plan.local.invalid + plan.peer.invalid
    if invalid:
        lines.append("Invalid candidates:")
        lines.extend(f"- {item}" for item in invalid[:20])
    if plan.safe_union and not confirm and (plan.local_only or plan.peer_only):
        lines.append("No files changed. Re-run `/everywhere reconcile --confirm` to copy only one-sided files in both directions.")
    return "\n".join(lines)


def _validated_copy(source_root: Path, destination_root: Path, expected: ProfileFile) -> tuple[Path, bytes]:
    source = (source_root / expected.relpath).resolve(strict=True)
    destination = (destination_root / expected.relpath).resolve(strict=False)
    if not _inside(source, source_root) or not _inside(destination, destination_root):
        raise ValueError("reconciliation path escapes a profile root")
    if destination.exists() or _path_is_link(destination):
        raise ValueError("safe union cannot overwrite an existing path")
    data = source.read_bytes()
    if len(data) != expected.size or hashlib.sha256(data).hexdigest() != expected.digest:
        raise ValueError("curated source changed after reconciliation; run the dry run again")
    return destination, data
