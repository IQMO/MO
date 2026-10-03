"""One-time migration into the canonical private-home layout.

Durable and user-authored state is moved without overwrite or deletion. Only
the two explicitly declared legacy graph caches are retired as rebuildable
artifacts.
"""
# COMPAT(state-home-upgraders): replaced-by explicit --init repair flow; remove-when operator retires the old-home upgrade lane
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..utils.atomic_write import atomic_write_text
from .paths import OBSOLETE_STATE_LAYOUT_MARKERS, STATE_LAYOUT_MARKER_PATH, STATE_LAYOUT_VERSION


LAYOUT_VERSION = STATE_LAYOUT_VERSION
MARKER_PATH = STATE_LAYOUT_MARKER_PATH


@dataclass
class LayoutMigrationResult:
    moved: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.warnings


_PATH_MOVES = (
    ("memory/learning.sqlite", "memory/learning/episodes.sqlite"),
    ("memory/learning_suggestions.jsonl", "memory/learning/suggestions.jsonl"),
    ("memory/workflow_candidates.jsonl", "memory/learning/workflows/candidates.jsonl"),
    ("memory/telegram.sqlite", "memory/surfaces/telegram.sqlite"),
    ("memory/everywhere.sqlite", "memory/surfaces/everywhere.sqlite"),
    ("memory/everywhere-device.sqlite", "memory/surfaces/everywhere-device.sqlite"),
    ("memory/session_closeouts", "memory/sessions/closeouts"),
    ("memory/sessions/active", "memory/sessions/conversations"),
    ("memory/taskboards", "memory/work/taskboards"),
    ("memory/goal-runs", "memory/work/goals"),
    ("memory/review_history", "memory/work/reviews"),
    ("memory/reviews", "memory/work/reviews"),
    ("memory/maintainer", "memory/work/reviews/maintainer"),
    ("memory/visualizations", "memory/archive/legacy-visualizations"),
    ("memory/perception", "memory/archive/retired-perception"),
    ("memory/skill_imports", "memory/learning/imports"),
    ("memory/profile_reconcile", "memory/profile/reconcile"),
    ("memory/exports", "memory/learning/bundles/exports"),
    ("memory/imports", "memory/learning/bundles/imports"),
    ("docs", "memory/archive/legacy-docs"),
    ("tmp", "memory/archive/legacy-tmp"),
    ("scripts", "memory/scheduler/scripts"),
    ("memory/heartbeat", "run/heartbeat"),
    ("runtime/voice", "models/voice"),
    ("memory/traces", "logs/traces"),
    ("memory/dashboard", "cache/dashboard"),
    ("memory/file_operations.jsonl", "logs/file_operations.jsonl"),
    ("memory/mo_desktop_issue_reports", "logs/desktop_issue_reports"),
    ("mo_desktop/attachments", "media/attachments"),
    ("gdesktop/attachments", "media/attachments"),
    ("critique/ANSWER.md", "answer_rules.md"),
)

_CONFIG_REPLACEMENTS = (
    ("memory/telegram.sqlite", "memory/surfaces/telegram.sqlite"),
    ("memory/everywhere.sqlite", "memory/surfaces/everywhere.sqlite"),
    ("memory/everywhere-device.sqlite", "memory/surfaces/everywhere-device.sqlite"),
    ("runtime/voice", "models/voice"),
    ("critique/ANSWER.md", "answer_rules.md"),
)


def migrate_state_layout(home: str | Path, *, config_path: str | Path | None = None, force: bool = False) -> LayoutMigrationResult:
    """Move known MO-owned state without deleting or overwriting user data.

    The marker keeps normal startup O(1). A conflicting source and destination
    aborts the migration and leaves both intact for explicit diagnosis. The
    separately named legacy graph-cache cleanup removes reproducible cache data,
    never durable or user-authored state.
    """
    root = Path(home).expanduser().resolve(strict=False)
    marker = root / MARKER_PATH
    result = LayoutMigrationResult()
    if marker.is_file() and not force:
        return result

    root.mkdir(parents=True, exist_ok=True)
    _migrate_session_files(root, result)
    for source_rel, target_rel in _PATH_MOVES:
        _move_merge(root / source_rel, root / target_rel, source_rel, target_rel, result)
    _move_sqlite_sidecars(root, result)
    _normalize_attachments(root, result)
    _retire_legacy_cache_dirs(root, result)
    _retire_default_identity_template(root, result)
    _remove_known_empty_legacy_dirs(root)
    if config_path:
        _update_default_config_paths(Path(config_path), result)

    if result.complete:
        for old_rel in OBSOLETE_STATE_LAYOUT_MARKERS:
            old_marker = root / old_rel
            try:
                if old_marker.is_file():
                    old_marker.unlink()
                    result.moved.append(f"retired obsolete layout marker {old_rel}")
            except OSError as exc:
                result.warnings.append(f"could not retire {old_rel}: {type(exc).__name__}")
        if not result.complete:
            return result
        marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(marker, f"{LAYOUT_VERSION}\n", encoding="utf-8")
    return result


