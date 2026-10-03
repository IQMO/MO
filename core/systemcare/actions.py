"""Fixed native action executors for the existing SystemCare plan owner."""
from __future__ import annotations

import ctypes
import base64
import json
import os
import re
import shutil
import sys
import time
from typing import Any, Callable

from .models import PlanStep, canonical_digest


_PROTECTED_SERVICES = {"rpcss", "dcomlaunch", "rpceptmapper", "plugplay", "eventlog", "samss", "lsm",
                       "bfe", "mpssvc", "windefend", "wscsvc", "cryptsvc", "keyiso", "winmgmt"}
_NATIVE_COMMANDS = {
    "integrity_repair": ("sfc.exe", "/scannow"),
    "component_repair": ("dism.exe", "/Online", "/Cleanup-Image", "/RestoreHealth", "/English"),
    "component_cleanup": ("dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup", "/English"),
    "network_dns": ("ipconfig.exe", "/flushdns"),
}


def validate_subject(action: str, subject: dict[str, Any]) -> None:
    kinds = {"registry_remove": "registry", "path_remove_duplicate": "environment", "startup_disable": "startup", "startup_shortcut_disable": "startup_file", "game_on": "game", "game_off": "game",
             "disk_optimize": "volume", "app_uninstall": "app", "app_update": "package", "driver_remove": "driver",
             "browser_history_clear": "browser", "recycle_remove": "recycle", "app_leftover_remove": "app_leftover",
             "windows_update": "update", "desktop_refresh": "native", **{name: "native" for name in _NATIVE_COMMANDS}}
    expected = "service" if action.startswith("service_") else "task" if action.startswith("task_") else kinds.get(action)
    if action == "app_uninstall" and subject.get("kind") == "appx":
        expected = "appx"
    if expected is None or subject.get("kind") != expected:
        raise ValueError("Selected subject does not belong to this native action")
    if expected == "native" and subject.get("owner") != action:
        raise ValueError("Native owner changed")
    if expected == "appx" and subject.get("scope") != "current-user":
        raise ValueError("Windows package removal is scoped to the current account")
    if expected == "startup" and subject.get("key") not in {r"Software\Microsoft\Windows\CurrentVersion\Run", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"}:
        raise ValueError("Selected value is outside the startup owner")
    if expected == "game" and subject.get("power_guid") and not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", str(subject["power_guid"])):
        raise ValueError("An exact supported power scheme is required")
    if expected == "task" and any(c in str(subject.get("name", "")) + str(subject.get("path", "")) for c in "*?[]"):
        raise ValueError("Ambiguous task selectors are unavailable")


def ps_query(adapter: Any, script: str, *, timeout: int = 30,
             cancelled: Callable[[], bool] | None = None) -> Any:
    executable = shutil.which("powershell.exe")
    if not executable or os.name != "nt":
        raise RuntimeError("Windows PowerShell query owner is unavailable")
    result = adapter._run_read_command([executable, "-NoProfile", "-NonInteractive", "-Command",
        "$ErrorActionPreference='Stop';[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();" + script],
        timeout=timeout, output_limit=128_000, **({"cancelled": cancelled} if cancelled is not None else {}))
    if result["returncode"] or result.get("truncated"):
        raise RuntimeError("Native owner did not return complete evidence")
    return json.loads(result["stdout"])


def _literal(value: str) -> str:
    if len(value) > 1024 or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid native item identifier")
    return "'" + value.replace("'", "''") + "'"


def _service_name(subject: dict[str, Any]) -> str:
    name = str(subject.get("name", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", name):
        raise ValueError("Invalid service identifier")
    return name


def capture(adapter: Any, subject: dict[str, Any], *, state: Any = None,
            cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    kind = subject.get("kind")
    if kind == "browser":
        from .browser_care import capture_browser
        return capture_browser(adapter, subject)
    if kind == "recycle":
        from .recycle import capture_recycle
        return capture_recycle(adapter, subject)
    if kind == "app_leftover":
        from .storage import capture_app_leftover
        return capture_app_leftover(adapter, subject, state)
    if kind == "service":
        name = _service_name(subject)
        return ps_query(adapter, f"$s=Get-Service -Name {_literal(name)}; $c=Get-CimInstance Win32_Service -Filter {_literal('Name='+chr(39)+name+chr(39))};"
            f"$delay=(Get-ItemProperty -LiteralPath {_literal('HKLM:'+chr(92)+'SYSTEM'+chr(92)+'CurrentControlSet'+chr(92)+'Services'+chr(92)+name)} -ErrorAction Stop).DelayedAutoStart;"
            "[pscustomobject]@{state=$s.Status.ToString();start=$c.StartMode;delayed=($delay -eq 1);dependencies=@($s.ServicesDependedOn.Name);"
            "dependents=@($s.DependentServices|Where-Object Status -eq Running|Select-Object -ExpandProperty Name)}|ConvertTo-Json -Compress")
    if kind == "task":
        return ps_query(adapter, f"$t=Get-ScheduledTask -TaskName {_literal(subject['name'])} -TaskPath {_literal(subject['path'])};"
            "[pscustomobject]@{enabled=$t.Settings.Enabled;state=$t.State.ToString()}|ConvertTo-Json -Compress")
    if kind in {"registry", "startup", "environment"}:
        from .registry import read_value
        return read_value(subject)
    if kind == "startup_file":
        path = _startup_file_path(adapter, subject)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return {"exists": False, "root": str(path.parent)}
        if not path.is_file() or adapter._is_reparse(path, info) or info.st_size > 1_048_576:
            raise ValueError("Only bounded regular Startup shortcuts can be changed")
        with path.open("rb") as stream:
            data = stream.read(1_048_577)
        after = path.stat()
        if (after.st_size, after.st_mtime_ns, after.st_ino) != (info.st_size, info.st_mtime_ns, info.st_ino) or len(data) != info.st_size:
            raise ValueError("Startup shortcut changed during inspection")
        return {"exists": True, "data": base64.b64encode(data).decode("ascii"), "size": info.st_size,
                "mtime_ns": info.st_mtime_ns, "mode": info.st_mode & 0o777, "root": str(path.parent)}
    if kind == "game":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar", 0, winreg.KEY_READ) as key:
                value, value_type = winreg.QueryValueEx(key, "AutoGameModeEnabled")
            registry = {"exists": True, "data": value, "type": value_type, "binary": False}
        except FileNotFoundError:
            registry = {"exists": False}
        result = adapter._run_read_command(["powercfg.exe", "/getactivescheme"], timeout=8)
        match = re.search(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", result["stdout"])
        if result["returncode"] or not match:
            raise RuntimeError("Power scheme GUID could not be captured")
        return {"game_value": registry, "power_guid": match.group(0).lower()}
    if kind == "volume":
        drive = str(subject.get("drive", ""))
        if not re.fullmatch(r"[A-Z]:", drive):
            raise ValueError("Choose an exact fixed volume")
        volume = ps_query(adapter, f"Get-Volume -DriveLetter {drive[0]}|Select-Object DriveLetter,UniqueId,"
                          "@{n='DriveType';e={$_.DriveType.ToString()}},FileSystem,HealthStatus,Size|ConvertTo-Json -Compress",
                          **({"cancelled": cancelled} if cancelled is not None else {}))
        if (not isinstance(volume, dict) or volume.get("DriveLetter") != drive[0]
                or volume.get("DriveType") != "Fixed" or not volume.get("UniqueId")):
            raise ValueError("The selected fixed volume identity is unavailable")
        return volume
    if kind == "native":
        return {"platform": os.name, "owner": subject.get("owner")}
    if kind == "app":
        import winreg
        roots = (r"Software\Microsoft\Windows\CurrentVersion\Uninstall", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
        key_path = str(subject.get("key", ""))
        if not any(key_path.startswith(root + "\\") and "\\" not in key_path[len(root) + 1:] for root in roots) or subject.get("hive") not in {"HKCU", "HKLM"}:
            raise ValueError("Select an exact installed-app registration")
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER if subject["hive"] == "HKCU" else winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ) as key:
                values = {}
                for name in ("DisplayName", "DisplayVersion", "Publisher", "UninstallString", "WindowsInstaller", "SystemComponent", "NoRemove", "InstallLocation"):
                    try:
                        values[name] = winreg.QueryValueEx(key, name)[0]
                    except FileNotFoundError:
                        values[name] = None
            return {"exists": True, **values}
        except FileNotFoundError:
            return {"exists": False}
    if kind == "appx":
        identity = str(subject.get("identity", ""))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,255}", identity):
            raise ValueError("Select an exact installed Windows package")
        return ps_query(adapter, "$p=@(Get-AppxPackage -PackageTypeFilter Main,Framework,Resource,Bundle|Where-Object PackageFullName -eq " + _literal(identity) + ");"
            "if($p.Count -gt 1){throw 'Ambiguous package registration'};if($p.Count -eq 0){@{exists=$false}|ConvertTo-Json -Compress}else{"
            "$p[0]|Select-Object @{n='exists';e={$true}},Name,PackageFullName,@{n='Version';e={$_.Version.ToString()}},Publisher,"
            "IsFramework,IsResourcePackage,NonRemovable,@{n='SignatureKind';e={$_.SignatureKind.ToString()}},InstallLocation|ConvertTo-Json -Compress}")
    if kind == "package":
        from .inspection import software_package
        return software_package(adapter, subject["identity"], subject["source"])
    if kind == "driver":
        from .inspection import windows_inventory
        inf = str(subject.get("inf", ""))
        if not re.fullmatch(r"oem[0-9]{1,6}\.inf", inf, re.I):
            raise ValueError("Select an exact third-party driver package")
        result = windows_inventory(adapter, "driver_packages")
        if result["state"] == "unavailable" or result.get("bounded"):
            raise RuntimeError("Complete current driver ownership is unavailable")
        row = next((r for r in result["rows"] if r["Driver"].lower() == inf.lower()), None)
        return {"exists": row is not None, **(row or {})}
    if kind == "update":
        return ps_query(adapter, _update_search(subject) +
            "if($r.ResultCode -ne 2 -or $r.Updates.Count -ne 1){throw 'Exact update owner unavailable'};$u=$r.Updates.Item(0);"
            "@{id=$u.Identity.UpdateID;revision=$u.Identity.RevisionNumber;title=$u.Title;installed=$u.IsInstalled;"
            "eula_accepted=$u.EulaAccepted;interactive=$u.InstallationBehavior.CanRequestUserInput}|ConvertTo-Json -Compress", timeout=180)
    raise ValueError("Unknown native subject")


def _update_search(subject: dict[str, Any]) -> str:
    identity = str(subject.get("identity", ""))
    revision = int(subject.get("revision", -1))
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identity) or not 0 <= revision <= 2**31 - 1:
        raise ValueError("An exact native update identity and revision are required")
    criteria = " or ".join(f"(IsInstalled={installed} and UpdateID='{identity}' and RevisionNumber={revision})" for installed in (0, 1))
    return "$s=New-Object -ComObject Microsoft.Update.Session;$s.ClientApplicationID='MO SystemCare';$q=$s.CreateUpdateSearcher();$q.Online=$false;$r=$q.Search(" + _literal(criteria) + ");"


def _startup_file_path(adapter: Any, subject: dict[str, Any]) -> Any:
    from pathlib import Path
    roots = adapter.startup_paths()
    root_key, name = subject.get("root_key"), str(subject.get("name", ""))
    if root_key not in {"startup_user", "startup_all"} or root_key not in roots:
        raise ValueError("Windows Startup folder ownership is unavailable")
    if not name or any(c in name for c in '\\/:*?<>|') or name in {".", ".."} or Path(name).suffix.lower() not in {".lnk", ".url"}:
        raise ValueError("Select an exact Windows Startup shortcut")
    root = Path(roots[root_key])
    adapter._check_unlinked_owner(Path(root.anchor), root)
    return root / name


def packaged_app_removable(row: dict[str, Any]) -> bool:
    """Only explicit current-user removable application registrations qualify."""
    return (row.get("exists", True) is True and bool(row.get("Name")) and bool(row.get("PackageFullName"))
            and all(row.get(flag) is False for flag in ("IsFramework", "IsResourcePackage", "NonRemovable"))
            and row.get("SignatureKind") in {"Store", "Developer", "Enterprise"})


def _uninstall_command(subject: dict[str, Any], original: dict[str, Any]) -> list[str]:
    if not original.get("exists") or original.get("SystemComponent") or original.get("NoRemove") or not original.get("DisplayName"):
        raise ValueError("Protected or missing app registration")
    location = original.get("InstallLocation")
    if isinstance(location, str) and location.strip():
        from pathlib import Path
        from core.state.paths import repo_root
        root = Path(os.path.expandvars(location)).resolve(strict=True)
        if root == Path(root.anchor) or Path(sys.executable).resolve().is_relative_to(root) or Path(repo_root()).is_relative_to(root):
            raise ValueError("The active MO runtime or an unproven install root is protected")
    if original.get("WindowsInstaller") == 1:
        product = subject["key"].rsplit("\\", 1)[-1]
        if not re.fullmatch(r"\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}", product):
            raise ValueError("Exact MSI product identity is unavailable")
        return [os.path.join(os.environ["SystemRoot"], "System32", "msiexec.exe"), "/x", product, "/norestart"]
    command = original.get("UninstallString")
    if not isinstance(command, str) or not command.strip() or len(command) > 8000:
        raise ValueError("Registered native uninstaller is unavailable")
    count = ctypes.c_int()
    parser = ctypes.windll.shell32.CommandLineToArgvW
    parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
    parser.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
    argv = parser(os.path.expandvars(command), ctypes.byref(count))
    if not argv:
        raise ValueError("Registered uninstaller command could not be parsed")
    try:
        args = [argv[i] for i in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))
    executable = args[0]
    if not os.path.isabs(executable) or not executable.lower().endswith(".exe") or not os.path.isfile(executable) or os.path.basename(executable).lower() in {"cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe", "rundll32.exe"}:
        raise ValueError("This registration needs its own interactive Windows app owner")
    return args


def _command(adapter: Any, command: list[str], *, timeout: int = 300) -> dict[str, Any]:
    result = adapter._run_read_command(command, timeout=timeout, output_limit=128_000)
    if result["returncode"] not in {0, 3010} or result.get("truncated"):
        from core.runtime.backend_monitor import redact_monitor_text
        detail = redact_monitor_text(result.get("stderr") or result.get("stdout") or "No complete native output", 500)
        raise RuntimeError("Native owner failed or returned incomplete output (exit " + str(result["returncode"]) + "): " + detail)
    return result


def _notify_environment() -> bool:
    result = ctypes.c_size_t()
    owner = ctypes.windll.user32.SendMessageTimeoutW
    owner.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p,
                      ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t))
    owner.restype = ctypes.c_void_p
    return bool(owner(0xFFFF, 0x001A, 0, "Environment", 2, 2000, ctypes.byref(result)))


