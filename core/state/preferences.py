"""Durable operator-selected runtime preferences.

``config.yaml`` remains the authored baseline. Interactive Terminal choices live
in one private overlay so slash commands can survive restarts without rewriting
that commented config. The overlay is read during Agent startup and ``/reload``;
every write merges under one cross-process lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..runtime.lock import file_byte_lock
from ..utils.atomic_write import atomic_write_json
from .paths import (
    RUNTIME_PREFERENCES_LOCK_PATH,
    RUNTIME_PREFERENCES_PATH,
    resolve_state_path,
    runtime_config_path,
)

_VERSION = 1
_TERMINAL_ROUTES = ("local", "host")   # where MO Desktop hands this project's implementation work
_THREAD_LOCK = threading.Lock()
_UNSET = object()


class RuntimePreferenceError(RuntimeError):
    """A runtime preference could not be read or saved safely."""


def _preference_path(config: dict[str, Any]) -> Path:
    if not runtime_config_path(config):
        raise RuntimePreferenceError("active config source is unavailable")
    return Path(resolve_state_path(RUNTIME_PREFERENCES_PATH, config))


def _preference_lock_path(config: dict[str, Any]) -> Path:
    return Path(resolve_state_path(RUNTIME_PREFERENCES_LOCK_PATH, config))


def _read_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": _VERSION, "terminal": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimePreferenceError(f"could not read {path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimePreferenceError(f"{path.name} must contain an object")
    version = value.get("version", _VERSION)
    if version != _VERSION:
        raise RuntimePreferenceError(f"unsupported {path.name} version: {version}")
    if not isinstance(value.get("terminal", {}), dict):
        raise RuntimePreferenceError(f"{path.name} terminal value must be an object")
    return value


def _normalized(raw: dict[str, Any]) -> dict[str, Any]:
    terminal = raw.get("terminal") if isinstance(raw.get("terminal"), dict) else {}
    result: dict[str, Any] = {"version": _VERSION, "terminal": {}}
    target = result["terminal"]
    model = terminal.get("model")
    if isinstance(model, dict):
        selection = {
            key: str(model.get(key) or "").strip()
            for key in ("source", "model", "thinking")
        }
        if all(selection.values()):
            target["model"] = selection
    show = terminal.get("show")
    if isinstance(show, dict):
        flags = {key: show[key] for key in ("reasoning", "tools") if isinstance(show.get(key), bool)}
        if flags:
            target["show"] = flags
    for key in ("hints", "activity"):
        if isinstance(terminal.get(key), bool):
            target[key] = terminal[key]
    projects = raw.get("projects")
    if isinstance(projects, dict):
        kept: dict[str, dict[str, Any]] = {}
        for key, row in projects.items():
            if not isinstance(key, str) or not isinstance(row, dict):
                continue
            entry: dict[str, Any] = {}
            if isinstance(row.get("lsp_enabled"), bool):
                entry["lsp_enabled"] = row["lsp_enabled"]
            if row.get("terminal_route") in _TERMINAL_ROUTES:
                entry["terminal_route"] = row["terminal_route"]
            if entry:
                kept[key] = entry
        result["projects"] = kept
    mail = raw.get("mail")
    if isinstance(mail, dict) and isinstance(mail.get("enabled"), bool):
        result["mail"] = {"enabled": mail["enabled"]}
    desktop = raw.get("desktop")
    if isinstance(desktop, dict) and isinstance(desktop.get("model"), dict):
        selection = {key: str(desktop["model"].get(key) or "").strip() for key in ("source", "model", "thinking")}
        if all(selection.values()):
            result["desktop"] = {"model": selection}
    graph = raw.get("graph")
    if isinstance(graph, dict):
        result["graph"] = {key: graph[key] for key in ("enabled", "auto_build", "context") if isinstance(graph.get(key), bool)}
    return result


def project_preference_key(root: str | Path) -> str:
    """Stable local identity without persisting a second project-path inventory."""
    canonical = os.path.normcase(str(Path(root).expanduser().resolve(strict=False)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def project_lsp_preference(config: dict[str, Any], root: str | Path) -> bool | None:
    if not runtime_config_path(config):
        return None
    row = load_runtime_preferences(config).get("projects", {}).get(project_preference_key(root), {})
    return row.get("lsp_enabled")


def persist_project_lsp_preference(config: dict[str, Any], root: str | Path, enabled: bool | None) -> None:
    """Save on/off for one project; None restores the authored config default."""
    if enabled is not None and not isinstance(enabled, bool):
        raise RuntimePreferenceError("LSP preference must be on, off, or default")
    path = _preference_path(config)
    with file_byte_lock(_preference_lock_path(config), _THREAD_LOCK):
        raw = _normalized(_read_raw(path))
        _set_project_field(raw, root, "lsp_enabled", enabled)
        atomic_write_json(path, raw, indent=2, sort_keys=True)


def _set_project_field(raw: dict[str, Any], root: str | Path, field: str, value: Any) -> None:
    """Set or (with None) clear one field of a project's row, keeping its other fields."""
    projects = raw.setdefault("projects", {})
    key = project_preference_key(root)
    row = dict(projects.get(key) or {})
    if value is None:
        row.pop(field, None)
    else:
        row[field] = value
    if row:
        projects[key] = row
    else:
        projects.pop(key, None)