def _migrate_session_files(root: Path, result: LayoutMigrationResult) -> None:
    sessions = root / "memory" / "sessions"
    if not sessions.is_dir():
        return
    conversations = sessions / "conversations"
    history = sessions / "history" / "handoffs"
    for source in list(sessions.glob("*.json")):
        target_dir = history if "-pre-handoff-" in source.stem else conversations
        _move_merge(source, target_dir / source.name, f"memory/sessions/{source.name}", _rel(root, target_dir / source.name), result)


def _move_sqlite_sidecars(root: Path, result: LayoutMigrationResult) -> None:
    for source_rel, target_rel in _PATH_MOVES:
        if not source_rel.endswith(".sqlite"):
            continue
        for suffix in ("-wal", "-shm"):
            _move_merge(
                root / f"{source_rel}{suffix}",
                root / f"{target_rel}{suffix}",
                f"{source_rel}{suffix}",
                f"{target_rel}{suffix}",
                result,
            )


def _move_merge(source: Path, target: Path, source_rel: str, target_rel: str, result: LayoutMigrationResult) -> None:
    if not source.exists():
        return
    try:
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            for child in list(source.iterdir()):
                _move_merge(child, target / child.name, f"{source_rel}/{child.name}", f"{target_rel}/{child.name}", result)
            try:
                source.rmdir()
            except OSError:
                pass
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if source.read_bytes() == target.read_bytes():
                source.unlink()
                result.moved.append(f"deduplicated {source_rel}")
                return
            normalized_target = target_rel.replace("\\", "/")
            if normalized_target.endswith("media/attachments/index.jsonl"):
                existing = target.read_text(encoding="utf-8", errors="replace").splitlines()
                incoming = source.read_text(encoding="utf-8", errors="replace").splitlines()
                merged = list(dict.fromkeys(line for line in existing + incoming if line.strip()))
                atomic_write_text(target, "\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")
                source.unlink()
                result.moved.append(f"merged {source_rel} -> {target_rel}")
                return
            result.warnings.append(f"kept conflicting paths: {source_rel} and {target_rel}")
            return
        source.replace(target)
        result.moved.append(f"{source_rel} -> {target_rel}")
    except OSError as exc:
        result.warnings.append(f"could not migrate {source_rel}: {type(exc).__name__}")


def _normalize_attachments(root: Path, result: LayoutMigrationResult) -> None:
    attachments = root / "media" / "attachments"
    if not attachments.is_dir():
        return
    _move_merge(
        attachments / "media",
        attachments / "audio-video",
        "media/attachments/media",
        "media/attachments/audio-video",
        result,
    )
    categories = {
        ".bmp": "gallery", ".gif": "gallery", ".jpeg": "gallery", ".jpg": "gallery", ".png": "gallery", ".webp": "gallery",
        ".avi": "audio-video", ".flac": "audio-video", ".m4a": "audio-video", ".mkv": "audio-video", ".mov": "audio-video",
        ".mp3": "audio-video", ".mp4": "audio-video", ".wav": "audio-video", ".webm": "audio-video",
        ".pdf": "documents", ".doc": "documents", ".docx": "documents", ".md": "documents", ".txt": "documents",
        ".csv": "documents", ".tsv": "documents", ".xls": "documents", ".xlsx": "documents", ".ppt": "documents", ".pptx": "documents",
    }
    for source in list(attachments.iterdir()):
        if not source.is_file() or source.name == "index.jsonl":
            continue
        category = categories.get(source.suffix.lower(), "files")
        _move_merge(source, attachments / category / source.name, _rel(root, source), _rel(root, attachments / category / source.name), result)
    _rewrite_attachment_index(attachments / "index.jsonl", root, result)


