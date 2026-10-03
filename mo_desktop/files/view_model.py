"""Thin Desktop adapter over the shared MO Files and transfer owners."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from core.files import FileManagerService
from core.state.everywhere_readiness import everywhere_authority
from core.transfer import HUB_TARGET_ID, TransferOutbox, TransferService
from core.transfer.addressing import transfer_targets
from mo_everywhere.client import (
    EverywhereClientError,
    FilesClient,
    TransferClient,
    load_credentials,
)
from mo_everywhere.live_host import live_host_client_config


LOCAL_SOURCE_ID = "desktop-local"
SAFE_OPEN_SUFFIXES = frozenset({
    ".avi", ".bmp", ".docx", ".flac", ".gif", ".jpeg", ".jpg", ".m4a",
    ".mkv", ".mov", ".mp3", ".mp4", ".odp", ".ods", ".odt", ".pdf",
    ".png", ".pptx", ".tif", ".tiff", ".wav", ".webp", ".xlsx",
})


def default_location_id(source_id: str, locations: list[dict[str, Any]]) -> str:
    """Pick the location a source should land on when none is chosen yet.

    The local machine opens on a real drive, an MO host opens on MO's own
    runtime home, and anything else keeps the source's own first entry.
    """
    if not locations:
        return ""
    preferred_kinds = ("drive",) if source_id == LOCAL_SOURCE_ID else ("mo_home",)
    for kind in preferred_kinds:
        for item in locations:
            if str(item.get("kind") or "") == kind:
                return str(item.get("location_id") or "")
    return str(locations[0].get("location_id") or "")


class FilesViewModel:
    """Route one UI source to its existing authenticated owner."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.files = FileManagerService(self.config)
        self.discovery_notice = ""

    def sources(self) -> list[dict[str, Any]]:
        self.discovery_notice = ""
        local_operations = [
            "list",
            "read",
            "edit_text",
            "create_folder",
            "rename",
            "copy",
            "move",
            "delete",
            "trash",
            "restore",
            "send",
        ] if self.files.enabled else []
        sources = [
            {
                "source_id": LOCAL_SOURCE_ID,
                "host_key": LOCAL_SOURCE_ID,
                "label": "This computer",
                "kind": "desktop",
                "online": self.files.enabled,
                "operations": local_operations,
            }
        ]
        try:
            remote = FilesClient(self.config).sources()
            local_host_key = self._local_host_key()
            hub_is_local = everywhere_authority(self.config).hub_owner
            sources.extend(
                item
                for item in remote
                if not (
                    (hub_is_local and item.get("source_id") == "hub")
                    or (
                        local_host_key
                        and item.get("host_key") == local_host_key
                    )
                )
            )
        except EverywhereClientError as exc:
            message = str(exc).casefold()
            if "not paired" in message:
                self.discovery_notice = (
                    "Remote sources are not connected. Pair this computer's "
                    "dedicated MO Files controller."
                )
            elif "rejected" in message and "(403)" in message:
                self.discovery_notice = (
                    "Remote sources denied this MO Files controller. It needs "
                    "control plus file_browse authority."
                )
            elif "unreachable" in message:
                self.discovery_notice = (
                    "Remote sources are temporarily unavailable because the MO hub "
                    "cannot be reached."
                )
            else:
                self.discovery_notice = (
                    "Remote sources could not be verified. Local files remain available."
                )
        return sources

    def _local_host_key(self) -> str:
        """Return the opaque Desktop host key without exposing its credential."""
        try:
            device_id = load_credentials(
                live_host_client_config(self.config)
            ).device_id
        except EverywhereClientError:
            return ""
        return hashlib.sha256(
            f"mo-live-host-v1\0{device_id}\0desktop".encode("utf-8")
        ).hexdigest()

    def locations(self, source_id: str) -> list[dict[str, Any]]:
        if source_id == LOCAL_SOURCE_ID:
            return self.files.locations()
        return FilesClient(self.config).locations(source_id)

    def directory(
        self, source_id: str, location_id: str, path: str = ""
    ) -> dict[str, Any]:
        if source_id == LOCAL_SOURCE_ID:
            return self.files.list(location_id, path)
        return FilesClient(self.config).directory(source_id, location_id, path)

    def read_text(
        self, source_id: str, location_id: str, path: str
    ) -> dict[str, Any]:
        if source_id == LOCAL_SOURCE_ID:
            return self.files.read_text(location_id, path)
        return FilesClient(self.config).read_text(source_id, location_id, path)

    def write_text(
        self,
        source_id: str,
        location_id: str,
        path: str,
        text: str,
        expected_sha256: str,
    ) -> dict[str, Any]:
        if source_id == LOCAL_SOURCE_ID:
            return self.files.write_text(
                location_id, path, text, expected_sha256=expected_sha256
            )
        return FilesClient(self.config).write_text(
            source_id, location_id, path, text, expected_sha256
        )

    def rename(
        self,
        source_id: str,
        location_id: str,
        path: str,
        name: str,
        expected_sha256: str = "",
    ) -> dict[str, Any]:
        if source_id == LOCAL_SOURCE_ID:
            return self.files.rename(
                location_id, path, name, expected_sha256=expected_sha256
            )
        return FilesClient(self.config).rename(
            source_id, location_id, path, name, expected_sha256
        )["item"]

    def create_folder(
        self, source_id: str, location_id: str, parent_path: str, name: str
    ) -> dict[str, Any]:
        if source_id == LOCAL_SOURCE_ID:
            return self.files.create_folder(location_id, parent_path, name)
        return FilesClient(self.config).create_folder(
            source_id, location_id, parent_path, name
        )["item"]

    def organize(
        self,
        operation: str,
        source_id: str,
        location_id: str,
        path: str,
        target_location_id: str,
        target_directory: str,
        expected_sha256: str = "",
    ) -> dict[str, Any]:
        if source_id == LOCAL_SOURCE_ID:
            action = self.files.copy if operation == "copy" else self.files.move
            return action(
                location_id,
                path,
                target_location_id,
                target_directory,
                expected_sha256=expected_sha256,
            )
        return FilesClient(self.config).organize(
            operation,
            source_id,
            location_id,
            path,
            target_location_id,
            target_directory,
            expected_sha256,
        )["item"]

    def delete(
        self,
        source_id: str,
        location_id: str,
        path: str,
        expected_sha256: str = "",
    ) -> dict[str, Any]:
        if source_id == LOCAL_SOURCE_ID:
            return self.files.delete(
                location_id, path, expected_sha256=expected_sha256
            )
        return FilesClient(self.config).delete(
            source_id, location_id, path, expected_sha256
        )

    def trash(self, source_id: str) -> dict[str, Any]:
        if source_id == LOCAL_SOURCE_ID:
            return self.files.trash()
        return FilesClient(self.config).trash(source_id)

    def restore(self, source_id: str, trash_id: str) -> dict[str, Any]:
        if source_id == LOCAL_SOURCE_ID:
            return self.files.restore(trash_id)["item"]
        return FilesClient(self.config).restore(source_id, trash_id)["item"]

    def open_local_file(self, source_id: str, location_id: str, path: str) -> Path:
        """Open an explicit local document/media selection with Windows Shell."""
        if source_id != LOCAL_SOURCE_ID:
            raise RuntimeError("Remote files must be transferred locally before opening.")
        source = self.files.source_path(location_id, path)
        if source.suffix.casefold() not in SAFE_OPEN_SUFFIXES:
            raise RuntimeError("This file type is not in MO Files' safe local-open list.")
        if os.name != "nt" or not hasattr(os, "startfile"):
            raise RuntimeError("Local opening is available on Windows only.")
        os.startfile(str(source), "open")  # type: ignore[attr-defined]
        return source

    def targets(self, source_id: str = "") -> tuple[list[dict[str, str]], bool]:
        targets, hub_local = transfer_targets(self.config)
        if hub_local:
            return (
                [
                    item
                    for item in targets
                    if source_id != LOCAL_SOURCE_ID
                    or item["device_id"] != HUB_TARGET_ID
                ],
                True,
            )
        client = TransferClient(self.config)
        targets.append(
            {
                "device_id": client.device_id,
                "label": "This computer",
                "kind": "device",
            }
        )
        unique = list({item["device_id"]: item for item in targets}.values())
        if source_id == LOCAL_SOURCE_ID:
            unique = [
                item for item in unique if item["device_id"] != client.device_id
            ]
        return unique, False

    def target_for_source(
        self,
        source_id: str,
        destination_source_id: str,
    ) -> tuple[dict[str, str] | None, bool]:
        """Resolve only destination sources with an exact transfer identity.

        A workstation Files source id is a browse identity, not a transfer
        device id, so it must never be guessed from a label or host id. Hub and
        this Desktop have canonical target identities and can be preselected.
        """
        targets, hub_local = self.targets(source_id)
        target_id = ""
        if destination_source_id == "hub":
            target_id = HUB_TARGET_ID
        elif destination_source_id == LOCAL_SOURCE_ID and not hub_local:
            try:
                target_id = TransferClient(self.config).device_id
            except EverywhereClientError:
                target_id = ""
        return (
            next(
                (
                    target
                    for target in targets
                    if str(target.get("device_id") or "") == target_id
                ),
                None,
            ),
            hub_local,
        )

    def send(
        self,
        source_id: str,
        location_id: str,
        path: str,
        target: dict[str, str],
        *,
        hub_local: bool,
    ) -> dict[str, Any]:
        if source_id != LOCAL_SOURCE_ID:
            return FilesClient(self.config).send(
                source_id,
                location_id,
                path,
                str(target["device_id"]),
            )["transfer"]
        source = self.files.source_path(location_id, path)
        record = TransferOutbox(self.config).send_now(
            source,
            target_device_id=str(target["device_id"]),
            target_label=str(target["label"]),
            source_surface="mo_desktop_files",
            hub_local=hub_local,
        )
        return record.public()

    def transfers(self) -> list[dict[str, Any]]:
        outbox = [
            item.public() for item in TransferOutbox(self.config).list_pending(limit=50)
        ]
        targets, hub_local = transfer_targets(self.config)
        if not targets:
            return outbox
        if hub_local:
            actor_device_id = HUB_TARGET_ID
            remote = [
                item.public()
                for item in TransferService(self.config).list_for(
                    HUB_TARGET_ID,
                    purposes=("cargo",),
                    limit=50,
                )
            ]
        else:
            client = TransferClient(self.config)
            actor_device_id = client.device_id
            remote = client.transfers(limit=50)
        seen = {str(item.get("transfer_id") or "") for item in outbox}
        outbox.extend(
            item
            for item in remote
            if str(item.get("transfer_id") or "") not in seen
        )
        rows = sorted(
            outbox,
            key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0),
            reverse=True,
        )[:80]
        for item in rows:
            direction = str(item.get("direction") or "").casefold()
            if direction in {"out", "outgoing"}:
                item["direction"] = "outgoing"
            elif (
                direction in {"in", "incoming"}
                or str(item.get("target_device_id") or "") == actor_device_id
            ):
                item["direction"] = "incoming"
            else:
                item["direction"] = "outgoing"
        return rows

    def clear_transfer_history(self) -> int:
        targets, hub_local = transfer_targets(self.config)
        remote_cleared = 0
        if hub_local:
            remote_cleared = TransferService(self.config).clear_terminal_history(
                HUB_TARGET_ID
            )
        elif targets:
            remote_cleared = TransferClient(self.config).clear_terminal_history()
        local_cleared = TransferOutbox(self.config).clear_terminal_history()
        return remote_cleared + local_cleared

    @staticmethod
    def can_retry_transfer(item: dict[str, Any]) -> bool:
        return bool(
            item.get("outbox_id")
            and str(item.get("state") or "").casefold() == "failed"
        )

    @staticmethod
    def can_cancel_transfer(item: dict[str, Any]) -> bool:
        state = str(item.get("state") or "").casefold()
        if state in {"done", "cancelled", "expired"}:
            return False
        if item.get("outbox_id"):
            return state not in {"sending", "staging"}
        if (
            str(item.get("direction") or "").casefold() == "outgoing"
            and state in {"claimed", "delivered"}
        ):
            return False
        return bool(item.get("transfer_id")) and state != "failed"

    def retry_transfer(self, item: dict[str, Any]) -> dict[str, Any]:
        outbox_id = str(item.get("outbox_id") or "")
        if not outbox_id:
            raise ValueError("Only sender-custody transfers can be retried.")
        return TransferOutbox(self.config).retry(outbox_id).public()

    def cancel_transfer(self, item: dict[str, Any]) -> dict[str, Any]:
        outbox_id = str(item.get("outbox_id") or "")
        if outbox_id:
            return TransferOutbox(self.config).cancel(outbox_id).public()
        transfer_id = str(item.get("transfer_id") or "")
        if not transfer_id:
            raise ValueError("This transfer has no cancellable identity.")
        _targets, hub_local = transfer_targets(self.config)
        if hub_local:
            return TransferService(self.config).cancel(
                transfer_id, HUB_TARGET_ID
            ).public()
        return TransferClient(self.config).cancel(transfer_id)
