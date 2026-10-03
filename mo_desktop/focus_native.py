"""Windows composition adapter for Focus; window enumeration/activation stay in Trackpad.

DWM streams the original windows directly. Explorer retains its icons and
auto-hide preferences; Focus owns only a reversible taskbar visibility scope.
"""
from __future__ import annotations

from functools import lru_cache
from dataclasses import dataclass
import sys
from typing import Any


@dataclass(frozen=True)
class TrayItem:
    name: str
    toolbar: int
    child: int
    owner: int
    identifier: int
    image: Any


def tray_items() -> list[TrayItem] | None:
    """Read classic Explorer's two notification areas on demand.

    Explorer owns the live icons and callbacks. XAML shells have no toolbar
    adapter and use their native flyout instead. No shell window is moved.
    """
    import ctypes as c
    from ctypes import wintypes as w
    import struct
    import win32gui
    import win32process
    import uiautomation as auto
    from mo_desktop.layered import icon_image
    roots = [win32gui.FindWindow(name, None) for name in ("Shell_TrayWnd", "NotifyIconOverflowWindow")]
    toolbars = []
    for root in filter(None, roots):
        def collect(hwnd: int, _extra: Any) -> None:
            if win32gui.GetClassName(hwnd) == "ToolbarWindow32":
                # Only notification toolbar descendants, never task buttons.
                parent = win32gui.GetParent(hwnd)
                if root == roots[1] or win32gui.GetClassName(parent) == "TrayNotifyWnd":
                    toolbars.append(hwnd)
        win32gui.EnumChildWindows(root, collect, None)
    if not toolbars:
        return None
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.VirtualAllocEx.argtypes = [w.HANDLE, c.c_void_p, c.c_size_t, w.DWORD, w.DWORD]
    kernel.VirtualAllocEx.restype = c.c_void_p
    kernel.ReadProcessMemory.argtypes = [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]
    kernel.VirtualFreeEx.argtypes = [w.HANDLE, c.c_void_p, c.c_size_t, w.DWORD]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.IsWow64Process.argtypes = [w.HANDLE, c.POINTER(w.BOOL)]
    result, seen = [], set()
    for toolbar in toolbars:
        process = kernel.OpenProcess(0x38, False, win32process.GetWindowThreadProcessId(toolbar)[1])
        if not process:
            continue
        remote = None
        try:
            wow = w.BOOL()
            if not kernel.IsWow64Process(process, c.byref(wow)):
                continue
            if c.sizeof(c.c_void_p) == 4:
                current_wow = w.BOOL()
                kernel.GetCurrentProcess.restype = w.HANDLE
                if not kernel.IsWow64Process(kernel.GetCurrentProcess(), c.byref(current_wow)):
                    continue
                if current_wow.value and not wow.value:
                    return None  # A 32-bit caller cannot read 64-bit Explorer addresses.
            wide = c.sizeof(c.c_void_p) == 8 and not wow.value
            remote = kernel.VirtualAllocEx(process, None, 32, 0x3000, 4)
            if not remote:
                continue
            def read(address: int, size: int) -> bytes:
                buffer, count = c.create_string_buffer(size), c.c_size_t()
                if not kernel.ReadProcessMemory(process, address, buffer, size, c.byref(count)) or count.value != size:
                    raise OSError("Explorer notification record unavailable")
                return buffer.raw
            for control in auto.ControlFromHandle(toolbar).GetChildren()[:80]:
                if control.ControlTypeName != "ButtonControl":
                    continue
                child = int(control.GetLegacyIAccessiblePattern().ChildId)
                if child < 1 or child > 80:
                    continue
                _, ok = win32gui.SendMessageTimeout(toolbar, 0x0417, child-1, remote, 2, 200)
                if not ok:
                    continue
                data = read(remote, 32 if wide else 20)
                address = struct.unpack_from("<Q" if wide else "<I", data, 16 if wide else 12)[0]
                data = read(address, 32 if wide else 24)
                owner = struct.unpack_from("<Q" if wide else "<I", data)[0]
                identifier = struct.unpack_from("<I", data, 8 if wide else 4)[0]
                hicon = struct.unpack_from("<Q" if wide else "<I", data, 24 if wide else 20)[0]
                identity = (owner, identifier)
                if not win32gui.IsWindow(owner) or not hicon or identity in seen:
                    continue
                image = icon_image(hicon)
                if image is not None:
                    seen.add(identity)
                    result.append(TrayItem(control.Name, toolbar, child, owner, identifier, image))
        finally:
            if remote:
                kernel.VirtualFreeEx(process, remote, 0, 0x8000)
            kernel.CloseHandle(process)
    return result


