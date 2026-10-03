"""Single lightweight MO Everywhere coordinator per device.

Terminal turns only append to the local journal. This optional resident worker
delivers continuity events and, on its own slower cadence, runs curated profile
Git synchronization. The two lanes fail independently.
"""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..runtime.lock import acquire_runtime_lock, release_runtime_lock
from ..utils.atomic_write import atomic_write_json
from .continuity_events import LocalContinuityStore
from .device import device_identity
from .everywhere_readiness import everywhere_authority
from .paths import resolve_state_path


COORDINATOR_STATUS = "sync/everywhere-status.json"


@dataclass(frozen=True)
class CoordinatorSettings:
    enabled: bool
    continuity_enabled: bool
    continuity_auto_sync: bool
    hub_local: bool
    interval_seconds: float
    profile_auto_sync: bool
    profile_interval_seconds: float
    transfer_enabled: bool

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "CoordinatorSettings":
        block = (config or {}).get("consistent_everywhere")
        block = block if isinstance(block, dict) else {}
        continuity = block.get("continuity") if isinstance(block.get("continuity"), dict) else {}
        sync = block.get("sync") if isinstance(block.get("sync"), dict) else {}
        transfer = (config or {}).get("file_transfer")
        transfer = transfer if isinstance(transfer, dict) else {}
        authority = everywhere_authority(config or {})
        enabled = authority.enabled and not authority.conflicts
        continuity_enabled = enabled and continuity.get("enabled", True) is True
        return cls(
            enabled=enabled,
            continuity_enabled=continuity_enabled,
            continuity_auto_sync=continuity_enabled and continuity.get("auto_sync") is True,
            hub_local=authority.hub_owner,
            interval_seconds=max(2.0, min(60.0, float(continuity.get("interval_seconds", 5) or 5))),
            profile_auto_sync=enabled and sync.get("enabled", True) is True and sync.get("auto_sync") is True and bool(str(sync.get("git_remote") or "").strip()),
            profile_interval_seconds=max(10.0, float(sync.get("interval_seconds", 30) or 30)),
            transfer_enabled=enabled and transfer.get("enabled") is True,
        )


def everywhere_coordinator_enabled(config: dict[str, Any] | None = None) -> bool:
    """Return whether this process is configured to start the coordinator."""
    cfg = config or {}
    settings = CoordinatorSettings.from_config(cfg)
    return (
        settings.enabled
        and not Path(resolve_state_path("run/everywhere.disabled", cfg)).is_file()
        and bool(
            settings.continuity_auto_sync
            or settings.profile_auto_sync
            or settings.transfer_enabled
        )
    )


