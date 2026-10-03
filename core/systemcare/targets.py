"""Selected project/host observations for the existing SystemCare owner."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import time
from pathlib import Path
from typing import Any, Callable


PROJECT_EXCLUDED_DIRS = {".git", ".svn", ".venv", "venv", "node_modules", "personal"}


def server_targets() -> list[str]:
    from core.profile.server_aliases import configured_server_aliases
    return configured_server_aliases()


def project_target(target: str, config: dict[str, Any]) -> Path:
    from core.state.paths import mo_home
    if not target.strip():
        raise ValueError("Choose a project root first")
    selected = Path(target).expanduser()
    personal = Path(os.path.abspath(Path(mo_home(config)) / "personal"))
    absolute = Path(os.path.abspath(selected))
    if absolute == personal or absolute.is_relative_to(personal) or personal.is_relative_to(absolute):
        raise ValueError("The opaque personal home is outside SystemCare inspection")
    if _reparse(selected):
        raise ValueError("Choose a real project directory without a link or junction")
    root = selected.resolve(strict=True)
    personal = Path(mo_home(config)).resolve() / "personal"
    if root == personal or root.is_relative_to(personal) or personal.is_relative_to(root):
        raise ValueError("The opaque personal home is outside SystemCare inspection")
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Choose a real project directory")
    return root


def project_targets(config: dict[str, Any]) -> list[dict[str, str]]:
    """Reuse curated declarations and the current folder, without saving a catalog."""
    from core.profile import Profile
    from core.state.paths import PROFILE_DB_PATH, project_cwd, resolve_state_path
    profile = Profile(_path=resolve_state_path(PROFILE_DB_PATH, config))
    entries = [(entry.path, entry.name, "Declared project") for entry in profile.project_locations()]
    entries.append((str(project_cwd()), project_cwd().name, "Current folder"))
    rows, seen = [], set()
    for path, name, source in entries[:64]:
        if not path:
            rows.append({"name": name, "path": "", "source": source, "detail": "No local folder declared"})
            continue
        try:
            root = project_target(path, config)
        except (OSError, ValueError, RuntimeError):
            continue
        key = os.path.normcase(str(root))
        if key not in seen:
            rows.append({"name": name or root.name, "path": str(root), "source": source, "detail": "Selected-folder checks only"})
            seen.add(key)
    return rows


def inspect_target(context: str, section: str, target: str, config: dict[str, Any], adapter: Any,
                   *, cancelled: Callable[[], bool]) -> dict[str, Any]:
    if context == "server":
        return _server(target, section, adapter, cancelled)
    if context != "projects":
        raise ValueError("Unknown maintenance target")
    root = project_target(target, config)
    if section in {"health", "dependencies"}:
        from core.diagnostics.system_health import check_graph_health
        graph = check_graph_health(str(root))
        manifests = [name for name in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "Pipfile", "poetry.lock", "uv.lock", "package.json", "Cargo.toml", "go.mod", "build.gradle", "build.gradle.kts") if (root / name).is_file()]
        rows = ([{"name": name, "state": "observed", "detail": "Root manifest present; contents and installed package use are not inferred"} for name in manifests]
                if section == "dependencies" else [{"name": "Selected project", "state": "observed", "detail": str(root)},
                    {"name": "Dependency manifests", "state": "observed" if manifests else "not found", "detail": ", ".join(manifests) or "No recognized root manifest"}])
        rows.append({"name": "Structural graph", "state": "stale" if graph.get("stale") else "observed" if graph.get("exists") else "not found",
                     "detail": f"{graph.get('nodes', 0)} nodes · {graph.get('edges', 0)} links · existing graph owner; no rebuild"})
        rows.append({"name": "Dependency use" if section == "dependencies" else "Build and test health", "state": "not checked",
                     "detail": "Manifest presence does not establish required, installed or unused packages. Ask MO for current dependency review."
                     if section == "dependencies" else "No project code, builds or tests executed. Ask MO to review the selected project."})
        return {"state": "partial", "at": time.time(), "rows": rows, "graph": graph,
                "detail": "Selected project " + ("manifest and graph evidence; installed dependencies require owner review" if section == "dependencies"
                                                  else "folder, manifests and saved graph; this is not a build or test result")}
    if section not in {"storage", "caches"}:
        raise ValueError("Unknown project inspection")
    rows = []
    examined = 0
    bounded = False
    directories, skipped = 0, 0
    deadline = time.monotonic() + 5
    def unreadable(_error: OSError) -> None:
        nonlocal skipped
        skipped += 1
    generated = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".gradle", "build", "dist"}
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        if cancelled():
            from .windows import ScanCancelled
            raise ScanCancelled("project inspection cancelled")
        directories += 1
        if directories > 1_000 or time.monotonic() >= deadline:
            bounded = True
            break
        dirs[:] = [name for name in dirs if name.casefold() not in PROJECT_EXCLUDED_DIRS
                   and not _reparse(Path(directory) / name)]
        for name in files:
            examined += 1
            if examined > 5_000 or time.monotonic() >= deadline:
                bounded = True
                break
            path = Path(directory) / name
            try:
                info = path.lstat()
                if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                    continue
            except OSError:
                skipped += 1
                continue
            relative = path.relative_to(root)
            category = "generated" if any(part in generated for part in relative.parts) else "retained"
            if section == "caches" and category != "generated":
                continue
            rows.append({"name": str(relative), "bytes": info.st_size, "modified_at": info.st_mtime,
                         "state": "Review" if category == "generated" else "Keep", "detail": category + "; current use and generator require verification"})
        if bounded:
            break
    rows.sort(key=lambda row: int(row["bytes"]), reverse=True)
    return {"state": "partial" if bounded or skipped or len(rows) > 300 else "measured", "at": time.time(), "rows": rows[:300],
            "examined": min(examined, 5_000), "bounded": bounded or len(rows) > 300,
            "skipped": skipped, "directories": min(directories, 1_000), "total_bytes": sum(row["bytes"] for row in rows),
            "detail": "Selected-root discovery; up to 5,000 files / 1,000 directories / 300 results / 5 seconds. "
                      "Directory names do not authorize deletion; active use requires review. " + str(skipped) + " entries could not be read"}


def _reparse(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _server(target: str, section: str, adapter: Any, cancelled: Callable[[], bool]) -> dict[str, Any]:
    if section not in {"health", "service", "logs", "caches", "repair"}:
        raise ValueError("Unknown host inspection")
    if target not in server_targets() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", target):
        raise ValueError("Select an existing configured host alias")
    executable = shutil.which("ssh")
    if not executable:
        return {"state": "unavailable", "rows": [], "at": time.time(), "detail": "The configured OpenSSH transport is unavailable"}
    if cancelled():
        from .windows import ScanCancelled
        raise ScanCancelled("host inspection cancelled")
    code = "section=" + repr(section) + "\n" + """import json,platform,os,shutil,subprocess
