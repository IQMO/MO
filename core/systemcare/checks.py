"""Fixed Windows checks. Process completion is distinct from machine health."""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from .actions import ps_query
from .inspection import resource_snapshot


def windows_updates(adapter: Any, *, drivers: bool = False) -> dict[str, Any]:
    kind = "Driver" if drivers else "Software"
    result = ps_query(adapter, "$s=New-Object -ComObject Microsoft.Update.Session;$s.ClientApplicationID='MO SystemCare';"
        f"$r=$s.CreateUpdateSearcher().Search(\"IsInstalled=0 and IsHidden=0 and Type='{kind}'\");"
        "$rows=@();for($i=0;$i -lt [Math]::Min($r.Updates.Count,100);$i++){$u=$r.Updates.Item($i);"
        "$rows+=[pscustomobject]@{name=$u.Title;update_id=$u.Identity.UpdateID;revision=$u.Identity.RevisionNumber;"
        "type=$u.Type;eula_accepted=$u.EulaAccepted;downloaded=$u.IsDownloaded;state='available';detail=($u.KBArticleIDs -join ',')}};"
        "@{result_code=[int]$r.ResultCode;count=$r.Updates.Count;rows=$rows}|ConvertTo-Json -Compress -Depth 5", timeout=180)
    complete = result.get("result_code") == 2
    return {"state": "measured" if complete and result["count"] <= 100 else "partial", "at": time.time(),
            "rows": result["rows"] if complete else [], "bounded": result["count"] > 100,
            "detail": "Windows Update Agent offered updates; current managed source policy is retained. New license acceptance remains with Windows Update. No install or reboot performed."}


