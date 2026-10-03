"""Bounded selected-root storage discovery, with content-verified duplicates."""
from __future__ import annotations

import hashlib
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from .targets import PROJECT_EXCLUDED_DIRS, _reparse, project_target


_LEFTOVER_SUFFIXES = {".exe", ".dll", ".pdb", ".log", ".tmp", ".dmp"}


def _leftover_root(adapter: Any, subject: dict[str, Any], state: Any, *,
                   cancelled: Callable[[], bool] | None = None) -> Path:
    from .actions import capture, ps_query
    if state is None:
        raise ValueError("A verified SystemCare uninstall receipt is required")
    row = state.backup(str(subject.get("uninstall_step", "")))
    if not row or row["action"] != "app_uninstall" or row["state"] != "applied" or row["subject"].get("kind") != "app" or row.get("after", {}).get("exists") is not False:
        raise ValueError("Select leftovers from a verified registered-app uninstall")
    if capture(adapter, row["subject"]).get("exists"):
        raise ValueError("App registration has returned; its files remain protected")
    raw = row["original"].get("InstallLocation")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Uninstaller did not declare an exact installation folder")
    root = Path(os.path.expandvars(raw.strip()))
    if not root.is_absolute() or len(root.parts) < 3:
        raise ValueError("Broad installation roots remain protected")
    from core.state.paths import repo_root
    if any(p == root or root in p.parents for p in (Path(repo_root()).resolve(), Path(sys.executable).resolve())):
        raise ValueError("The active MO checkout and runtime remain protected")
    protected = [Path(p) for p in (os.environ.get("SystemRoot"), os.environ.get("USERPROFILE")) if p]
    local_programs = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
    if any(root == p or p in root.parents for p in protected) and local_programs not in root.parents:
        raise ValueError("Windows and user data are outside leftover cleanup")
    adapter._check_unlinked_owner(Path(root.anchor), root)
    # Another registration or running binary changes this folder's ownership.
    from .actions import _literal
    evidence = ps_query(adapter, "$root=" + _literal(str(root).rstrip("\\") + "\\") + ";"
        "$locations=@('HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall',"
        "'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall',"
        "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall');"
        "$apps=@(foreach($p in $locations){if(Test-Path -LiteralPath $p){Get-ChildItem -LiteralPath $p|Get-ItemProperty|Where-Object {"
        "$_.InstallLocation -and (($_.InstallLocation.TrimEnd('\\')+'\\').StartsWith($root,[StringComparison]::OrdinalIgnoreCase) -or "
        "$root.StartsWith(($_.InstallLocation.TrimEnd('\\')+'\\'),[StringComparison]::OrdinalIgnoreCase))}}});"
        "$running=@(Get-CimInstance Win32_Process|Where-Object {$_.ExecutablePath -and $_.ExecutablePath.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)});"
        "@{registered=$apps.Count;running=$running.Count}|ConvertTo-Json -Compress",
        **({"cancelled": cancelled} if cancelled is not None else {}))
    if evidence.get("registered") != 0 or evidence.get("running") != 0:
        raise ValueError("Folder has a current app or process owner; leftovers remain protected")
    return root


def capture_app_leftover(adapter: Any, subject: dict[str, Any], state: Any) -> dict[str, Any]:
    root = _leftover_root(adapter, subject, state)
    relative = Path(str(subject.get("relative", "")))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts or relative.suffix.casefold() not in _LEFTOVER_SUFFIXES:
        raise ValueError("Select one reviewed binary or generated file; app data is protected")
    path = root / relative
    try:
        adapter._check_unlinked_owner(root, path)
        info = path.stat()
    except FileNotFoundError:
        return {"exists": False}
    if not path.is_file() or info.st_size > 128 * 1024**2:
        raise ValueError("Leftover is not a bounded regular file")
    from .models import canonical_digest
    return {"exists": True, "root": str(root), "relative": relative.as_posix(), "bytes": info.st_size,
            "fingerprint": canonical_digest((info.st_ino, info.st_size, info.st_mtime_ns))}