disk=shutil.disk_usage(os.path.abspath(os.sep))
rows=[]
units=[]
if section=='health':
 rows=[{'name':'Host OS','state':'observed','detail':platform.platform()},{'name':'Host storage','state':'measured','bytes':disk.free,'total_bytes':disk.total,'detail':'Root volume free space'},{'name':'Host CPU','state':'observed','detail':str(os.cpu_count())+' logical processors'}]
 if hasattr(os,'getloadavg'): rows.append({'name':'Host load','state':'measured','detail':str(os.getloadavg())})
 if platform.system()=='Linux':
  with open('/proc/meminfo') as f: mem={line.split(':')[0]:line.split(':')[1].strip() for line in f if line.startswith(('MemTotal:','MemAvailable:','SwapFree:'))}
  rows.append({'name':'Host memory','state':'measured','detail':json.dumps(mem)})
if platform.system()=='Linux':
 try:
  units=[]
  for scope,flags in [('system',[]),('user',['--user'])]:
   try:
    found=json.loads(subprocess.run(['systemctl',*flags,'list-units','--type=service','--all','--output=json','--no-pager'],capture_output=True,text=True,timeout=8,check=True).stdout)
    try:
     installed=json.loads(subprocess.run(['systemctl',*flags,'list-unit-files','--type=service','--output=json','--no-pager'],capture_output=True,text=True,timeout=8,check=True).stdout)
    except Exception:
     installed=[]
     rows.append({'name':scope+' service startup inventory','state':'unavailable','detail':'Installed unit-file evidence unavailable; loaded units still inspected'})
    matching={}
    for u in [*installed,*found]:
     name=u.get('unit') or u.get('unit_file','')
     if name in ('mo.service','mo-agent.service','mo-everywhere.service') or name.startswith('mo-agent-'):
      matching[name]={**matching.get(name,{}),**u,'unit':name,'scope':scope,'flags':flags}
    units.extend(matching.values())
   except Exception: rows.append({'name':scope+' service manager','state':'unavailable','detail':'Service-scope evidence unavailable'})
  units=units[:16]
  if not units: rows.append({'name':'MO service','state':'not observed','detail':'No matching MO unit; a custom service name has not been inferred'})
  if section in ('health','service','repair'):
   for u in units:
    result=subprocess.run(['systemctl',*u['flags'],'show',u['unit'],'--property=ActiveState,SubState,UnitFileState,UnitFilePreset,MainPID,NRestarts,ExecMainStatus,MemoryCurrent,CPUUsageNSec,FragmentPath,Requires,Wants,After','--no-pager'],capture_output=True,text=True,timeout=5)
    properties=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line) if result.returncode==0 else {}
    rows.append({'name':u['scope']+': '+u['unit'],'scope':u['scope'],'unit':u['unit'],'state':properties.get('ActiveState','unavailable'),
                 'startup':properties.get('UnitFileState',u.get('state','unknown')),
                 'properties':properties,
                 'detail':('State: '+properties.get('SubState','unknown')+' · PID: '+properties.get('MainPID','unknown')+' · restarts: '+properties.get('NRestarts','unknown')+' · Review for exact dependency properties') if result.returncode==0 else 'Unit details unavailable'})
  if section=='logs':
   for u in units:
    result=subprocess.run(['journalctl',*u['flags'],'--unit='+u['unit'],'--lines=50','--no-pager','--output=json'],capture_output=True,text=True,timeout=8)
    if result.returncode: rows.append({'name':u['scope']+': '+u['unit'],'state':'unavailable','detail':'Journal permission or owner unavailable'})
    else:
     for line in result.stdout.splitlines()[-50:]:
      entry=json.loads(line); message=entry.get('MESSAGE')
      rows.append({'name':u['scope']+': '+u['unit'],'state':'recorded','detail':str(message)[:500]})
      if len(rows)>=100: break
    if len(rows)>=100: break
  if section=='caches':
   for scope,flags in [('system',[]),('user',['--user'])]:
    result=subprocess.run(['journalctl',*flags,'--disk-usage'],capture_output=True,text=True,timeout=8)
    rows.append({'name':scope+' journal storage','state':'observed' if result.returncode==0 else 'unavailable','detail':result.stdout[:1000] if result.returncode==0 else 'Journal storage owner unavailable'})
   rows.append({'name':'MO data retention','state':'review','detail':'Active writers, configured state home and useful artifacts require MO-owner review before cleanup'})
 except Exception: rows.append({'name':'MO service','state':'unavailable','detail':'Service manager evidence unavailable'})
