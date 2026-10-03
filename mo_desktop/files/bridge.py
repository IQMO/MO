"""Trusted WebView adapter over MO Files' existing boundary owners."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from .view_model import FilesViewModel, LOCAL_SOURCE_ID, default_location_id


class FilesBridge:
    """Expose bounded UI actions; keep source IDs and mutations in FilesViewModel."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.model = FilesViewModel(config)
        self._lock = threading.RLock()
        self._window: Any = None
        self._transfer_rows: dict[str, dict[str, Any]] = {}
        self._quick_share: Any = None
        self._current: tuple[str, str, str] = (LOCAL_SOURCE_ID, "", "")
        self.on_status: Any = None
        self.on_ui_ready: Any = None

    def attach_window(self, window: Any) -> None:
        self._window = window

    def ui_ready(self) -> None:
        """Reveal the prepared board after its first render."""
        if self.on_ui_ready:
            self.on_ui_ready()
        if self.on_status:
            self.on_status({"kind": "board_ready"})

    def window_control(self, action: str, width: int = 0, height: int = 0) -> dict[str, bool]:
        window = self._window
        if window is None:
            raise RuntimeError("MO Files window is unavailable.")
        if action == "close":
            window.destroy()
            if self.on_status:
                self.on_status({"kind": "visible", "visible": False})
        elif action == "minimize":
            window.minimize()
        elif action == "toggle_maximize":
            native = getattr(window, "native", None)
            if native is not None and str(native.WindowState).endswith("Maximized"):
                window.restore()
            else:
                window.maximize()
        elif action == "toggle_pin":
            window.on_top = not bool(window.on_top)
        elif action == "resize":
            window.resize(max(980, min(3840, int(width))), max(620, min(2160, int(height))))
        else:
            raise ValueError("Unknown MO Files window control.")
        return {"ok": True, "pinned": bool(window.on_top)}

    def browse(
        self, source_id: str = "", location_id: str = "", path: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            sources = self.model.sources()
            online = [row for row in sources if row.get("online") is True]
            if not online:
                raise RuntimeError("No MO Files source is currently online.")
            selected = next(
                (row for row in online if row.get("source_id") == source_id), None,
            ) or next(
                (row for row in online if row.get("source_id") == LOCAL_SOURCE_ID),
                online[0],
            )
            source_id = str(selected["source_id"])
            locations = self.model.locations(source_id)
            if not locations:
                raise RuntimeError("This source has no available file locations.")
            location_id = (
                location_id if any(row.get("location_id") == location_id for row in locations)
                else default_location_id(source_id, locations)
            )
            listing = self.model.directory(source_id, location_id, path)
            self._current = source_id, location_id, str(listing.get("path") or "")
            return {
                "sources": sources,
                "locations": locations,
                "source_id": source_id,
                "location_id": location_id,
                "listing": listing,
                "notice": self.model.discovery_notice,
            }

    def set_drop_target(self, source_id: str, location_id: str, path: str) -> None:
        with self._lock:
            self.model.directory(source_id, location_id, path)
            self._current = source_id, location_id, path

    def quick_share_addresses(self) -> list[dict[str, str]]:
        from .quick_share import local_addresses

        return local_addresses()

    def quick_share_start(
        self, source_id: str, location_id: str, path: str,
        items: list[dict[str, Any]], address: str,
    ) -> dict[str, Any]:
        from .quick_share import MAX_FILE_BYTES, MAX_FILES, QuickShareSession

        with self._lock:
            if source_id != LOCAL_SOURCE_ID:
                raise RuntimeError("Browser QR transfer currently opens files on this computer.")
            if address not in {row["address"] for row in self.quick_share_addresses()}:
                raise RuntimeError("Choose an active private Wi-Fi or LAN address.")
            self.model.directory(source_id, location_id, path)
            rows = self._selected(source_id, location_id, items, "read") if items else []
            if len(rows) > MAX_FILES or any(row.get("kind") != "file" or not isinstance(row.get("bytes"), int) or row["bytes"] > MAX_FILE_BYTES for row in rows):
                raise RuntimeError("Choose up to eight files of at most 64 MiB each.")
            location = next((row for row in self.model.locations(source_id) if row["location_id"] == location_id), None)
            if location is None:
                raise RuntimeError("This folder is no longer available.")
            can_upload = "create_folder" in location.get("operations", ())
            if not can_upload and not rows:
                raise RuntimeError("Select files to share from this read-only folder.")
            if self._quick_share is not None:
                self._quick_share.stop()
            session = QuickShareSession(
                self.model.files, address=address, location_id=location_id,
                parent_path=path, selected=rows, can_upload=can_upload,
            )
            try:
                public = session.public()
            except Exception:
                session.stop()
                raise
            self._quick_share = session
            return public

    def quick_share_status(self) -> dict[str, Any]:
        with self._lock:
            return self._quick_share.status() if self._quick_share is not None else {"active": False, "received": []}

    def quick_share_stop(self) -> None:
        with self._lock:
            if self._quick_share is not None:
                self._quick_share.stop()
                self._quick_share = None

    def close(self) -> None:
        self.quick_share_stop()

    def focus_item(self, source_id: str, location_id: str, path: str) -> dict[str, Any]:
        """Resolve the focused folder through its existing parent listing."""
        if not path:
            raise RuntimeError("This location root cannot be renamed or moved.")
        with self._lock:
            parent = path.rpartition("/")[0]
            entries = self.model.directory(source_id, location_id, parent)["entries"]
            item = next((row for row in entries if row.get("path") == path), None)
            if item is None or item.get("kind") != "folder":
                raise RuntimeError("This folder changed. Refresh the board.")
            return item

    def _location(self, source_id: str, location_id: str, operation: str) -> dict[str, Any]:
        sources = self.model.sources()
        selected = next(
            (row for row in sources if row.get("source_id") == source_id and row.get("online") is True),
            None,
        )
        if selected is None:
            raise RuntimeError("The selected source is no longer online.")
        location = next(
            (row for row in self.model.locations(source_id) if row.get("location_id") == location_id),
            None,
        )
        if location is None or operation not in (location.get("operations") or ()):
            raise RuntimeError(f"This location does not permit {operation}.")
        return location

    def _selected(
        self, source_id: str, location_id: str, items: list[dict[str, Any]], operation: str,
    ) -> list[dict[str, Any]]:
        self._location(source_id, location_id, operation)
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise ValueError("Select between 1 and 100 items.")
        resolved: list[dict[str, Any]] = []
        seen: set[str] = set()
        listings: dict[str, dict[str, dict[str, Any]]] = {}
        for item in items:
            path = str(item.get("path") or "")
            if not path or path in seen:
                raise ValueError("The selection contains an invalid or repeated path.")
            seen.add(path)
            parent = path.rpartition("/")[0]
            if parent not in listings:
                listing = self.model.directory(source_id, location_id, parent)
                listings[parent] = {
                    str(row.get("path") or ""): row
                    for row in listing.get("entries") or ()
                }
            actual = listings[parent].get(path)
            if actual is None:
                raise RuntimeError("The selection changed. Refresh this folder.")
            expected = str(item.get("sha256") or "")
            if expected and expected != str(actual.get("sha256") or ""):
                raise RuntimeError("The selected file changed. Refresh before acting.")
            resolved.append(actual)
        return resolved

    def create_folder(
        self, source_id: str, location_id: str, parent_path: str, name: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._location(source_id, location_id, "create_folder")
            self.model.directory(source_id, location_id, parent_path)
            return self.model.create_folder(source_id, location_id, parent_path, name)

    def rename(
        self, source_id: str, location_id: str, item: dict[str, Any], name: str,
    ) -> dict[str, Any]:
        with self._lock:
            actual = self._selected(source_id, location_id, [item], "rename")[0]
            return self.model.rename(
                source_id, location_id, str(actual["path"]), name,
                str(item.get("sha256") or ""),
            )

    def organize(
        self, operation: str, source_id: str, location_id: str,
        items: list[dict[str, Any]], target_location_id: str, target_directory: str,
    ) -> dict[str, Any]:
        if operation not in {"move", "copy"}:
            raise ValueError("Unsupported file operation.")
        with self._lock:
            selected = self._selected(source_id, location_id, items, operation)
            self._location(source_id, target_location_id, operation)
            self.model.directory(source_id, target_location_id, target_directory)
            completed: list[str] = []
            for requested, actual in zip(items, selected):
                try:
                    self.model.organize(
                        operation, source_id, location_id, str(actual["path"]),
                        target_location_id, target_directory,
                        str(requested.get("sha256") or ""),
                    )
                except Exception as exc:
                    return {"completed": completed, "error": str(exc)}
                completed.append(str(actual["path"]))
            return {"completed": completed, "error": ""}

    def delete(
        self, source_id: str, location_id: str, items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            selected = self._selected(source_id, location_id, items, "delete")
            completed: list[str] = []
            for requested, actual in zip(items, selected):
                try:
                    self.model.delete(
                        source_id, location_id, str(actual["path"]),
                        str(requested.get("sha256") or ""),
                    )
                except Exception as exc:
                    return {"completed": completed, "error": str(exc)}
                completed.append(str(actual["path"]))
            return {"completed": completed, "error": ""}

    def read_text(self, source_id: str, location_id: str, item: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            actual = self._selected(source_id, location_id, [item], "edit_text")[0]
            if not actual.get("editable"):
                raise RuntimeError("This file cannot be edited as text.")
            return self.model.read_text(source_id, location_id, str(actual["path"]))

    def write_text(
        self, source_id: str, location_id: str, path: str, text: str, expected_sha256: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._location(source_id, location_id, "edit_text")
            if not expected_sha256:
                raise ValueError("The original file revision is required.")
            return self.model.write_text(
                source_id, location_id, path, text, expected_sha256,
            )

    def open_local(self, source_id: str, location_id: str, item: dict[str, Any]) -> str:
        with self._lock:
            actual = self._selected(source_id, location_id, [item], "read")[0]
            if source_id != LOCAL_SOURCE_ID or actual.get("kind") != "file":
                raise RuntimeError("Only a local file can open with its Windows app.")
            self.model.open_local_file(source_id, location_id, str(actual["path"]))
            return str(actual.get("name") or "File")

    def targets(self, source_id: str) -> dict[str, Any]:
        with self._lock:
            targets, hub_local = self.model.targets(source_id)
            return {"targets": targets, "hub_local": hub_local}

    def send(
        self, source_id: str, location_id: str, items: list[dict[str, Any]],
        target_device_id: str = "", destination_source_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            selected = self._selected(source_id, location_id, items, "send")
            if any(item.get("kind") != "file" for item in selected):
                raise RuntimeError("Cross-source sending supports files only.")
            if destination_source_id:
                target, hub_local = self.model.target_for_source(
                    source_id, destination_source_id,
                )
            else:
                choices, hub_local = self.model.targets(source_id)
                target = next(
                    (row for row in choices if row.get("device_id") == target_device_id),
                    None,
                )
            if target is None:
                raise RuntimeError("This source has no exact paired transfer destination.")
            completed: list[str] = []
            for item in selected:
                try:
                    self.model.send(
                        source_id, location_id, str(item["path"]), target,
                        hub_local=hub_local,
                    )
                except Exception as exc:
                    return {"completed": completed, "error": str(exc)}
                completed.append(str(item["path"]))
            return {"completed": completed, "error": ""}

    def transfers(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.model.transfers()
            self._transfer_rows = {
                str(row.get("outbox_id") or row.get("transfer_id")): row
                for row in rows if row.get("outbox_id") or row.get("transfer_id")
            }
            result = []
            for row in rows:
                progress = row.get("progress")
                if progress is None:
                    progress = int(row.get("uploaded_bytes") or 0) / max(1, int(row.get("bytes") or 1))
                result.append({
                    "id": str(row.get("outbox_id") or row.get("transfer_id") or ""),
                    "name": str(row.get("name") or "file"),
                    "direction": str(row.get("direction") or "outgoing"),
                    "route_label": (
                        "To this computer" if row.get("direction") == "incoming"
                        else str(row.get("target_label") or "To connected place")
                    ),
                    "state": str(row.get("state") or "pending"),
                    "progress": max(0, min(100, round(float(progress) * 100))),
                    "can_cancel": self.model.can_cancel_transfer(row),
                    "can_retry": self.model.can_retry_transfer(row),
                })
            if self.on_status:
                self.on_status({
                    "kind": "transfer_count",
                    "count": sum(row["state"] not in {"done", "cancelled", "expired", "failed"} for row in result),
                })
            return result

    def transfer_action(self, action: str, transfer_id: str) -> None:
        with self._lock:
            row = self._transfer_rows.get(transfer_id)
            if row is None:
                self.transfers()
                row = self._transfer_rows.get(transfer_id)
            if row is None:
                raise RuntimeError("Refresh the transfer before acting.")
            if action == "cancel" and self.model.can_cancel_transfer(row):
                self.model.cancel_transfer(row)
            elif action == "retry" and self.model.can_retry_transfer(row):
                self.model.retry_transfer(row)
            else:
                raise RuntimeError("That transfer action is unavailable.")

    def clear_transfer_history(self) -> int:
        with self._lock:
            return self.model.clear_transfer_history()

    def trash(self, source_id: str) -> dict[str, Any]:
        with self._lock:
            sources = self.model.sources()
            if not any(
                row.get("source_id") == source_id and "trash" in (row.get("operations") or ())
                for row in sources
            ):
                raise RuntimeError("Trash is unavailable for this source.")
            return self.model.trash(source_id)

    def restore(self, source_id: str, trash_id: str) -> dict[str, Any]:
        with self._lock:
            self.trash(source_id)
            return self.model.restore(source_id, trash_id)

    def external_paths(
        self, source_id: str, location_id: str, items: list[dict[str, Any]],
    ) -> list[str]:
        """Resolve explicitly selected local files for a native shell drag."""
        if os.name != "nt" or source_id != LOCAL_SOURCE_ID:
            raise RuntimeError("Windows drag-out is available for local files only.")
        with self._lock:
            selected = self._selected(source_id, location_id, items, "read")
            if any(item.get("kind") != "file" for item in selected):
                raise RuntimeError("Drag-out currently supports local files only.")
            return [
                str(self.model.files.source_path(location_id, str(item["path"])))
                for item in selected
            ]

    def moved_outside(
        self, source_id: str, location_id: str, items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Complete an external copy as a recoverable MO Files move."""
        return self.delete(source_id, location_id, items)

    def start_external_drag(
        self, source_id: str, location_id: str, items: list[dict[str, Any]],
        move: bool = True,
    ) -> dict[str, Any]:
        """Offer real CF_HDROP files to Explorer, then use guarded Trash for move."""
        paths = self.external_paths(source_id, location_id, items)
        window = self._window
        native = getattr(window, "native", None)
        if native is None:
            raise RuntimeError("The Windows file drag is not ready.")
        from System import Action
        from System.Collections.Specialized import StringCollection
        from System.Windows.Forms import Control, DataObject, DragDropEffects

        outcome: dict[str, Any] = {"copied": False, "outside": False}

        def drag() -> None:
            collection = StringCollection()
            for path in paths:
                collection.Add(path)
            data = DataObject()
            data.SetFileDropList(collection)
            effect = native.DoDragDrop(data, DragDropEffects.Copy)
            point = native.PointToClient(Control.MousePosition)
            outcome["copied"] = effect == DragDropEffects.Copy
            outcome["outside"] = not native.ClientRectangle.Contains(point)

        native.Invoke(Action(drag))
        if outcome["copied"] and outcome["outside"] and move:
            removed = self.moved_outside(source_id, location_id, items)
            return {"copied": True, "outside": True, "moved": len(removed["completed"]), "error": removed["error"]}
        return {"copied": outcome["copied"], "outside": outcome["outside"], "moved": 0, "error": ""}

    def import_managed_drop(
        self, absolute_paths: list[str], destination_location_id: str,
        destination_path: str,
    ) -> dict[str, Any]:
        """Move Explorer drops already inside an allowed local location via the owner."""
        with self._lock:
            self._location(LOCAL_SOURCE_ID, destination_location_id, "move")
            self.model.directory(LOCAL_SOURCE_ID, destination_location_id, destination_path)
            roots = [
                (
                    str(location["location_id"]),
                    self.model.files._location(location["location_id"]).root,
                )
                for location in self.model.locations(LOCAL_SOURCE_ID)
                if "move" in (location.get("operations") or ())
            ]
            plan: list[tuple[str, str, dict[str, Any]]] = []
            for raw in absolute_paths[:100]:
                candidate = Path(raw).resolve(strict=True)
                match = next(
                    ((lid, candidate.relative_to(root).as_posix()) for lid, root in roots if candidate.is_relative_to(root)),
                    None,
                )
                if match is None:
                    raise RuntimeError("This Explorer item is outside MO Files locations.")
                lid, relative = match
                actual = self._selected(LOCAL_SOURCE_ID, lid, [{"path": relative}], "move")[0]
                plan.append((lid, relative, actual))
            completed: list[str] = []
            for lid, relative, actual in plan:
                try:
                    self.model.organize(
                        "move", LOCAL_SOURCE_ID, lid, relative,
                        destination_location_id, destination_path,
                        str(actual.get("sha256") or ""),
                    )
                except Exception as exc:
                    return {"completed": completed, "error": str(exc)}
                completed.append(relative)
            return {"completed": completed, "error": ""}
