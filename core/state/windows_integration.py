"""MO's own Windows integration: "Open with MO" on folder menus and MO in Settings → Apps.

`mo --init` writes it and `mo --uninstall` (also run by Settings → Apps → MO → Uninstall) removes
it. Only MO's own keys under the current user's classes, so no administrator rights. Windows 11
lists such folder entries under "Show more options" (Shift+F10); its short menu takes only
packaged apps. A key MO did not write is preserved and reported, never replaced.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any

MENU_KEYS = (r"Software\Classes\Directory\shell\MO", r"Software\Classes\Directory\Background\shell\MO")
_MENU_NAMES = dict(zip(MENU_KEYS, ("folder menu (folders)", "folder menu (inside folders)")))
APP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MO"
MENU_LABEL = "Open with MO"
ICON_NAME = "mo.ico"
_SHELL_FLAG = ' --shell "%V"'          # what marks a folder-menu command as MO's own
_UNINSTALL_FLAG = " --uninstall"


def _registry(registry: Any = None) -> Any:
    """The registry to change: the one given, else this user's on Windows. An isolated private home
    (MO_STATE_HOME: tests, synthetic runs) never repoints the user's real entries; None then."""
    if registry is not None:
        return registry
    from core.state.paths import ENV_MO_STATE_HOME

    if sys.platform != "win32" or os.environ.get(ENV_MO_STATE_HOME):
        return None
    import winreg

    return winreg


def _commands(mo_py: Path) -> tuple[str, str]:
    """The folder-menu and Settings uninstall commands, both without a console window of their own:
    MO Shell is the window, and `mo.py` answers in a Windows message when it has no console."""
    from core.runtime.subprocess_flags import gui_python_executable

    python = gui_python_executable()
    return f'"{python}" "{mo_py}"{_SHELL_FLAG}', f'"{python}" "{mo_py}"{_UNINSTALL_FLAG}'


def _read(registry: Any, key: str, name: str = "") -> str | None:
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, key) as handle:
            return str(registry.QueryValueEx(handle, name)[0])
    except OSError:
        return None


def _write(registry: Any, key: str, values: dict[str, Any]) -> None:
    with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, key, 0, registry.KEY_SET_VALUE) as handle:
        for name, value in values.items():
            kind = registry.REG_DWORD if isinstance(value, int) else registry.REG_SZ
            registry.SetValueEx(handle, name, 0, kind, value)


def _delete_tree(registry: Any, key: str) -> None:
    """Remove one of MO's own keys and the subkeys MO put under it."""
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, key) as handle:
            children = []
            while True:
                try:
                    children.append(registry.EnumKey(handle, len(children)))
                except OSError:
                    break
    except OSError:
        return
    for child in children:
        _delete_tree(registry, key + "\\" + child)
    registry.DeleteKey(registry.HKEY_CURRENT_USER, key)


def _ours(command: str | None, flag: str) -> bool:
    return bool(command) and flag in command and "mo.py" in command


def _write_icon(path: Path, report: Any) -> str:
    """The MO mark in the current skin as a Windows icon; "" when it cannot be drawn here."""
    try:
        from interface.desktop_brand import make_four_cube_ico
        from interface.desktop_ui import active_desktop_visual_state

        payload = make_four_cube_ico(palette=active_desktop_visual_state().palette)
    except ImportError:                                   # Pillow comes with the Desktop extras
        report.warnings.append("Open with MO has no MO icon yet: install the Desktop extras "
                               "(requirements-computer-use.txt), then run `mo --init` again.")
        return ""
    existed = path.exists()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    except OSError:
        report.warnings.append(f"Could not write bin/{ICON_NAME}; Open with MO shows no icon.")
        return ""
    (report.existing if existed else report.created).append("bin/" + ICON_NAME)
    return str(path)


def install(bin_dir: Path, report: Any, *, registry: Any = None) -> None:
    """Write MO's folder-menu entry and Settings → Apps entry; refresh them when the checkout moved."""
    registry = _registry(registry)
    if registry is None:
        return
    from core.state.paths import repo_root
    from core.update.version import current_version

    mo_py = Path(repo_root()).resolve(strict=False) / "mo.py"
    menu_command, uninstall_command = _commands(mo_py)
    icon = _write_icon(bin_dir / ICON_NAME, report)
    for key in MENU_KEYS:
        current = _read(registry, key + r"\command")
        if current is not None and not _ours(current, _SHELL_FLAG):
            report.warnings.append(f"Preserved another program's folder-menu entry at HKCU\\{key}.")
            continue
        _write(registry, key, {"MUIVerb": MENU_LABEL, **({"Icon": icon} if icon else {})})
        _write(registry, key + r"\command", {"": menu_command})
        if current is None:
            report.created.append(f"{MENU_LABEL}: {_MENU_NAMES[key]}")
        elif current != menu_command:
            report.migrations.append(f"refreshed {MENU_LABEL}: {_MENU_NAMES[key]}")
    current = _read(registry, APP_KEY, "UninstallString")
    if current is not None and not _ours(current, _UNINSTALL_FLAG):
        report.warnings.append(f"Preserved another program's Settings → Apps entry at HKCU\\{APP_KEY}.")
        return
    _write(registry, APP_KEY, {"DisplayName": "MO", "DisplayVersion": current_version().removeprefix("MO "),
                               "InstallLocation": str(mo_py.parent), "UninstallString": uninstall_command,
                               "NoModify": 1, "NoRepair": 1, **({"DisplayIcon": icon} if icon else {})})
    (report.created if current is None else report.existing).append("Settings → Apps: MO")


def uninstall(home: Path, *, registry: Any = None) -> list[str]:
    """Remove what `mo --init` put outside the private home and the generated launchers in it.

    Config, credentials, memory and every other private file stay; so does this checkout.
    Returns plain lines saying what was removed or preserved."""
    from core.state.initializer import _looks_like_generated_shim

    lines: list[str] = []
    registry = _registry(registry)
    if registry is not None:
        for key, flag, label in (*((key, _SHELL_FLAG, name) for key, name in _MENU_NAMES.items()),
                                 (APP_KEY, _UNINSTALL_FLAG, "Settings → Apps entry")):
            command = _read(registry, key + r"\command") if flag == _SHELL_FLAG else _read(registry, key, "UninstallString")
            if command is None:
                continue
            if _ours(command, flag):
                _delete_tree(registry, key)
                lines.append("Removed " + label + ".")
            else:
                lines.append(f"Preserved another program's {label} at HKCU\\{key}.")
    bin_dir = home / "bin"
    icon = bin_dir / ICON_NAME
    if icon.is_file():
        icon.unlink()
        lines.append("Removed bin/" + ICON_NAME + ".")
    for name in ("mo", "mo.cmd"):
        shim = bin_dir / name
        try:
            generated = shim.is_file() and _looks_like_generated_shim(shim.read_text(encoding="utf-8"))
        except OSError:
            generated = False
        if generated:
            shim.unlink()
            lines.append(f"Removed the bin/{name} launcher.")
        elif shim.exists():
            lines.append(f"Preserved your custom bin/{name}.")
    lines.append(f"Kept your private home {home} (config, credentials, memory) and this MO folder; "
                 "delete them yourself if you no longer want them. `mo --init` puts everything back.")
    return lines