def project_terminal_route(config: dict[str, Any], root: str | Path) -> str | None:
    """'local' or 'host' once chosen for this project in MO Desktop; None until then."""
    if not runtime_config_path(config):
        return None
    row = load_runtime_preferences(config).get("projects", {}).get(project_preference_key(root), {})
    return row.get("terminal_route")


def persist_project_terminal_route(config: dict[str, Any], root: str | Path, route: str | None) -> None:
    """Remember where this project's handed-off work runs; None forgets the choice."""
    if route is not None and route not in _TERMINAL_ROUTES:
        raise RuntimePreferenceError("terminal route must be local, host, or unset")
    if not runtime_config_path(config):
        return
    path = _preference_path(config)
    with file_byte_lock(_preference_lock_path(config), _THREAD_LOCK):
        raw = _normalized(_read_raw(path))
        _set_project_field(raw, root, "terminal_route", route)
        atomic_write_json(path, raw, indent=2, sort_keys=True)


def load_runtime_preferences(config: dict[str, Any]) -> dict[str, Any]:
    """Read and validate the private runtime-preference overlay."""
    path = _preference_path(config)
    with file_byte_lock(_preference_lock_path(config), _THREAD_LOCK):
        return _normalized(_read_raw(path))


def apply_runtime_preferences(config: dict[str, Any], preferences: dict[str, Any]) -> None:
    """Overlay validated Terminal display choices onto the in-memory config."""
    normalized = _normalized(preferences)
    terminal = normalized["terminal"]
    if "mail" in normalized:
        config.setdefault("mail", {})["enabled"] = normalized["mail"]["enabled"]
    show = terminal.get("show")
    if isinstance(show, dict):
        config.setdefault("interface", {}).setdefault("show", {}).update(show)
    if "hints" in terminal:
        config.setdefault("interface", {}).setdefault("hints", {})["enabled"] = terminal["hints"]
    if "activity" in terminal:
        config.setdefault("interface", {}).setdefault("activity", {})["enabled"] = terminal["activity"]


def persist_mail_enabled(config: dict[str, Any], enabled: bool) -> None:
    """Save the local Email opt-in in the existing private runtime preferences."""
    if not isinstance(enabled, bool):
        raise RuntimePreferenceError("Mail enabled must be on or off")
    path = _preference_path(config)
    with file_byte_lock(_preference_lock_path(config), _THREAD_LOCK):
        raw = _normalized(_read_raw(path))
        raw["mail"] = {"enabled": enabled}
        atomic_write_json(path, raw, indent=2, sort_keys=True)
    apply_runtime_preferences(config, raw)