else: rows.append({'name':'MO service','state':'not checked','detail':'Host service-owner inspection required'})
print(json.dumps({'state':'partial','rows':rows,'bounded':len(rows)>=100 or len(units)>=16,'coverage':'Selected host and matching system/user MO units; 16 units and 100 log rows maximum; custom service/dependency names and deployment health are not inferred'}))"""
    remote = "python3 -c " + shlex.quote(code)
    result = adapter._run_read_command([executable, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                                       "-o", "StrictHostKeyChecking=yes", target, remote], timeout=30, output_limit=128_000,
                                      cancelled=cancelled)
    try:
        if result["returncode"] or result.get("truncated"):
            raise ValueError("Transport unavailable")
        payload = json.loads(result["stdout"])
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
            raise ValueError("Incomplete host evidence")
    except (ValueError, TypeError):
        from core.tooling.sandbox import redact_sensitive_text
        detail = ("Host inspection timed out; no host health result" if result.get("error_kind") == "TimeoutExpired"
                  else "Host inspection output exceeded its bound" if result.get("truncated")
                  else "Host transport failed" if result["returncode"] else "Host returned incomplete inspection evidence")
        error = redact_sensitive_text(str(result.get("stderr") or result.get("error_kind") or ""))[:500]
        return {"state": "unavailable", "rows": [], "at": time.time(),
                "detail": detail + (": " + error if error else "")}
    from core.tooling.sandbox import redact_sensitive_text
    for row in payload["rows"]:
        row["detail"] = redact_sensitive_text(str(row.get("detail", "")))
    return {**payload, "at": time.time()}
