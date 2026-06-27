"""Safe migration of legacy checkout-local MO runtime state into private home.

The migration command is intentionally conservative:
- dry-run by default;
- apply requires an explicit confirmation flag;
- only legacy MO-owned state directories are considered by default;
- existing destination files are never overwritten;
- reports show paths/status only, never file contents or secret values.
"""
from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
import traceback

from .paths import mo_home, repo_root

LEGACY_STATE_ENTRIES: tuple[str, ...] = ("memory", "logs", "critique")
EXCLUDED_REL_PREFIXES: tuple[str, ...] = (
    "memory/pre_release_evidence",
    "memory/cache",
    "memory/runtime",
)


@dataclass(frozen=True)
class MigrationFile:
    """One file-level migration decision."""

    rel_path: str
    source: Path
    dest: Path
    action: str
    reason: str = ""
    size: int = 0


@dataclass
class MigrationPlan:
    source_root: Path
    home: Path
    entries: tuple[str, ...] = LEGACY_STATE_ENTRIES
    files: list[MigrationFile] = field(default_factory=list)
    dirs_to_create: list[str] = field(default_factory=list)
    missing_entries: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def planned_files(self) -> list[MigrationFile]:
        return [item for item in self.files if item.action == "copy"]

    @property
    def conflicts(self) -> list[MigrationFile]:
        return [item for item in self.files if item.action == "conflict"]

    @property
    def already_present(self) -> list[MigrationFile]:
        return [item for item in self.files if item.action in {"same", "exists"}]


@dataclass
class MigrationResult:
    copied: list[str] = field(default_factory=list)
    removed_sources: list[str] = field(default_factory=list)
    created_dirs: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    remove_source: bool = False


class MigrationApprovalError(RuntimeError):
    """Raised when an apply/move migration is requested without confirmation."""


def parse_migration_request(text: str | list[str] | tuple[str, ...] | None) -> tuple[str, bool]:
    """Return (action, confirm) for CLI/slash migration args.

    Actions:
    - ``dry-run``: inspect only (default)
    - ``apply``: copy missing files into private home
    - ``move``: copy missing files, then remove only the copied legacy files
    """
    if isinstance(text, (list, tuple)):
        parts = [str(part) for part in text]
    else:
        try:
            parts = shlex.split(str(text or ""))
        except ValueError:
            parts = str(text or "").split()
    lowered = [part.lower() for part in parts]
    confirm = "--confirm" in lowered or "confirm" in lowered
    action = "dry-run"
    for part in lowered:
        if part in {"dry-run", "dryrun", "plan", "status", "check"}:
            action = "dry-run"
            break
        if part in {"apply", "copy"}:
            action = "apply"
            break
        if part == "move":
            action = "move"
            break
    return action, confirm


def plan_state_migration(
    *,
    source_root: str | Path | None = None,
    home: str | Path | None = None,
    entries: tuple[str, ...] | list[str] | None = None,
) -> MigrationPlan:
    """Build a deterministic migration plan without writing anything."""
    source = Path(source_root or repo_root()).expanduser().resolve(strict=False)
    target_home = Path(home).expanduser().resolve(strict=False) if home else mo_home()
    selected_entries = tuple(entries or LEGACY_STATE_ENTRIES)
    plan = MigrationPlan(source_root=source, home=target_home, entries=selected_entries)

    if _same_path(source, target_home):
        plan.warnings.append("Source root and private home are the same path; no migration is needed.")
        return plan
    if _is_relative_to(target_home, source):
        plan.warnings.append("Private home is inside the migration source; refusing to plan recursive state migration. Choose a private home outside the source or pass an explicit source.")
        return plan

    for entry in selected_entries:
        rel_entry = _clean_entry(entry)
        if not rel_entry:
            continue
        src_entry = source / rel_entry
        if not src_entry.exists():
            plan.missing_entries.append(rel_entry)
            continue
        if src_entry.is_file():
            _plan_file(plan, src_entry, Path(rel_entry))
            continue
        if not src_entry.is_dir():
            plan.warnings.append(f"Skipping non-file state entry: {rel_entry}")
            continue
        _plan_dir(plan, src_entry, Path(rel_entry))

    plan.dirs_to_create = sorted(set(plan.dirs_to_create), key=lambda value: value.replace("\\", "/"))
    plan.files.sort(key=lambda item: item.rel_path.replace("\\", "/"))
    return plan