def inspect_app_leftovers(adapter: Any, state: Any, *, cancelled: Callable[[], bool]) -> dict[str, Any]:
    from .windows import ScanCancelled
    rows, excluded, examined = [], 0, 0
    deadline, bounded = time.monotonic() + 10, False
    # Irreversible uninstall originals intentionally do not appear in the undo
    # list. Use retained apply receipts, rather than inventing a second ledger.
    step_ids = dict.fromkeys(step for receipt in state.recent_receipts(200) for step in receipt.get("applied_steps", []))
    for step_id in step_ids:
        if cancelled():
            raise ScanCancelled("app leftover inspection cancelled")
        if time.monotonic() >= deadline:
            bounded = True
            break
        receipt = state.backup(step_id)
        if not receipt:
            continue
        if receipt["action"] != "app_uninstall" or receipt["state"] != "applied" or receipt["subject"].get("kind") != "app":
            continue
        subject = {"kind": "app_leftover", "uninstall_step": step_id}
        try:
            root = _leftover_root(adapter, subject, state, cancelled=cancelled)
        except ScanCancelled:
            raise
        except (OSError, ValueError, RuntimeError):
            excluded += 1
            continue
        stack = [root]
        while stack and not bounded:
            directory = stack.pop()
            try:
                adapter._check_unlinked_owner(root, directory)
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if cancelled():
                            raise ScanCancelled("app leftover inspection cancelled")
                        if examined >= 1000 or len(rows) >= 300 or time.monotonic() >= deadline:
                            bounded = True
                            break
                        examined += 1
                        path = Path(entry.path)
                        if _reparse(path):
                            excluded += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(path)
                            continue
                        if not entry.is_file(follow_symlinks=False) or path.suffix.casefold() not in _LEFTOVER_SUFFIXES:
                            excluded += 1
                            continue
                        info = entry.stat(follow_symlinks=False)
                        if info.st_size > 128 * 1024**2:
                            excluded += 1
                            continue
                        rows.append({"name": receipt["original"].get("DisplayName", "Uninstalled app") + " · " + entry.name,
                                     "state": "Review", "eligible": True, "bytes": info.st_size,
                                     "subject": {**subject, "name": entry.name, "relative": str(path.relative_to(root))},
                                     "detail": "File retained in the exact captured installation folder after a verified uninstall; configuration, saves and databases are excluded"})
            except (OSError, ValueError):
                excluded += 1
        if bounded:
            break
    return {"state": "partial", "rows": rows, "excluded": excluded, "bounded": bounded, "at": time.time(),
            "detail": "Verified SystemCare uninstall receipts only; up to 1,000 entries / 300 results and a 10-second discovery budget, plus the current native ownership query. Each file is reviewed separately; no guessed user-data folder or recursive folder removal."}


def remove_app_leftover(adapter: Any, subject: dict[str, Any], expected: dict[str, Any], state: Any, *, cancelled: Callable[[], bool]) -> str:
    if capture_app_leftover(adapter, subject, state) != expected or not expected.get("exists"):
        raise ValueError("Leftover changed after review")
    if cancelled():
        from .windows import ScanCancelled
        raise ScanCancelled("leftover cleanup cancelled before mutation")
    path = Path(expected["root"]) / expected["relative"]
    path.unlink()
    if path.exists():
        raise RuntimeError("Selected leftover remains")
    return "Selected leftover file removed; application data and other folders retained."


def inspect_storage(target: str, config: dict[str, Any], section: str, *, cancelled: Callable[[], bool]) -> dict[str, Any]:
    root = project_target(target, config)
    rows, files, examined, skipped = [], [], 0, 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        if cancelled():
            from .windows import ScanCancelled
            raise ScanCancelled("storage discovery cancelled")
        dirs[:] = [n for n in dirs if n.casefold() not in PROJECT_EXCLUDED_DIRS and not _reparse(Path(directory) / n)]
        if section == "empty" and not dirs and not names:
            rows.append({"name": str(Path(directory).relative_to(root)), "state": "Review", "detail": "Empty at inspection; ownership and later writes are not inferred"})
        for name in names:
            examined += 1
            if examined > 5000:
                break
            path = Path(directory) / name
            if _reparse(path):
                skipped += 1
                continue
            try:
                info = path.stat()
            except OSError:
                skipped += 1
                continue
            files.append((path, info.st_size, info.st_mtime_ns))
        if examined > 5000:
            break
    if section == "large":
        rows = [{"name": str(p.relative_to(root)), "bytes": size, "state": "Review", "detail": "Measured size; no deletion or organization authorized"} for p, size, _ in sorted(files, key=lambda f: f[1], reverse=True)[:300]]
    elif section == "duplicates":
        sizes = defaultdict(list)
        for file in files:
            sizes[file[1]].append(file)
        groups = defaultdict(list)
        hashed = 0
        for size, candidates in sizes.items():
            if size == 0 or len(candidates) < 2:
                continue
            for path, _, modified in candidates:
                if cancelled():
                    from .windows import ScanCancelled
                    raise ScanCancelled("duplicate inspection cancelled")
                if size > 512 * 1024**2 or hashed + size > 2 * 1024**3:
                    skipped += 1
                    continue
                digest = hashlib.sha256()
                try:
                    with path.open("rb") as handle:
                        while chunk := handle.read(1024 * 1024):
                            if cancelled():
                                from .windows import ScanCancelled
                                raise ScanCancelled("duplicate inspection cancelled")
                            digest.update(chunk)
                    after = path.stat()
                    if after.st_size != size or after.st_mtime_ns != modified or _reparse(path):
                        skipped += 1
                        continue
                except OSError:
                    skipped += 1
                    continue
                hashed += size
                groups[(size, digest.hexdigest())].append(str(path.relative_to(root)))
        rows = [{"name": paths[0], "bytes": size, "state": "Review", "detail": "SHA-256 content match; required copies must be reviewed", "copies": paths, "digest": digest}
                for (size, digest), paths in groups.items() if len(paths) > 1][:300]
    elif section != "empty":
        raise ValueError("Unknown storage discovery")
    return {"state": "partial", "at": time.time(), "rows": rows[:300], "examined": min(examined, 5000), "skipped": skipped,
            "detail": "Selected folder; up to 5,000 files / 300 results. Dependency and private-state exclusions remain protected."}
