"""Authenticated native-host adapter for the shared MO Files boundary.

The Everywhere live-host socket supplies identity and transport. This adapter
owns no network listener and exposes only FileManagerService's opaque locations
and relative paths.
"""
from __future__ import annotations

from typing import Any

from core.transfer import TransferError, TransferOutbox, safe_transfer_error
from core.transfer.addressing import resolve_transfer_target, transfer_targets

from .service import FileBoundaryError, FileManagerService


WORKSTATION_FILES_LANE = "files_v1"
FILE_HOST_OPERATIONS = frozenset(
    {
        "locations",
        "list",
        "read_text",
        "preview",
        "write_text",
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


class FileHostLane:
    """Execute one bounded file request on the host that owns the paths."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        service: FileManagerService | None = None,
    ):
        self.config = config or {}
        self.files = service or FileManagerService(self.config)

    @property
    def enabled(self) -> bool:
        return self.files.enabled

    def execute(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        clean = str(operation or "").strip().casefold()
        if clean not in FILE_HOST_OPERATIONS or not isinstance(arguments, dict):
            raise FileBoundaryError("MO Files operation is not supported")
        if clean == "locations":
            _exact(arguments, set())
            return {"locations": self.files.locations()}
        if clean == "list":
            _exact(arguments, {"location_id", "path", "limit"}, optional={"path", "limit"})
            return self.files.list(
                arguments.get("location_id"),
                arguments.get("path") or "",
                limit=arguments.get("limit"),
            )
        if clean == "read_text":
            _exact(arguments, {"location_id", "path"})
            return self.files.read_text(
                arguments.get("location_id"), arguments.get("path")
            )
        if clean == "preview":
            _exact(arguments, {"location_id", "path"})
            return self.files.preview(
                arguments.get("location_id"), arguments.get("path")
            )
        if clean == "write_text":
            _exact(
                arguments,
                {"location_id", "path", "text", "expected_sha256"},
            )
            return self.files.write_text(
                arguments.get("location_id"),
                arguments.get("path"),
                arguments.get("text"),
                expected_sha256=arguments.get("expected_sha256"),
            )
        if clean == "create_folder":
            _exact(arguments, {"location_id", "parent_path", "name"}, optional={"parent_path"})
            return {
                "item": self.files.create_folder(
                    arguments.get("location_id"),
                    arguments.get("parent_path") or "",
                    arguments.get("name"),
                )
            }
        if clean == "rename":
            _exact(
                arguments,
                {"location_id", "path", "new_name", "expected_sha256"},
                optional={"expected_sha256"},
            )
            return {
                "item": self.files.rename(
                    arguments.get("location_id"),
                    arguments.get("path"),
                    arguments.get("new_name"),
                    expected_sha256=arguments.get("expected_sha256") or "",
                )
            }
        if clean in {"copy", "move"}:
            _exact(
                arguments,
                {
                    "source_location_id",
                    "path",
                    "target_location_id",
                    "target_directory",
                    "expected_sha256",
                },
                optional={"target_directory", "expected_sha256"},
            )
            action = self.files.copy if clean == "copy" else self.files.move
            return {
                "item": action(
                    arguments.get("source_location_id"),
                    arguments.get("path"),
                    arguments.get("target_location_id"),
                    arguments.get("target_directory") or "",
                    expected_sha256=arguments.get("expected_sha256") or "",
                )
            }
        if clean == "delete":
            _exact(
                arguments,
                {"location_id", "path", "expected_sha256"},
                optional={"expected_sha256"},
            )
            return self.files.delete(
                arguments.get("location_id"),
                arguments.get("path"),
                expected_sha256=arguments.get("expected_sha256") or "",
            )
        if clean == "trash":
            _exact(arguments, {"limit"}, optional={"limit"})
            return self.files.trash(limit=arguments.get("limit"))
        if clean == "restore":
            _exact(arguments, {"trash_id"})
            return self.files.restore(arguments.get("trash_id"))
        _exact(arguments, {"location_id", "path", "target_device_id"})
        targets, hub_local = transfer_targets(self.config)
        target = resolve_transfer_target(arguments.get("target_device_id"), targets)
        source = self.files.source_path(
            arguments.get("location_id"), arguments.get("path")
        )
        record = TransferOutbox(self.config).enqueue(
            source,
            target_device_id=target["device_id"],
            target_label=target["label"],
            source_surface="files_host",
            hub_local=hub_local,
        )
        return {"transfer": record.public()}

    @staticmethod
    def error_message(error: Exception) -> str:
        if isinstance(error, FileBoundaryError):
            return " ".join(str(error).split())[:240]
        if isinstance(error, TransferError):
            return safe_transfer_error(error)
        return "MO Files operation failed"


def _exact(
    value: dict[str, Any],
    allowed: set[str],
    *,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    required = allowed - optional
    if set(value) - allowed or not required.issubset(value):
        raise FileBoundaryError("MO Files operation fields are invalid")
