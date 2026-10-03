"""Bounded native observations used by the SystemCare service.

This module supplies fixed Windows queries, not a command or mutation API.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import time
from typing import Any, Callable


_SCRIPTS = {
    "startup": """$roots=@(@{h='HKCU';k='Software\\Microsoft\\Windows\\CurrentVersion\\Run'},@{h='HKLM';k='Software\\Microsoft\\Windows\\CurrentVersion\\Run'},@{h='HKLM';k='Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Run'});$rows=@();foreach($r in $roots){$p=$r.h+':\\'+$r.k;if(Test-Path -LiteralPath $p){$k=Get-Item -LiteralPath $p;foreach($n in $k.GetValueNames()){$rows+=[pscustomobject]@{name=$n;source='registry';hive=$r.h;key=$r.k;value_name=$n;state='registered'}}}};$rows|Select-Object -First 300|ConvertTo-Json -Compress""",
    "services": """Get-CimInstance Win32_Service | Sort-Object Name | Select-Object -First 400 Name,DisplayName,State,StartMode | ConvertTo-Json -Compress""",
    "tasks": """Get-ScheduledTask | Where-Object { $_.Triggers.CimClass.CimClassName -contains 'MSFT_TaskLogonTrigger' -or $_.Triggers.CimClass.CimClassName -contains 'MSFT_TaskBootTrigger' } | Select-Object -First 200 TaskName,TaskPath,State | ConvertTo-Json -Compress""",
    "apps": """$roots=@(@{h='HKCU';k='Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall'},@{h='HKLM';k='Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall'},@{h='HKLM';k='Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall'});$rows=@();foreach($r in $roots){Get-ItemProperty ($r.h+':\\'+$r.k+'\\*') -ErrorAction SilentlyContinue|Where-Object DisplayName|ForEach-Object {$rows+=[pscustomobject]@{DisplayName=$_.DisplayName;DisplayVersion=$_.DisplayVersion;Publisher=$_.Publisher;DisplayIcon=$_.DisplayIcon;hive=$r.h;key=($r.k+'\\'+$_.PSChildName);SystemComponent=$_.SystemComponent;WindowsInstaller=$_.WindowsInstaller}}};$rows|Sort-Object DisplayName|Select-Object -First 300|ConvertTo-Json -Compress""",
    "packaged_apps": """@(Get-AppxPackage -PackageTypeFilter Main,Framework,Resource,Bundle | Sort-Object Name | Select-Object -First 500 Name,PackageFullName,@{n='Version';e={$_.Version.ToString()}},Publisher,IsFramework,IsResourcePackage,NonRemovable,@{n='SignatureKind';e={$_.SignatureKind.ToString()}},InstallLocation)|ConvertTo-Json -Compress""",
    "drivers": """Get-CimInstance Win32_PnPSignedDriver | Where-Object DeviceName | Sort-Object DeviceName | Select-Object -First 300 DeviceName,DriverVersion,DriverProviderName,InfName,IsSigned | ConvertTo-Json -Compress""",
    "storage": """Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object -First 64 DeviceID,Size,FreeSpace,FileSystem,VolumeName | ConvertTo-Json -Compress""",
    "devices": """Get-CimInstance Win32_DiskDrive | Select-Object -First 64 Model,MediaType,Size,Status | ConvertTo-Json -Compress""",
    "driver_packages": """$used=@(Get-CimInstance Win32_PnPSignedDriver|Select-Object -ExpandProperty InfName);@(Get-WindowsDriver -Online -All|Select-Object -First 500 Driver,OriginalFileName,ProviderName,Version,Inbox,BootCritical,@{n='InUse';e={$used -contains $_.Driver}})|ConvertTo-Json -Compress""",
}


def windows_inventory(adapter: Any, section: str, *, cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    if section not in _SCRIPTS:
        raise ValueError("Unknown SystemCare observation")
    if cancelled and cancelled():
        from .windows import ScanCancelled
        raise ScanCancelled("inspection cancelled")
    executable = shutil.which("powershell.exe") if os.name == "nt" else None
    if not executable:
        return {"state": "unavailable", "rows": [], "detail": "Windows native query owner is unavailable", "at": time.time()}
    script = "$ErrorActionPreference='Stop';[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();" + _SCRIPTS[section]
    result = adapter._run_read_command([executable, "-NoProfile", "-NonInteractive", "-Command", script],
                                      timeout=45, output_limit=256_000)
    if result["returncode"] or result.get("truncated"):
        return {"state": "unavailable", "rows": [], "detail": "Native query failed or exceeded the bounded result size", "at": time.time()}
    try:
        value = json.loads(result["stdout"]) if result["stdout"].strip() else []
        rows = value if isinstance(value, list) else [value]
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("Unexpected native result")
    except (ValueError, TypeError):
        return {"state": "unavailable", "rows": [], "detail": "Native query did not return complete structured evidence", "at": time.time()}
    if section == "packaged_apps":
        from .actions import packaged_app_removable
        rows = [{**row, "can_remove": packaged_app_removable(row)} for row in rows]
    limit = 500 if section in {"driver_packages", "packaged_apps"} else 200 if section == "tasks" else 400 if section == "services" else 64 if section in {"storage", "devices", "network"} else 300
    bounded = len(rows) >= limit
    return {"state": "partial" if bounded or section == "apps" else "measured", "rows": rows,
            "bounded": bounded, "limit": limit,
            "detail": "Bounded current Windows inventory" + ("; unreadable registration roots may be absent" if section == "apps" else "; installed packages for the current Windows account only" if section == "packaged_apps" else ""), "at": time.time()}


def power_schemes(adapter: Any) -> list[dict[str, str]]:
    result = adapter._run_read_command(["powercfg.exe", "/list"], timeout=10)
    if result["returncode"] or result.get("truncated"):
        return []
    rows = []
    for line in result["stdout"].splitlines():
        match = re.search(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", line)
        if match:
            rows.append({"guid": match.group(0).lower(), "name": line[match.end():].strip().strip("* ").strip("()")})
    return rows[:64]


def startup_measurements(adapter: Any) -> dict[str, Any]:
    """Windows' recorded boot/degradation samples, not inferred Task Manager impact."""
    from .actions import ps_query
    try:
        rows = ps_query(adapter, "$events=@(Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Diagnostics-Performance/Operational';Id=100,101} -MaxEvents 40);"
            "@($events|ForEach-Object {$event=$_;[xml]$x=$event.ToXml();$d=@{};foreach($v in $x.Event.EventData.Data){$d[$v.Name]=[string]$v.'#text'};"
            "[pscustomobject]@{name=if($event.Id -eq 100){'Windows boot'}else{$d.Name};source_event=$event.Id;record_id=$event.RecordId;"
            "observed_at=$event.TimeCreated.ToUniversalTime().ToString('o');milliseconds=if($event.Id -eq 100){$d.BootTime}else{$d.TotalTime};"
            "degradation_milliseconds=$d.DegradationTime}})|ConvertTo-Json -Compress", timeout=20)
    except (OSError, ValueError, RuntimeError):
        return {"state": "unavailable", "rows": [], "detail": "Windows boot/degradation events are absent or inaccessible; no startup impact is invented"}
    rows = rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else []
    valid = []
    for row in rows:
        try:
            milliseconds = int(row["milliseconds"])
            if milliseconds < 0 or row["source_event"] not in {100, 101}:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        valid.append({**row, "milliseconds": milliseconds})
    return {"state": "measured" if valid else "unavailable", "rows": valid,
            "detail": "Last 40 recorded Windows boot/application-degradation events; duration in milliseconds. These historical samples do not cover every startup item or predict the next boot."}


