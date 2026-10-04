"""Bounded Windows application discovery and deterministic native launch.

The model never supplies a command line or executable path. Discovery creates
owner-scoped refs from installed App Paths and Start Menu entries; launch also
resolves one uniquely best catalog name. Packaged App Paths entries activate
through their registered AppsFolder identity instead of executing an internal
WindowsApps binary. This keeps startup native without adding another service or
routing model input through a shell.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_APPS = 80
MAX_APP_PATH_ENTRIES = 400
MAX_START_MENU_SHORTCUTS = 1_000


@dataclass(frozen=True)
class DiscoveredApp:
    ref: str
    name: str
    source: str
    path: str
    launch_kind: str


@dataclass(frozen=True)
class AppResolution:
    status: str
    query: str
    app: DiscoveredApp | None = None
    candidates: tuple[DiscoveredApp, ...] = ()


@dataclass(frozen=True)
class AppLaunchResult:
    status: str
    query: str
    app: DiscoveredApp | None = None
    pid: int = 0
    candidates: tuple[DiscoveredApp, ...] = ()
    detail: str = ""


_APPS_BY_OWNER: dict[str, dict[str, DiscoveredApp]] = {}


def discover_apps(*, query: str = "", max_results: int = 40) -> list[DiscoveredApp]:
    """Return a bounded exact-ref catalog for the current owner."""
    from .runtime import current_owner_id
    try:
        requested = int(max_results or 40)
    except (TypeError, ValueError):
        requested = 40
    limit = max(1, min(requested, MAX_APPS))
    selected = installed_apps(query=query, max_results=limit)
    _APPS_BY_OWNER[current_owner_id()] = {app.ref: app for app in selected}
    return selected


def installed_apps(*, query: str = "", max_results: int = 1_000) -> list[DiscoveredApp]:
    """Read the same bounded native catalog without replacing a tool owner's refs."""
    query_text = str(query or "").strip().casefold()
    candidates: dict[str, tuple[str, str, str, str]] = {}
    if os.name == "nt":
        _collect_app_paths(candidates)
        _collect_start_menu(candidates)
        _collect_system_fallbacks(candidates)
    apps: list[DiscoveredApp] = []
    for _path_key, (name, source, launch_kind, path) in candidates.items():
        if query_text and query_text not in f"{name} {Path(path).name}".casefold():
            continue
        digest = hashlib.sha256(f"{launch_kind}\0{path.casefold()}".encode("utf-8", errors="replace")).hexdigest()[:10]
        apps.append(
            DiscoveredApp(
                ref=f"app-{digest}",
                name=name,
                source=source,
                path=path,
                launch_kind=launch_kind,
            )
        )
    apps.sort(key=lambda item: (item.name.casefold(), item.source, item.path.casefold()))
    return apps[:max(1, min(int(max_results), 1_000))]


def pinned_taskbar_apps() -> list[DiscoveredApp]:
    """Read Explorer's actual per-user taskbar shortcuts; never MO favorites."""
    if os.name != "nt":
        return []
    from win32com.shell import shell, shellcon
    folder = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_APPDATA, 0, 0)) / "Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar"
    try:
        paths = sorted(folder.glob("*.lnk"), key=lambda path: path.name.casefold())[:80]
    except OSError:
        return []
    return [DiscoveredApp(str(path), path.stem, "taskbar", str(path), "shortcut") for path in paths]


def format_discovered_apps(*, query: str = "", max_results: int = 40) -> str:
    apps = discover_apps(query=query, max_results=max_results)
    if not apps:
        suffix = f" matching {query!r}" if str(query or "").strip() else ""
        return f"[computer targets: applications]\n  (no installed applications{suffix})"
    lines = ["[computer targets: applications]"]
    lines.extend(f"  [{app.ref}] {app.name!r} source={app.source}" for app in apps)
    return "\n".join(lines)


