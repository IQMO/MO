"""Resident Desktop's single native message and deadline loop.

Window painting/input stays in layered.py. This owner only schedules work on
the GUI thread, sleeping until a Windows message, queued call, or timer is due.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import math
import threading
import time
from collections.abc import Callable


class NativeGuiLoop:
    def __init__(self) -> None:
        import win32event
        import pywintypes

        self._wake = win32event.CreateEvent(None, False, False, None)
        try:
            try:
                # Per-handle precision; never raise the system timer resolution.
                self._timer = win32event.CreateWaitableTimerEx(None, None, 2, 0x1F0003)
            except pywintypes.error as error:
                if error.winerror != 87:  # Older Windows lacks HIGH_RESOLUTION.
                    raise
                self._timer = win32event.CreateWaitableTimerEx(None, None, 0, 0x1F0003)
        except Exception:
            self._wake.Close()
            raise
        self._lock = threading.RLock()
        self._sequence = itertools.count()
        self._deadlines: list[tuple[float, int]] = []
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._stopped = False
        self._thread_id = threading.get_ident()

    def wake(self) -> None:
        import win32event

        with self._lock:
            if self._wake is not None:
                win32event.SetEvent(self._wake)

    def schedule(self, delay_ms: float, callback: Callable[[], None]) -> int | None:
        with self._lock:
            if self._stopped:
                return None
            token = next(self._sequence)
            self._callbacks[token] = callback
            # perf_counter is monotonic and uses QPC on supported Python/Windows.
            # Older Python's monotonic clock uses coarse GetTickCount64 instead.
            heapq.heappush(self._deadlines, (time.perf_counter() + max(0, delay_ms) / 1000, token))
            if threading.get_ident() != self._thread_id:
                self.wake()
            return token

    def cancel(self, token: int | None) -> None:
        with self._lock:
            self._callbacks.pop(token, None)
            if len(self._deadlines) > max(32, 2 * len(self._callbacks)):
                self._deadlines = [item for item in self._deadlines if item[1] in self._callbacks]
                heapq.heapify(self._deadlines)

    def run(self, drain: Callable[[], None]) -> None:
        import win32con
        import win32event
        from mo_desktop.layered import pump_native_window_messages

        while not self._stopped:
            pump_native_window_messages()
            drain()
            now = time.perf_counter()
            with self._lock:
                due = []
                while self._deadlines and self._deadlines[0][0] <= now:
                    _, token = heapq.heappop(self._deadlines)
                    due.append(token)
            for token in due:
                with self._lock:
                    callback = self._callbacks.pop(token, None)
                if self._stopped:
                    break
                if callback is not None:
                    try:
                        callback()
                    except Exception:
                        logging.getLogger(__name__).exception("Desktop scheduled callback failed")
            with self._lock:
                if self._stopped:
                    break
                while self._deadlines and self._deadlines[0][1] not in self._callbacks:
                    heapq.heappop(self._deadlines)
                if self._deadlines:
                    remaining = self._deadlines[0][0] - time.perf_counter()
                    if remaining <= 0:
                        continue
                    # Relative due time uses 100 ns units; period zero is one-shot.
                    win32event.SetWaitableTimer(self._timer, -max(1, math.ceil(remaining * 10_000_000)),
                                                0, None, None, False)
                else:
                    win32event.CancelWaitableTimer(self._timer)
            win32event.MsgWaitForMultipleObjects((self._wake, self._timer), False,
                                                 win32event.INFINITE, win32con.QS_ALLINPUT)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self.wake()

    def close(self) -> None:
        """Release on the GUI thread, after run and all surface teardown."""
        import win32event

        self.stop()
        with self._lock:
            self._callbacks.clear()
            self._deadlines.clear()
            if self._timer is not None:
                win32event.CancelWaitableTimer(self._timer)
                self._timer.Close()
                self._timer = None
            if self._wake is not None:
                self._wake.Close()
                self._wake = None


def screen_size() -> tuple[int, int]:
    import win32api

    return win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)


def pointer_position() -> tuple[int, int]:
    import win32gui

    return win32gui.GetCursorPos()


def clipboard_text() -> str:
    import win32clipboard
    import win32con

    win32clipboard.OpenClipboard()
    try:
        return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard_text(value: str) -> None:
    import win32clipboard

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(value)
    finally:
        win32clipboard.CloseClipboard()
