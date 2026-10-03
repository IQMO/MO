"""User-bound encrypted storage for restricted Gmail data on Windows."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import tempfile


class SecureStoreUnavailable(RuntimeError):
    """The host cannot protect Gmail credentials and derived state at rest."""


class _DataBlob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte)))


def available() -> bool:
    return sys.platform == "win32" and hasattr(ctypes, "windll")


def _blob(data: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _windows_crypt(data: bytes, *, encrypt: bool) -> bytes:
    if not available():
        raise SecureStoreUnavailable("Gmail secure storage is unavailable on this host")
    source, keepalive = _blob(data)
    target = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    operation = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    operation.restype = wintypes.BOOL
    operation.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(_DataBlob)]
    ok = operation(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target))
    _ = keepalive
    if not ok:
        raise SecureStoreUnavailable("Gmail secure storage could not protect or unlock data")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def read(path: Path) -> bytes | None:
    try:
        protected = path.read_bytes()
    except FileNotFoundError:
        return None
    if not protected:
        raise SecureStoreUnavailable("Gmail secure storage is empty")
    return _windows_crypt(protected, encrypt=False)


def write(path: Path, data: bytes) -> None:
    protected = _windows_crypt(data, encrypt=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".gmail-", delete=False) as handle:
            staged = Path(handle.name)
            handle.write(protected)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
