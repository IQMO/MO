"""MO Everywhere adapter for Desktop's existing notice and choice contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core.runtime.surface_identity import normalize_runtime_surface as normalize_heartbeat_surface
from core.runtime.instance import recent_instance_snapshots
from core.state.continuity_events import LOCAL_CONTINUITY_PATH, LocalContinuityStore, default_thread_id, opaque_id
from core.state.device import device_identity
from core.state.everywhere_coordinator import read_coordinator_status
from core.state.paths import resolve_state_path
from core.session.sessions import session_snapshot_path
from mo_desktop.notify import Notice, register_source


DESKTOP_CONTINUITY_KEY = "mo-desktop"


class EverywhereNoticeSource:
    """Stateful, bounded producer; it never opens a new Desktop panel."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        path = Path(resolve_state_path(LOCAL_CONTINUITY_PATH, self.config))
        self._last_event_rowid = LocalContinuityStore(self.config).latest_rowid() if path.is_file() else 0
        status = read_coordinator_status(self.config)
        self._last_status_at = float(status.get("created_at") or 0.0) if isinstance(status, dict) else 0.0
        self._last_profile_notice_signature = ""

    def __call__(self) -> list[Notice]:
        block = self.config.get("consistent_everywhere") if isinstance(self.config.get("consistent_everywhere"), dict) else {}
        if block.get("enabled") is not True:
            return []
        path = Path(resolve_state_path(LOCAL_CONTINUITY_PATH, self.config))
        if path.is_file():
            rows = LocalContinuityStore(self.config).events_after(self._last_event_rowid, limit=20)
            if rows:
                self._last_event_rowid = rows[-1][0]
                local_device = str(device_identity(self.config).get("device_id") or "")
                relevant = [
                    event for _rowid, event in rows
                    if event.source_surface in {"api", "telegram"}
                    or (event.source_device_id != local_device and event.source_surface in {"terminal", "mo_desktop", "companion"})
                ]
                if not relevant:
                    return []
                event = relevant[-1]
                count = len(relevant)
                if event.status == "active":
                    return []
                source = {
                    "telegram": "Telegram",
                    "api": "Phone",
                    "terminal": "Terminal",
                    "mo_desktop": "Desktop",
                    "companion": "Desktop",
                }.get(event.source_surface, "MO")
                state_label = {
                    "completed": "done",
                    "failed": "failed",
                    "cancelled": "cancelled",
                    "paused": "paused",
                    "active": "active",
                }.get(event.status, event.status)
                title = f"{source} {state_label}"
                detail = _summary(event.outcome)
                if count > 1:
                    detail = _summary(f"{count} updates · {detail}")
                emote = "notify_telegram" if event.source_surface == "telegram" else "notify"
                return [Notice(f"everywhere:event:{event.event_id}", title, detail, emote, 3.0)]

        status = read_coordinator_status(self.config)
        created_at = float(status.get("created_at") or 0.0) if isinstance(status, dict) else 0.0
        if created_at <= self._last_status_at:
            return []
        self._last_status_at = created_at
        profile = status.get("profile") if isinstance(status.get("profile"), dict) else {}
        if profile.get("state") in {"blocked", "error"}:
            signature = f"{profile.get('state')}\0{profile.get('detail', '')}"
            if signature == self._last_profile_notice_signature:
                return []
            self._last_profile_notice_signature = signature
            return [Notice("everywhere:profile:blocked", "Sync blocked", _summary(profile.get("detail", "Profile reconciliation required")),
                           "notify", 3.0, tone="warn")]
        self._last_profile_notice_signature = ""
        if profile.get("state") == "clean" and any(profile.get(key) for key in ("changed", "pulled", "pushed")):
            return [Notice(
                "everywhere:profile:updated",
                "MO state synced",
                _profile_sync_detail(profile),
                "notify",
                3.0,
            )]
        return []


def register_everywhere_desktop(config: dict[str, Any] | None = None) -> EverywhereNoticeSource:
    source = EverywhereNoticeSource(config)
    register_source(source, order=30, key="everywhere")
    return source


def _profile_sync_detail(profile: dict[str, Any]) -> str:
    raw_paths = profile.get("paths", [])
    raw_paths = raw_paths if isinstance(raw_paths, (list, tuple)) else []
    paths = [str(path or "").replace("\\", "/") for path in raw_paths]
    labels: list[str] = []
    if any(path == "skills" or path.startswith("skills/") for path in paths):
        labels.append("Skill metadata")
    if any(path.startswith("memory/profile/") for path in paths):
        labels.append("profile files")
    if "skin" in paths:
        labels.append("Desktop skin")
    if not labels:
        labels.append("Private profile or skill state")
    subject = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + f" and {labels[-1]}"
    if profile.get("pulled") and not profile.get("changed") and not profile.get("pushed"):
        return _summary(f"{subject} received")
    return _summary(f"{subject} synced")


def select_desktop_thread(config: dict[str, Any] | None, thread_id: str) -> None:
    LocalContinuityStore(config or {}).bind(
        "mo_desktop",
        DESKTOP_CONTINUITY_KEY,
        thread_id,
    )