def resolve_app(value: str) -> AppResolution:
    """Resolve one exact owner ref or one uniquely best installed-app name."""
    from .runtime import current_owner_id

    query = str(value or "").strip()
    owner = current_owner_id()
    catalog = _APPS_BY_OWNER.setdefault(owner, {})
    if not query:
        return AppResolution("missing", query)
    if query in catalog:
        return AppResolution("resolved", query, app=catalog[query])
    if query.startswith("app-"):
        return AppResolution("stale", query)

    ranked = [
        (score, app)
        for app in installed_apps(max_results=1_000)
        if (score := _app_match_score(app, query)) is not None
    ]
    if not ranked:
        return AppResolution("missing", query)
    best_score = min(score for score, _app in ranked)
    best = tuple(app for score, app in ranked if score == best_score)
    catalog.update((app.ref, app) for app in best)
    if len(best) != 1:
        return AppResolution("ambiguous", query, candidates=best[:8])
    return AppResolution("resolved", query, app=best[0], candidates=best)


def launch_resolved_app(resolved: AppResolution) -> AppLaunchResult:
    """Dispatch a prior safe resolution; process dispatch is not outcome verification."""
    from .runtime import native_desktop_scope

    if resolved.app is None:
        return AppLaunchResult(
            resolved.status,
            resolved.query,
            candidates=resolved.candidates,
        )
    app = resolved.app
    try:
        if app.launch_kind == "packaged":
            with native_desktop_scope(invalidate=True):
                os.startfile(app.path)  # type: ignore[attr-defined]
            return AppLaunchResult("dispatched", resolved.query, app=app)
        path = Path(app.path)
        if not path.is_file():
            return AppLaunchResult(
                "missing", resolved.query, app=app,
                detail=f"Discovered application {app.name!r} is no longer available.",
            )
        if app.launch_kind == "shortcut":
            with native_desktop_scope(invalidate=True):
                os.startfile(str(path))  # type: ignore[attr-defined]
            return AppLaunchResult("dispatched", resolved.query, app=app)
        from core.runtime.subprocess_flags import apply_windows_hidden_process_flags

        kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        apply_windows_hidden_process_flags(kwargs)
        with native_desktop_scope(invalidate=True):
            process = subprocess.Popen([str(path)], **kwargs)
        return AppLaunchResult("dispatched", resolved.query, app=app, pid=int(process.pid or 0))
    except Exception as exc:  # noqa: BLE001
        return AppLaunchResult(
            "failed", resolved.query, app=app,
            detail=f"{type(exc).__name__}: {exc}",
        )


def format_launch_result(result: AppLaunchResult) -> str:
    """Render a truthful dispatch result without claiming that a window opened."""
    if result.status == "ambiguous":
        matches = ", ".join(f"[{app.ref}] {app.name!r}" for app in result.candidates)
        return f"Error: application name {result.query!r} is ambiguous; nothing launched. Matches: {matches}"
    if result.status == "stale":
        return "Error: application ref is missing or stale; discover applications again or pass an installed-app name."
    if result.status == "missing":
        return result.detail or f"Error: no installed application uniquely matches {result.query!r}; nothing launched."
    if result.status == "failed":
        return f"Error: application launch failed: {result.detail}"
    assert result.app is not None
    return f"Launch of {result.app.name!r} was dispatched; its window is not yet verified."


def _app_match_score(app: DiscoveredApp, query: str) -> int | None:
    query_tokens = _match_tokens(query)
    if not query_tokens:
        return None
    query_text = " ".join(query_tokens)
    values = (app.name, Path(app.path).stem)
    normalized = [(" ".join(_match_tokens(value)), set(_match_tokens(value))) for value in values]
    if any(query_text == text for text, _tokens in normalized):
        return 0
    if any(text.startswith(query_text) for text, _tokens in normalized):
        return 1
    if any(set(query_tokens) <= tokens for _text, tokens in normalized):
        return 2
    compact = "".join(query_tokens)
    if any(compact in text.replace(" ", "") for text, _tokens in normalized):
        return 3
    return None


def _match_tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def reset_app_cache(*, owner_id: str | None = None) -> None:
    if owner_id is None:
        _APPS_BY_OWNER.clear()
    else:
        _APPS_BY_OWNER.pop(owner_id, None)