@dataclass
class EverywhereCoordinator:
    config: dict[str, Any]
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _wake: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _runtime_lock: Any = field(default=None, init=False, repr=False)
    _last_profile_status: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def start(self) -> bool:
        if not everywhere_coordinator_enabled(self.config):
            return False
        lock = acquire_runtime_lock(lock_name="mo-everywhere-coordinator.lock", label="MO Everywhere coordinator")
        if lock is None:
            return False
        self._runtime_lock = lock
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._loop, name="mo-everywhere", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.0, timeout))
        release_runtime_lock(self._runtime_lock)
        self._runtime_lock = None

    def wake(self) -> None:
        self._wake.set()

    def run_once(self, *, include_profile: bool = True) -> dict[str, Any]:
        settings = CoordinatorSettings.from_config(self.config)
        if Path(resolve_state_path("run/everywhere.disabled", self.config)).is_file():
            status = {
                "created_at": time.time(),
                "continuity": {"state": "disabled"},
                "profile": {"state": "disabled"},
                "transfer": {"state": "disabled"},
            }
            self._write_status(status)
            return status
        continuity = self._continuity_cycle(settings) if settings.continuity_enabled else {"state": "disabled"}
        if settings.profile_auto_sync and include_profile:
            profile = self._profile_cycle()
            profile.setdefault("checked_at", time.time())
            self._last_profile_status = dict(profile)
        elif settings.profile_auto_sync:
            if not self._last_profile_status:
                previous = read_coordinator_status(self.config).get("profile")
                if isinstance(previous, dict) and previous.get("state") not in {None, "disabled"}:
                    self._last_profile_status = dict(previous)
            profile = dict(self._last_profile_status) if self._last_profile_status else {"state": "pending"}
        else:
            profile = {"state": "disabled"}
        transfer = (
            self._transfer_cycle(settings)
            if settings.transfer_enabled
            else {"state": "disabled"}
        )
        status = {
            "created_at": time.time(),
            "continuity": continuity,
            "profile": profile,
            "transfer": transfer,
        }
        self._write_status(status)
        return status

    def _loop(self) -> None:
        settings = CoordinatorSettings.from_config(self.config)
        next_profile = 0.0
        idle_rounds = 0
        profile_idle_rounds = 0
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                run_profile = settings.profile_auto_sync and now >= next_profile
                status = self.run_once(include_profile=run_profile)
                if run_profile:
                    profile = status.get("profile") if isinstance(status.get("profile"), dict) else {}
                    profile_active = bool(profile.get("changed") or profile.get("pulled") or profile.get("pushed") or profile.get("state") not in {"clean", "disabled"})
                    profile_idle_rounds = 0 if profile_active else min(9, profile_idle_rounds + 1)
                    next_profile = now + min(300.0, settings.profile_interval_seconds * (1.0 + profile_idle_rounds))
                continuity = status.get("continuity") if isinstance(status.get("continuity"), dict) else {}
                transfer = status.get("transfer") if isinstance(status.get("transfer"), dict) else {}
                changed = (
                    int(continuity.get("published", 0) or 0)
                    + int(continuity.get("received", 0) or 0)
                    + int(transfer.get("outgoing", 0) or 0)
                    + int(transfer.get("received", 0) or 0)
                )
                idle_rounds = 0 if changed else min(6, idle_rounds + 1)
                wait_seconds = min(60.0, settings.interval_seconds * (1.0 + idle_rounds))
            except Exception as exc:
                # A resident process may hold the singleton lock for days. One
                # transient status-file/runtime failure must not silently kill
                # its coordinator thread while the process still looks alive.
                self._record_loop_failure(exc)
                idle_rounds = min(6, idle_rounds + 1)
                wait_seconds = min(60.0, settings.interval_seconds * (1.0 + idle_rounds))
            self._wake.wait(wait_seconds)
            self._wake.clear()

    def _record_loop_failure(self, exc: Exception) -> None:
        status = {
            "created_at": time.time(),
            "continuity": {"state": "error"},
            "profile": dict(self._last_profile_status) if self._last_profile_status else {"state": "pending"},
            "transfer": {"state": "error"},
            "coordinator": {"state": "error", "detail": _safe_error(exc)},
        }
        try:
            self._write_status(status)
        except Exception:
            # The next loop still retries even when the failed operation was the
            # status write itself.
            return

    def _continuity_cycle(self, settings: CoordinatorSettings) -> dict[str, Any]:
        if not settings.continuity_auto_sync:
            return {"state": "disabled"}
        store = LocalContinuityStore(self.config)
        identity = device_identity(self.config)
        published = 0
        received = 0
        try:
            if settings.hub_local:
                from mo_everywhere.continuity import ContinuityHub

                hub = ContinuityHub(self.config)
                hub_key = "local-" + hashlib.sha256(str(hub.path).encode("utf-8", errors="replace")).hexdigest()[:24]
                for event in store.pending_outbound(100):
                    remote = hub.publish(event, source_device_id=identity["device_id"])
                    store.mark_delivered(event.event_id, remote.remote_cursor)
                    published += 1
                cursor = store.cursor(hub_key)
                events = hub.list_events(after=cursor, limit=200)
            else:
                from mo_everywhere.client import ContinuityClient

                client = ContinuityClient(self.config)
                hub_key = client.hub_key
                for event in store.pending_outbound(100):
                    remote_cursor = client.publish(event)
                    store.mark_delivered(event.event_id, remote_cursor)
                    published += 1
                cursor = store.cursor(hub_key)
                events, remote_cursor = client.events(after=cursor, limit=200)
                if remote_cursor > cursor:
                    store.set_cursor(hub_key, remote_cursor)
            if events:
                received = store.ingest_inbound(events)
                highest = max(event.remote_cursor for event in events)
                if highest > store.cursor(hub_key):
                    store.set_cursor(hub_key, highest)
            store.prune()
            return {
                "state": "clean",
                "published": published,
                "received": received,
                "cursor": store.cursor(hub_key),
                **store.status(),
            }
        except Exception as exc:
            return {
                "state": "blocked",
                "published": published,
                "received": received,
                "error": _safe_error(exc),
                **store.status(),
            }

    def _profile_cycle(self) -> dict[str, Any]:
        try:
            from .sync import GitStateSync

            backend = GitStateSync(self.config)
            first_attach_blocker = _first_attach_blocker(backend)
            if first_attach_blocker:
                return {"state": "blocked", "ok": False, "detail": first_attach_blocker}
            result = backend.sync_once()
            return {
                "state": result.state,
                "ok": result.ok,
                "changed": result.changed,
                "pulled": result.pulled,
                "pushed": result.pushed,
                "paths": list(result.paths),
                "detail": result.detail,
            }
        except Exception as exc:
            return {"state": "error", "ok": False, "detail": _safe_error(exc)}

    def _transfer_cycle(self, settings: CoordinatorSettings) -> dict[str, Any]:
        try:
            from core.transfer import TransferOutbox

            outgoing = TransferOutbox(self.config).process_due()
            if settings.hub_local:
                from core.transfer import TransferService

                service = TransferService(self.config)
                received = 0
                if service.settings.auto_accept:
                    for transfer in service.list_for(
                        "hub",
                        direction="incoming",
                        states=("offered", "claimed", "delivered"),
                        purposes=("cargo",),
                        limit=20,
                    ):
                        if (
                            transfer.destination == "named_path"
                            and not service.settings.auto_accept_named_paths
                        ):
                            continue
                        service.receive_local(
                            transfer.transfer_id,
                            "hub",
                            destination_path=(
                                transfer.path_hint
                                if transfer.destination == "named_path"
                                else None
                            ),
                        )
                        received += 1
                expired = service.expire()
                return {
                    "state": "clean",
                    "outgoing": len(outgoing),
                    "received": received,
                    "expired": expired,
                }
            from mo_everywhere.client import TransferClient

            received = TransferClient(self.config).receive_available()
            return {
                "state": "clean",
                "outgoing": len(outgoing),
                "received": len(received),
            }
        except Exception as exc:
            from core.transfer import safe_transfer_error

            return {
                "state": "blocked",
                "outgoing": 0,
                "received": 0,
                "error": safe_transfer_error(exc),
            }

    def _write_status(self, status: dict[str, Any]) -> None:
        path = Path(resolve_state_path(COORDINATOR_STATUS, self.config))
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, status, indent=2, ensure_ascii=False)


