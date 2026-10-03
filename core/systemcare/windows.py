"""Narrow Windows inspection and allowlisted SystemCare operations."""
from __future__ import annotations

import ctypes
import csv
from dataclasses import replace
import fnmatch
import hashlib
from itertools import islice
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from core.runtime.subprocess_flags import run_with_file_capture
from core.state.device import device_identity

from .catalog import RuleSpec, rule_for_id
from .models import (
    CALIBRATION_SCHEMA_VERSION,
    Calibration,
    Candidate,
    Finding,
    PlanStep,
    RiskLevel,
    Severity,
    UndoQuality,
    canonical_digest,
    new_id,
)


CALIBRATION_TTL_SECONDS = 30 * 86_400
FILE_SCAN_DEADLINE_SECONDS = 8.0
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


class ScanCancelled(RuntimeError):
    pass


class WindowsSystemCareAdapter:
    """Read current Windows state and execute only catalog-owned operations."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config if config is not None else {}

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def probe_fingerprint(self) -> str:
        facts = self._fingerprint_probe()
        return self._machine_fingerprint(facts, self._discover_paths())

    def calibrate(
        self,
        *,
        progress: Callable[[str, float], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Calibration:
        if not self.supported:
            raise RuntimeError("MO SystemCare is available only on Windows")

        def stage(label: str, fraction: float) -> None:
            if cancelled and cancelled():
                raise ScanCancelled("calibration cancelled")
            if progress:
                progress(label, fraction)

        stage("Reading Windows identity", 0.08)
        stable = self._stable_probe()
        stage("Discovering Windows roots", 0.28)
        paths = self._discover_paths()
        stage("Discovering fixed volumes", 0.48)
        drives = self._fixed_drives()
        stage("Checking hardware capabilities", 0.67)
        memory_bytes = self._physical_memory_bytes()
        battery = self._power_status().get("battery_present", False)
        stage("Checking supported Windows owners", 0.84)
        capabilities = {
            "file_inventory": True,
            "startup_registry": self._winreg_available(),
            "power_scheme": bool(shutil.which("powercfg")),
            "battery": bool(battery),
            "dism": bool(shutil.which("dism")),
            "sfc": bool(shutil.which("sfc")),
            "recovery": bool(shutil.which("reagentc")),
            "protected_read": self._is_admin(),
            "apply_user_cleanup": True,
        }
        now = time.time()
        stage("Calibration ready", 1.0)
        return Calibration(
            calibration_id=new_id("calibration"),
            device_id=device_identity(self.config)["device_id"],
            created_at=now,
            refresh_after=now + CALIBRATION_TTL_SECONDS,
            fingerprint=self._machine_fingerprint(stable, paths),
            os_name=str(stable.get("os_name") or "Windows"),
            os_version=str(stable.get("os_version") or ""),
            os_build=str(stable.get("os_build") or ""),
            architecture=str(stable.get("architecture") or ""),
            cpu_logical=int(stable.get("cpu_logical") or 0),
            cpu_name=str(stable.get("cpu_name") or ""),
            memory_bytes=memory_bytes,
            machine_model=str(stable.get("machine_model") or ""),
            gpu_names=tuple(str(item) for item in stable.get("gpu_names") or ()),
            windows_edition=str(stable.get("windows_edition") or ""),
            system_drive=str(stable.get("system_drive") or ""),
            drive_roots=tuple(drives),
            paths=paths,
            capabilities=capabilities,
            schema_version=CALIBRATION_SCHEMA_VERSION,
        )

    def scan_rule(
        self,
        rule: RuleSpec,
        calibration: Calibration,
        scan_id: str,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> Finding:
        scanners: dict[str, Callable[..., Finding]] = {
            "file_inventory": self._scan_files,
            "browser_cache": self._scan_browser_cache,
            "disk_pressure": self._scan_disk_pressure,
            "startup_inventory": self._scan_startup,
            "restart_state": self._scan_restart,
            "storage_sense": self._scan_storage_sense,
            "health_services": self._scan_health_services,
            "performance_baseline": self._scan_performance,
            "component_store": self._scan_component_store,
            "integrity_readiness": self._scan_integrity,
            "service_inventory": self._scan_services_advisory,
            "startup_target_health": self._scan_startup_target_health,
        }
        scanner = scanners.get(rule.scanner)
        if scanner is None:
            raise RuntimeError(f"SystemCare scanner is unavailable for {rule.rule_id}")
        if cancelled and cancelled():
            raise ScanCancelled("scan cancelled")
        return scanner(rule, calibration, scan_id, cancelled=cancelled)

    def apply_step(
        self,
        step: PlanStep,
        calibration: Calibration,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        rule = rule_for_id(step.rule_id)
        if step.action != "delete_files" or rule.action != "delete_files":
            raise RuntimeError("SystemCare action is not enabled for this rule")
        if step.requires_elevation:
            raise RuntimeError("elevated SystemCare steps are not enabled in this process")
        if rule.owner_processes and self._owner_busy(rule, fresh=True):
            raise RuntimeError("Close the owning application before this cache cleanup")
        deleted = 0
        reclaimed = 0
        skipped = 0
        failed = 0
        for candidate in step.candidates:
            if cancelled and cancelled():
                raise ScanCancelled("apply cancelled at a safe file boundary")
            if rule.owner_processes and self._owner_busy(rule):
                raise RuntimeError("Owning application became active; remaining caches are protected")
            try:
                path = self._validated_candidate_path(rule, calibration, candidate)
            except (OSError, ValueError):
                skipped += 1
                continue
            try:
                os.remove(path)
            except (FileNotFoundError, PermissionError, OSError):
                failed += 1
                continue
            if path.exists():
                failed += 1
                continue
            deleted += 1
            reclaimed += candidate.size_bytes
        return {
            "deleted": deleted,
            "reclaimed_bytes": reclaimed,
            "skipped": skipped,
            "failed": failed,
            "verified": failed == 0,
        }

    def _stable_probe(self) -> dict[str, Any]:
        result = self._fingerprint_probe()
        hardware = self._hardware_identity()
        result.update({
            "cpu_name": hardware.get("cpu_name", ""),
            "machine_model": hardware.get("machine_model", ""),
            "gpu_names": hardware.get("gpu_names", []),
            "windows_edition": hardware.get("windows_edition", ""),
        })
        return result

    def _fingerprint_probe(self) -> dict[str, Any]:
        """Read only the cheap stable shape used to validate a calibration."""
        win = sys.getwindowsversion() if self.supported and hasattr(sys, "getwindowsversion") else None
        system_drive = str(os.environ.get("SystemDrive") or Path(os.environ.get("SystemRoot") or "C:\\").anchor or "C:").upper()
        roots = sorted(root.upper() for root in self._fixed_drives()) if self.supported else []
        return {
            "schema": CALIBRATION_SCHEMA_VERSION,
            "os_name": platform.system() or "Windows",
            "os_version": platform.version(),
            "os_build": str(getattr(win, "build", "") or platform.release()),
            "architecture": platform.machine(),
            "cpu_logical": int(os.cpu_count() or 0),
            "memory_bytes": self._physical_memory_bytes(),
            "system_drive": system_drive,
            "fixed_roots": roots,
        }

    def _discover_paths(self) -> dict[str, str]:
        home = Path.home().resolve(strict=False)
        environment = os.environ
        candidates: dict[str, Path] = {
            "home": home,
            "temp": Path(tempfile.gettempdir()),
            "local_appdata": Path(environment.get("LOCALAPPDATA") or home / "AppData" / "Local"),
            "roaming_appdata": Path(environment.get("APPDATA") or home / "AppData" / "Roaming"),
            "programdata": Path(environment.get("PROGRAMDATA") or "C:/ProgramData"),
            "system_root": Path(environment.get("SystemRoot") or "C:/Windows"),
        }
        candidates.update(self.startup_paths())
        resolved: dict[str, str] = {}
        for key, path in candidates.items():
            try:
                resolved[key] = str(path.expanduser().resolve(strict=False))
            except OSError:
                resolved[key] = str(path.expanduser())
        return resolved

    @staticmethod
    def startup_paths() -> dict[str, Path]:
        """Resolve actual Windows Startup folders, including redirected locations."""
        if os.name != "nt":
            return {}
        owner = ctypes.windll.shell32.SHGetFolderPathW
        owner.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p)
        owner.restype = ctypes.c_long
        paths = {}
        for name, folder_id in (("startup_user", 0x07), ("startup_all", 0x18)):
            buffer = ctypes.create_unicode_buffer(260)
            if owner(None, folder_id, None, 0, buffer) == 0 and buffer.value:
                paths[name] = Path(buffer.value)
        return paths

    @staticmethod
    def _machine_fingerprint(stable: Mapping[str, Any], paths: Mapping[str, str]) -> str:
        stable_shape = {
            key: stable.get(key)
            for key in (
                "schema",
                "os_name",
                "os_version",
                "os_build",
                "architecture",
                "cpu_logical",
                "memory_bytes",
                "system_drive",
                "fixed_roots",
            )
        }
        path_shape = {
            str(key): os.path.normcase(str(Path(value).resolve(strict=False)))
            for key, value in sorted(paths.items())
        }
        return canonical_digest({"stable": stable_shape, "path_shape": path_shape})

    def _scan_files(
        self,
        rule: RuleSpec,
        calibration: Calibration,
        scan_id: str,
        *,
        cancelled: Callable[[], bool] | None,
        deadline: float | None = None,
    ) -> Finding:
        if rule.owner_processes and self._owner_busy(rule):
            return self._finding(rule, scan_id, "Owning application is running or process evidence is unavailable; its caches remain protected.",
                                 evidence={"active_owner_excluded": True})
        root_raw = str(calibration.paths.get(rule.root_key) or "")
        if not root_raw:
            return self._finding(rule, scan_id, "Location is unavailable on this Windows profile.", evidence={"available": False})
        root = Path(root_raw)
        base = root / rule.relative_root if rule.relative_root else root
        if not self._is_within(base, root):
            return self._finding(rule, scan_id, "Configured location is outside its calibrated Windows owner.", severity=Severity.ATTENTION, evidence={"protected": True})
        try:
            exists = base.exists()
        except (PermissionError, OSError):
            return self._finding(
                rule,
                scan_id,
                "The Windows-owned location is protected from this non-elevated scan; no action is available.",
                severity=Severity.INFO if not rule.selectable else Severity.REVIEW,
                evidence={"protected": True, "mutation_enabled": False},
            )
        if not exists:
            return self._finding(rule, scan_id, "No matching Windows-owned location is present.", severity=Severity.GOOD)
        try:
            self._check_unlinked_owner(root, base)
        except (ValueError, OSError):
            return self._finding(rule, scan_id, "Linked or substituted cleanup owner is protected.", severity=Severity.REVIEW, evidence={"protected": True})
        deadline = deadline if deadline is not None else time.monotonic() + FILE_SCAN_DEADLINE_SECONDS
        cutoff_ns = int((time.time() - rule.minimum_age_days * 86_400) * 1_000_000_000)
        candidates: list[Candidate] = []
        measured = 0
        scanned = 0
        truncated = False
        stack = [base]
        while stack:
            if cancelled and cancelled():
                raise ScanCancelled("scan cancelled")
            if time.monotonic() >= deadline or len(candidates) >= rule.max_candidates:
                truncated = True
                break
            current = stack.pop()
            try:
                with os.scandir(current) as iterator:
                    entries = list(islice(iterator, 10_001))
                if len(entries) > 10_000:
                    entries = entries[:10_000]
                    truncated = True
            except (FileNotFoundError, PermissionError, OSError):
                continue
            for entry in entries:
                if time.monotonic() >= deadline:
                    truncated = True
                    break
                if cancelled and cancelled():
                    raise ScanCancelled("scan cancelled")
                scanned += 1
                try:
                    stat = entry.stat(follow_symlinks=False)
                    if self._is_reparse(entry.path, stat):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if rule.recursive:
                            stack.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except (FileNotFoundError, PermissionError, OSError):
                    continue
                if rule.patterns and not any(fnmatch.fnmatch(entry.name.casefold(), pattern.casefold()) for pattern in rule.patterns):
                    continue
                modified_ns = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))
                if modified_ns > cutoff_ns:
                    continue
                path = Path(entry.path)
                if not self._is_within(path, root):
                    continue
                relative = path.relative_to(root).as_posix()
                size = max(0, int(stat.st_size))
                fingerprint = self._candidate_fingerprint(rule.rule_id, rule.root_key, relative, size, modified_ns)
                candidates.append(Candidate(
                    candidate_id=fingerprint[:24],
                    root_key=rule.root_key,
                    relative_path=relative,
                    size_bytes=size,
                    modified_ns=modified_ns,
                    fingerprint=fingerprint,
                ))
                measured += size
                if len(candidates) >= rule.max_candidates:
                    truncated = True
                    break
        if candidates:
            suffix = " (bounded result)" if truncated else ""
            qualifier = "eligible" if rule.selectable else "measured"
            summary = f"{len(candidates):,} {qualifier} file{'s' if len(candidates) != 1 else ''} · {_format_bytes(measured)}{suffix}."
            severity = Severity.REVIEW
        else:
            summary = "No eligible files were found." if rule.selectable else "No matching files were measured."
            severity = Severity.GOOD
        return self._finding(
            rule,
            scan_id,
            summary,
            severity=severity,
            item_count=len(candidates),
            measured_bytes=measured,
            reclaimable_bytes=measured if rule.selectable else 0,
            candidates=tuple(candidates) if rule.selectable else (),
            evidence={"scanned_entries": scanned, "bounded": truncated, "minimum_age_days": rule.minimum_age_days},
        )

    def _scan_disk_pressure(self, rule: RuleSpec, calibration: Calibration, scan_id: str, **_kwargs: Any) -> Finding:
        rows: list[str] = []
        attention = False
        total_free = 0
        total_size = 0
        for root in calibration.drive_roots[:32]:
            try:
                usage = shutil.disk_usage(root)
            except OSError:
                continue
            free_ratio = usage.free / usage.total if usage.total else 0.0
            label = Path(root).drive or root[:2]
            rows.append(f"{label} {_format_bytes(usage.free)} free ({free_ratio:.0%})")
            total_free += usage.free
            total_size += usage.total
            attention = attention or free_ratio < 0.10
        if not rows:
            return self._finding(rule, scan_id, "Fixed-volume capacity could not be read.", severity=Severity.ATTENTION)
        return self._finding(
            rule,
            scan_id,
            f"{len(rows)} fixed volume{'s' if len(rows) != 1 else ''} measured; " + ("one or more need attention." if attention else "capacity is available."),
            severity=Severity.ATTENTION if attention else Severity.GOOD,
            measured_bytes=total_size,
            evidence={"volumes": rows, "total_free_bytes": total_free},
        )

    def _scan_startup(self, rule: RuleSpec, calibration: Calibration, scan_id: str, **_kwargs: Any) -> Finding:
        rows = self._startup_entries(calibration)
        registry_count = sum(1 for item in rows if item[0] == "registry")
        folder_count = len(rows) - registry_count
        return self._finding(
            rule,
            scan_id,
            f"{len(rows)} startup entr{'y' if len(rows) == 1 else 'ies'} inventoried; impact is not assumed.",
            severity=Severity.REVIEW if rows else Severity.GOOD,
            item_count=len(rows),
            evidence={"registry_entries": registry_count, "startup_folder_entries": folder_count},
        )

    def _scan_restart(self, rule: RuleSpec, _calibration: Calibration, scan_id: str, **_kwargs: Any) -> Finding:
        signals = self._pending_restart_signals()
        return self._finding(
            rule,
            scan_id,
            "Windows reports a pending restart or servicing completion." if signals else "No known pending-restart signal was found.",
            severity=Severity.ATTENTION if signals else Severity.GOOD,
            item_count=len(signals),
            evidence={"signals": signals},
        )

    def _scan_storage_sense(self, rule: RuleSpec, _calibration: Calibration, scan_id: str, **_kwargs: Any) -> Finding:
        state = self._storage_sense_state()
        return self._finding(
            rule,
            scan_id,
            f"Windows Storage Sense is {state}. SystemCare does not change its schedule or personal-folder choices.",
            severity=Severity.INFO if state in {"on", "off"} else Severity.REVIEW,
            evidence={"state": state, "mutation_enabled": False},
        )

    def _scan_health_services(self, rule: RuleSpec, _calibration: Calibration, scan_id: str, **_kwargs: Any) -> Finding:
        services = {
            "Windows Update": self._service_state("wuauserv"),
            "Security Center": self._service_state("wscsvc"),
            "Microsoft Defender": self._service_state("WinDefend"),
        }
        missing = [label for label, state in services.items() if state in {"missing", "disabled", "error"}]
        return self._finding(
            rule,
            scan_id,
            "Windows health service attention is needed." if missing else "Windows health services were found; stopped demand services are not treated as failures.",
            severity=Severity.ATTENTION if missing else Severity.GOOD,
            item_count=len(services),
            evidence={"states": [f"{label}: {state}" for label, state in services.items()]},
        )

    def _scan_performance(self, rule: RuleSpec, calibration: Calibration, scan_id: str, **_kwargs: Any) -> Finding:
        scheme = self._active_power_scheme()
        power = self._power_status()
        game_mode = self._game_mode_state()
        summary = f"Power: {scheme or 'not reported'} · source: {power.get('source', 'unknown')} · Game Mode: {game_mode}."
        return self._finding(
            rule,
            scan_id,
            summary,
            severity=Severity.INFO,
            evidence={
                "power_scheme": scheme or "not reported",
                "power_source": power.get("source", "unknown"),
                "battery_present": bool(calibration.capabilities.get("battery")),
                "game_mode": game_mode,
            },
        )

    def _scan_component_store(self, rule: RuleSpec, calibration: Calibration, scan_id: str, **_kwargs: Any) -> Finding:
        available = bool(calibration.capabilities.get("dism"))
        protected = bool(calibration.capabilities.get("protected_read"))
        if not available:
            summary = "Windows component analysis owner is unavailable."
            severity = Severity.ATTENTION
        elif protected:
            summary = "Component-store analysis is available; no cleanup or repair was run."
            severity = Severity.INFO
        else:
            summary = "Component-store analysis is available but protected evidence needs a separate one-shot elevated read."
            severity = Severity.REVIEW
        return self._finding(rule, scan_id, summary, severity=severity, evidence={"available": available, "protected_read_ready": protected})

    def _scan_integrity(self, rule: RuleSpec, calibration: Calibration, scan_id: str, **_kwargs: Any) -> Finding:
        owners = [name for name in ("dism", "sfc", "recovery") if calibration.capabilities.get(name)]
        return self._finding(
            rule,
            scan_id,
            f"{len(owners)} Windows integrity/recovery owner{'s' if len(owners) != 1 else ''} available; no verification or repair was run.",
            severity=Severity.INFO if owners else Severity.ATTENTION,
            item_count=len(owners),
            evidence={"owners": owners},
        )

    def _scan_services_advisory(self, rule: RuleSpec, _calibration: Calibration, scan_id: str, **_kwargs: Any) -> Finding:
        automatic = self._automatic_service_count()
        return self._finding(
            rule,
            scan_id,
            f"{automatic} automatic service registration{'s' if automatic != 1 else ''} counted for evidence; none are classified or disabled.",
            severity=Severity.INFO,
            item_count=automatic,
            evidence={"automatic_services": automatic, "mutation_enabled": False},
        )

    def _scan_startup_target_health(self, rule: RuleSpec, calibration: Calibration, scan_id: str, **_kwargs: Any) -> Finding:
        entries = self._startup_entries(calibration)
        checked = 0
        missing = 0
        for _kind, _name, command in entries:
            target = self._command_executable(command)
            if target is None:
                continue
            checked += 1
            if not target.exists():
                missing += 1
        summary = f"{checked} exact executable target{'s' if checked != 1 else ''} checked; {missing} missing."
        return self._finding(
            rule,
            scan_id,
            summary,
            severity=Severity.REVIEW if missing else Severity.GOOD,
            item_count=missing,
            evidence={"checked_targets": checked, "missing_targets": missing, "generic_registry_scan": False},
        )

    def _finding(
        self,
        rule: RuleSpec,
        scan_id: str,
        summary: str,
        *,
        severity: Severity = Severity.INFO,
        item_count: int = 0,
        measured_bytes: int = 0,
        reclaimable_bytes: int = 0,
        candidates: tuple[Candidate, ...] = (),
        evidence: Mapping[str, Any] | None = None,
    ) -> Finding:
        suffix = hashlib.sha256(f"{scan_id}:{rule.rule_id}".encode()).hexdigest()[:20]
        selectable = bool(rule.selectable and candidates)
        return Finding(
            finding_id=f"finding-{suffix}",
            scan_id=scan_id,
            rule_id=rule.rule_id,
            domain=rule.domain,
            title=rule.title,
            summary=summary,
            severity=severity,
            item_count=item_count,
            measured_bytes=measured_bytes,
            reclaimable_bytes=reclaimable_bytes,
            selectable=selectable,
            default_selected=bool(rule.default_selected and selectable),
            requires_elevation=rule.requires_elevation,
            risk=rule.risk if selectable else RiskLevel.NONE,
            undo=rule.undo if selectable else UndoQuality.NOT_APPLICABLE,
            action=rule.action if selectable else "review",
            evidence=evidence or {},
            candidates=candidates,
        )

    def _validated_candidate_path(self, rule: RuleSpec, calibration: Calibration, candidate: Candidate) -> Path:
        if candidate.root_key != rule.root_key:
            raise ValueError("candidate root does not match rule")
        root_raw = str(calibration.paths.get(rule.root_key) or "")
        if not root_raw:
            raise ValueError("calibrated root is unavailable")
        root = Path(root_raw)
        path = root / candidate.relative_path
        self._check_unlinked_owner(root, path)
        if not self._is_within(path, root):
            raise ValueError("candidate escaped calibrated root")
        base = root / rule.relative_root if rule.relative_root else root
        if not self._is_within(path, base):
            raise ValueError("candidate escaped rule root")
        if rule.scanner == "browser_cache" and not self._browser_relative_allowed(rule, path.relative_to(base)):
            raise ValueError("candidate is outside the browser's rebuildable cache owners")
        stat = path.stat(follow_symlinks=False)
        if self._is_reparse(path, stat) or not path.is_file():
            raise ValueError("candidate is not a regular file")
        modified_ns = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))
        size = max(0, int(stat.st_size))
        fingerprint = self._candidate_fingerprint(rule.rule_id, rule.root_key, candidate.relative_path, size, modified_ns)
        if size != candidate.size_bytes or modified_ns != candidate.modified_ns or fingerprint != candidate.fingerprint:
            raise ValueError("candidate changed after scan")
        cutoff_ns = int((time.time() - rule.minimum_age_days * 86_400) * 1_000_000_000)
        if modified_ns > cutoff_ns:
            raise ValueError("candidate is no longer old enough")
        if rule.patterns and not any(fnmatch.fnmatch(path.name.casefold(), pattern.casefold()) for pattern in rule.patterns):
            raise ValueError("candidate no longer matches rule")
        return path

    def _check_unlinked_owner(self, root: Path, path: Path) -> None:
        relative = path.relative_to(root)
        if any(part == ".." for part in relative.parts) or not root.is_absolute():
            raise ValueError("Cleanup path escaped its declared owner")
        current = root
        for part in ("", *relative.parts):
            current = current / part if part else current
            if self._is_reparse(current, current.lstat()):
                raise ValueError("Linked cleanup ancestor is protected")

    def _owner_busy(self, rule: RuleSpec, *, fresh: bool = False) -> bool:
        sample = getattr(self, "_process_names_sample", None)
        if fresh or sample is None or time.monotonic() - sample[0] > 2:
            result = self._run_read_command(["tasklist.exe", "/FO", "CSV", "/NH"], timeout=8, output_limit=256_000)
            names = None
            if result["returncode"] == 0 and not result.get("truncated"):
                names = {row[0].casefold() for row in csv.reader(str(result["stdout"]).splitlines()) if len(row) >= 2}
                if not names:
                    names = None
            sample = (time.monotonic(), names)
            self._process_names_sample = sample
        return sample[1] is None or any(name.casefold() in sample[1] for name in rule.owner_processes)

    @staticmethod
    def _browser_relative_allowed(rule: RuleSpec, relative: Path) -> bool:
        parts = relative.parts
        if len(parts) < 3:
            return False
        if rule.rule_id.endswith("firefox"):
            return bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", parts[0])) and parts[1] in {"cache2", "startupCache"}
        return (parts[0] == "Default" or bool(re.fullmatch(r"Profile \d+", parts[0]))) and parts[1] in {"Cache", "Code Cache", "GPUCache"}

    def _scan_browser_cache(self, rule: RuleSpec, calibration: Calibration, scan_id: str, *, cancelled: Callable[[], bool] | None) -> Finding:
        root_raw = str(calibration.paths.get(rule.root_key) or "")
        if not root_raw:
            return self._finding(rule, scan_id, "Browser cache root is unavailable")
        root = Path(root_raw)
        base = root / rule.relative_root
        if rule.owner_processes and self._owner_busy(rule):
            return self._finding(rule, scan_id, "Open browser or unavailable process evidence; caches protected.", evidence={"active_owner_excluded": True})
        candidates = []
        bounded = False
        try:
            profiles = list(islice(base.iterdir(), 65)) if base.is_dir() else []
            bounded = len(profiles) > 64
            profiles = profiles[:64]
        except OSError:
            profiles = []
        cache_names = ("cache2", "startupCache") if rule.rule_id.endswith("firefox") else ("Cache", "Code Cache", "GPUCache")
        deadline = time.monotonic() + FILE_SCAN_DEADLINE_SECONDS
        for profile in profiles:
            if self._is_reparse(profile) or not profile.is_dir():
                continue
            for name in cache_names:
                if not self._browser_relative_allowed(rule, Path(profile.name) / name / "candidate"):
                    continue
                cache_rule = replace(rule, relative_root=rule.relative_root + "/" + profile.name + "/" + name,
                                     scanner="file_inventory", max_candidates=rule.max_candidates - len(candidates))
                if time.monotonic() >= deadline:
                    bounded = True
                    break
                result = self._scan_files(cache_rule, calibration, scan_id, cancelled=cancelled, deadline=deadline)
                candidates.extend(result.candidates)
                bounded = bounded or bool(result.evidence.get("bounded"))
                if len(candidates) >= rule.max_candidates:
                    bounded = True
                    break
            if len(candidates) >= rule.max_candidates or time.monotonic() >= deadline:
                bounded = True
                break
        measured = sum(c.size_bytes for c in candidates)
        return self._finding(rule, scan_id, f"{len(candidates)} closed-browser cache candidates; credentials and session data excluded.",
                             severity=Severity.REVIEW if candidates else Severity.INFO, item_count=len(candidates),
                             measured_bytes=measured, reclaimable_bytes=measured, candidates=tuple(candidates),
                             evidence={"bounded": bounded, "profiles_limit": 64, "minimum_age_days": rule.minimum_age_days})

    @staticmethod
    def _candidate_fingerprint(rule_id: str, root_key: str, relative: str, size: int, modified_ns: int) -> str:
        value = f"{rule_id}\0{root_key}\0{relative.casefold()}\0{size}\0{modified_ns}"
        return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            common = os.path.commonpath((str(path), str(root)))
            return os.path.normcase(common) == os.path.normcase(str(root))
        except (OSError, ValueError):
            return False

    def _hardware_identity(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "cpu_name": "",
            "machine_model": "",
            "gpu_names": [],
            "windows_edition": "",
        }
        if not self._winreg_available():
            return result
        import winreg

        def value(path: str, name: str) -> str:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
                    raw, _kind = winreg.QueryValueEx(key, name)
                return " ".join(str(raw or "").split())[:160]
            except (FileNotFoundError, PermissionError, OSError):
                return ""

        result["cpu_name"] = value(
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            "ProcessorNameString",
        )
        manufacturer = value(r"HARDWARE\DESCRIPTION\System\BIOS", "SystemManufacturer")
        product = value(r"HARDWARE\DESCRIPTION\System\BIOS", "SystemProductName")
        result["machine_model"] = " ".join(item for item in (manufacturer, product) if item)[:160]
        edition = value(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName")
        display = value(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "DisplayVersion")
        result["windows_edition"] = " ".join(item for item in (edition, display) if item)[:120]
        result["gpu_names"] = self._display_device_names()
        return result

    @staticmethod
    def _display_device_names() -> list[str]:
        if sys.platform != "win32":
            return []
        try:
            class DisplayDevice(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("DeviceName", ctypes.c_wchar * 32),
                    ("DeviceString", ctypes.c_wchar * 128),
                    ("StateFlags", ctypes.c_ulong),
                    ("DeviceID", ctypes.c_wchar * 128),
                    ("DeviceKey", ctypes.c_wchar * 128),
                ]

            names: list[str] = []
            index = 0
            while index < 16:
                device = DisplayDevice()
                device.cb = ctypes.sizeof(DisplayDevice)
                if not ctypes.windll.user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
                    break
                name = " ".join(str(device.DeviceString or "").split())[:160]
                if name and name not in names:
                    names.append(name)
                index += 1
            return names[:8]
        except (AttributeError, OSError):
            return []

    @staticmethod
    def _is_reparse(path: str | Path, stat: os.stat_result | None = None) -> bool:
        try:
            info = stat or os.stat(path, follow_symlinks=False)
            attributes = int(getattr(info, "st_file_attributes", 0) or 0)
            return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT) or os.path.islink(path)
        except OSError:
            return True

    def _fixed_drives(self) -> list[str]:
        if not self.supported:
            return []
        roots: list[str] = []
        try:
            mask = int(ctypes.windll.kernel32.GetLogicalDrives())
            for index in range(26):
                if not mask & (1 << index):
                    continue
                root = f"{chr(65 + index)}:\\"
                if int(ctypes.windll.kernel32.GetDriveTypeW(root)) == 3:
                    roots.append(root)
        except (AttributeError, OSError):
            pass
        if not roots:
            system = str(os.environ.get("SystemDrive") or "C:") + "\\"
            if Path(system).exists():
                roots.append(system)
        return roots[:64]

    @staticmethod
    def _physical_memory_bytes() -> int:
        if sys.platform != "win32":
            return 0
        try:
            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            state = MemoryStatus()
            state.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
                return int(state.total_physical)
        except (AttributeError, OSError):
            pass
        return 0

    @staticmethod
    def _power_status() -> dict[str, Any]:
        if sys.platform != "win32":
            return {"source": "unknown", "battery_present": False}
        try:
            class PowerStatus(ctypes.Structure):
                _fields_ = [
                    ("ac_line_status", ctypes.c_ubyte),
                    ("battery_flag", ctypes.c_ubyte),
                    ("battery_life_percent", ctypes.c_ubyte),
                    ("system_status_flag", ctypes.c_ubyte),
                    ("battery_life_time", ctypes.c_ulong),
                    ("battery_full_life_time", ctypes.c_ulong),
                ]

            status = PowerStatus()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                source = "AC" if status.ac_line_status == 1 else ("battery" if status.ac_line_status == 0 else "unknown")
                return {
                    "source": source,
                    "battery_present": status.battery_flag != 128,
                    "battery_percent": int(status.battery_life_percent) if status.battery_life_percent <= 100 else None,
                }
        except (AttributeError, OSError):
            pass
        return {"source": "unknown", "battery_present": False}

    @staticmethod
    def _is_admin() -> bool:
        if sys.platform != "win32":
            return False
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False

    @staticmethod
    def _winreg_available() -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winreg  # noqa: F401

            return True
        except ImportError:
            return False

    def _startup_entries(self, calibration: Calibration) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        if self._winreg_available():
            import winreg

            paths = (
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
            )
            for hive, key_path in paths:
                try:
                    with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ) as key:
                        index = 0
                        while index < 512:
                            try:
                                name, value, _kind = winreg.EnumValue(key, index)
                            except OSError:
                                break
                            rows.append(("registry", str(name)[:160], str(value)[:2_048]))
                            index += 1
                except (FileNotFoundError, PermissionError, OSError):
                    continue
        for key in ("startup_user", "startup_all"):
            root = Path(str(calibration.paths.get(key) or ""))
            try:
                for item in islice(root.iterdir(), 512) if root.is_dir() else []:
                    if item.is_file() and not self._is_reparse(item):
                        rows.append(("folder", item.name[:160], str(item)))
            except (PermissionError, OSError):
                continue
        return rows[:1_024]

    def _pending_restart_signals(self) -> list[str]:
        if not self._winreg_available():
            return []
        import winreg

        checks = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending", "servicing"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired", "windows update"),
        )
        found: list[str] = []
        for hive, path, label in checks:
            try:
                with winreg.OpenKey(hive, path, 0, winreg.KEY_READ):
                    found.append(label)
            except (FileNotFoundError, PermissionError, OSError):
                continue
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager",
                0,
                winreg.KEY_READ,
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "PendingFileRenameOperations")
                if value:
                    found.append("pending file rename")
        except (FileNotFoundError, PermissionError, OSError):
            pass
        return found

    def _service_state(self, name: str) -> str:
        if name not in {"wuauserv", "wscsvc", "WinDefend"}:
            return "error"
        if self._winreg_available():
            import winreg

            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    rf"SYSTEM\CurrentControlSet\Services\{name}",
                    0,
                    winreg.KEY_READ,
                ) as key:
                    start, _kind = winreg.QueryValueEx(key, "Start")
                    if int(start) == 4:
                        return "disabled"
            except FileNotFoundError:
                return "missing"
            except (PermissionError, OSError, TypeError, ValueError):
                pass
        result = self._run_read_command(["sc.exe", "query", name], timeout=8)
        text = str(result.get("stdout") or "")
        code = int(result.get("returncode") or 0)
        if code == 1060 or "does not exist" in text.casefold():
            return "missing"
        state = re.search(r"STATE\s*:\s*\d+\s+([A-Z_]+)", text, re.I)
        if not state:
            return "error" if code else "unknown"
        value = state.group(1).lower()
        return "running" if value == "running" else "stopped"

    def _active_power_scheme(self) -> str:
        result = self._run_read_command(["powercfg.exe", "/getactivescheme"], timeout=8)
        text = str(result.get("stdout") or "")
        match = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})\s*(?:\(([^)]{1,100})\))?", text, re.I)
        if not match:
            return ""
        label = " ".join(str(match.group(2) or "").split())
        return label[:100] or match.group(1).lower()

    def _game_mode_state(self) -> str:
        if not self._winreg_available():
            return "not available"
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar", 0, winreg.KEY_READ) as key:
                value, _kind = winreg.QueryValueEx(key, "AutoGameModeEnabled")
                return "on" if int(value) else "off"
        except FileNotFoundError:
            return "Windows default"
        except (PermissionError, OSError, TypeError, ValueError):
            return "not reported"

    def _storage_sense_state(self) -> str:
        if not self._winreg_available():
            return "not available"
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy",
                0,
                winreg.KEY_READ,
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "01")
                return "on" if int(value) else "off"
        except FileNotFoundError:
            return "not configured"
        except (PermissionError, OSError, TypeError, ValueError):
            return "not reported"

    def _automatic_service_count(self) -> int:
        if not self._winreg_available():
            return 0
        import winreg

        count = 0
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services", 0, winreg.KEY_READ) as root:
                index = 0
                while index < 20_000:
                    try:
                        name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(root, name, 0, winreg.KEY_READ) as key:
                            start, _kind = winreg.QueryValueEx(key, "Start")
                            if int(start) == 2:
                                count += 1
                    except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError):
                        continue
        except (FileNotFoundError, PermissionError, OSError):
            return 0
        return count

    @staticmethod
    def _command_executable(command: str) -> Path | None:
        expanded = os.path.expandvars(str(command or "").strip())
        if not expanded:
            return None
        if expanded.startswith('"'):
            end = expanded.find('"', 1)
            raw = expanded[1:end] if end > 1 else ""
        else:
            match = re.match(r"(.+?\.exe)(?:\s|$)", expanded, re.I)
            raw = match.group(1) if match else ""
        if not raw or not raw.lower().endswith(".exe"):
            return None
        path = Path(raw).expanduser()
        return path.resolve(strict=False) if path.is_absolute() else None

    @staticmethod
    def _run_read_command(command: list[str], *, timeout: int, output_limit: int = 32_000,
                          cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
        from core.runtime.backend_monitor import monitor_phase
        status: dict[str, Any] = {}
        try:
            with monitor_phase("systemcare_native_command", executable=Path(command[0]).name,
                               timeout_seconds=max(1, min(300, int(timeout))), result=status):
                try:
                    completed = run_with_file_capture(command, timeout=max(1, min(300, int(timeout))), encoding=None,
                        **({"cancelled": cancelled} if cancelled is not None else {}))
                except InterruptedError:
                    status.update(cancelled=True, error_kind="ScanCancelled")
                    raise ScanCancelled("Native inspection cancelled")
                except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired) as exc:
                    status.update(returncode=-1, error_kind=type(exc).__name__)
                    raise
                output = _decode_native_output(completed.stdout or b"")
                error = _decode_native_output(completed.stderr or b"")
                limit = max(1_024, min(1_000_000, int(output_limit)))
                status.update(returncode=completed.returncode, truncated=len(output) > limit or len(error) > limit)
                return {**status, "stdout": output[:limit], "stderr": error[:limit]}
        except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired) as exc:
            return {"returncode": -1, "stdout": "", "error_kind": type(exc).__name__}


def _decode_native_output(value: bytes | str) -> str:
    if isinstance(value, str):
        return value
    if value.startswith((b"\xff\xfe", b"\xfe\xff")):
        return value.decode("utf-16", errors="replace")
    if value and value.count(b"\x00") > len(value) // 8:
        return value.decode("utf-16-le", errors="replace")
    try:
        return value.decode("utf-8-sig")
    except UnicodeDecodeError:
        encoding = "cp" + str(ctypes.windll.kernel32.GetOEMCP()) if os.name == "nt" else "utf-8"
        return value.decode(encoding, errors="replace")


def _format_bytes(value: int) -> str:
    amount = float(max(0, int(value or 0)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024.0 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024.0
    return "0 B"
