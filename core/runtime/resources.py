"""Bounded native resource sampling shared by local and authenticated host views."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import os
from pathlib import Path
import sys
import time
from typing import Callable, Mapping

_SAMPLE_INTERVAL_SECONDS = 1.0
_MAX_PROCESSES = 512
_MAX_PROCESS_TABLE = 32_768


@dataclass(frozen=True)
class ProcessReading:
    parent_pid: int
    cpu_seconds: float | None
    memory_bytes: int | None
    started_at: float | None = None


@dataclass(frozen=True)
class HostReading:
    system_total: float | None
    system_idle: float | None
    memory_total: int | None
    memory_available: int | None
    parents: Mapping[int, int] = field(default_factory=dict)
    processes: Mapping[int, ProcessReading] = field(default_factory=dict)
    source: str = "unavailable"
    process_table_complete: bool = True


@dataclass(frozen=True)
class ProcessResourceSample:
    cpu_percent: float | None
    memory_bytes: int | None
    process_count: int | None
    state: str = "ready"


@dataclass(frozen=True)
class ResourceSnapshot:
    sampled_at: float
    system_cpu_percent: float | None
    memory_percent: float | None
    memory_used_bytes: int | None
    memory_total_bytes: int | None
    trees: Mapping[str, ProcessResourceSample]
    source: str
    state: str = "ready"


class _FileTime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("memory_load", wintypes.DWORD),
        ("total_physical", ctypes.c_ulonglong),
        ("available_physical", ctypes.c_ulonglong),
        ("total_page_file", ctypes.c_ulonglong),
        ("available_page_file", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("available_virtual", ctypes.c_ulonglong),
        ("available_extended_virtual", ctypes.c_ulonglong),
    ]


def _filetime_value(value: _FileTime) -> int:
    return (int(value.high) << 32) | int(value.low)


def _windows_process_table(kernel32) -> tuple[dict[int, int], bool]:
    """Take one bounded Toolhelp parent snapshot."""
    parents: dict[int, int] = {}
    invalid_handle = ctypes.c_void_p(-1).value
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    snapshot_value = getattr(snapshot, "value", snapshot)
    complete = False
    if snapshot_value not in {0, invalid_handle, None}:
        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok and len(parents) < _MAX_PROCESS_TABLE:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            complete = bool(parents) and not ok and ctypes.get_last_error() == 18
        finally:
            kernel32.CloseHandle(snapshot)
    return parents, complete


def _stable_parent_edges(
    before: Mapping[int, int],
    after: Mapping[int, int],
    candidates: set[int],
) -> dict[int, int]:
    """Keep only candidate parent edges that match across two snapshots."""
    return {
        pid: int(before[pid])
        for pid in candidates
        if pid in before and after.get(pid) == before[pid]
    }


def _descendants(
    roots: set[int],
    parents: Mapping[int, int],
    started_at: Mapping[int, float] | None = None,
) -> set[int]:
    """Return bounded descendants, rejecting stale parent-PID relationships.

    Operating systems retain numeric parent IDs after a parent exits. If that ID
    is reused, an older unrelated process can otherwise appear below the new
    root. When creation times are available, a child must not predate its parent.
    """
    children: dict[int, list[int]] = {}
    for pid, parent_pid in parents.items():
        children.setdefault(int(parent_pid), []).append(int(pid))
    selected: set[int] = set()
    pending = [pid for pid in roots if pid > 0]
    while pending and len(selected) < _MAX_PROCESSES:
        pid = pending.pop()
        if pid in selected:
            continue
        selected.add(pid)
        child_ids = children.get(pid, ())
        if started_at is not None:
            parent_started = started_at.get(pid)
            child_ids = tuple(
                child_pid
                for child_pid in child_ids
                if parent_started is not None
                and started_at.get(child_pid) is not None
                and float(started_at[child_pid]) >= float(parent_started)
            )
        pending.extend(child_ids)
    return selected


def _windows_probe(root_pids: set[int]) -> HostReading:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetSystemTimes.argtypes = [
        ctypes.POINTER(_FileTime), ctypes.POINTER(_FileTime), ctypes.POINTER(_FileTime)
    ]
    kernel32.GetSystemTimes.restype = wintypes.BOOL
    kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatus)]
    kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    parents, table_complete = _windows_process_table(kernel32)

    candidates = _descendants(root_pids, parents)
    processes: dict[int, ProcessReading] = {}
    for pid in candidates:
        handle = kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
        if not handle:
            continue
        try:
            created, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
            cpu_seconds = None
            started_at = None
            if kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                started_at = float(_filetime_value(created))
                cpu_seconds = (_filetime_value(kernel) + _filetime_value(user)) / 10_000_000.0
            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            memory_bytes = None
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                memory_bytes = int(counters.WorkingSetSize)
            processes[pid] = ProcessReading(
                parents.get(pid, 0), cpu_seconds, memory_bytes, started_at
            )
        finally:
            kernel32.CloseHandle(handle)

    current_parents, current_table_complete = _windows_process_table(kernel32)
    current_candidates = _descendants(root_pids, current_parents)
    stable_parents = _stable_parent_edges(parents, current_parents, candidates)
    process_starts = {
        pid: float(row.started_at)
        for pid, row in processes.items()
        if row.started_at is not None
    }
    relevant = _descendants(root_pids, stable_parents, process_starts)
    if (
        not current_table_complete
        or current_candidates != candidates
        or len(stable_parents) != len(candidates)
        or len(candidates) >= _MAX_PROCESSES
        or len(processes) != len(candidates)
        or len(process_starts) != len(processes)
    ):
        table_complete = False
    processes = {pid: row for pid, row in processes.items() if pid in relevant}

    idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
    system_total = system_idle = None
    if kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        system_idle = float(_filetime_value(idle))
        system_total = float(_filetime_value(kernel) + _filetime_value(user))
    memory = _MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    memory_total = memory_available = None
    if kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        memory_total = int(memory.total_physical)
        memory_available = int(memory.available_physical)
    return HostReading(
        system_total,
        system_idle,
        memory_total,
        memory_available,
        stable_parents,
        processes,
        "windows",
        table_complete,
    )


def _linux_probe(root_pids: set[int]) -> HostReading:
    raw: dict[int, tuple[int, float, int, float]] = {}
    table_complete = True
    try:
        process_dirs = (path for path in Path("/proc").iterdir() if path.name.isdigit())
        for index, path in enumerate(process_dirs):
            if index >= _MAX_PROCESS_TABLE:
                table_complete = False
                break
            try:
                text = (path / "stat").read_text(encoding="utf-8", errors="replace")
                fields = text[text.rfind(")") + 2:].split()
                pid = int(path.name)
                parent_pid = int(fields[1])
                cpu_seconds = (int(fields[11]) + int(fields[12])) / float(os.sysconf("SC_CLK_TCK"))
                started_at = float(fields[19])
                memory_bytes = int(fields[21]) * int(os.sysconf("SC_PAGE_SIZE"))
                raw[pid] = (parent_pid, cpu_seconds, memory_bytes, started_at)
            except FileNotFoundError:
                # A process can exit between enumeration and reading its stat.
                continue
            except (OSError, ValueError, IndexError):
                table_complete = False
                continue
    except OSError:
        table_complete = False
    parents = {pid: values[0] for pid, values in raw.items()}
    starts = {pid: values[3] for pid, values in raw.items()}
    relevant = _descendants(root_pids, parents, starts)
    processes = {
        pid: ProcessReading(parent, cpu, memory, started_at)
        for pid, (parent, cpu, memory, started_at) in raw.items()
        if pid in relevant
    }

    system_total = system_idle = None
    try:
        values = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()[1:]
        ticks = [float(value) for value in values]
        # Guest time is already included in user/nice on Linux.
        system_total = sum(ticks[:8])
        system_idle = sum(ticks[3:5])
    except (OSError, ValueError, IndexError):
        pass
    memory_total = memory_available = None
    try:
        memory_values = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, _separator, value = line.partition(":")
            memory_values[key] = int(value.strip().split()[0]) * 1024
        memory_total = memory_values.get("MemTotal")
        memory_available = memory_values.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        pass
    return HostReading(
        system_total,
        system_idle,
        memory_total,
        memory_available,
        parents,
        processes,
        "linux",
        table_complete,
    )


def _default_probe(root_pids: set[int]) -> HostReading:
    if os.name == "nt":
        return _windows_probe(root_pids)
    if sys.platform.startswith("linux"):
        return _linux_probe(root_pids)
    return HostReading(None, None, None, None, source="unsupported")


class ResourceSampler:
    """Cache one live host/process-tree sample per bounded interval."""

    def __init__(
        self,
        *,
        probe: Callable[[set[int]], HostReading] = _default_probe,
        clock: Callable[[], float] = time.monotonic,
        interval_seconds: float = _SAMPLE_INTERVAL_SECONDS,
        exclusive_roots: bool = False,
    ) -> None:
        self._probe = probe
        self._clock = clock
        self._interval = max(0.1, float(interval_seconds))
        self._exclusive_roots = exclusive_roots
        self._last_sample_at: float | None = None
        self._previous_system: tuple[float, float] | None = None
        self._previous_process_cpu: dict[int, float] = {}
        self._cached: ResourceSnapshot | None = None
        self._cached_roots: dict[str, int | None] | None = None

    def sample(self, roots: Mapping[str, int | None]) -> ResourceSnapshot:
        now = self._clock()
        clean_roots = {
            str(pane_id): int(pid) if pid is not None and int(pid) > 0 else None
            for pane_id, pid in roots.items()
        }
        if (
            self._cached is not None
            and clean_roots == self._cached_roots
            and self._last_sample_at is not None
            and now - self._last_sample_at < self._interval
        ):
            return self._cached
        try:
            reading = self._probe({pid for pid in clean_roots.values() if pid is not None})
        except Exception:
            reading = HostReading(None, None, None, None)

        process_starts = {
            pid: float(row.started_at)
            for pid, row in reading.processes.items()
            if row.started_at is not None
        }
        validated_starts = (
            process_starts
            if process_starts and len(process_starts) == len(reading.processes)
            else None
        )
        pane_members = {
            pane_id: _descendants({pid}, reading.parents, validated_starts)
            if pid is not None else set()
            for pane_id, pid in clean_roots.items()
        }
        if self._exclusive_roots:
            # Attribute a nested named root to itself, not also its ancestor.
            # Equal roots belong to the first name. Existing pane semantics
            # remain unchanged unless a caller explicitly needs a joint total.
            original_members = {name: set(members) for name, members in pane_members.items()}
            seen_roots: set[int] = set()
            for name, pid in clean_roots.items():
                if pid in seen_roots:
                    pane_members[name] = set()
                    continue
                if pid is not None:
                    seen_roots.add(pid)
                for other, other_pid in clean_roots.items():
                    if other_pid != pid and other_pid in original_members[name]:
                        pane_members[name] -= original_members[other]
        elif "main" in pane_members:
            attributed = set().union(*(
                members for pane_id, members in pane_members.items() if pane_id != "main"
            )) if len(pane_members) > 1 else set()
            pane_members["main"] -= attributed

        elapsed = None if self._last_sample_at is None else max(0.001, now - self._last_sample_at)
        cpu_count = max(1, int(os.cpu_count() or 1))
        trees: dict[str, ProcessResourceSample] = {}
        for pane_id, members in pane_members.items():
            if (self._exclusive_roots and not members
                    and clean_roots[pane_id] in reading.processes):
                trees[pane_id] = ProcessResourceSample(0.0, 0, 0)
                continue
            member_ids = tuple(sorted(members))
            rows = [reading.processes.get(pid) for pid in member_ids]
            complete_tree = reading.process_table_complete and len(members) < _MAX_PROCESSES
            complete_cpu = complete_tree and bool(rows) and all(row is not None and row.cpu_seconds is not None for row in rows)
            cpu_percent = None
            if elapsed is not None and complete_cpu and all(
                pid in self._previous_process_cpu for pid in member_ids
            ):
                delta = sum(
                    max(0.0, float(row.cpu_seconds) - self._previous_process_cpu.get(pid, float(row.cpu_seconds)))
                    for pid, row in zip(member_ids, rows)
                    if row is not None and row.cpu_seconds is not None
                )
                cpu_percent = min(100.0, delta * 100.0 / elapsed / cpu_count)
            complete_memory = complete_tree and bool(rows) and all(
                row is not None and row.memory_bytes is not None for row in rows
            )
            memory_bytes = (
                sum(int(row.memory_bytes) for row in rows if row is not None and row.memory_bytes is not None)
                if complete_memory else None
            )
            process_count = len(rows) if complete_tree and rows and all(row is not None for row in rows) else None
            if clean_roots[pane_id] is None:
                state = "unmeasured"
            elif reading.source in {"unsupported", "unavailable"}:
                state = reading.source
            elif cpu_percent is not None and memory_bytes is not None:
                state = "ready"
            elif complete_cpu and complete_memory:
                state = "warming"
            elif cpu_percent is not None or memory_bytes is not None or process_count is not None:
                state = "partial"
            else:
                state = "unavailable"
            trees[pane_id] = ProcessResourceSample(cpu_percent, memory_bytes, process_count, state)

        system_cpu = None
        if (
            self._previous_system is not None
            and reading.system_total is not None
            and reading.system_idle is not None
        ):
            total_delta = float(reading.system_total) - self._previous_system[0]
            idle_delta = float(reading.system_idle) - self._previous_system[1]
            if total_delta > 0:
                system_cpu = min(100.0, max(0.0, (total_delta - idle_delta) * 100.0 / total_delta))
        memory_total = reading.memory_total if reading.memory_total and reading.memory_total > 0 else None
        memory_used = None
        memory_percent = None
        if memory_total is not None and reading.memory_available is not None:
            memory_used = max(0, memory_total - max(0, int(reading.memory_available)))
            memory_percent = min(100.0, memory_used * 100.0 / memory_total)

        if reading.source in {"unsupported", "unavailable"}:
            state = reading.source
        elif system_cpu is not None and memory_percent is not None:
            state = "ready"
        elif reading.system_total is not None and self._previous_system is None:
            state = "warming"
        elif system_cpu is not None or memory_percent is not None:
            state = "partial"
        else:
            state = "unavailable"
        if reading.system_total is not None and reading.system_idle is not None:
            self._previous_system = (float(reading.system_total), float(reading.system_idle))
        else:
            self._previous_system = None
        self._previous_process_cpu = {
            pid: float(row.cpu_seconds)
            for pid, row in reading.processes.items()
            if row.cpu_seconds is not None
        }
        self._last_sample_at = now
        self._cached_roots = clean_roots
        self._cached = ResourceSnapshot(
            sampled_at=now,
            system_cpu_percent=system_cpu,
            memory_percent=memory_percent,
            memory_used_bytes=memory_used,
            memory_total_bytes=memory_total,
            trees=trees,
            source=reading.source,
            state=state,
        )
        return self._cached


__all__ = [
    "HostReading",
    "ProcessResourceSample",
    "ProcessReading",
    "ResourceSampler",
    "ResourceSnapshot",
]
