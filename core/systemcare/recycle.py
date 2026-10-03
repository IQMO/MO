"""Exact Windows Recycle Bin Shell items, separate from MO Files' private trash."""
from __future__ import annotations

import ctypes
import os
import struct
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from .models import canonical_digest


class _GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_byte * 16)]

    @classmethod
    def of(cls, value: str):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _check(result: int) -> None:
    if result < 0:
        raise OSError("Windows Shell operation failed (HRESULT " + hex(result & 0xFFFFFFFF) + ")")


def _call(pointer: ctypes.c_void_p, index: int, *arguments: Any, types: tuple = ()) -> int:
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])(pointer, *arguments)


@contextmanager
def _shell():
    if os.name != "nt":
        raise RuntimeError("Windows Recycle Bin owner unavailable")
    ole, shell = ctypes.WinDLL("ole32"), ctypes.WinDLL("shell32")
    ole.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    result = ole.CoInitializeEx(None, 2)
    if result not in {0, 1, -2147417850}:
        _check(result)
    try:
        yield ole, shell
    finally:
        if result in {0, 1}:
            ole.CoUninitialize()


def _pidl_bytes(pointer: ctypes.c_void_p) -> bytes:
    offset = 0
    while offset < 65536:
        size = ctypes.c_ushort.from_address(pointer.value + offset).value
        if size == 0:
            return ctypes.string_at(pointer, offset + 2)
        if size < 2 or offset + size > 65534:
            break
        offset += size
    raise ValueError("Invalid Shell item identity")


@contextmanager
def _bin(shell: Any, ole: Any):
    pidl = ctypes.c_void_p()
    _check(shell.SHGetSpecialFolderLocation(None, 10, ctypes.byref(pidl)))
    desktop, folder = ctypes.c_void_p(), ctypes.c_void_p()
    iid = _GUID.of("000214e6-0000-0000-c000-000000000046")
    try:
        _check(shell.SHGetDesktopFolder(ctypes.byref(desktop)))
        _check(_call(desktop, 5, pidl, None, ctypes.byref(iid), ctypes.byref(folder),
                     types=(ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))))
        yield folder, _pidl_bytes(pidl)
    finally:
        for pointer in (folder, desktop):
            if pointer:
                _call(pointer, 2)
        ole.CoTaskMemFree(pidl)


@contextmanager
def _item(shell: Any, identity: bytes):
    memory, item = ctypes.create_string_buffer(identity), ctypes.c_void_p()
    iid = _GUID.of("43826d1e-e718-42ee-bc55-a1e261c37bfe")
    _check(shell.SHCreateItemFromIDList(memory, ctypes.byref(iid), ctypes.byref(item)))
    try:
        yield item
    finally:
        _call(item, 2)


def _display(item: ctypes.c_void_p, ole: Any, kind: int) -> str:
    value = ctypes.c_void_p()
    _check(_call(item, 5, kind, ctypes.byref(value), types=(ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))))
    try:
        return ctypes.wstring_at(value)
    finally:
        ole.CoTaskMemFree(value)


def _identity(subject: dict[str, Any], prefix: bytes) -> bytes:
    value = str(subject.get("shell_id", ""))
    if len(value) > 131072:
        raise ValueError("Invalid selected Recycle Bin item")
    try:
        identity = bytes.fromhex(value)
    except ValueError:
        raise ValueError("Invalid selected Recycle Bin item") from None
    if not identity.startswith(prefix[:-2]) or len(identity) <= len(prefix) or not identity.endswith(b"\0\0"):
        raise ValueError("Selected item is outside the Windows Recycle Bin")
    offset = 0
    while offset + 2 <= len(identity):
        size = struct.unpack_from("<H", identity, offset)[0]
        if size == 0:
            break
        if size < 2 or offset + size > len(identity) - 2:
            raise ValueError("Invalid selected Recycle Bin item")
        offset += size
    if offset + 2 != len(identity):
        raise ValueError("Invalid selected Recycle Bin item")
    return identity


def _snapshot(adapter: Any, path: Path) -> dict[str, Any]:
    try:
        adapter._check_unlinked_owner(Path(path.anchor), path)
    except FileNotFoundError:
        return {"exists": False}
    entries, total, stack = [], 0, [path]
    deadline = time.monotonic() + 5
    while stack:
        current = stack.pop()
        if len(entries) >= 4096 or time.monotonic() >= deadline:
            raise ValueError("Selected item exceeds the exact review budget; use Windows Recycle Bin")
        info = current.lstat()
        if adapter._is_reparse(current, info):
            raise ValueError("Linked Recycle Bin contents are protected")
        entries.append((str(current.relative_to(path)) if current != path else "", info.st_ino,
                        info.st_size, info.st_mtime_ns, info.st_mode))
        if current.is_dir():
            from itertools import islice
            children = list(islice(current.iterdir(), 4097))
            if len(children) > 4096:
                raise ValueError("Selected folder exceeds the exact review budget")
            stack.extend(children)
        else:
            total += info.st_size
    return {"exists": True, "path": str(path), "bytes": total, "entries": len(entries),
            "fingerprint": canonical_digest(sorted(entries))}