def invoke_tray_item(item: TrayItem, *, context: bool = False) -> bool:
    """Revalidate the observed button and dispatch through Explorer's own handler."""
    import win32gui
    import uiautomation as auto
    if not win32gui.IsWindow(item.toolbar) or not win32gui.IsWindow(item.owner):
        return False
    for control in auto.ControlFromHandle(item.toolbar).GetChildren()[:80]:
        if control.Name != item.name or control.ControlTypeName != "ButtonControl":
            continue
        if int(control.GetLegacyIAccessiblePattern().ChildId) != item.child:
            continue
        bounds = control.BoundingRectangle
        x, y = win32gui.ScreenToClient(item.toolbar, (round((bounds.left+bounds.right)/2), round((bounds.top+bounds.bottom)/2)))
        point = (x & 0xffff) | ((y & 0xffff) << 16)
        down, up = (0x0204, 0x0205) if context else (0x0201, 0x0202)
        win32gui.PostMessage(item.toolbar, down, 2 if context else 1, point)
        win32gui.PostMessage(item.toolbar, up, 0, point)
        return True
    return False


@lru_cache(maxsize=1)
def _api() -> Any:
    import ctypes as c
    from ctypes import wintypes as w
    from types import SimpleNamespace

    class Properties(c.Structure):
        _fields_ = [("flags", w.DWORD), ("destination", w.RECT), ("source", w.RECT),
                    ("opacity", w.BYTE), ("visible", w.BOOL), ("client_only", w.BOOL)]

    class Size(c.Structure):
        _fields_ = [("width", w.LONG), ("height", w.LONG)]

    dwm = c.WinDLL("dwmapi")
    user = c.WinDLL("user32")
    dwm.DwmRegisterThumbnail.argtypes = [w.HWND, w.HWND, c.POINTER(w.HANDLE)]
    dwm.DwmRegisterThumbnail.restype = c.c_long
    dwm.DwmUnregisterThumbnail.argtypes = [w.HANDLE]
    dwm.DwmUnregisterThumbnail.restype = c.c_long
    dwm.DwmUpdateThumbnailProperties.argtypes = [w.HANDLE, c.POINTER(Properties)]
    dwm.DwmUpdateThumbnailProperties.restype = c.c_long
    dwm.DwmQueryThumbnailSourceSize.argtypes = [w.HANDLE, c.POINTER(Size)]
    dwm.DwmQueryThumbnailSourceSize.restype = c.c_long
    user.GetAncestor.argtypes = [w.HWND, w.UINT]
    user.GetAncestor.restype = w.HWND
    user.IsIconic.argtypes = [w.HWND]
    user.IsIconic.restype = w.BOOL
    user.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
    user.FindWindowW.restype = w.HWND
    user.FindWindowExW.argtypes = [w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR]
    user.FindWindowExW.restype = w.HWND
    user.GetDlgItem.argtypes = [w.HWND, c.c_int]
    user.GetDlgItem.restype = w.HWND
    user.GetClientRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
    user.GetClientRect.restype = w.BOOL
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.PostMessageW.restype = w.BOOL
    user.IsWindowVisible.argtypes = [w.HWND]
    user.IsWindowVisible.restype = w.BOOL
    user.IsWindow.argtypes = [w.HWND]
    user.IsWindow.restype = w.BOOL
    user.ShowWindow.argtypes = [w.HWND, c.c_int]
    user.ShowWindow.restype = w.BOOL
    user.GetDoubleClickTime.argtypes = []
    user.GetDoubleClickTime.restype = w.UINT
    user.SystemParametersInfoW.argtypes = [w.UINT, w.UINT, c.c_void_p, w.UINT]
    user.SystemParametersInfoW.restype = w.BOOL
    user.GetWindowRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
    user.GetWindowRect.restype = w.BOOL
    user.SetWindowPos.argtypes = [w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT]
    user.SetWindowPos.restype = w.BOOL
    return SimpleNamespace(c=c, w=w, dwm=dwm, user=user, Properties=Properties, Size=Size)


def native_handle(window: Any) -> int:
    if isinstance(window, int):
        return window
    api = _api()
    handle = int(window.hwnd if hasattr(window, "hwnd") else window.winfo_id())
    return int(api.user.GetAncestor(handle, 2) or handle)