def _write_game(value: dict[str, Any]) -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar", 0, winreg.KEY_SET_VALUE) as key:
        if value["exists"]:
            winreg.SetValueEx(key, "AutoGameModeEnabled", 0, int(value["type"]), value["data"])
        else:
            try:
                winreg.DeleteValue(key, "AutoGameModeEnabled")
            except FileNotFoundError:
                pass


def _game_session_resources(adapter: Any) -> dict[str, Any]:
    """Take one cheap native sample; telemetry must never block recovery controls."""
    try:
        from .inspection import resource_snapshot
        return resource_snapshot(adapter)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return {"at": time.time(), "cpu_percent": None, "memory_total": None, "memory_used": None,
                "storage_free": None, "storage_total": None}


def execute(adapter: Any, state: Any, step: PlanStep, *, cancelled: Callable[[], bool]) -> dict[str, Any]:
    subject = dict(step.subject)
    validate_subject(step.action, subject)
    expected = subject.pop("original", None)
    if cancelled():
        from .windows import ScanCancelled
        raise ScanCancelled("action cancelled before mutation")
    original = capture(adapter, subject, state=state)
    if expected is None or canonical_digest(original) != canonical_digest(expected):
        raise ValueError("Selected item changed after review; inspect and plan again")
    action = step.action
    path_value = None
    if action == "path_remove_duplicate":
        from .registry import path_without_duplicate
        path_value = path_without_duplicate(original, subject.get("index"))
    if action.startswith("service_"):
        name = _service_name(subject)
        verb = action.removeprefix("service_")
        if verb in {"stop", "restart", "disabled"} and (name.lower() in _PROTECTED_SERVICES or original["dependents"]):
            raise ValueError("Critical service or running dependents prevent this change")
    if action == "registry_remove":
        from .registry import reference_evidence, eligible_reference
        target, missing, _kind = reference_evidence(subject.get("family", ""), subject["key"], subject["value_name"],
                                                  original.get("data"), original.get("type", 0), adapter)
        if not eligible_reference(subject, original) or not missing or subject.get("target", target) != target:
            raise ValueError("Missing-target evidence no longer holds")
    if action == "driver_remove" and (not original.get("exists") or original.get("InUse") is not False or original.get("Inbox") is not False or original.get("BootCritical") is not False):
        raise ValueError("In-use, inbox, boot-critical or unproven driver packages are protected")
    uninstall = _uninstall_command(subject, original) if action == "app_uninstall" and subject["kind"] == "app" else None
    if action == "app_uninstall" and subject["kind"] == "appx":
        if not packaged_app_removable(original) or original.get("PackageFullName") != subject["identity"]:
            raise ValueError("Protected, missing or unproven Windows packages are inspect-only")
        location = original.get("InstallLocation")
        if not isinstance(location, str) or not os.path.isabs(location):
            raise ValueError("Current package installation ownership is unavailable")
        from pathlib import Path
        from core.state.paths import repo_root
        root = Path(location).resolve(strict=True)
        if root == Path(root.anchor) or Path(sys.executable).resolve().is_relative_to(root) or Path(repo_root()).is_relative_to(root):
            raise ValueError("The active MO runtime or an unproven install root is protected")
    if action == "windows_update" and (original.get("installed") or original.get("eula_accepted") is not True or original.get("interactive") is not False):
        raise ValueError("This update needs its native Windows Update interaction or is already installed")
    if action == "game_on" and state.active_game() is not None:
        raise ValueError("Restore the existing Game Mode journal before enabling again")
    if action == "game_off":
        recovery = state.backup(subject.get("recovery_step", ""))
        if not recovery or recovery["state"] not in {"prepared", "applied", "needs_review"}:
            raise ValueError("Exact captured original is unavailable")
    backup = {"action": action, "subject": subject, "original": original, "state": "prepared", "at": time.time(),
              "reversible": step.undo.value in {"full", "partial"}}
    if action == "game_on":
        backup["session"] = {
            "session_id": str(subject.get("session_id") or step.step_id),
            "started_at": time.time(),
            "calibration_id": str(subject.get("calibration_id") or ""),
            "hardware": dict(subject.get("hardware") or {}),
            "baseline": _game_session_resources(adapter),
        }
    state.save_backup(step.step_id, backup)
    try:
        output = ""
        if action.startswith("service_"):
            verb = action.removeprefix("service_")
            name = _service_name(subject)
            if verb in {"start", "stop", "restart"}:
                ps_query(adapter, f"{verb.title()}-Service -Name {_literal(name)};@{{done=$true}}|ConvertTo-Json -Compress")
            else:
                start = {"automatic": "auto", "manual": "demand", "disabled": "disabled"}[verb]
                _command(adapter, ["sc.exe", "config", name, "start=", start], timeout=30)
        elif action.startswith("task_"):
            verb = "Enable" if action.endswith("enable") else "Disable"
            ps_query(adapter, f"{verb}-ScheduledTask -TaskName {_literal(subject['name'])} -TaskPath {_literal(subject['path'])}|Out-Null;@{{done=$true}}|ConvertTo-Json -Compress")
        elif action in {"registry_remove", "startup_disable"}:
            from .registry import write_value
            write_value(subject, {"exists": False})
        elif action == "path_remove_duplicate":
            from .registry import write_value
            write_value(subject, path_value)
            output = json.dumps({"environment_notification_sent": _notify_environment(),
                                 "scope": subject["scope"], "effect": "Applications reload their environment; existing processes retain their own PATH"})
        elif action == "startup_shortcut_disable":
            if original.get("exists") is not True:
                raise ValueError("The selected Startup shortcut is no longer registered")
            _startup_file_path(adapter, subject).unlink()
        elif action == "game_on":
            _write_game({"exists": True, "data": 1, "type": 4})
            if subject.get("power_guid"):
                _command(adapter, ["powercfg.exe", "/setactive", subject["power_guid"]], timeout=15)
        elif action == "game_off":
            restore(adapter, state, subject["recovery_step"])
        elif action == "desktop_refresh":
            if os.name != "nt":
                raise RuntimeError("Windows shell owner unavailable")
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
        elif action == "disk_optimize":
            drive = subject["drive"]
            output = _command(adapter, ["defrag.exe", drive, "/O", "/U"], timeout=300)["stdout"]
        elif action == "app_uninstall":
            if subject["kind"] == "appx":
                result = ps_query(adapter, "Remove-AppxPackage -Package " + _literal(subject["identity"]) +
                                  ";@{scope='current-user';removed=$true}|ConvertTo-Json -Compress", timeout=120)
                output = json.dumps(result)
            else:
                output = _command(adapter, uninstall)["stdout"]
        elif action == "browser_history_clear":
            from .browser_care import clear_browser
            output = clear_browser(adapter, subject, original, cancelled=cancelled)
        elif action == "recycle_remove":
            from .recycle import remove_recycle
            output = remove_recycle(adapter, subject, original, cancelled=cancelled)
        elif action == "app_leftover_remove":
            from .storage import remove_app_leftover
            output = remove_app_leftover(adapter, subject, original, state, cancelled=cancelled)
        elif action == "app_update":
            executable = shutil.which("winget.exe")
            if not executable:
                raise RuntimeError("Windows Package Manager is unavailable")
            output = _command(adapter, [executable, "upgrade", "--id", subject["identity"], "--exact", "--source", subject["source"],
                                       "--version", subject["version"], "--disable-interactivity"])["stdout"]
        elif action == "driver_remove":
            output = _command(adapter, ["pnputil.exe", "/delete-driver", subject["inf"]])["stdout"]
        elif action == "windows_update":
            result = ps_query(adapter, _update_search(subject) +
                "if($r.ResultCode -ne 2 -or $r.Updates.Count -ne 1){throw 'Exact update owner unavailable'};$u=$r.Updates.Item(0);"
                "if(!$u.EulaAccepted -or $u.InstallationBehavior.CanRequestUserInput -or $u.IsInstalled){throw 'Native Windows Update interaction required'};"
                "$c=New-Object -ComObject Microsoft.Update.UpdateColl;[void]$c.Add($u);"
                "$d=$s.CreateUpdateDownloader();$d.Updates=$c;$download=$d.Download();if($download.ResultCode -ne 2){throw 'Native download incomplete'};"
                "$i=$s.CreateUpdateInstaller();if($i.RebootRequiredBeforeInstallation){throw 'Windows requires a restart before installation'};"
                "$i.Updates=$c;$result=$i.Install();if($result.ResultCode -ne 2){throw 'Native installation incomplete'};"
                "@{result_code=[int]$result.ResultCode;reboot_required=$result.RebootRequired}|ConvertTo-Json -Compress", timeout=300)
            output = json.dumps(result)
        elif action in _NATIVE_COMMANDS:
            output = _command(adapter, list(_NATIVE_COMMANDS[action]))["stdout"]
        else:
            raise ValueError("Unknown catalog action")
    except Exception:
        # Preserve the real partial post-state when a native owner reports failure.
        # Original values are already durable; a failure never becomes success.
        try:
            partial = capture(adapter, subject, state=state)
        except Exception:
            partial = None
        state.save_backup(step.step_id, {**backup, **({"after": partial} if partial is not None else {}), "state": "needs_review"})
        raise
    after = capture(adapter, subject, state=state)
    verified = _verified(action, original, after, state, subject)
    if not verified:
        state.save_backup(step.step_id, {**backup, "after": after, "state": "needs_review"})
        return {"failed": 1, "verified": False, "changed": 0}
    applied = {**backup, "after": after, "state": "applied", "output": output[-12000:]}
    if action == "game_on":
        applied["session"] = {**backup["session"], "active_at": time.time()}
    state.save_backup(step.step_id, applied)
    removed_bytes = max(0, int(original.get("bytes") or 0)) if action in {"recycle_remove", "app_leftover_remove"} else 0
    return {"changed": 1, "verified": True, "failed": 0, "reclaimed_bytes": removed_bytes}