def apply_state_migration(
    plan: MigrationPlan,
    *,
    confirm: bool = False,
    remove_source: bool = False,
) -> MigrationResult:
    """Apply a migration plan. Requires explicit confirmation."""
    if not confirm:
        raise MigrationApprovalError("State migration apply/move requires --confirm.")

    result = MigrationResult(remove_source=remove_source)
    for rel in plan.dirs_to_create:
        dest_dir = plan.home / rel
        if dest_dir.exists():
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        result.created_dirs.append(rel)

    copied_items: list[MigrationFile] = []
    for item in plan.planned_files:
        if item.dest.exists():
            result.skipped.append(f"{item.rel_path} (destination appeared before copy)")
            continue
        item.dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, item.dest)
        result.copied.append(item.rel_path)
        copied_items.append(item)

    if remove_source:
        for item in copied_items:
            try:
                if item.source.exists() and item.source.is_file() and not _same_path(item.source, item.dest):
                    item.source.unlink()
                    result.removed_sources.append(item.rel_path)
            except Exception as exc:  # pragma: no cover - platform/filesystem dependent
                result.warnings.append(f"Could not remove legacy source {item.rel_path}: {type(exc).__name__}")
        _remove_empty_legacy_dirs(plan, result)
    return result


def render_state_migration_report(plan: MigrationPlan, result: MigrationResult | None = None) -> str:
    """Render a migration plan/result without exposing file contents."""
    applied = result is not None
    mode = "move copied files" if (result and result.remove_source) else "copy missing files"
    title = "MO state migration result:" if applied else "MO state migration dry-run:"
    lines = [
        title,
        f"  source: {plan.source_root}",
        f"  private home: {plan.home}",
        f"  entries: {', '.join(plan.entries)}",
        f"  mode: {mode}",
        f"  planned copies: {len(plan.planned_files)} files",
        f"  existing/same: {len(plan.already_present)} files",
        f"  conflicts: {len(plan.conflicts)} files",
    ]
    if plan.dirs_to_create:
        lines.append(f"  dirs to create: {len(plan.dirs_to_create)}")
    if plan.missing_entries:
        lines.append("  missing legacy entries: " + ", ".join(plan.missing_entries))

    if plan.planned_files:
        lines.append("Planned copies:" if not applied else "Copy plan:")
        for item in plan.planned_files[:12]:
            lines.append(f"- {item.rel_path} ({_format_bytes(item.size)})")
        if len(plan.planned_files) > 12:
            lines.append(f"- ... +{len(plan.planned_files) - 12} more")
    if plan.conflicts:
        lines.append("Conflicts/skipped because destination exists and differs:")
        for item in plan.conflicts[:8]:
            lines.append(f"- {item.rel_path}")
        if len(plan.conflicts) > 8:
            lines.append(f"- ... +{len(plan.conflicts) - 8} more")

    warnings = list(plan.warnings)
    if result:
        lines.extend([
            "Applied:",
            f"- copied: {len(result.copied)} files",
            f"- removed legacy sources: {len(result.removed_sources)} files" if result.remove_source else "- removed legacy sources: 0 files (copy mode)",
            f"- created dirs: {len(result.created_dirs)}",
            f"- skipped at apply-time: {len(result.skipped)}",
        ])
        warnings.extend(result.warnings)
    else:
        lines.extend([
            "No changes made.",
            "To copy missing legacy files: `python mo.py --migrate-state apply --confirm` or `/migrate apply --confirm`.",
            "To move after copying: `python mo.py --migrate-state move --confirm` or `/migrate move --confirm`.",
            "Existing destination files are never overwritten; conflicts require manual review.",
        ])
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)


