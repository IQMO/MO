"""Direct Windows ConPTY adapter for MO split-terminal panes.

The module is imported only on Windows and only when a local pane is opened.
It follows the native pseudoconsole lifecycle without a binary wrapper.
"""
from __future__ import annotations

import ctypes
import subprocess
import threading
from ctypes import wintypes
from typing import Mapping, Sequence

_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_STARTF_USESTDHANDLES = 0x00000100
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_STILL_ACTIVE = 259
_EXPECTED_PIPE_ERRORS = frozenset({6, 109, 232, 995})


class _COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _failed_hresult(value: int) -> bool:
    return bool(int(value) & 0x80000000)


def _environment_block(environment: Mapping[str, str]) -> ctypes.Array:
    # CreateProcessW requires a sorted, double-NUL-terminated UTF-16 block.
    entries = [
        f"{str(key)}={str(value)}"
        for key, value in sorted(environment.items(), key=lambda item: str(item[0]).upper())
        if "\x00" not in str(key) and "\x00" not in str(value)
    ]
    return ctypes.create_unicode_buffer("\x00".join(entries) + "\x00\x00")


class NativeConPtyProcess:
    """One child process tree attached to a native Windows pseudoconsole."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str],
        columns: int,
        rows: int,
    ) -> None:
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_api()
        self._input_write = wintypes.HANDLE()
        self._output_read = wintypes.HANDLE()
        self._hpc = wintypes.HANDLE()
        self._process = wintypes.HANDLE()
        self._closed = False
        self._lock = threading.RLock()
        self.pid = 0
        self._spawn(
            list(command),
            cwd=str(cwd),
            env=env,
            columns=max(1, int(columns)),
            rows=max(1, int(rows)),
        )

    def _configure_api(self) -> None:
        kernel32 = self._kernel32
        kernel32.CreatePipe.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.CreatePipe.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.CreatePseudoConsole.argtypes = [
            _COORD,
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        kernel32.CreatePseudoConsole.restype = ctypes.c_long
        kernel32.ResizePseudoConsole.argtypes = [wintypes.HANDLE, _COORD]
        kernel32.ResizePseudoConsole.restype = ctypes.c_long
        kernel32.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
        kernel32.ClosePseudoConsole.restype = None
        kernel32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        kernel32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        kernel32.DeleteProcThreadAttributeList.restype = None
        kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(_STARTUPINFOW),
            ctypes.POINTER(_PROCESS_INFORMATION),
        ]
        kernel32.CreateProcessW.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL

    @staticmethod
    def _valid(handle: wintypes.HANDLE | None) -> bool:
        return bool(handle and getattr(handle, "value", handle))

    def _close_handle(self, handle: wintypes.HANDLE) -> None:
        if self._valid(handle):
            value = getattr(handle, "value", handle)
            self._kernel32.CloseHandle(wintypes.HANDLE(value))
            if hasattr(handle, "value"):
                handle.value = None

    def _spawn(
        self,
        command: list[str],
        *,
        cwd: str,
        env: Mapping[str, str],
        columns: int,
        rows: int,
    ) -> None:
        if not command:
            raise ValueError("ConPTY command cannot be empty")
        input_read = wintypes.HANDLE()
        output_write = wintypes.HANDLE()
        attribute_list = None
        attribute_storage = None
        process_info = _PROCESS_INFORMATION()
        try:
            if not self._kernel32.CreatePipe(
                ctypes.byref(input_read), ctypes.byref(self._input_write), None, 0
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self._kernel32.CreatePipe(
                ctypes.byref(self._output_read), ctypes.byref(output_write), None, 0
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            result = self._kernel32.CreatePseudoConsole(
                _COORD(columns, rows),
                input_read,
                output_write,
                0,
                ctypes.byref(self._hpc),
            )
            if _failed_hresult(result):
                raise OSError(
                    f"CreatePseudoConsole failed with HRESULT 0x{int(result) & 0xffffffff:08X}"
                )

            size = ctypes.c_size_t()
            self._kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attribute_storage = ctypes.create_string_buffer(size.value)
            attribute_list = ctypes.cast(attribute_storage, ctypes.c_void_p)
            if not self._kernel32.InitializeProcThreadAttributeList(
                attribute_list, 1, 0, ctypes.byref(size)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self._kernel32.UpdateProcThreadAttribute(
                attribute_list,
                0,
                _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                self._hpc,
                ctypes.sizeof(self._hpc),
                None,
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())

            startup = _STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = None
            startup.StartupInfo.hStdOutput = None
            startup.StartupInfo.hStdError = None
            startup.lpAttributeList = attribute_list
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            environment = _environment_block(env)
            created = self._kernel32.CreateProcessW(
                None,
                command_line,
                None,
                None,
                False,
                _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT,
                ctypes.cast(environment, ctypes.c_void_p),
                cwd,
                ctypes.cast(ctypes.byref(startup), ctypes.POINTER(_STARTUPINFOW)),
                ctypes.byref(process_info),
            )
            if not created:
                raise ctypes.WinError(ctypes.get_last_error())
            self._process = wintypes.HANDLE(process_info.hProcess)
            self.pid = int(process_info.dwProcessId)
            self._close_handle(process_info.hThread)
        except Exception:
            if self._valid(self._hpc):
                # Both host pipe handles are closed below before this call.
                self._close_handle(self._output_read)
                self._close_handle(self._input_write)
                self._kernel32.ClosePseudoConsole(self._hpc)
                self._hpc.value = None
            self._close_handle(self._process)
            raise
        finally:
            if attribute_list is not None:
                self._kernel32.DeleteProcThreadAttributeList(attribute_list)
            self._close_handle(input_read)
            self._close_handle(output_write)

    def isalive(self) -> bool:
        with self._lock:
            if self._closed or not self._valid(self._process):
                return False
            return self._kernel32.WaitForSingleObject(self._process, 0) == _WAIT_TIMEOUT

    @property
    def exitstatus(self) -> int | None:
        with self._lock:
            if not self._valid(self._process):
                return None
            code = wintypes.DWORD()
            if not self._kernel32.GetExitCodeProcess(self._process, ctypes.byref(code)):
                return None
            return None if int(code.value) == _STILL_ACTIVE else int(code.value)

    def read(self, size: int = 4_096) -> bytes:
        size = max(1, min(65_536, int(size)))
        handle = self._output_read
        if not self._valid(handle):
            raise EOFError
        buffer = ctypes.create_string_buffer(size)
        count = wintypes.DWORD()
        if not self._kernel32.ReadFile(
            handle, buffer, size, ctypes.byref(count), None
        ):
            error = ctypes.get_last_error()
            if error in _EXPECTED_PIPE_ERRORS:
                raise EOFError
            raise ctypes.WinError(error)
        if not count.value:
            raise EOFError
        return bytes(buffer.raw[: int(count.value)])

    def write(self, data: bytes) -> int:
        payload = bytes(data)
        if not payload:
            return 0
        with self._lock:
            if self._closed or not self._valid(self._input_write):
                raise EOFError
            total = 0
            while total < len(payload):
                remaining = payload[total:]
                buffer = ctypes.create_string_buffer(remaining)
                count = wintypes.DWORD()
                if not self._kernel32.WriteFile(
                    self._input_write,
                    buffer,
                    len(remaining),
                    ctypes.byref(count),
                    None,
                ):
                    error = ctypes.get_last_error()
                    if error in _EXPECTED_PIPE_ERRORS:
                        raise EOFError
                    raise ctypes.WinError(error)
                if not count.value:
                    raise EOFError
                total += int(count.value)
            return total

    def resize(self, *, columns: int, rows: int) -> None:
        with self._lock:
            if self._closed or not self._valid(self._hpc):
                return
            result = self._kernel32.ResizePseudoConsole(
                self._hpc, _COORD(max(1, int(columns)), max(1, int(rows)))
            )
            if _failed_hresult(result):
                raise OSError(
                    f"ResizePseudoConsole failed with HRESULT 0x{int(result) & 0xffffffff:08X}"
                )

    def terminate(self, exit_code: int = 1) -> None:
        with self._lock:
            if self.isalive():
                self._kernel32.TerminateProcess(self._process, max(1, int(exit_code)))

    def close(self) -> None:
        """Drain output while closing HPCON, then release the host handles."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            input_write = self._input_write
            process = self._process
            hpc = self._hpc
            output_read = self._output_read
            self._input_write = wintypes.HANDLE()
        self._close_handle(input_write)
        if self._valid(process):
            if self._kernel32.WaitForSingleObject(process, 150) == _WAIT_TIMEOUT:
                self._kernel32.TerminateProcess(process, 1)
                self._kernel32.WaitForSingleObject(process, 350)
        # ClosePseudoConsole can emit a final frame and wait for clients.  The
        # LocalTerminalProcess reader remains active here and drains that frame.
        if self._valid(hpc):
            self._kernel32.ClosePseudoConsole(hpc)
            hpc.value = None
        self._close_handle(output_read)
        self._close_handle(process)
        with self._lock:
            self._hpc = wintypes.HANDLE()
            self._output_read = wintypes.HANDLE()
            self._process = wintypes.HANDLE()


__all__ = ["NativeConPtyProcess"]