def persist_desktop_model(config: dict[str, Any], selection: dict[str, str] | None) -> None:
    """Save Desktop's request selection; None follows the shared Terminal choice."""
    if selection is not None and (not isinstance(selection, dict) or
            any(not isinstance(selection.get(key), str) or not selection[key].strip() for key in ("source", "model", "thinking"))):
        raise RuntimePreferenceError("Desktop model selection is incomplete")
    with file_byte_lock(_preference_lock_path(config), _THREAD_LOCK):
        raw = _normalized(_read_raw(_preference_path(config)))
        if selection is None:
            raw.pop("desktop", None)
        else:
            raw["desktop"] = {"model": {key: selection[key] for key in ("source", "model", "thinking")}}
        atomic_write_json(_preference_path(config), raw, indent=2, sort_keys=True)


def graph_preferences(config: dict[str, Any] | None = None) -> dict[str, bool]:
    """Graph tools also run without an Agent/config file; use the same profile."""
    path = Path(resolve_state_path(RUNTIME_PREFERENCES_PATH, config))
    try:
        stamp = path.stat()
    except FileNotFoundError:
        return {}
    return dict(_cached_graph_preferences(str(path), stamp.st_mtime_ns, stamp.st_size))


@lru_cache(maxsize=8)
def _cached_graph_preferences(path: str, modified: int, size: int) -> tuple:
    # Writers replace the file atomically. Repeated graph queries only stat it;
    # they neither parse JSON repeatedly nor create a background watcher.
    return tuple(_normalized(_read_raw(Path(path))).get("graph", {}).items())


def persist_graph_preferences(config: dict[str, Any], key: str, value: bool | None) -> None:
    if key not in {"enabled", "auto_build", "context"} or (value is not None and not isinstance(value, bool)):
        raise RuntimePreferenceError("Unknown graph preference")
    with file_byte_lock(_preference_lock_path(config), _THREAD_LOCK):
        raw = _normalized(_read_raw(_preference_path(config)))
        graph = raw.setdefault("graph", {})
        if value is None:
            graph.pop(key, None)
        else:
            graph[key] = value
        atomic_write_json(_preference_path(config), raw, indent=2, sort_keys=True)


def persist_terminal_preferences(
    config: dict[str, Any],
    *,
    model: dict[str, Any] | object = _UNSET,
    show_reasoning: bool | object = _UNSET,
    show_tools: bool | object = _UNSET,
    hints: bool | object = _UNSET,
    activity: bool | object = _UNSET,
) -> dict[str, Any]:
    """Merge explicit Terminal preference changes and return the saved overlay."""
    path = _preference_path(config)
    with file_byte_lock(_preference_lock_path(config), _THREAD_LOCK):
        raw = _read_raw(path)
        terminal = raw.setdefault("terminal", {})
        raw["version"] = _VERSION
        if model is not _UNSET:
            if not isinstance(model, dict):
                raise RuntimePreferenceError("model preference must be an object")
            selection = {key: str(model.get(key) or "").strip() for key in ("source", "model", "thinking")}
            if not all(selection.values()):
                raise RuntimePreferenceError("model preference is incomplete")
            terminal["model"] = selection
        if show_reasoning is not _UNSET or show_tools is not _UNSET:
            show = terminal.setdefault("show", {})
            if show_reasoning is not _UNSET:
                show["reasoning"] = bool(show_reasoning)
            if show_tools is not _UNSET:
                show["tools"] = bool(show_tools)
        if hints is not _UNSET:
            terminal["hints"] = bool(hints)
        if activity is not _UNSET:
            terminal["activity"] = bool(activity)
        saved = _normalized(raw)
        atomic_write_json(path, saved, indent=2, sort_keys=True)
    apply_runtime_preferences(config, saved)
    return saved