def startup_shortcuts(adapter: Any, *, cancelled: Callable[[], bool]) -> dict[str, Any]:
    """Folder registration presence is distinct from Windows startup approval."""
    from itertools import islice
    from .actions import capture
    roots = adapter.startup_paths()
    rows, unavailable, bounded = [], [k for k in ("startup_user", "startup_all") if k not in roots], False
    for root_key, root in roots.items():
        try:
            adapter._check_unlinked_owner(type(root)(root.anchor), root)
            with os.scandir(root) as entries:
                for index, item in enumerate(islice(entries, 301)):
                    if cancelled():
                        from .windows import ScanCancelled
                        raise ScanCancelled("startup inspection cancelled")
                    if index == 300:
                        bounded = True
                        break
                    if not item.is_file(follow_symlinks=False):
                        continue
                    subject = {"kind": "startup_file", "root_key": root_key, "name": item.name}
                    try:
                        original = capture(adapter, subject)
                    except (OSError, ValueError):
                        continue
                    if not original.get("exists"):
                        continue
                    rows.append({"source": "folder", "name": item.name, "root_key": root_key,
                                 "state": "registered", "bytes": original["size"],
                                 "detail": "Startup shortcut present; Task Manager approval and measured impact are separate"})
        except (OSError, ValueError):
            unavailable.append(root_key)
    return {"rows": rows, "bounded": bounded, "unavailable": unavailable}


