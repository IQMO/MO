"""One fail-closed file boundary shared by the MO Files surfaces.

Public callers address files with an opaque location ID and a POSIX-style
relative path. Absolute roots, raw MO state, credentials, symlinks, and hidden
control files never cross this boundary.
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import hashlib
import base64
import json
import os
import shutil
import stat
import string
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from core.state.attachments import ATTACHMENT_INDEX_NAME
from core.state.paths import (
    default_project_roots,
    mo_home,
    resolve_state_path,
)
from core.utils.atomic_write import atomic_write_json, atomic_write_text
from core.utils.file_hash import file_sha256


MO_HOME_LOCATION_ID = "mo-home"

# One vocabulary for the whole MO Files contract.
#
# Producers build from these and every consumer validates against them. They
# are defined once because the validators fail closed on the entire response:
# a value that one side knows and the other does not does not degrade a single
# row, it discards every source or location in the payload.
FILE_OPERATIONS = frozenset(
    {
        "list",
        "read",
        "preview",
        "edit_text",
        "create_folder",
        "rename",
        "copy",
        "move",
        "delete",
        "trash",
        "restore",
        "send",
    }
)
# Location kinds. The phone kinds come from a consented phone serving this same
# contract from its own storage.
FILE_LOCATION_KINDS = frozenset(
    {
        "mo_home",
        "drive",
        "project",
        "personal",
        "phone_storage",
    }
)
# Source kinds the hub projects for an online file authority.
FILE_SOURCE_KINDS = frozenset(
    {"hub", "desktop", "terminal", "phone", "machine"}
)


def valid_file_capability_descriptor(
    *,
    label: str,
    kind: str,
    availability: Any,
    operations: Any,
    allowed_kinds: frozenset[str],
) -> bool:
    """Validate the fields shared by MO Files source and location descriptors."""
    if (
        not 1 <= len(label) <= 80
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in label)
        or kind not in allowed_kinds
        or type(availability) is not bool
        or not isinstance(operations, list)
        or len(operations) > len(FILE_OPERATIONS)
        or any(not isinstance(item, str) for item in operations)
    ):
        return False
    return (
        len(set(operations)) == len(operations)
        and all(item in FILE_OPERATIONS for item in operations)
    )


MAX_ENTRIES = 500
MAX_TEXT_BYTES = 1024 * 1024
MAX_COPY_ENTRIES = 20_000
MAX_COPY_BYTES = 2 * 1024 * 1024 * 1024
MAX_TRASH_ENTRIES = 500
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
TRASH_MANIFEST_NAME = ".mo-files-trash.json"
PREVIEW_SUFFIXES = {
    ".bmp": ("image", "image/bmp"),
    ".gif": ("image", "image/gif"),
    ".heic": ("image", "image/heic"),
    ".heif": ("image", "image/heif"),
    ".jpeg": ("image", "image/jpeg"),
    ".jpg": ("image", "image/jpeg"),
    ".pdf": ("pdf", "application/pdf"),
    ".png": ("image", "image/png"),
    ".webp": ("image", "image/webp"),
}
TEXT_SUFFIXES = frozenset(
    {
        ".c",
        ".cfg",
        ".conf",
        ".cpp",
        ".css",
        ".csv",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".kt",
        ".kts",
        ".md",
        ".properties",
        ".py",
        ".rs",
        ".sh",
        ".sql",
        ".toml",
        ".tsv",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
_RESERVED_COMPONENTS = frozenset(
    {
        ".git",
        ".hg",
        ".mo",
        ".svn",
        "__pycache__",
        "credentials",
        "node_modules",
    }
)
_SECRET_NAMES = frozenset(
    {
        ".env",
        "authorized_keys",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
        "service-account.json",
    }
)


class FileBoundaryError(RuntimeError):
    """Safe MO Files boundary error."""


class FileBoundaryNotFound(FileBoundaryError):
    pass


class FileBoundaryConflict(FileBoundaryError):
    pass


class FileBoundaryLimit(FileBoundaryError):
    pass


@dataclass(frozen=True)
class FileLocation:
    location_id: str
    label: str
    kind: str
    root: Path
    writable: bool

    def public(self) -> dict[str, Any]:
        operations = ["list", "read", "preview", "copy", "send"]
        if self.writable:
            operations.append("edit_text")
            operations.extend(
                ["create_folder", "rename", "move", "delete", "trash", "restore"]
            )
        return {
            "location_id": self.location_id,
            "label": self.label,
            "kind": self.kind,
            "writable": self.writable,
            "operations": operations,
        }


class FileManagerService:
    """Resolve and mutate only the configured MO Files locations."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        block = self.config.get("file_manager")
        self.settings = block if isinstance(block, dict) else {}
        self.enabled = bool(self.settings.get("enabled", DEFAULT_PREFERENCES["file_manager.enabled"]))
        self.max_entries = _bounded_int(
            self.settings.get("max_entries"), default=MAX_ENTRIES, low=20, high=2000
        )
        self.max_text_bytes = _bounded_int(
            self.settings.get("max_text_bytes"),
            default=MAX_TEXT_BYTES,
            low=4096,
            high=4 * MAX_TEXT_BYTES,
        )
        self.max_copy_entries = _bounded_int(
            self.settings.get("max_copy_entries"),
            default=MAX_COPY_ENTRIES,
            low=100,
            high=100_000,
        )
        self.max_copy_bytes = _bounded_int(
            self.settings.get("max_copy_bytes"),
            default=MAX_COPY_BYTES,
            low=1024 * 1024,
            high=16 * MAX_COPY_BYTES,
        )
        self._locations = self._build_locations()

    def locations(self) -> list[dict[str, Any]]:
        self._require_enabled()
        return [item.public() for item in self._locations.values()]

    def list(self, location_id: Any, relative_path: Any = "", *, limit: Any = None) -> dict[str, Any]:
        location = self._location(location_id)
        directory = self._existing(location, relative_path, expect_directory=True)
        clean_relative = self._relative(relative_path)
        bounded_limit = _bounded_int(limit, default=self.max_entries, low=1, high=self.max_entries)
        entries: list[dict[str, Any]] = []
        try:
            children = sorted(
                directory.iterdir(),
                key=lambda child: (not child.is_dir(), child.name.casefold()),
            )
        except OSError as exc:
            raise FileBoundaryError("location cannot be read") from exc
        for child in children:
            if len(entries) >= bounded_limit:
                break
            if not self._visible_child(location, child):
                continue
            try:
                entries.append(self._entry(location, child))
            except (FileBoundaryError, OSError):
                # A full-access drive can contain protected system entries.
                # One unreadable child must not make the entire location fail.
                continue
        return {
            "location": location.public(),
            "path": clean_relative,
            "entries": entries,
            "truncated": len(entries) >= bounded_limit and len(children) > len(entries),
        }

    def read_text(self, location_id: Any, relative_path: Any) -> dict[str, Any]:
        location = self._location(location_id)
        path = self._existing(location, relative_path, expect_file=True)
        self._require_text(location, path)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise FileBoundaryError("file cannot be read") from exc
        if len(raw) > self.max_text_bytes:
            raise FileBoundaryLimit("text file is too large")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise FileBoundaryError("file is not UTF-8 text") from None
        return {
            "location_id": location.location_id,
            "path": self._relative(relative_path),
            "text": text,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }

    def preview(self, location_id: Any, relative_path: Any) -> dict[str, Any]:
        """Return one bounded, inert image/PDF payload for local native rendering."""
        location = self._location(location_id)
        path = self._existing(location, relative_path, expect_file=True)
        preview = PREVIEW_SUFFIXES.get(path.suffix.casefold())
        if preview is None:
            raise FileBoundaryError("native preview is unavailable for this file type")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise FileBoundaryError("file cannot be read") from exc
        if size < 1 or size > MAX_PREVIEW_BYTES:
            raise FileBoundaryLimit("preview file is too large")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise FileBoundaryError("file cannot be read") from exc
        if len(raw) != size or len(raw) > MAX_PREVIEW_BYTES:
            raise FileBoundaryConflict("file changed while previewing")
        kind, mime_type = preview
        return {
            "location_id": location.location_id,
            "path": self._relative(relative_path),
            "name": path.name,
            "kind": kind,
            "mime_type": mime_type,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "payload_base64": base64.b64encode(raw).decode("ascii"),
        }

    def write_text(
        self,
        location_id: Any,
        relative_path: Any,
        text: Any,
        *,
        expected_sha256: Any,
    ) -> dict[str, Any]:
        location = self._writable_location(location_id)
        path = self._existing(location, relative_path, expect_file=True)
        self._require_text(location, path)
        expected = _digest(expected_sha256)
        if not isinstance(text, str):
            raise FileBoundaryError("file text must be a string")
        value = text
        raw = value.encode("utf-8")
        if len(raw) > self.max_text_bytes:
            raise FileBoundaryLimit("text file is too large")
        current = _file_digest(path)
        if current != expected:
            raise FileBoundaryConflict("file changed since it was opened")
        try:
            atomic_write_text(path, value)
        except OSError as exc:
            raise FileBoundaryError("file could not be saved") from exc
        return self.read_text(location.location_id, relative_path)

    def create_folder(
        self,
        location_id: Any,
        parent_path: Any,
        name: Any,
    ) -> dict[str, Any]:
        location = self._writable_location(location_id)
        parent = self._existing(location, parent_path, expect_directory=True)
        target = parent / self._name(name)
        self._guard_destination(location, target)
        try:
            target.mkdir()
        except OSError as exc:
            raise FileBoundaryError("folder could not be created") from exc
        return self._entry(location, target)

    def import_stream(
        self,
        location_id: Any,
        parent_path: Any,
        name: Any,
        stream: Any,
        size: int,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Install one bounded external file into an allowed writable folder."""
        location = self._writable_location(location_id)
        parent = self._existing(location, parent_path, expect_directory=True)
        target = parent / self._name(name)
        self._guard_destination(location, target)
        if not isinstance(size, int) or size < 0 or size > self.max_copy_bytes:
            raise FileBoundaryLimit("incoming file is too large")
        temporary = parent / f".{target.name}.{uuid.uuid4().hex}.incoming"
        try:
            with temporary.open("xb") as output:
                remaining = size
                while remaining:
                    chunk = stream.read(min(256 * 1024, remaining))
                    if not chunk or len(chunk) > remaining:
                        raise FileBoundaryError("incoming file was interrupted")
                    output.write(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            if cancelled is not None and cancelled():
                raise FileBoundaryError("incoming transfer ended")
            # A hard link publishes the finished file only if the name remains
            # free. It cannot replace a file created after the first guard.
            os.link(temporary, target)
        except FileExistsError as exc:
            raise FileBoundaryConflict("destination already exists") from exc
        except FileBoundaryError:
            raise
        except OSError as exc:
            raise FileBoundaryError("incoming file could not be saved") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return self._entry(location, target)

    def rename(
        self,
        location_id: Any,
        relative_path: Any,
        new_name: Any,
        *,
        expected_sha256: Any = "",
    ) -> dict[str, Any]:
        location = self._writable_location(location_id)
        source = self._existing_item(location, relative_path)
        name = self._name(new_name)
        target = source.with_name(name)
        self._guard_destination(location, target)
        self._verify_revision(source, expected_sha256)
        try:
            source.rename(target)
        except OSError as exc:
            raise FileBoundaryError("file could not be renamed") from exc
        return self._entry(location, target)

    def move(
        self,
        source_location_id: Any,
        relative_path: Any,
        target_location_id: Any,
        target_directory: Any = "",
        *,
        expected_sha256: Any = "",
    ) -> dict[str, Any]:
        source_location = self._writable_location(source_location_id)
        target_location = self._writable_location(target_location_id)
        source = self._existing_item(source_location, relative_path)
        destination_dir = self._existing(
            target_location, target_directory, expect_directory=True
        )
        target = destination_dir / source.name
        self._guard_destination(target_location, target)
        self._guard_tree_destination(source, target)
        self._verify_revision(source, expected_sha256)
        try:
            if source_location.location_id == target_location.location_id:
                source.rename(target)
            else:
                self._copy_verified(source, target)
                try:
                    self._trash(source_location, source)
                except Exception:
                    self._remove_created_copy(target)
                    raise
        except FileBoundaryError:
            raise
        except OSError as exc:
            raise FileBoundaryError("file could not be moved") from exc
        return self._entry(target_location, target)

    def copy(
        self,
        source_location_id: Any,
        relative_path: Any,
        target_location_id: Any,
        target_directory: Any = "",
        *,
        expected_sha256: Any = "",
    ) -> dict[str, Any]:
        source_location = self._location(source_location_id)
        target_location = self._writable_location(target_location_id)
        source = self._existing_item(source_location, relative_path)
        destination_dir = self._existing(
            target_location, target_directory, expect_directory=True
        )
        target = destination_dir / source.name
        self._guard_destination(target_location, target)
        self._guard_tree_destination(source, target)
        self._verify_revision(source, expected_sha256)
        self._copy_verified(source, target)
        return self._entry(target_location, target)

    def delete(
        self,
        location_id: Any,
        relative_path: Any,
        *,
        expected_sha256: Any = "",
    ) -> dict[str, Any]:
        location = self._writable_location(location_id)
        source = self._existing_item(location, relative_path)
        self._verify_revision(source, expected_sha256)
        trash_id = self._trash(location, source)
        return {"deleted": True, "trash_id": trash_id}

    def trash(self, *, limit: Any = None) -> dict[str, Any]:
        """List restorable entries without exposing the private trash path."""
        self._require_enabled()
        bounded_limit = _bounded_int(
            limit,
            default=min(self.max_entries, MAX_TRASH_ENTRIES),
            low=1,
            high=MAX_TRASH_ENTRIES,
        )
        root = self._trash_root()
        try:
            dated_candidates: list[tuple[float, Path]] = []
            if root.is_dir():
                for candidate in root.iterdir():
                    try:
                        dated_candidates.append((candidate.stat().st_mtime, candidate))
                    except OSError:
                        continue
            candidates = [
                candidate
                for _modified, candidate in sorted(
                    dated_candidates, key=lambda item: item[0], reverse=True
                )
            ]
        except OSError as exc:
            raise FileBoundaryError("MO Files Trash cannot be read") from exc
        items: list[dict[str, Any]] = []
        restorable_count = 0
        for candidate in candidates:
            try:
                item = self._trash_item(candidate)
            except (FileBoundaryError, OSError):
                # Preserve unmanifested/incomplete entries, but never guess their
                # original destination and present them as restorable.
                continue
            restorable_count += 1
            if len(items) < bounded_limit:
                items.append(item)
        return {"items": items, "truncated": restorable_count > len(items)}

    def restore(self, trash_id: Any) -> dict[str, Any]:
        """Restore one manifest-backed item to its exact original location."""
        clean_id = _trash_id(trash_id)
        item_dir = self._trash_root() / clean_id
        item = self._trash_item(item_dir)
        location = self._writable_location(item["location_id"])
        relative = self._relative(item["original_path"])
        relative_path = PurePosixPath(relative)
        parent_relative = (
            "" if len(relative_path.parts) == 1 else relative_path.parent.as_posix()
        )
        parent = self._existing(location, parent_relative, expect_directory=True)
        target = parent / relative_path.name
        self._guard_destination(location, target)
        source = item_dir / item["name"]
        try:
            source.rename(target)
        except OSError:
            self._copy_verified(source, target)
            try:
                if source.is_dir():
                    shutil.rmtree(source)
                else:
                    source.unlink()
            except OSError:
                # The verified destination is now the complete restored copy.
                # Preserve it even if private Trash cleanup is interrupted.
                pass
        try:
            shutil.rmtree(item_dir)
        except OSError:
            try:
                (item_dir / TRASH_MANIFEST_NAME).unlink(missing_ok=True)
            except OSError:
                pass
        return {"item": self._entry(location, target)}

    def source_path(self, location_id: Any, relative_path: Any) -> Path:
        """Return a guarded local source for the existing transfer custody owner."""
        return self._existing(self._location(location_id), relative_path, expect_file=True)

    def _build_locations(self) -> dict[str, FileLocation]:
        if not self.enabled:
            return {}
        locations: list[FileLocation] = []
        state_home = mo_home(self.config)
        # MO's own runtime home is one browsable root: received attachments and
        # everything else MO keeps live inside it, so it needs no separate
        # curated views. This is the default landing location for an MO host.
        locations.append(
            FileLocation(
                MO_HOME_LOCATION_ID,
                "MO home",
                "mo_home",
                Path(state_home),
                True,
            )
        )
        access = self.config.get("access")
        access = access if isinstance(access, dict) else {}
        if str(access.get("mode") or "").strip().casefold() == "full":
            for label, root in _full_access_roots():
                locations.append(
                    FileLocation(
                        _location_id("drive", root),
                        label,
                        "drive",
                        root,
                        True,
                    )
                )
        else:
            for root_text in default_project_roots(self.config):
                root = Path(root_text).expanduser().resolve(strict=False)
                if root.is_dir():
                    locations.append(
                        FileLocation(
                            _location_id("project", root),
                            root.name or "Project",
                            "project",
                            root,
                            True,
                        )
                    )
        personal = self.settings.get("personal_roots")
        if isinstance(personal, list):
            for item in personal:
                label = ""
                value: Any = item
                if isinstance(item, dict):
                    value = item.get("path")
                    label = " ".join(str(item.get("label") or "").split())[:80]
                root = Path(str(value or "")).expanduser().resolve(strict=False)
                if root.is_dir():
                    locations.append(
                        FileLocation(
                            _location_id("personal", root),
                            label or root.name or "Personal",
                            "personal",
                            root,
                            True,
                        )
                    )
        return {item.location_id: item for item in locations}

    def _location(self, location_id: Any) -> FileLocation:
        self._require_enabled()
        clean = str(location_id or "").strip()
        location = self._locations.get(clean)
        if location is None:
            raise FileBoundaryNotFound("file location was not found")
        return location

    def _writable_location(self, location_id: Any) -> FileLocation:
        location = self._location(location_id)
        if not location.writable:
            raise FileBoundaryError("file location is read-only")
        return location

    def _existing(
        self,
        location: FileLocation,
        relative_path: Any,
        *,
        expect_file: bool = False,
        expect_directory: bool = False,
    ) -> Path:
        clean = self._relative(relative_path)
        path = location.root if not clean else location.root.joinpath(*PurePosixPath(clean).parts)
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise FileBoundaryNotFound("file item was not found") from None
        self._inside(location, resolved)
        current = location.root
        for part in (() if not clean else PurePosixPath(clean).parts):
            current = current / part
            try:
                if stat.S_ISLNK(current.lstat().st_mode):
                    raise FileBoundaryError("symbolic links are not available in MO Files")
            except FileNotFoundError:
                raise FileBoundaryNotFound("file item was not found") from None
        if expect_file and not resolved.is_file():
            raise FileBoundaryNotFound("file was not found")
        if expect_directory and not resolved.is_dir():
            raise FileBoundaryNotFound("folder was not found")
        return resolved

    def _existing_item(self, location: FileLocation, relative_path: Any) -> Path:
        if not self._relative(relative_path):
            raise FileBoundaryError("a file or folder path is required")
        return self._existing(location, relative_path)

    def _inside(self, location: FileLocation, path: Path) -> None:
        try:
            if os.path.commonpath((str(location.root.resolve()), str(path))) != str(
                location.root.resolve()
            ):
                raise FileBoundaryError("file path escapes its location")
        except ValueError:
            raise FileBoundaryError("file path escapes its location") from None

    def _guard_destination(self, location: FileLocation, target: Path) -> None:
        self._inside(location, target.resolve(strict=False))
        self._name(target.name)
        if target.exists():
            raise FileBoundaryConflict("destination already exists")
        if not target.parent.is_dir():
            raise FileBoundaryNotFound("destination folder was not found")

    @staticmethod
    def _guard_tree_destination(source: Path, target: Path) -> None:
        if not source.is_dir():
            return
        try:
            target.resolve(strict=False).relative_to(source.resolve(strict=True))
        except ValueError:
            return
        raise FileBoundaryError("a folder cannot be copied or moved inside itself")

    def _entry(self, location: FileLocation, path: Path) -> dict[str, Any]:
        self._inside(location, path.resolve(strict=True))
        relative = path.relative_to(location.root).as_posix()
        info = path.stat()
        is_dir = path.is_dir()
        editable = (
            not is_dir
            and path.suffix.casefold() in TEXT_SUFFIXES
            and info.st_size <= self.max_text_bytes
        )
        return {
            "name": path.name,
            "path": relative,
            "kind": "folder" if is_dir else "file",
            "bytes": None if is_dir else info.st_size,
            "modified_at": info.st_mtime,
            "editable": editable,
            "sha256": None if is_dir else _file_digest(path),
        }

    def _visible_child(self, location: FileLocation, child: Path) -> bool:
        if child.is_symlink():
            return False
        if location.kind == "mo_home" and child.name in {
            ATTACHMENT_INDEX_NAME,
            f"{ATTACHMENT_INDEX_NAME}.lock",
        }:
            return False
        try:
            self._name(child.name)
        except FileBoundaryError:
            return False
        return True

    def _relative(self, value: Any) -> str:
        raw = str(value or "").strip().replace("\\", "/")
        if not raw:
            return ""
        path = PurePosixPath(raw)
        if path.is_absolute() or ":" in raw:
            raise FileBoundaryError("file path must be relative")
        parts = path.parts
        if any(part in {"", ".", ".."} for part in parts):
            raise FileBoundaryError("file path is invalid")
        for part in parts:
            self._name(part)
        return path.as_posix()

    def _name(self, value: Any) -> str:
        name = str(value or "").strip()
        lowered = name.casefold()
        if (
            not name
            or len(name) > 180
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "\0" in name
            or name.startswith(".")
            or lowered in _RESERVED_COMPONENTS
            or lowered in _SECRET_NAMES
            or lowered.endswith((".pem", ".key", ".p12", ".pfx"))
        ):
            raise FileBoundaryError("file name is not available in MO Files")
        return name

    def _require_text(self, location: FileLocation, path: Path) -> None:
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            raise FileBoundaryError("file is not an editable text type")
        if path.stat().st_size > self.max_text_bytes:
            raise FileBoundaryLimit("text file is too large")

    def _verify_revision(self, path: Path, expected_sha256: Any) -> None:
        if path.is_dir():
            if str(expected_sha256 or "").strip():
                raise FileBoundaryError("folder revisions do not use a digest")
            return
        expected = _digest(expected_sha256)
        if _file_digest(path) != expected:
            raise FileBoundaryConflict("file changed since it was selected")

    def _trash(self, location: FileLocation, source: Path) -> str:
        trash_id = uuid.uuid4().hex
        trash_root = self._trash_root()
        target_dir = trash_root / trash_id
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / source.name
        info = source.stat()
        manifest = {
            "version": 1,
            "trash_id": trash_id,
            "location_id": location.location_id,
            "original_path": source.relative_to(location.root).as_posix(),
            "name": source.name,
            "kind": "folder" if source.is_dir() else "file",
            "bytes": None if source.is_dir() else info.st_size,
            "deleted_at": time.time(),
        }
        try:
            atomic_write_json(
                target_dir / TRASH_MANIFEST_NAME,
                manifest,
                ensure_ascii=False,
                sort_keys=True,
            )
        except OSError as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise FileBoundaryError("trash metadata could not be written") from exc
        try:
            source.rename(target)
        except OSError:
            try:
                self._copy_verified(source, target)
            except Exception:
                shutil.rmtree(target_dir, ignore_errors=True)
                raise
            try:
                if source.is_dir():
                    shutil.rmtree(source)
                else:
                    source.unlink()
            except OSError as exc:
                raise FileBoundaryError(
                    "item was preserved in Trash but its source could not be fully removed"
                ) from exc
        return trash_id

    def _trash_root(self) -> Path:
        return Path(resolve_state_path("memory/files-trash", self.config))

    def _trash_item(self, item_dir: Path) -> dict[str, Any]:
        clean_id = _trash_id(item_dir.name)
        if item_dir.is_symlink() or not item_dir.is_dir():
            raise FileBoundaryError("trash item metadata is invalid")
        try:
            raw = json.loads(
                (item_dir / TRASH_MANIFEST_NAME).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FileBoundaryError("trash item metadata is unavailable") from exc
        required = {
            "version", "trash_id", "location_id", "original_path", "name",
            "kind", "bytes", "deleted_at",
        }
        if not isinstance(raw, dict) or set(raw) != required:
            raise FileBoundaryError("trash item metadata is invalid")
        name = self._name(raw.get("name"))
        relative = self._relative(raw.get("original_path"))
        location_id = str(raw.get("location_id") or "").strip()
        kind = raw.get("kind")
        size = raw.get("bytes")
        deleted_at = raw.get("deleted_at")
        payload = item_dir / name
        if (
            raw.get("version") != 1
            or raw.get("trash_id") != clean_id
            or PurePosixPath(relative).name != name
            or location_id not in self._locations
            or kind not in {"file", "folder"}
            or type(deleted_at) not in {int, float}
            or not 0 < float(deleted_at) <= time.time() + 300
            or (kind == "folder" and (size is not None or not payload.is_dir()))
            or (
                kind == "file"
                and (
                    type(size) is not int
                    or size < 0
                    or not payload.is_file()
                    or payload.stat().st_size != size
                )
            )
            or payload.is_symlink()
        ):
            raise FileBoundaryError("trash item metadata is invalid")
        return {
            "trash_id": clean_id,
            "location_id": location_id,
            "location_label": self._locations[location_id].label,
            "original_path": relative,
            "name": name,
            "kind": kind,
            "bytes": size,
            "deleted_at": float(deleted_at),
        }

    def _copy_verified(self, source: Path, target: Path) -> None:
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.copying")
        try:
            if source.is_dir():
                self._verify_copy_tree_bounds(source)
                shutil.copytree(source, temporary, symlinks=True)
                self._verify_copied_tree(source, temporary)
            else:
                shutil.copy2(source, temporary)
                if _file_digest(source) != _file_digest(temporary):
                    raise FileBoundaryError("copied file verification failed")
            os.replace(temporary, target)
        except FileBoundaryError:
            raise
        except OSError as exc:
            raise FileBoundaryError("file could not be copied") from exc
        finally:
            if temporary.is_dir() and not temporary.is_symlink():
                shutil.rmtree(temporary, ignore_errors=True)
            else:
                temporary.unlink(missing_ok=True)

    def _verify_copy_tree_bounds(self, root: Path) -> None:
        entries = 0
        total_bytes = 0
        stack = [root]
        while stack:
            directory = stack.pop()
            try:
                children = list(directory.iterdir())
            except OSError as exc:
                raise FileBoundaryError("folder cannot be copied") from exc
            for child in children:
                entries += 1
                if entries > self.max_copy_entries:
                    raise FileBoundaryLimit("folder contains too many items")
                try:
                    self._name(child.name)
                    if child.is_symlink():
                        raise FileBoundaryError(
                            "symbolic links are not available in MO Files"
                        )
                    if child.is_dir():
                        stack.append(child)
                    elif child.is_file():
                        total_bytes += child.stat().st_size
                        if total_bytes > self.max_copy_bytes:
                            raise FileBoundaryLimit(
                                "folder exceeds the configured copy limit"
                            )
                    else:
                        raise FileBoundaryError(
                            "folder contains an unsupported file type"
                        )
                except OSError as exc:
                    raise FileBoundaryError("folder cannot be copied") from exc

    @staticmethod
    def _remove_created_copy(target: Path) -> None:
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        except OSError as exc:
            raise FileBoundaryError(
                "source was preserved but the copied destination could not be removed"
            ) from exc

    def _verify_copied_tree(self, source: Path, target: Path) -> None:
        stack = [(source, target)]
        while stack:
            source_dir, target_dir = stack.pop()
            try:
                source_children = {
                    item.name: item for item in source_dir.iterdir()
                }
                target_children = {
                    item.name: item for item in target_dir.iterdir()
                }
            except OSError as exc:
                raise FileBoundaryError("copied folder could not be verified") from exc
            if source_children.keys() != target_children.keys():
                raise FileBoundaryError("copied folder verification failed")
            for name, source_child in source_children.items():
                target_child = target_children[name]
                if source_child.is_symlink() or target_child.is_symlink():
                    raise FileBoundaryError(
                        "symbolic links are not available in MO Files"
                    )
                if source_child.is_dir() and target_child.is_dir():
                    stack.append((source_child, target_child))
                elif source_child.is_file() and target_child.is_file():
                    if _file_digest(source_child) != _file_digest(target_child):
                        raise FileBoundaryError("copied file verification failed")
                else:
                    raise FileBoundaryError("copied folder verification failed")

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise FileBoundaryError("MO Files is disabled")


def _bounded_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _full_access_roots() -> list[tuple[str, Path]]:
    """Return only real local filesystem roots for an explicit full-access host."""
    if os.name == "nt":
        roots: list[tuple[str, Path]] = []
        for letter in string.ascii_uppercase:
            root = Path(f"{letter}:\\")
            try:
                if root.is_dir():
                    roots.append((f"{letter}:", root.resolve(strict=False)))
            except OSError:
                continue
        return roots
    root = Path("/").resolve(strict=False)
    return [("Filesystem", root)] if root.is_dir() else []


def _location_id(kind: str, path: Path) -> str:
    normalized = os.path.normcase(str(path.resolve(strict=False)))
    digest = hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{kind}-{digest}"


def _digest(value: Any) -> str:
    clean = str(value or "").strip().casefold()
    if len(clean) != 64 or any(ch not in "0123456789abcdef" for ch in clean):
        raise FileBoundaryError("a valid file revision is required")
    return clean


def _trash_id(value: Any) -> str:
    clean = str(value or "").strip().casefold()
    if len(clean) != 32 or any(ch not in "0123456789abcdef" for ch in clean):
        raise FileBoundaryError("trash item was not found")
    return clean


def _file_digest(path: Path) -> str:
    try:
        return file_sha256(path)
    except OSError as exc:
        raise FileBoundaryError("file could not be verified") from exc