def fit_preview(bounds: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Letterbox the actual source aspect ratio; never stretch or crop a window."""
    x, y, width, height = bounds
    sw, sh = size
    if min(width, height, sw, sh) <= 0:
        return x, y, x, y
    scale = min(width / sw, height / sh)
    dw, dh = max(1, round(sw * scale)), max(1, round(sh * scale))
    left, top = x + (width - dw) // 2, y + (height - dh) // 2
    return left, top, left + dw, top + dh


class WindowPreview:
    """One explicitly owned DWM thumbnail, released when its card disappears."""

    def __init__(self, destination: int, source: int) -> None:
        import win32gui
        self.source = int(source)
        self._destination = int(destination)
        self.handle: Any = None
        self._last: Any = None
        # DWM thumbnails compose into a redirected HWND, not an UpdateLayeredWindow
        # bitmap. This small click-through host belongs to the thumbnail lifecycle.
        self._host = win32gui.CreateWindowEx(0x080800A0, "Static", "", 0x80000000,
            0, 0, 1, 1, destination, 0, 0, None)
        win32gui.SetLayeredWindowAttributes(self._host, 0, 255, 2)
        if sys.platform == "win32":
            api = _api()
            handle = api.w.HANDLE()
            if api.dwm.DwmRegisterThumbnail(self._host, source, api.c.byref(handle)) == 0:
                self.handle = handle

    def source_size(self) -> tuple[int, int] | None:
        if self.handle is None:
            return None
        api = _api()
        size = api.Size()
        if api.dwm.DwmQueryThumbnailSourceSize(self.handle, api.c.byref(size)) != 0:
            return None
        return size.width, size.height

    def update(self, bounds: tuple[int, int, int, int], opacity: float) -> bool:
        size = self.source_size()
        if size is None:
            return False
        api = _api()
        rect = fit_preview(bounds, size)
        alpha = max(0, min(255, round(opacity * 255)))
        import win32gui
        left, top = win32gui.ClientToScreen(self._destination, (rect[0], rect[1]))
        width, height = rect[2]-rect[0], rect[3]-rect[1]
        state = (left, top, width, height, alpha)
        if state == self._last:
            return True
        if not width or not height or not alpha:
            win32gui.ShowWindow(self._host, 0)
            self._last = state
            return True
        win32gui.SetWindowPos(self._host, -1, left, top, width, height, 0x0010 | 0x0040)
        win32gui.SetLayeredWindowAttributes(self._host, 0, alpha, 2)
        props = api.Properties()
        props.flags = 1 | 4 | 8 | 16  # destination, opacity, visible, source-client-only
        props.destination = api.w.RECT(0, 0, width, height)
        props.opacity = 255
        props.visible = rect[2] > rect[0] and rect[3] > rect[1]
        props.client_only = False
        success = api.dwm.DwmUpdateThumbnailProperties(self.handle, api.c.byref(props)) == 0
        if success:
            self._last = state
        return success

    def close(self) -> None:
        if self.handle is not None:
            _api().dwm.DwmUnregisterThumbnail(self.handle)
            self.handle = None
        if self._host:
            import win32gui
            win32gui.DestroyWindow(self._host)
            self._host = 0


def is_minimized(handle: int) -> bool:
    return bool(_api().user.IsIconic(handle)) if sys.platform == "win32" else False


class TaskbarVisibility:
    """Hide only visible shell taskbars and restore exactly those windows once.

    No registry or auto-hide setting is changed. Already-hidden bars stay hidden;
    showing an auto-hidden window at its existing position preserves shell policy.
    """

    def __init__(self) -> None:
        import atexit
        self._hidden: list[tuple[int, tuple[int, int, int, int]]] = []
        self._work_areas: list[tuple[Any, tuple[int, int, int, int], tuple[int, int, int, int]]] = []
        api = _api()
        atexit.register(self.restore)
        try:
            for name in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                previous = None
                while True:
                    handle = api.user.FindWindowExW(None, previous, name, None)
                    if not handle:
                        break
                    previous = handle
                    if api.user.IsWindowVisible(handle):
                        rect = api.w.RECT()
                        if api.user.GetWindowRect(handle, api.c.byref(rect)):
                            self._hidden.append((int(handle), (rect.left, rect.top, rect.right, rect.bottom)))
                            api.user.ShowWindow(handle, 0)
            self._expand_work_areas()
            self.maintain()
        except Exception:
            self.restore()
            raise

    def _expand_work_areas(self) -> None:
        import win32api
        for monitor, _dc, _rect in win32api.EnumDisplayMonitors():
            info = win32api.GetMonitorInfo(monitor)
            before = tuple(info["Work"])
            after = self._expanded_area(before, info["Monitor"])
            if before != after:
                self._set_work_area(after)
                self._work_areas.append((monitor, before, after))

    @staticmethod
    def _set_work_area(rect: tuple[int, int, int, int]) -> None:
        api = _api()
        area = api.w.RECT(*rect)
        # SPI_SETWORKAREA + SPIF_SENDCHANGE: notify desktop/app windows without
        # persisting a preference or restarting Explorer.
        if not api.user.SystemParametersInfoW(0x002F, 0, api.c.byref(area), 0x0002):
            raise OSError("Windows rejected Focus's temporary work area")

    def work_area(self, window: Any) -> tuple[int, int, int, int]:
        import win32api
        info = win32api.GetMonitorInfo(win32api.MonitorFromWindow(native_handle(window), 2))
        return tuple(info["Work"])

    def maintain(self) -> None:
        # Explorer can reveal an appbar in response to the work-area broadcast.
        api = _api()
        for handle, _rect in self._hidden:
            if api.user.IsWindow(handle) and api.user.IsWindowVisible(handle):
                api.user.ShowWindow(handle, 0)
                # Reassert only our recorded reservation; never broadcast a loop.
                import win32api
                for monitor, before, after in self._work_areas:
                    if tuple(win32api.GetMonitorInfo(monitor)["Work"]) == before:
                        area = api.w.RECT(*after)
                        api.user.SystemParametersInfoW(0x002F, 0, api.c.byref(area), 0)

    def _expanded_area(self, work: Any, monitor: Any) -> tuple[int, int, int, int]:
        left, top, right, bottom = work
        ml, mt, mr, mb = monitor
        for _handle, (x, y, r, b) in self._hidden:
            if x <= ml and r >= mr:
                if b == top and y <= mt:
                    top = mt
                elif y == bottom and b >= mb:
                    bottom = mb
            if y <= mt and b >= mb:
                if r == left and x <= ml:
                    left = ml
                elif x == right and r >= mr:
                    right = mr
        return left, top, right, bottom

    def restore(self) -> None:
        import atexit
        import win32api
        api = _api()
        areas, self._work_areas = self._work_areas, []
        for monitor, before, after in areas:
            try:
                current = tuple(win32api.GetMonitorInfo(monitor)["Work"])
                if current == after:
                    self._set_work_area(before)
            except win32api.error:
                continue  # A disconnected display no longer owns a work area.
            except OSError:
                from mo_desktop.desktop_log import log_exception
                log_exception("Focus work-area restoration failed")
        hidden, self._hidden = self._hidden, []
        for handle, _rect in hidden:
            if api.user.IsWindow(handle):
                api.user.ShowWindow(handle, 8)  # SW_SHOWNA: retain position and foreground.
        atexit.unregister(self.restore)


def window_icon(handle: int) -> Any:
    """Read the window's own icon once per card, with a bounded hung-window wait."""
    if sys.platform != "win32":
        return None
    import win32gui
    from mo_desktop.layered import icon_image

    try:
        # WM_GETICON / ICON_BIG, then the class icons used by Windows itself.
        try:
            _, icon = win32gui.SendMessageTimeout(handle, 0x007F, 1, 0, 2, 40)
        except win32gui.error:
            icon = 0
        icon = icon or win32gui.GetClassLong(handle, -14) or win32gui.GetClassLong(handle, -34)
        return icon_image(icon) if icon else None
    except win32gui.error:
        return None  # The enumerated window may close before its card is painted.


def open_system_tray() -> bool:
    """Open Explorer's native hidden-icons flyout without moving the taskbar."""
    if sys.platform != "win32":
        return False
    import uiautomation as auto
    api = _api()
    for name in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        handle = api.user.FindWindowW(name, None)
        if not handle:
            continue
        notify = api.user.FindWindowExW(handle, None, "TrayNotifyWnd", None)
        button = api.user.GetDlgItem(notify, 1502) if notify else None
        if button:
            rect = api.w.RECT()
            if not api.user.GetClientRect(button, api.c.byref(rect)):
                return False
            point = ((rect.bottom//2) << 16) | (rect.right//2)
            return bool(api.user.PostMessageW(button, 0x0201, 1, point) and
                        api.user.PostMessageW(button, 0x0202, 0, point))
        queue = [(auto.ControlFromHandle(handle), 0)]
        visited = 0
        while queue and visited < 100:
            control, depth = queue.pop(0)
            visited += 1
            if str(control.AutomationId) == "SystemTrayIcon" and str(control.ControlTypeName) == "ButtonControl":
                control.GetInvokePattern().Invoke()
                return True
            if depth < 7:
                queue.extend((child, depth+1) for child in control.GetChildren())
    return False


def system_tray_popup() -> tuple[int, tuple[int, int, int, int]] | None:
    """Find only Explorer's currently visible native overflow, never tray replicas."""
    api = _api()
    for name in ("NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland"):
        handle = api.user.FindWindowW(name, None)
        if handle and api.user.IsWindowVisible(handle):
            rect = api.w.RECT()
            if api.user.GetWindowRect(handle, api.c.byref(rect)):
                return int(handle), (rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top)
    return None


def position_system_tray(handle: int, x: int, y: int) -> bool:
    # The flyout retains Explorer's icons, input, focus and dismissal lifecycle.
    # No reparenting, icon copying or second tray owner.
    api = _api()
    return bool(api.user.IsWindowVisible(handle) and
                api.user.SetWindowPos(handle, None, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010))