def _add_candidate(
    candidates: dict[str, tuple[str, str, str, str]],
    path: str | Path,
    *,
    name: str,
    source: str,
    launch_kind: str,
) -> None:
    if launch_kind == "packaged":
        target = str(path or "").strip()
        if not re.fullmatch(r"shell:AppsFolder\\[A-Za-z0-9._-]+![A-Za-z0-9._-]+", target):
            return
        candidates.setdefault(
            target.casefold(),
            (str(name or "application").strip(), source, launch_kind, target),
        )
        return
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except Exception:
        return
    suffix = resolved.suffix.casefold()
    if launch_kind == "shortcut" and suffix != ".lnk":
        return
    if launch_kind == "executable" and suffix not in {".exe", ".com"}:
        return
    candidates.setdefault(
        str(resolved).casefold(),
        (str(name or resolved.stem).strip(), source, launch_kind, str(resolved)),
    )


def _packaged_activation_target(executable: str | Path) -> str:
    """Return a registered AppsFolder target for one packaged App Paths binary."""
    try:
        resolved = Path(executable).expanduser().resolve(strict=True)
    except Exception:
        return ""
    package_root: Path | None = None
    manifest: Path | None = None
    for parent in list(resolved.parents)[:6]:
        candidate = parent / "AppxManifest.xml"
        if candidate.is_file() and "__" in parent.name:
            package_root = parent
            manifest = candidate
            break
    if package_root is None or manifest is None:
        return ""
    try:
        relative = resolved.relative_to(package_root).as_posix().casefold()
        root = ET.parse(manifest).getroot()
    except (OSError, ValueError, ET.ParseError):
        return ""
    identity = next(
        (element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "Identity"),
        None,
    )
    applications = [
        element
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "Application"
        and str(element.attrib.get("Executable") or "").replace("\\", "/").casefold() == relative
    ]
    if identity is None or len(applications) != 1:
        return ""
    package_name = str(identity.attrib.get("Name") or "").strip()
    app_id = str(applications[0].attrib.get("Id") or "").strip()
    publisher_id = package_root.name.rsplit("__", 1)[-1].strip()
    if not all(
        re.fullmatch(r"[A-Za-z0-9._-]+", value)
        for value in (package_name, publisher_id, app_id)
    ):
        return ""
    return f"shell:AppsFolder\\{package_name}_{publisher_id}!{app_id}"


def _collect_app_paths(candidates: dict[str, tuple[str, str, str, str]]) -> None:
    try:
        import winreg
    except Exception:
        return
    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    key_path = r"Software\Microsoft\Windows\CurrentVersion\App Paths"
    for root in roots:
        for view in dict.fromkeys(views):
            try:
                base = winreg.OpenKey(root, key_path, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with base:
                index = 0
                while index < MAX_APP_PATH_ENTRIES:
                    try:
                        subkey_name = winreg.EnumKey(base, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(base, subkey_name) as subkey:
                            value, _kind = winreg.QueryValueEx(subkey, None)
                    except OSError:
                        continue
                    executable = os.path.expandvars(str(value).strip().strip('"'))
                    packaged_target = _packaged_activation_target(executable)
                    _add_candidate(
                        candidates,
                        packaged_target or executable,
                        name=Path(subkey_name).stem,
                        source="app-paths",
                        launch_kind="packaged" if packaged_target else "executable",
                    )


def _collect_start_menu(candidates: dict[str, tuple[str, str, str, str]]) -> None:
    roots = (
        Path(os.environ.get("PROGRAMDATA") or "") / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA") or "") / "Microsoft/Windows/Start Menu/Programs",
    )
    for root in roots:
        if not root.is_dir():
            continue
        try:
            shortcuts = root.rglob("*.lnk")
            for index, shortcut in enumerate(shortcuts):
                if index >= MAX_START_MENU_SHORTCUTS:
                    break
                _add_candidate(
                    candidates,
                    shortcut,
                    name=shortcut.stem,
                    source="start-menu",
                    launch_kind="shortcut",
                )
        except OSError:
            continue


def _collect_system_fallbacks(candidates: dict[str, tuple[str, str, str, str]]) -> None:
    for executable in ("notepad.exe", "mspaint.exe", "calc.exe", "explorer.exe"):
        path = shutil.which(executable)
        if path:
            _add_candidate(
                candidates,
                path,
                name=Path(executable).stem,
                source="system",
                launch_kind="executable",
            )