def _plan_dir(plan: MigrationPlan, src_entry: Path, rel_entry: Path) -> None:
    for current, dirs, files in os.walk(src_entry):
        dirs.sort()
        files.sort()
        current_path = Path(current)
        rel_dir = rel_entry / current_path.relative_to(src_entry)
        rel_dir_text_probe = _rel_text(rel_dir)
        dirs[:] = [name for name in dirs if not _migration_rel_excluded(_rel_text(rel_dir / name), plan)]
        if _migration_rel_excluded(rel_dir_text_probe, plan):
            continue
        rel_dir_text = _rel_text(rel_dir)
        if rel_dir_text != "." and not (plan.home / rel_dir).exists():
            plan.dirs_to_create.append(rel_dir_text)
        for name in files:
            _plan_file(plan, current_path / name, rel_dir / name)


def _plan_file(plan: MigrationPlan, source: Path, rel_path: Path) -> None:
    rel_text = _rel_text(rel_path)
    if _migration_rel_excluded(rel_text, plan):
        return
    dest = plan.home / rel_path
    size = _safe_size(source)
    if _same_path(source, dest):
        plan.files.append(MigrationFile(rel_text, source, dest, "same", "same path", size))
        return
    if dest.exists():
        if dest.is_file() and _same_file(source, dest):
            plan.files.append(MigrationFile(rel_text, source, dest, "same", "same content", size))
        else:
            plan.files.append(MigrationFile(rel_text, source, dest, "conflict", "destination exists", size))
        return
    plan.files.append(MigrationFile(rel_text, source, dest, "copy", "missing destination", size))
    parent = _rel_text(rel_path.parent)
    if parent and parent != "." and not (plan.home / rel_path.parent).exists():
        plan.dirs_to_create.append(parent)


def _remove_empty_legacy_dirs(plan: MigrationPlan, result: MigrationResult) -> None:
    for entry in plan.entries:
        clean = _clean_entry(entry)
        if not clean:
            continue
        src_entry = plan.source_root / clean
        if not src_entry.exists() or not src_entry.is_dir():
            continue
        for current, dirs, _files in os.walk(src_entry, topdown=False):
            dirs.sort()
            current_path = Path(current)
            try:
                current_path.rmdir()
            except OSError:
                continue
            except Exception as exc:  # pragma: no cover - platform/filesystem dependent
                try:
                    rel = _rel_text(current_path.relative_to(plan.source_root))
                except Exception:
                    rel = str(current_path)
                result.warnings.append(f"Could not remove empty legacy directory {rel}: {type(exc).__name__}")


def _clean_entry(entry: str) -> str:
    text = str(entry or "").strip().replace("\\", "/").strip("/")
    if not text or text.startswith("../") or "/../" in text or text == "..":
        return ""
    return text


def _rel_text(path: Path) -> str:
    return str(path).replace("\\", "/")


def _safe_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except Exception:
        return 0


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except Exception:
        return str(left) == str(right)


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except Exception:
        return False


def _migration_rel_excluded(rel_text: str, plan: MigrationPlan) -> bool:
    normalized = str(rel_text or "").replace("\\", "/").strip("/")
    if not normalized or normalized == ".":
        return False
    if normalized in EXCLUDED_REL_PREFIXES or any(normalized.startswith(prefix + "/") for prefix in EXCLUDED_REL_PREFIXES):
        return True
    try:
        candidate = (plan.source_root / normalized).resolve(strict=False)
        if _is_relative_to(candidate, plan.home):
            return True
    except Exception:
        traceback.print_exc()
    return False


def _same_file(left: Path, right: Path) -> bool:
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        with left.open("rb") as lf, right.open("rb") as rf:
            while True:
                lb = lf.read(1024 * 1024)
                rb = rf.read(1024 * 1024)
                if lb != rb:
                    return False
                if not lb:
                    return True
    except Exception:
        return False


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f} {unit}"
    return f"{value / 1024.0:.1f} TB"
