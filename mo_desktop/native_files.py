"""Windows file boundaries for Desktop surfaces; no UI or attachment policy."""
from __future__ import annotations

from typing import Any


class FileDropTarget:
    """Own one OLE registration; copy paths before posting to the GUI lane."""

    def __init__(self, hwnd: int, post: Any, *, drag: Any, leave: Any, drop: Any) -> None:
        import ctypes
        import pythoncom
        from types import SimpleNamespace
        from win32com.server.util import wrap
        from win32com.shell import shell

        file_format = (15, None, 1, -1, pythoncom.TYMED_HGLOBAL)  # CF_HDROP

        class Target:
            _com_interfaces_ = [pythoncom.IID_IDropTarget]
            _public_methods_ = ["DragEnter", "DragOver", "DragLeave", "Drop"]
            supported = False

            def DragEnter(target, data: Any, keys: int, point: Any, effect: int) -> int:
                try:
                    data.QueryGetData(file_format)
                    target.supported = True
                except pythoncom.com_error:
                    target.supported = False
                return target.DragOver(keys, point, effect)

            def DragOver(target, keys: int, point: Any, effect: int) -> int:
                if target.supported:
                    event = SimpleNamespace(x_root=point[0], y_root=point[1])
                    self._post(lambda: drag(event))
                    return effect & 1  # Copy only; never move/delete the source.
                return 0

            def DragLeave(target) -> None:
                target.supported = False
                self._post(leave)

            def Drop(target, data: Any, keys: int, point: Any, effect: int) -> int:
                target.supported = False
                try:
                    medium = data.GetData(file_format)
                    handle = medium.data_handle
                    paths = tuple(shell.DragQueryFileW(handle, i)
                                  for i in range(shell.DragQueryFileW(handle, -1)))
                except pythoncom.com_error:
                    self._post(leave)
                    return 0
                event = SimpleNamespace(x_root=point[0], y_root=point[1], paths=paths)
                self._post(lambda: drop(event))
                return effect & 1

        self._hwnd = hwnd
        self._queue = post
        pythoncom.OleInitialize()
        try:
            self._target = wrap(Target(), pythoncom.IID_IDropTarget)
            pythoncom.RegisterDragDrop(hwnd, self._target)
        except Exception:
            ctypes.windll.ole32.OleUninitialize()
            raise

    def _post(self, callback: Any) -> None:
        self._queue(lambda: callback() if self._hwnd else None)

    def close(self) -> None:
        if self._hwnd:
            import ctypes
            import pythoncom
            hwnd, self._hwnd = self._hwnd, 0
            try:
                pythoncom.RevokeDragDrop(hwnd)
            finally:
                self._target = None
                ctypes.windll.ole32.OleUninitialize()


def choose_path(hwnd: int, *, folder: bool) -> str | None:
    """Show the modern Windows picker, owned by the calling Desktop HWND."""
    import ctypes as ct
    from ctypes import wintypes as wt
    from uuid import UUID

    ole = ct.OleDLL("ole32")
    ole.CoInitializeEx.argtypes = [ct.c_void_p, wt.DWORD]
    ole.CoCreateInstance.argtypes = [ct.c_void_p, ct.c_void_p, wt.DWORD,
                                    ct.c_void_p, ct.POINTER(ct.c_void_p)]
    ole.CoTaskMemFree.argtypes = [ct.c_void_p]
    dialog, item = ct.c_void_p(), ct.c_void_p()

    def method(obj: Any, slot: int, *types: Any) -> Any:
        vtable = ct.cast(obj, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
        return ct.WINFUNCTYPE(ct.c_long, ct.c_void_p, *types)(vtable[slot])

    def checked(result: int) -> None:
        if result < 0:
            raise ct.WinError(result)

    # IFileOpenDialog inherits IModalWindow/IFileDialog. Keep this boundary
    # native without loading a WebView/.NET runtime just to choose one path.
    ole.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    try:
        clsid = ct.create_string_buffer(UUID("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7").bytes_le)
        iid = ct.create_string_buffer(UUID("D57C7288-D4AD-4768-BE02-9D969532D960").bytes_le)
        ole.CoCreateInstance(clsid, None, 1, iid, ct.byref(dialog))
        options = wt.DWORD()
        checked(method(dialog, 10, ct.POINTER(wt.DWORD))(dialog, ct.byref(options)))
        # FORCEFILESYSTEM | PATHMUSTEXIST; PICKFOLDERS or FILEMUSTEXIST.
        flags = options.value | 0x40 | 0x800 | (0x20 if folder else 0x1000)
        checked(method(dialog, 9, wt.DWORD)(dialog, flags))
        checked(method(dialog, 17, wt.LPCWSTR)(dialog, "Choose folder" if folder else "Choose file"))
        result = method(dialog, 3, wt.HWND)(dialog, hwnd)  # IModalWindow.Show
        if result & 0xffffffff == 0x800704C7:  # ERROR_CANCELLED
            return None
        checked(result)
        checked(method(dialog, 20, ct.POINTER(ct.c_void_p))(dialog, ct.byref(item)))
        path = ct.c_void_p()
        checked(method(item, 5, wt.DWORD, ct.POINTER(ct.c_void_p))(
            item, 0x80058000, ct.byref(path)))  # SIGDN_FILESYSPATH
        try:
            return ct.wstring_at(path)
        finally:
            ole.CoTaskMemFree(path)
    finally:
        for obj in (item, dialog):
            if obj:
                method(obj, 2)(obj)  # IUnknown.Release
        ole.CoUninitialize()