def _verified(action: str, before: dict, after: dict, state: Any, subject: dict) -> bool:
    if action.startswith("service_"):
        verb = action.removeprefix("service_")
        expected = {"start": "Running", "stop": "Stopped", "restart": "Running", "automatic": "Auto", "manual": "Manual", "disabled": "Disabled"}[verb]
        return after["state" if verb in {"start", "stop", "restart"} else "start"] == expected
    if action.startswith("task_"):
        return after["enabled"] is action.endswith("enable")
    if action in {"registry_remove", "startup_disable", "startup_shortcut_disable"}:
        return not after["exists"]
    if action == "path_remove_duplicate":
        from .registry import path_without_duplicate
        return after == path_without_duplicate(before, subject["index"])
    if action == "game_on":
        return after["game_value"].get("type") == 4 and after["game_value"].get("data") == 1 and after["power_guid"] == (subject.get("power_guid") or before["power_guid"])
    if action == "game_off":
        return after == state.backup(subject["recovery_step"])["original"]
    if action in {"app_uninstall", "driver_remove"}:
        return after.get("exists") is False
    if action in {"recycle_remove", "app_leftover_remove"}:
        return after.get("exists") is False
    if action == "browser_history_clear":
        return bool(after.get("counts")) and not any(after["counts"].values())
    if action == "app_update":
        return after.get("DisplayVersion") == subject["version"] and after.get("DisplayVersion") != before.get("DisplayVersion")
    if action == "windows_update":
        return after.get("installed") is True
    if action == "disk_optimize":
        return after == before
    # A successful native command is its operation receipt, not proof of health
    # or improved performance. The UI retains this distinction.
    return True