def native_check(adapter: Any, section: str, *, cancelled: Callable[[], bool], target: str = "") -> dict[str, Any]:
    if cancelled():
        from .windows import ScanCancelled
        raise ScanCancelled("check cancelled")
    if os.name != "nt":
        return {"state": "unavailable", "rows": [], "at": time.time(), "detail": "Windows owner unavailable"}
    if section == "filesystem":
        from .actions import capture
        before = capture(adapter, {"kind": "volume", "drive": target})
        if not adapter._is_admin():
            return {"state": "unavailable", "rows": [], "at": time.time(), "detail": "File-system checks require Windows permission"}
        result = adapter._run_read_command(["chkdsk.exe", target], timeout=300, output_limit=128_000)
        after = capture(adapter, {"kind": "volume", "drive": target})
        complete = not result.get("truncated") and result["returncode"] in {0, 1, 2} and after == before
        return {"state": "measured" if complete else "partial", "at": time.time(), "drive": target,
                "rows": [{"name": target + " file system", "state": "completed" if complete else "incomplete or needs review",
                          "detail": (result.get("stdout") or result.get("stderr") or result.get("error_kind") or "No native result")[-16000:],
                          "exit_code": result["returncode"]}],
                "detail": "Read-only CHKDSK; no repair switches, dismount or restart scheduled. On an active volume, concurrent writes can produce apparent errors; review native output before repair."}
    if section == "network":
        rows = ps_query(adapter, "@(Get-NetIPConfiguration -All|Select-Object -First 64|ForEach-Object {"
            "$ip=(@($_.IPv4Address.IPAddress)+@($_.IPv6Address.IPAddress)|Select-Object -First 16) -join ', ';"
            "$dns=(@($_.DNSServer.ServerAddresses)|Select-Object -First 16) -join ', ';"
            "$gateway=(@($_.IPv4DefaultGateway.NextHop)+@($_.IPv6DefaultGateway.NextHop)|Select-Object -First 16) -join ', ';"
            "[pscustomobject]@{name=$_.InterfaceAlias;state=if($_.NetAdapter){$_.NetAdapter.Status.ToString()}else{'not observed'};"
            "detail=('Addresses: '+$ip+'; DNS: '+$dns+'; gateway: '+$gateway)}})|ConvertTo-Json -Compress")
        rows = rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else []
        return {"state": "partial", "at": time.time(), "rows": rows,
                "detail": "Current adapter addresses, DNS and default gateways; at most 64 interfaces and 16 entries per field. No external connection probe or network preference changed."}
    if section in {"integrity", "component_store"}:
        command = ["sfc.exe", "/verifyonly"] if section == "integrity" else ["dism.exe", "/Online", "/Cleanup-Image", "/AnalyzeComponentStore", "/English"]
        if not adapter._is_admin():
            return {"state": "unavailable", "rows": [], "at": time.time(), "detail": "This protected check requires an elevated SystemCare process"}
        result = adapter._run_read_command(command, timeout=300, output_limit=128_000)
        completed = result["returncode"] == 0 and not result.get("truncated")
        return {"state": "measured" if completed else "unavailable", "at": time.time(),
                "rows": [{"name": "Windows native check", "state": "completed" if completed else "failed or incomplete",
                          "detail": result["stdout"][-16000:] or "No complete native result", "exit_code": result["returncode"]}],
                "detail": "Native output is the evidence. Exit code alone does not establish a healthy system."}
    if section == "updates":
        signals = adapter._pending_restart_signals()
        rows = ps_query(adapter, "@(Get-Service -Name wuauserv,bits,TrustedInstaller|Select-Object Name,@{n='State';e={$_.Status.ToString()}})|ConvertTo-Json -Compress")
        readiness = ([{"name": r["Name"], "state": r["State"], "detail": "Servicing owner state"} for r in rows] +
                [{"name": "Pending restart", "state": "attention" if signals else "not observed", "detail": ", ".join(signals) or "Known restart signals not observed"}])
        available = windows_updates(adapter)
        available["readiness"] = readiness
        return available
    if section == "environment":
        from .registry import environment_subject, read_value, path_without_duplicate
        rows = []
        for scope in ("User", "Machine"):
            try:
                original = read_value(environment_subject(scope))
            except OSError:
                rows.append({"name": scope + " PATH", "state": "unavailable", "detail": "Registry owner could not be read"})
                continue
            value = original.get("data") if original.get("type") in {1, 2} else None
            if not isinstance(value, str):
                rows.append({"name": scope + " PATH", "state": "not observed", "detail": "No supported typed PATH value"})
                continue
            for index, part in enumerate(value.split(";")[:256]):
                if not part:
                    continue
                expanded = os.path.expandvars(part.strip('"'))
                try:
                    path_without_duplicate(original, index)
                    duplicate = True
                except ValueError:
                    duplicate = False
                state = "duplicate" if duplicate else "not checked" if "%" in expanded or expanded.startswith("\\\\") else "present" if os.path.isdir(expanded) else "review"
                rows.append({"name": scope + " PATH", "state": state, "detail": part, "scope": scope, "index": index})
        return {"state": "partial", "at": time.time(), "rows": rows, "detail": "Typed User/Machine PATH. Only a selected literal duplicate can be removed with restoration; an absent directory alone does not prove an entry is unnecessary"}
    if section == "desktop":
        rows = ps_query(adapter, "@(Get-Process|Where-Object {$_.MainWindowHandle -ne 0}|Select-Object -First 100 ProcessName,Id,Responding,WorkingSet64)|ConvertTo-Json -Compress")
        return {"state": "measured", "at": time.time(), "resources": resource_snapshot(adapter),
                "rows": [{"name": r["ProcessName"], "state": "responding" if r["Responding"] else "review",
                          "detail": "PID " + str(r["Id"]), "bytes": r["WorkingSet64"]} for r in rows],
                "detail": "Visible app responsiveness and memory; no process terminated or memory emptied"}
    if section == "protection":
        result = ps_query(adapter, "Get-MpComputerStatus|Select-Object AntivirusEnabled,AntispywareEnabled,RealTimeProtectionEnabled,AntivirusSignatureLastUpdated|ConvertTo-Json -Compress")
        return {"state": "measured", "at": time.time(), "rows": [{"name": k, "state": "enabled" if v is True else "disabled" if v is False else "observed", "detail": str(v)} for k, v in result.items()], "detail": "Current Microsoft Defender owner state"}
    raise ValueError("Unknown native check")