def capture_recycle(adapter: Any, subject: dict[str, Any]) -> dict[str, Any]:
    with _shell() as (ole, shell), _bin(shell, ole) as (_folder, prefix):
        identity = _identity(subject, prefix)
        with _item(shell, identity) as item:
            path = Path(_display(item, ole, 0x80058000))
            return _snapshot(adapter, path)


def inspect_recycle(adapter: Any, *, cancelled: Callable[[], bool]) -> dict[str, Any]:
    rows, excluded, bounded = [], 0, False
    deadline = time.monotonic() + 10
    with _shell() as (ole, shell), _bin(shell, ole) as (folder, prefix):
        enum = ctypes.c_void_p()
        _check(_call(folder, 4, None, 0x20 | 0x40 | 0x80, ctypes.byref(enum),
                     types=(ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))))
        try:
            for index in range(301):
                if cancelled():
                    from .windows import ScanCancelled
                    raise ScanCancelled("Recycle Bin inspection cancelled")
                if time.monotonic() >= deadline:
                    bounded = True
                    break
                relative, count = ctypes.c_void_p(), ctypes.c_ulong()
                result = _call(enum, 3, 1, ctypes.byref(relative), ctypes.byref(count),
                               types=(ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_ulong)))
                _check(result)
                if not count.value:
                    break
                try:
                    if index == 300:
                        bounded = True
                        break
                    identity = prefix[:-2] + _pidl_bytes(relative)
                    with _item(shell, identity) as item:
                        name = Path(_display(item, ole, 0)).name
                        path = Path(_display(item, ole, 0x80058000))
                        original = _snapshot(adapter, path)
                    rows.append({"name": name, "state": "Review", "bytes": original.get("bytes", 0),
                                 "eligible": original.get("exists", False),
                                 "subject": {"kind": "recycle", "name": name, "shell_id": identity.hex()},
                                 "detail": "Selected Windows Recycle Bin item; permanent removal after exact-plan review"})
                except (OSError, ValueError):
                    excluded += 1
                finally:
                    ole.CoTaskMemFree(relative)
        finally:
            if enum:
                _call(enum, 2)
    return {"state": "partial" if bounded or excluded else "measured", "rows": rows, "excluded": excluded,
            "bounded": bounded, "at": time.time(), "detail": "Current account's Windows Shell Recycle Bin, up to 300 items; 10-second discovery budget plus the current bounded item read. Redirected or unreviewable contents are excluded; no whole-bin emptying and no MO Files trash mutation."}


def remove_recycle(adapter: Any, subject: dict[str, Any], expected: dict[str, Any], *, cancelled: Callable[[], bool]) -> str:
    with _shell() as (ole, shell), _bin(shell, ole) as (_folder, prefix):
        with _item(shell, _identity(subject, prefix)) as item:
            path = Path(_display(item, ole, 0x80058000))
            if not expected.get("exists") or _snapshot(adapter, path) != expected:
                raise ValueError("Recycle Bin item changed after review")
            if cancelled():
                from .windows import ScanCancelled
                raise ScanCancelled("Recycle Bin cleanup cancelled before mutation")
            operation = ctypes.c_void_p()
            clsid = _GUID.of("3ad05575-8857-4850-9277-11b85bdb8e09")
            iid = _GUID.of("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8")
            _check(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(operation)))
            try:
                # Silent, no extra dialogs, no undo/recycling, stop on an error.
                _check(_call(operation, 5, 0x100000 | 0x400 | 0x10 | 0x4, types=(ctypes.c_uint,)))
                _check(_call(operation, 18, item, None, types=(ctypes.c_void_p, ctypes.c_void_p)))
                _check(_call(operation, 21))
                aborted = ctypes.c_int()
                _check(_call(operation, 22, ctypes.byref(aborted), types=(ctypes.POINTER(ctypes.c_int),)))
                if aborted.value or path.exists():
                    raise RuntimeError("Selected Recycle Bin removal did not pass its post-check")
            finally:
                _call(operation, 2)
    return "Selected Windows Recycle Bin item permanently removed; other items retained."