def restore(adapter: Any, state: Any, step_id: str) -> dict[str, Any]:
    row = state.backup(step_id)
    if not row or row["state"] not in {"prepared", "applied", "needs_review"}:
        raise ValueError("An unrestored original is required")
    subject, original = row["subject"], row["original"]
    current = capture(adapter, subject, state=state)
    if row.get("after") is not None and current != row["after"]:
        raise ValueError("Item changed since this action; restoration needs a fresh review")
    if row.get("after") is None and current != original and not _verified(row["action"], original, current, state, subject):
        raise ValueError("Interrupted action has no matching post-state; inspect before restoration")
    kind = subject["kind"]
    if kind in {"registry", "startup", "environment"}:
        from .registry import write_value
        write_value(subject, original)
        if kind == "environment":
            _notify_environment()
    elif kind == "startup_file":
        path = _startup_file_path(adapter, subject)
        if current.get("exists"):
            raise ValueError("A Startup shortcut already exists; restoration cannot overwrite it")
        data = base64.b64decode(original["data"], validate=True)
        with path.open("xb") as stream:
            stream.write(data)
        os.chmod(path, original["mode"])
        os.utime(path, ns=(original["mtime_ns"], original["mtime_ns"]))
    elif kind == "game":
        _write_game(original["game_value"])
        _command(adapter, ["powercfg.exe", "/setactive", original["power_guid"]], timeout=15)
    elif kind == "task":
        verb = "Enable" if original["enabled"] else "Disable"
        ps_query(adapter, f"{verb}-ScheduledTask -TaskName {_literal(subject['name'])} -TaskPath {_literal(subject['path'])}|Out-Null;@{{done=$true}}|ConvertTo-Json -Compress")
    elif kind == "service":
        name = _service_name(subject)
        start = {"Auto": "delayed-auto" if original.get("delayed") else "auto", "Manual": "demand", "Disabled": "disabled"}[original["start"]]
        _command(adapter, ["sc.exe", "config", name, "start=", start], timeout=30)
        verb = "Start" if original["state"] == "Running" else "Stop"
        ps_query(adapter, f"{verb}-Service -Name {_literal(name)};@{{done=$true}}|ConvertTo-Json -Compress")
    else:
        raise ValueError("This native operation has no reversible state")
    after = capture(adapter, subject, state=state)
    # Dynamic dependency state is not a setting owned by service restoration.
    verified = all(after.get(k) == original.get(k) for k in ("state", "start", "delayed")) if kind == "service" else after == original
    if not verified:
        raise RuntimeError("Original state did not pass its restoration check")
    restored_at = time.time()
    restored = {**row, "state": "restored", "restored_at": restored_at}
    if kind == "game" and row.get("action") == "game_on":
        restored["session"] = {**dict(row.get("session") or {}), "ended_at": restored_at,
                               "final": _game_session_resources(adapter)}
    state.save_backup(step_id, restored)
    return {"state": "restored", "step_id": step_id, "verified": True}
