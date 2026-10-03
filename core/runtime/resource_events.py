"""Transition-only resource evidence for optional owned components.

This module deliberately has no polling loop and no optional dependency. Owners
emit only when their process state changes.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
import time
from typing import Iterable


def _windows_process_metrics(pids: set[int]) -> tuple[int, int, int, int]:
    if os.name != "nt" or not pids:
        return len(pids), 0, 0, 0

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
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

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    class PROCESSENTRY32W(ctypes.Structure):
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

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    invalid_handle = ctypes.c_void_p(-1).value

    # Expand only the exact supplied owners and their current descendants. This
    # is used for Design's WebView tree without inspecting command lines.
    descendants = set(pids)
    process_snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if process_snapshot not in {0, invalid_handle}:
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            parent_rows: list[tuple[int, int]] = []
            ok = kernel32.Process32FirstW(process_snapshot, ctypes.byref(entry))
            while ok:
                parent_rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
                ok = kernel32.Process32NextW(process_snapshot, ctypes.byref(entry))
            changed = True
            while changed:
                changed = False
                for pid, parent_pid in parent_rows:
                    if parent_pid in descendants and pid not in descendants:
                        descendants.add(pid)
                        changed = True
        finally:
            kernel32.CloseHandle(process_snapshot)

    working_set = 0
    private_bytes = 0
    live_pids: set[int] = set()
    for pid in descendants:
        handle = kernel32.OpenProcess(0x1000 | 0x0010, False, int(pid))
        if not handle:
            continue
        live_pids.add(pid)
        try:
            counters = PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(
                handle,
                ctypes.byref(counters),
                counters.cb,
            ):
                working_set += int(counters.WorkingSetSize)
                private_bytes += int(counters.PrivateUsage)
        finally:
            kernel32.CloseHandle(handle)

    thread_count = 0
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
    if snapshot not in {0, invalid_handle}:
        try:
            entry = THREADENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            ok = kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while ok:
                if int(entry.th32OwnerProcessID) in live_pids:
                    thread_count += 1
                ok = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
    return len(live_pids), thread_count, working_set, private_bytes


def emit_component_resource_event(
    component: str,
    transition: str,
    *,
    pids: Iterable[int] = (),
    owned_thread_count: int = 0,
    elapsed_seconds: float = 0.0,
    reason: str = "",
) -> None:
    """Emit one bounded process snapshot; failures never affect the component."""
    try:
        clean_pids = {int(pid) for pid in pids if int(pid) > 0}
        process_count, threads, working_set, private_bytes = _windows_process_metrics(clean_pids)
        if not clean_pids:
            threads = max(0, int(owned_thread_count or 0))
        identity_seed = ",".join(str(pid) for pid in sorted(clean_pids))
        pid_identity = (
            hashlib.sha256(identity_seed.encode("ascii")).hexdigest()[:16]
            if identity_seed else "none"
        )
        from .backend_monitor import get_monitor

        get_monitor().emit("component_resource", {
            "component": str(component or "unknown")[:48],
            "transition": str(transition or "unknown")[:32],
            "pid_identity": pid_identity,
            "process_count": process_count,
            "thread_count": threads,
            "working_set_bytes": working_set,
            "private_bytes": private_bytes,
            "elapsed_ms": max(0, int(float(elapsed_seconds or 0.0) * 1000)),
            "reason": str(reason or "")[:80],
            "observed_at": round(time.time(), 3),
        })
    except Exception:
        return


__all__ = ["emit_component_resource_event"]