def start_everywhere_coordinator_if_enabled(config: dict[str, Any] | None = None) -> EverywhereCoordinator | None:
    coordinator = EverywhereCoordinator(config or {})
    return coordinator if coordinator.start() else None


def read_coordinator_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    import json

    path = Path(resolve_state_path(COORDINATOR_STATUS, config or {}))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _safe_error(exc: Exception) -> str:
    text = str(exc or "").replace("\r", " ").replace("\n", " ").strip()
    return (text or f"{type(exc).__name__}: operation failed")[:240]


def _first_attach_blocker(backend: Any) -> str:
    """Require an exact trusted comparison before automatic profile Git attach."""
    if backend.baseline_established():
        return ""
    from .paths import mo_home
    from .profile_reconcile import compare_profiles, configured_peer

    local = mo_home(backend.config)
    peer = configured_peer(backend.config)
    if peer is None:
        return "first attach requires a trusted reconcile_peer_path"
    if peer == local:
        return "reconcile_peer_path must be a distinct trusted profile snapshot"
    if not peer.is_dir():
        return "configured peer snapshot is unavailable"
    plan = compare_profiles(local, peer)
    invalid = len(plan.local.invalid) + len(plan.peer.invalid)
    if invalid:
        return f"first attach has {invalid} invalid curated profile candidate(s)"
    if plan.different:
        return f"first attach has {len(plan.different)} same-file conflict(s)"
    if plan.local_only or plan.peer_only:
        return "first attach safe union is pending; run /everywhere reconcile --confirm"
    return ""
