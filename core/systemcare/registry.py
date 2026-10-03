"""Explained registry inspection and exact, typed value recovery.

Missing-path evidence can nominate a value. Category names and a confidence
number never authorize deleting a key or its descendants.
"""
from __future__ import annotations

import base64
import os
import re
import time
from pathlib import Path
from typing import Any, Callable


FAMILIES = (
    ("Startup", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("Startup once", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    ("Uninstall registrations", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("32-bit uninstall registrations", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("Application paths", r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
    ("Application registrations", r"Software\Classes\Applications"),
    ("COM classes", r"Software\Classes\CLSID"),
    ("COM interfaces", r"Software\Classes\Interface"),
    ("Type libraries", r"Software\Classes\TypeLib"),
    ("File extensions", r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"),
    ("Shared libraries", r"Software\Microsoft\Windows\CurrentVersion\SharedDLLs"),
    ("Fonts", r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"),
    ("Help files", r"Software\Microsoft\Windows\Help"),
    ("Shell extensions", r"Software\Microsoft\Windows\CurrentVersion\Shell Extensions"),
    ("Explorer handlers", r"Software\Classes\*\shellex"),
    ("Directory handlers", r"Software\Classes\Directory\shellex"),
    ("Folder handlers", r"Software\Classes\Folder\shellex"),
    ("Context menu registrations", r"Software\Classes\Directory\shell"),
    ("URL protocols", r"Software\Classes\http"),
    ("Installed components", r"Software\Microsoft\Active Setup\Installed Components"),
    ("Installer registrations", r"Software\Microsoft\Windows\CurrentVersion\Installer\UserData"),
    ("Service registrations", r"SYSTEM\CurrentControlSet\Services"),
    ("Application compatibility", r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags"),
    ("Windows event registrations", r"SYSTEM\CurrentControlSet\Services\EventLog"),
)
_SENSITIVE = re.compile(r"password|passwd|credential|token|secret|private.?key", re.I)
_PATH_VALUE = re.compile(r"^(?:[A-Za-z]:\\[^\r\n<>|*?]+|%[^%]+%\\[^\r\n<>|*?]+)$")
_GUID = re.compile(r"\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}")


def encode_value(value: Any, value_type: int) -> dict[str, Any]:
    return {"type": int(value_type), "data": base64.b64encode(value).decode("ascii") if isinstance(value, bytes) else value,
            "binary": isinstance(value, bytes)}


def decode_value(value: dict[str, Any]) -> tuple[Any, int]:
    data = base64.b64decode(value["data"], validate=True) if value.get("binary") else value["data"]
    return data, int(value["type"])


def _hive(name: str) -> Any:
    import winreg
    if name not in {"HKCU", "HKLM"}:
        raise ValueError("Unsupported registry hive")
    return winreg.HKEY_CURRENT_USER if name == "HKCU" else winreg.HKEY_LOCAL_MACHINE


def _validate(subject: dict[str, Any]) -> None:
    key = str(subject.get("key", ""))
    if subject.get("kind") == "environment":
        expected = environment_subject(str(subject.get("scope", "")))
        if any(subject.get(k) != expected[k] for k in ("hive", "key", "value_name")):
            raise ValueError("Select the exact scoped PATH owner")
        return
    if not any(key == root or key.startswith(root + "\\") for _, root in (*FAMILIES, ("32-bit startup", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"))):
        raise ValueError("Registry value is outside the declared inspection families")
    if _SENSITIVE.search(str(subject.get("value_name", ""))) or ".." in key.split("\\"):
        raise ValueError("Registry value is protected")


def environment_subject(scope: str) -> dict[str, Any]:
    if scope not in {"User", "Machine"}:
        raise ValueError("Choose User or Machine PATH explicitly")
    return {"kind": "environment", "scope": scope, "hive": "HKCU" if scope == "User" else "HKLM",
            "key": "Environment" if scope == "User" else r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            "value_name": "Path"}


def path_without_duplicate(original: dict[str, Any], index: int) -> dict[str, Any]:
    if not original.get("exists") or original.get("type") not in {1, 2} or not isinstance(original.get("data"), str):
        raise ValueError("A typed PATH value is required")
    parts = original["data"].split(";")
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(parts):
        raise ValueError("Select an exact PATH entry")
    normalized = lambda part: part.strip().strip('"').casefold()
    if not normalized(parts[index]) or not any(normalized(p) == normalized(parts[index]) for p in parts[:index]):
        raise ValueError("This PATH entry is not an exact earlier duplicate")
    return {**original, "data": ";".join(parts[:index] + parts[index + 1:])}


def read_value(subject: dict[str, Any]) -> dict[str, Any]:
    import winreg
    _validate(subject)
    try:
        with winreg.OpenKey(_hive(subject["hive"]), subject["key"], 0, winreg.KEY_READ) as key:
            value, value_type = winreg.QueryValueEx(key, subject["value_name"])
        return {"exists": True, **encode_value(value, value_type)}
    except FileNotFoundError:
        return {"exists": False}


def write_value(subject: dict[str, Any], original: dict[str, Any]) -> None:
    import winreg
    _validate(subject)
    with winreg.OpenKey(_hive(subject["hive"]), subject["key"], 0, winreg.KEY_SET_VALUE) as key:
        if original["exists"]:
            value, value_type = decode_value(original)
            winreg.SetValueEx(key, subject["value_name"], 0, value_type, value)
        else:
            winreg.DeleteValue(key, subject["value_name"])


def missing_target(value: Any, adapter: Any) -> tuple[str, bool]:
    if not isinstance(value, str):
        return "", False
    raw = value.strip()
    if raw.startswith('"') and raw.endswith('"') and raw.count('"') == 2:
        raw = raw[1:-1]
    # A command or DLL entry-point is not a literal file owner. In particular,
    # stripping quotes from '"existing.exe" --flag' fabricates an absent path.
    if '"' in raw or not _PATH_VALUE.fullmatch(raw):
        return "", False
    extensions = r"exe|dll|ocx|cpl|sys|tlb|olb|ttf|otf|fon|chm|hlp|bat|cmd|ps1|vbs"
    if not re.search(r"\.(?:" + extensions + r")$", raw, re.I) or re.search(r"\.(?:" + extensions + r")(?:\s|,)", raw, re.I):
        return "", False
    target = os.path.expandvars(raw)
    # Unexpanded variables, network paths, commands and unsupported drive owners
    # require review rather than an absence conclusion.
    if "%" in target or not re.match(r"^[A-Za-z]:\\", target):
        return target, False
    drive = Path(target).anchor
    if not Path(drive).exists():
        return target, False
    try:
        Path(target).stat()
    except FileNotFoundError:
        return target, True
    except OSError:
        return target, False
    return target, False


def eligible_reference(subject: dict[str, Any], original: dict[str, Any]) -> bool:
    """Only declared exact file or merged class-link references can be repaired."""
    key, name = subject.get("key", ""), subject.get("value_name", "")
    direct = key in {r"Software\Microsoft\Windows\CurrentVersion\Run", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"}
    app_path = key.startswith(r"Software\Microsoft\Windows\CurrentVersion\App Paths" + "\\") and name == ""
    com_file = key.startswith(r"Software\Classes\CLSID" + "\\") and key.rsplit("\\", 1)[-1] in {"InprocServer32", "LocalServer32"} and name == ""
    app_command = key.startswith(r"Software\Classes\Applications" + "\\") and key.casefold().endswith(r"\shell\open\command") and name == ""
    font = key == r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
    class_link = ("\\shellex\\" in key.casefold() or key.rsplit("\\", 1)[-1].casefold() in {"proxystubclsid", "proxystubclsid32", "clsid", "typelib"}) and bool(_GUID.fullmatch(str(original.get("data", ""))))
    approved = key == r"Software\Microsoft\Windows\CurrentVersion\Shell Extensions\Approved" and bool(_GUID.fullmatch(name))
    return bool(original.get("exists") and original.get("type") in {1, 2} and (direct or app_path or com_file or app_command or font or class_link or approved))


def _class_registration(path: str) -> bool | None:
    """Check merged user/machine classes in both Windows views; denied is unknown."""
    import winreg
    denied = False
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, path, 0, winreg.KEY_READ | view):
                return True
        except FileNotFoundError:
            continue
        except OSError:
            denied = True
    return None if denied else False


def reference_evidence(family: str, path: str, name: str, value: Any, value_type: int,
                       adapter: Any) -> tuple[str, bool, str]:
    """Return checked reference identity, absence and its actual owner kind."""
    if family == "Shared libraries" and value_type == 4:
        target, absent = missing_target(name, adapter)
        return target, absent, "shared-library file; reference count alone does not authorize removal"
    if value_type not in {1, 2} or not isinstance(value, str):
        return "", False, ""
    target, absent = missing_target(value, adapter)
    if target:
        return target, absent, "local file"
    # Only an explicitly quoted executable token is unambiguous. Preserve the
    # full typed command original; arguments are never part of an absence test.
    command = re.fullmatch(r'"([^"\r\n]+\.exe)"(?:\s+[^\r\n]*)?', value.strip(), re.I)
    if command and (path.casefold().endswith(r"\command") or path.rsplit("\\", 1)[-1] in {"Run", "RunOnce", "LocalServer32"}):
        target, absent = missing_target(command[1], adapter)
        return target, absent, "quoted executable command target"
    if family == "Fonts" and re.fullmatch(r"[^\\/:*?<>|]+\.(?:ttf|otf|fon)", value, re.I):
        fonts = Path(os.environ.get("SystemRoot", "")) / "Fonts"
        if fonts.is_absolute():
            target, absent = missing_target(str(fonts / value), adapter)
            return target, absent, "Windows Fonts file"
    registry_path = ""
    leaf = path.rsplit("\\", 1)[-1].casefold()
    if _GUID.fullmatch(value) and ("\\shellex\\" in path.casefold() or leaf in {"proxystubclsid", "proxystubclsid32", "clsid"}):
        registry_path = "CLSID\\" + value
    elif family == "Shell extensions" and _GUID.fullmatch(name):
        registry_path = "CLSID\\" + name
    elif _GUID.fullmatch(value) and leaf == "typelib":
        registry_path = "TypeLib\\" + value
    elif ((family == "File extensions" and name.casefold() == "progid") or
          (family == "COM classes" and not name and leaf in {"progid", "versionindependentprogid"})) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}", value):
        registry_path = value
    if registry_path:
        present = _class_registration(registry_path)
        return "HKCR\\" + registry_path, present is False, "merged class registration in both Windows views" if present is not None else "class-registration access unavailable"
    return "", False, ""


def inspect_registry(adapter: Any, *, cancelled: Callable[[], bool]) -> dict[str, Any]:
    if os.name != "nt":
        return {"state": "unavailable", "rows": [], "detail": "Windows registry owner unavailable", "at": time.time()}
    import winreg
    rows, coverage = [], []
    total = 0
    deadline = time.monotonic() + 20
    for family, root in FAMILIES:
        for hive_name in ("HKCU", "HKLM"):
            stack, examined, denied, checked, depth_limited, values_limited = [(root, 0)], 0, 0, 0, False, False
            while stack and examined < 200 and total < 8_000 and len(rows) < 300 and time.monotonic() < deadline:
                if cancelled():
                    from .windows import ScanCancelled
                    raise ScanCancelled("registry inspection cancelled")
                path, depth = stack.pop()
                try:
                    with winreg.OpenKey(_hive(hive_name), path, 0, winreg.KEY_READ) as key:
                        children, values, _ = winreg.QueryInfoKey(key)
                        examined += 1
                        total += 1
                        values_limited |= values > 80
                        for index in range(min(values, 80)):
                            if time.monotonic() >= deadline:
                                values_limited = True
                                break
                            name, value, value_type = winreg.EnumValue(key, index)
                            if _SENSITIVE.search(name):
                                continue
                            target, missing, reference_kind = reference_evidence(family, path, name, value, value_type, adapter)
                            if not target:
                                continue
                            checked += 1
                            # Service and installer data have their own dependency
                            # owners; a missing file alone cannot remove their values.
                            subject = {"kind": "registry", "hive": hive_name, "key": path, "value_name": name, "target": target, "family": family}
                            original = {"exists": True, **encode_value(value, value_type)}
                            eligible = missing and eligible_reference(subject, original)
                            if missing:
                                rows.append({"name": family + " · " + (name or "(default)"), "state": "Review",
                                             "detail": "Absent " + reference_kind + ": " + target,
                                             "eligible": eligible, "subject": subject, "original": original})
                            if len(rows) >= 300:
                                break
                        if depth < (4 if family == "Application registrations" else 3):
                            stack.extend((path + "\\" + winreg.EnumKey(key, i), depth + 1) for i in range(min(children, 200)))
                            depth_limited |= children > 200
                        else:
                            depth_limited |= children > 0
                except FileNotFoundError:
                    pass
                except OSError:
                    denied += 1
            coverage.append({"family": family, "hive": hive_name, "examined": examined,
                             "reference_checks": checked, "bounded": bool(stack) or examined >= 200 or depth_limited or values_limited,
                             "denied": denied, "time_limited": time.monotonic() >= deadline})
    return {"state": "partial", "at": time.time(), "rows": rows, "coverage": coverage,
            "detail": "24 declared families, two hives, bounded depth/count and 20-second traversal budget. Local-file, shared-library and merged class references are checked; only eligible exact missing-target values can be repaired. Coverage records excluded/limited reads; this is not a complete registry health score."}