def _rewrite_attachment_index(path: Path, root: Path, result: LayoutMigrationResult) -> None:
    if not path.is_file():
        return
    try:
        changed = False
        rows: list[str] = []
        old_prefixes = (
            str(root / "mo_desktop" / "attachments"),
            str(root / "gdesktop" / "attachments"),
        )
        new_prefix = str(root / "media" / "attachments")
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                rows.append(raw)
                continue
            saved = str(row.get("saved_path") or "")
            matched_prefix = next(
                (prefix for prefix in old_prefixes if saved.casefold().startswith(prefix.casefold())),
                "",
            )
            if matched_prefix:
                suffix = saved[len(matched_prefix):].lstrip("/\\")
                if suffix.casefold().startswith("media\\") or suffix.casefold().startswith("media/"):
                    suffix = "audio-video" + suffix[5:]
                row["saved_path"] = str(Path(new_prefix) / suffix)
                changed = True
            rows.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        if changed:
            atomic_write_text(path, "\n".join(rows) + "\n", encoding="utf-8")
            result.moved.append("updated media/attachments/index.jsonl paths")
    except OSError as exc:
        result.warnings.append(f"could not update attachment index: {type(exc).__name__}")


def _update_default_config_paths(path: Path, result: LayoutMigrationResult) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
        updated = text
        for old, new in _CONFIG_REPLACEMENTS:
            updated = updated.replace(old, new)
        state_paths_changed = updated != text
        updated, retired_face_config = _remove_retired_face_candidate_config(updated)
        if updated != text:
            atomic_write_text(path, updated, encoding="utf-8")
        if state_paths_changed:
            result.moved.append("updated config default state paths")
        if retired_face_config:
            result.moved.append("retired removed face-candidate config")
    except OSError as exc:
        result.warnings.append(f"could not update config paths: {type(exc).__name__}")


def _remove_retired_face_candidate_config(text: str) -> tuple[str, bool]:
    """Remove the retired top-level perception.face_candidates block safely."""
    lines = text.splitlines(keepends=True)
    for start, line in enumerate(lines):
        if line.rstrip("\r\n") != "perception:":
            continue
        end = start + 1
        while end < len(lines):
            stripped = lines[end].strip()
            if stripped and not lines[end].startswith((" ", "\t", "#")):
                break
            end += 1

        significant = [
            index for index in range(start + 1, end)
            if lines[index].strip() and not lines[index].lstrip().startswith("#")
        ]
        face_start = next((index for index in significant if lines[index].strip() == "face_candidates:"), None)
        if face_start is None:
            continue
        face_indent = len(lines[face_start]) - len(lines[face_start].lstrip())
        if face_indent <= 0 or any(
            len(lines[index]) - len(lines[index].lstrip()) < face_indent for index in significant
        ):
            continue

        face_end = face_start + 1
        while face_end < end:
            item = lines[face_end]
            if item.strip() and len(item) - len(item.lstrip()) <= face_indent:
                break
            face_end += 1
        remaining = lines[start + 1:face_start] + lines[face_end:end]
        has_sibling = any(item.strip() and not item.lstrip().startswith("#") for item in remaining)
        if has_sibling:
            return "".join(lines[:face_start] + lines[face_end:]), True
        return "".join(lines[:start] + remaining + lines[end:]), True
    return text, False


def _remove_known_empty_legacy_dirs(root: Path) -> None:
    for rel in (
        "mo_desktop", "gdesktop", "critique", "memory/runtime", "memory/pre_release_evidence", "memory/heartbeat",
        "memory/dashboard", "memory/traces", "memory/session_closeouts", "memory/taskboards",
        "memory/goal-runs", "memory/review_history", "memory/skill_imports", "memory/profile_reconcile",
        "memory/exports", "memory/imports", "runtime",
    ):
        path = root / rel
        try:
            path.rmdir()
        except OSError:
            pass


def _retire_legacy_cache_dirs(root: Path, result: LayoutMigrationResult) -> None:
    """Delete only obsolete, reproducible graph caches from private state."""
    for rel in ("memory/code_graph", "memory/structural_graph", "cache/code_graph"):
        path = root / rel
        if not path.exists():
            continue
        try:
            shutil.rmtree(path)
            result.moved.append(f"retired obsolete cache {rel}")
        except OSError as exc:
            result.warnings.append(f"could not retire obsolete cache {rel}: {type(exc).__name__}")


def _retire_default_identity_template(root: Path, result: LayoutMigrationResult) -> None:
    """Remove only the exact obsolete product-identity template, never custom prose."""
    path = root / "memory" / "profile" / "identity.md"
    if not path.is_file():
        return
    try:
        normalized = "\n".join(line.rstrip() for line in path.read_text(encoding="utf-8").strip().splitlines())
        if normalized == "# MO Identity\n\nYou are MO. Backend models are runtime providers, not identity.":
            path.unlink()
            result.moved.append("retired default memory/profile/identity.md")
    except OSError:
        return


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
