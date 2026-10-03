"""Cross-surface private attachment catalog.

The catalog owns type routing, safe names, the canonical state-root home, and
the counts-only provenance index. Surface-specific intake and authorization
remain with the surface that receives a file.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.runtime.lock import file_byte_lock
from core.state.paths import MEDIA_ATTACHMENTS_DIR, resolve_state_path


IMAGE_ATTACHMENT_SUFFIXES = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"})
MEDIA_ATTACHMENT_SUFFIXES = frozenset(
    {".avi", ".flac", ".m4a", ".mkv", ".mov", ".mp3", ".mp4", ".wav", ".webm"}
)
DOCUMENT_ATTACHMENT_SUFFIXES = frozenset({
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".csv", ".tsv",
    ".xls", ".xlsx", ".ppt", ".pptx", ".json", ".yaml", ".yml",
})
ATTACHMENT_CATEGORIES = ("gallery", "audio-video", "documents", "files")
ATTACHMENT_INDEX_NAME = "index.jsonl"
MAX_ATTACHMENT_NAME_CHARS = 180
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS_PER_TURN = 8
_ATTACHMENT_INDEX_THREAD_LOCK = threading.Lock()


@contextmanager
def _attachment_index_lock(config: dict[str, Any] | None):
    """Serialize catalog index transactions across local threads and processes."""
    lock_path = attachment_home(config) / f"{ATTACHMENT_INDEX_NAME}.lock"
    with file_byte_lock(lock_path, _ATTACHMENT_INDEX_THREAD_LOCK):
        yield


def _attachment_home_path(config: dict[str, Any] | None = None) -> Path:
    return Path(resolve_state_path(MEDIA_ATTACHMENTS_DIR, config or {})).expanduser().resolve(
        strict=False
    )


def attachment_home(config: dict[str, Any] | None = None) -> Path:
    """Return the one canonical private attachment home, created lazily."""
    root = _attachment_home_path(config)
    root.mkdir(parents=True, exist_ok=True)
    return root


def attachment_category(path: Any) -> str:
    """Route a file into the shared content category using its suffix."""
    suffix = str(getattr(path, "suffix", "") or "").lower() or Path(str(path)).suffix.lower()
    if suffix in IMAGE_ATTACHMENT_SUFFIXES:
        return "gallery"
    if suffix in MEDIA_ATTACHMENT_SUFFIXES:
        return "audio-video"
    if suffix in DOCUMENT_ATTACHMENT_SUFFIXES:
        return "documents"
    return "files"


def attachment_category_dir(config: dict[str, Any] | None, path: Any) -> Path:
    dest = attachment_home(config) / attachment_category(path)
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def attachment_index(config: dict[str, Any] | None = None) -> Path:
    return attachment_home(config) / ATTACHMENT_INDEX_NAME


def safe_attachment_name(value: Any) -> str:
    """Return a bounded display filename with no path or control semantics."""
    raw = str(value or "").strip()
    clean = "".join(
        "_" if ch in "/\\" or unicodedata.category(ch).startswith("C") else ch
        for ch in raw
    ).strip(" .")
    if not clean or clean in {".", ".."}:
        clean = "attachment"
    if len(clean) > MAX_ATTACHMENT_NAME_CHARS:
        suffix = Path(clean).suffix[:24]
        keep = max(1, MAX_ATTACHMENT_NAME_CHARS - len(suffix))
        clean = clean[:keep].rstrip(" .") + suffix
    return clean or "attachment"


def unique_attachment_path(config: dict[str, Any] | None, name: Any) -> Path:
    """Reserve-by-convention a non-existing categorized path for a safe name."""
    clean = safe_attachment_name(name)
    folder = attachment_category_dir(config, clean)
    candidate = folder / clean
    stem, suffix = candidate.stem, candidate.suffix
    index = 1
    while candidate.exists():
        candidate = folder / f"{stem}-{index}{suffix}"
        index += 1
    return candidate


def import_attachment(
    config: dict[str, Any] | None,
    source_path: Any,
    *,
    session_id: str = "",
    turn_count: int = 0,
    origin: str = "desktop_drop",
    attachment_id: str = "",
    max_bytes: int | None = None,
    allow_empty: bool = True,
) -> dict[str, Any]:
    """Copy one external file atomically into the canonical private catalog.

    The source is measured before and after the staged copy, the destination is
    exclusively reserved, and only this transaction's own files are cleaned on
    failure. Stable IDs are idempotent when their original catalog file remains
    valid.
    """
    stable_id = str(attachment_id or "").strip()
    try:
        source = Path(source_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("attachment source is unavailable") from None
    if not source.is_file():
        raise ValueError("attachment source must be a file")
    try:
        source_size = int(source.stat().st_size)
    except OSError:
        raise ValueError("attachment source is unavailable") from None
    if source_size < 1 and not allow_empty:
        raise ValueError("attachment is empty")
    if max_bytes is not None and source_size > max(0, int(max_bytes)):
        raise ValueError("attachment exceeds the size limit")
    if stable_id:
        existing = find_attachment(config, stable_id)
        if existing is not None:
            try:
                existing_source = Path(str(existing.get("source_path") or "")).resolve(
                    strict=False
                )
            except (OSError, RuntimeError):
                existing_source = Path()
            if (
                existing_source == source
                and int(existing.get("bytes") or -1) == source_size
            ):
                return existing
            raise ValueError("attachment id already belongs to a different source")

    folder = attachment_category_dir(config, source.name)
    clean = safe_attachment_name(source.name)
    base = folder / clean
    stem, suffix = base.stem, base.suffix
    destination: Path | None = None
    staged: Path | None = None
    index = 0
    while destination is None:
        candidate = base if index == 0 else folder / f"{stem}-{index}{suffix}"
        try:
            candidate.open("xb").close()
            destination = candidate
        except FileExistsError:
            index += 1
    try:
        staged = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
        shutil.copy2(source, staged)
        source_after = int(source.stat().st_size)
        staged_size = int(staged.stat().st_size)
        if source_after != source_size or staged_size != source_size:
            raise ValueError("attachment byte count changed during import")
        os.replace(staged, destination)
        staged = None
        try:
            return record_attachment(
                config,
                destination,
                source_path=source,
                session_id=session_id,
                turn_count=turn_count,
                origin=origin,
                attachment_id=stable_id,
            )
        except ValueError:
            # Two intake threads may both pass the optimistic ID lookup before
            # either catalogs its copy. Treat the loser as the same idempotent
            # request only when the winning row names this exact source and byte
            # count; remove only our exclusively reserved duplicate.
            existing = find_attachment(config, stable_id) if stable_id else None
            try:
                existing_source = Path(str((existing or {}).get("source_path") or "")).resolve(
                    strict=False
                )
            except (OSError, RuntimeError):
                existing_source = Path()
            if (
                existing is None
                or existing_source != source
                or int(existing.get("bytes") or -1) != source_size
            ):
                raise
            destination.unlink(missing_ok=True)
            destination = None
            return existing
    except Exception:
        if staged is not None:
            staged.unlink(missing_ok=True)
        if destination is not None:
            destination.unlink(missing_ok=True)
        raise


def find_attachment(
    config: dict[str, Any] | None,
    attachment_id: str,
) -> dict[str, Any] | None:
    """Resolve one opaque catalog ID to a still-valid canonical private file."""
    target_id = str(attachment_id or "").strip()
    if not target_id:
        return None
    home = attachment_home(config).resolve(strict=False)
    index = attachment_index(config)
    found: dict[str, Any] | None = None
    with _attachment_index_lock(config):
        try:
            with index.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(row, dict) and str(row.get("id") or "") == target_id:
                        found = dict(row)
                        break
        except FileNotFoundError:
            return None
    if found is None:
        return None
    try:
        saved = Path(str(found.get("saved_path") or "")).expanduser().resolve(strict=True)
        saved.relative_to(home)
        size = int(saved.stat().st_size)
    except (OSError, RuntimeError, ValueError):
        return None
    recorded_size = found.get("bytes")
    if not isinstance(recorded_size, int) or isinstance(recorded_size, bool) or recorded_size != size:
        return None
    if not saved.is_file():
        return None
    found["saved_path"] = str(saved)
    found["name"] = safe_attachment_name(found.get("name") or saved.name)
    found["category"] = attachment_category(saved)
    return found


def record_attachment(
    config: dict[str, Any] | None,
    saved_path: Any,
    *,
    source_path: Any = "",
    session_id: str = "",
    turn_count: int = 0,
    origin: str = "desktop_drop",
    attachment_id: str = "",
) -> dict[str, Any]:
    """Append private provenance for a durable attachment."""
    saved = Path(saved_path).expanduser().resolve(strict=False)
    source = Path(source_path).expanduser().resolve(strict=False) if str(source_path or "").strip() else None
    stable_id = str(attachment_id or "").strip()
    index = attachment_index(config)
    with _attachment_index_lock(config):
        if stable_id:
            try:
                with index.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            existing = json.loads(line)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if str(existing.get("id") or "") != stable_id:
                            continue
                        if Path(str(existing.get("saved_path") or "")).resolve(
                            strict=False
                        ) != saved:
                            raise ValueError(
                                "attachment id already belongs to a different file"
                            )
                        return existing
            except FileNotFoundError:
                pass
        try:
            size = int(saved.stat().st_size)
        except OSError:
            size = 0
        record = {
            "id": stable_id or uuid.uuid4().hex,
            "attached_at": time.time(),
            "origin": str(origin or "desktop_drop"),
            "category": attachment_category(saved),
            "name": saved.name,
            "saved_path": str(saved),
            "source_path": str(source) if source is not None else "",
            "bytes": size,
            "session_id": str(session_id or ""),
            "turn_count": max(0, int(turn_count or 0)),
            "summary": "",
        }
        with index.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
        return record


def attachment_summary(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return counts only; never expose indexed paths or content."""
    root = _attachment_home_path(config)
    categories = dict.fromkeys(ATTACHMENT_CATEGORIES, 0)
    total_bytes = 0
    for category in categories:
        folder = root / category
        try:
            files = [item for item in folder.iterdir() if item.is_file()]
        except OSError:
            files = []
        categories[category] = len(files)
        for item in files:
            try:
                total_bytes += int(item.stat().st_size)
            except OSError:
                pass
    indexed = 0
    try:
        with (root / ATTACHMENT_INDEX_NAME).open("r", encoding="utf-8", errors="replace") as handle:
            indexed = sum(1 for line in handle if line.strip())
    except OSError:
        pass
    return {
        "total": sum(categories.values()),
        "indexed": indexed,
        "bytes": total_bytes,
        "categories": categories,
    }