def desktop_binding(config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    path = Path(resolve_state_path(LOCAL_CONTINUITY_PATH, config or {}))
    if not path.is_file():
        return None
    return LocalContinuityStore(config or {}).binding("mo_desktop", DESKTOP_CONTINUITY_KEY)


def terminal_session_candidates(config: dict[str, Any] | None, sessions_dir: str | Path, *, require_session: bool = True) -> list[dict[str, Any]]:
    """Return live terminals, requiring a saved conversation for follow by default.

    Navigation may include idle terminals without conversations; it must not
    launch a duplicate merely because the existing terminal has not sent a turn.
    """
    cfg = config or {}
    sessions = Path(sessions_dir)
    result: list[dict[str, Any]] = []
    block = cfg.get("consistent_everywhere") if isinstance(cfg.get("consistent_everywhere"), dict) else {}
    device_id = str(device_identity(cfg).get("device_id") or "") if block.get("enabled") is True else "desktop-local"
    continuity_path = Path(resolve_state_path(LOCAL_CONTINUITY_PATH, cfg))
    store = LocalContinuityStore(cfg) if continuity_path.is_file() else None
    try:
        snapshots = recent_instance_snapshots(cfg, current_pid=os.getpid(), max_age_seconds=300, limit=32)
    except Exception:
        snapshots = []
    seen_slots: set[str] = set()
    for snapshot in snapshots:
        if not snapshot.get("pid_alive") or normalize_heartbeat_surface(str(snapshot.get("surface") or "")) != "terminal":
            continue
        slot = str(snapshot.get("slot") or "").strip()
        if not slot or slot in seen_slots:
            continue
        seen_slots.add(slot)
        path = session_snapshot_path(sessions, slot)
        if require_session and not path.is_file():
            continue
        project_id = _project_id(snapshot.get("cwd"))
        thread_id = default_thread_id(
            "terminal",
            slot,
            project_id=project_id,
            device_id=device_id,
        )
        event = store.latest_for_thread(thread_id, source_surface="terminal") if store is not None else None
        result.append({
            "thread_id": thread_id,
            "instance_id": str(snapshot.get("instance_id") or ""),
            "pid": int(snapshot.get("pid") or 0),
            "slot": slot,
            "cwd": str(snapshot.get("cwd") or ""),
            "project_id": project_id,
            "source_surface": "terminal",
            "status": event.status if event is not None else "active",
            "intent": event.intent if event is not None else _session_focus(path),
            "outcome": event.outcome if event is not None else "",
            "taskboard": snapshot.get("taskboard") if isinstance(snapshot.get("taskboard"), dict) else {},
            "updated_at": max(float(snapshot.get("created_at") or 0.0), event.created_at if event is not None else 0.0),
            "path": path,
        })
    result.sort(key=lambda item: float(item.get("updated_at") or 0.0), reverse=True)
    return result


def resolve_terminal_session(config: dict[str, Any] | None, sessions_dir: str | Path) -> tuple[Path | None, list[dict[str, Any]]]:
    cfg = config or {}
    candidates = terminal_session_candidates(cfg, sessions_dir)
    binding = desktop_binding(cfg)
    if binding:
        bound = str(binding.get("thread_id") or "")
        return next((item["path"] for item in candidates if item.get("thread_id") == bound), None), candidates
    if len(candidates) == 1:
        select_desktop_thread(cfg, str(candidates[0].get("thread_id") or ""))
        return candidates[0]["path"], candidates
    return None, candidates


def terminal_binding_status(
    config: dict[str, Any] | None,
    sessions_dir: str | Path,
    *,
    session_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Small renderer-neutral status for Desktop's existing dashboard card."""
    cfg = config or {}
    candidates = (
        terminal_session_candidates(cfg, sessions_dir)
        if session_candidates is None
        else list(session_candidates)
    )
    binding = desktop_binding(cfg)
    bound_id = str((binding or {}).get("thread_id") or "")
    bound = next((item for item in candidates if item.get("thread_id") == bound_id), None)
    if bound is not None:
        return {
            "state": "following",
            "detail": _summary(bound.get("intent") or "Live terminal", 96),
            "live_count": len(candidates),
            "bound": True,
        }
    if binding:
        return {
            "state": "offline",
            "detail": "Selected terminal is no longer live",
            "live_count": len(candidates),
            "bound": True,
        }
    if len(candidates) == 1:
        return {
            "state": "available",
            "detail": _summary(candidates[0].get("intent") or "One live terminal", 96),
            "live_count": 1,
            "bound": False,
        }
    if candidates:
        return {
            "state": "choose",
            "detail": f"{len(candidates)} live terminals",
            "live_count": len(candidates),
            "bound": False,
        }
    return {"state": "none", "detail": "No live terminal", "live_count": 0, "bound": False}


def _summary(value: Any, limit: int = 150) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]).rstrip(" ,.;:") + "…"


def _project_id(value: Any) -> str:
    try:
        root = Path(str(value or "")).expanduser().resolve(strict=False)
    except OSError:
        return ""
    return opaque_id("project", str(root)) if str(value or "").strip() else ""


def _session_focus(path: Path) -> str:
    try:
        messages = json.loads(path.read_text(encoding="utf-8")).get("messages") or []
    except (OSError, json.JSONDecodeError, AttributeError):
        return ""
    for message in reversed(messages):
        if isinstance(message, dict) and str(message.get("role") or "") == "user":
            focus = " ".join(str(message.get("content") or "").split())
            if focus:
                return focus[:240]
    return ""