def software_updates(adapter: Any) -> dict[str, Any]:
    """Read WinGet's own available-update table; reject ambiguous/truncated IDs."""
    executable = shutil.which("winget.exe") if os.name == "nt" else None
    if not executable:
        return {"state": "unavailable", "rows": [], "at": time.time(), "detail": "Installed Windows Package Manager is unavailable"}
    result = adapter._run_read_command([executable, "list", "--upgrade-available", "--disable-interactivity"],
                                      timeout=60, output_limit=128_000)
    if result["returncode"] or result.get("truncated"):
        return {"state": "unavailable", "rows": [], "at": time.time(), "detail": "WinGet did not provide complete evidence; source agreements may require review in its native owner"}
    rows = parse_updates(result["stdout"])
    return {"state": "partial" if rows is not None else "unavailable", "rows": rows or [], "at": time.time(),
            "detail": "WinGet-correlated update candidates from its current inventory; unmatched or truncated identities remain unavailable"}


def parse_updates(output: str, *, updates: bool = True) -> list[dict[str, Any]] | None:
    lines = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output).splitlines()
    divider = next((i for i, line in enumerate(lines) if re.fullmatch(r"-{12,}", line.strip())), None)
    if divider is None or divider == 0:
        return None
    header = lines[divider - 1]
    # Fail closed for layouts we cannot correlate; do not guess localized columns.
    labels = ("Name", "Id", "Version", "Available", "Source") if updates else ("Name", "Id", "Version", "Source")
    positions = [header.find(label) for label in labels]
    if any(p < 0 for p in positions) or positions != sorted(positions):
        return None
    rows = []
    for line in lines[divider + 1:]:
        fields = [line[start:end].strip() for start, end in zip(positions, positions[1:] + [len(line)])]
        name, identity, version = fields[:3]
        source = fields[-1]
        available = fields[3] if updates else ""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{1,199}", identity) or source not in {"winget", "msstore"} or not version or (updates and not available):
            continue
        rows.append({"DisplayName": name, "Id": identity, "DisplayVersion": version, "Available": available, "Source": source})
    return rows[:300]


def software_package(adapter: Any, identity: str, source: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{1,199}", identity) or source not in {"winget", "msstore"}:
        raise ValueError("An exact native package identity and source are required")
    executable = shutil.which("winget.exe")
    if not executable:
        raise RuntimeError("Windows Package Manager is unavailable")
    result = adapter._run_read_command([executable, "list", "--id", identity, "--exact", "--source", source,
                                       "--disable-interactivity"], timeout=60, output_limit=128_000)
    rows = parse_updates(result["stdout"], updates=False) if not result["returncode"] and not result.get("truncated") else None
    matches = [r for r in rows or [] if r["Id"] == identity and r["Source"] == source]
    if len(matches) != 1:
        raise RuntimeError("Native package registration could not be correlated exactly")
    return matches[0]


def resource_snapshot(adapter: Any) -> dict[str, Any]:
    """Cheap live memory/disk samples; CPU needs two real observations."""
    result: dict[str, Any] = {"at": time.time(), "cpu_percent": None, "memory_total": None, "memory_used": None,
                              "storage_free": None, "storage_total": None}
    if os.name != "nt":
        return result
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    *[(name, ctypes.c_ulonglong) for name in ("total", "available", "total_page", "available_page", "total_virtual", "available_virtual", "extended")]]
    memory = Memory()
    memory.length = ctypes.sizeof(memory)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        result.update(memory_total=memory.total, memory_used=memory.total - memory.available)
    idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
    if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        current = (idle.value, kernel.value + user.value)
        previous = getattr(adapter, "_resource_cpu_sample", None)
        adapter._resource_cpu_sample = current
        if previous is not None:
            total = current[1] - previous[1]
            if total > 0:
                result["cpu_percent"] = max(0.0, min(100.0, 100 * (1 - (current[0] - previous[0]) / total)))
    try:
        disk = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
        result.update(storage_free=disk.free, storage_total=disk.total)
    except OSError:
        pass
    return result
