"""Resident Desktop's single native message and deadline loop.

Window painting/input stays in layered.py. This owner only schedules work on
the GUI thread, sleeping until a Windows message, queued call, or timer is due.
Deadlines scheduled with ``frame=True`` are display-synchronous: they run on the
compositor tick nearest their deadline, so animation frames reach the screen on
a steady whole-refresh cadence instead of a free-running timer's 2/3-refresh beat.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import math
import threading
import time
from collections.abc import Callable

_TICK = 1  # DCompositionWaitForCompositorClock result for a compositor tick (== handle count)


def _compositor_wait():
    """DirectComposition's compositor-clock wait, or None before Windows 8.1."""
    try:
        import ctypes
        from ctypes import wintypes

        wait = ctypes.WinDLL("dcomp").DCompositionWaitForCompositorClock
        wait.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD]
        wait.restype = wintypes.DWORD
        return wait
    except (OSError, AttributeError):
        return None


def display_period() -> float:
    """One refresh of the primary display, in seconds (60 Hz when unreadable)."""
    try:
        import win32api
        import win32con

        hertz = int(win32api.EnumDisplaySettings(None, win32con.ENUM_CURRENT_SETTINGS).DisplayFrequency)
    except Exception:
        hertz = 0
    return 1 / hertz if hertz > 1 else 1 / 60


_clock_wait = None  # resolved once; False where the compositor clock is unavailable


def display_tick(timeout_ms: int = 25) -> float | None:
    """Sleep to the next display tick and return its ``perf_counter`` time.

    For a one-off aligned frame outside NativeGuiLoop (a WebView host's finite
    entrance). A timeout still returns the current time; None means no clock.
    """
    global _clock_wait
    if _clock_wait is None:
        _clock_wait = _compositor_wait() or False
    if not _clock_wait:
        return None
    _clock_wait(0, None, timeout_ms)
    return time.perf_counter()


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
        self._tick_wait = _compositor_wait()
        self._tick_handles = None
        self._frame_tokens: set[int] = set()
        self._period = display_period()  # one display refresh; refined from consecutive ticks
        self._last_tick: float | None = None

    def wake(self) -> None:
        import win32event

        with self._lock:
            if self._wake is not None:
                win32event.SetEvent(self._wake)

    def schedule(self, delay_ms: float, callback: Callable[[], None], *, frame: bool = False) -> int | None:
        with self._lock:
            if self._stopped:
                return None
            token = next(self._sequence)
            self._callbacks[token] = callback
            if frame and self._tick_wait is not None:
                self._frame_tokens.add(token)
            # perf_counter is monotonic and uses QPC on supported Python/Windows.
            # Older Python's monotonic clock uses coarse GetTickCount64 instead.
            heapq.heappush(self._deadlines, (time.perf_counter() + max(0, delay_ms) / 1000, token))
            if threading.get_ident() != self._thread_id:
                self.wake()
            return token

    def cancel(self, token: int | None) -> None:
        with self._lock:
            self._callbacks.pop(token, None)
            self._frame_tokens.discard(token)
            if len(self._deadlines) > max(32, 2 * len(self._callbacks)):
                self._deadlines = [item for item in self._deadlines if item[1] in self._callbacks]
                heapq.heapify(self._deadlines)

    def run(self, drain: Callable[[], None]) -> None:
        import win32con
        import win32event
        from mo_desktop.layered import pump_native_window_messages

        ticked = False
        while not self._stopped:
            pump_native_window_messages()
            drain()
            now = time.perf_counter()
            with self._lock:
                due = []
                while self._deadlines:
                    deadline, token = self._deadlines[0]
                    if token in self._frame_tokens:
                        # Released by a display tick, at the tick nearest the deadline.
                        if not ticked or now < deadline - self._period / 2:
                            break
                    elif deadline > now:
                        break
                    heapq.heappop(self._deadlines)
                    due.append(token)
            ticked = False
            for index, token in enumerate(due):
                if self._stopped:
                    # A stop that was due in the same batch leaves the rest for the next run():
                    # they are off the heap already and would otherwise be lost silently.
                    with self._lock:
                        for rest in due[index:]:
                            if rest in self._callbacks:
                                heapq.heappush(self._deadlines, (now, rest))
                    break
                with self._lock:
                    callback = self._callbacks.pop(token, None)
                    self._frame_tokens.discard(token)
                if callback is not None:
                    try:
                        callback()
                    except Exception:
                        logging.getLogger(__name__).exception("Desktop scheduled callback failed")
            if due:
                self._last_tick = None  # callback time must not look like a display period
            near_frame = False
            with self._lock:
                if self._stopped:
                    break
                while self._deadlines and self._deadlines[0][1] not in self._callbacks:
                    heapq.heappop(self._deadlines)
                if self._deadlines:
                    deadline, token = self._deadlines[0]
                    frame = token in self._frame_tokens
                    remaining = deadline - (self._period / 2 if frame else 0.0) - time.perf_counter()
                    if frame and remaining <= 2 * self._period:
                        near_frame = True
                    elif remaining <= 0:
                        continue
                    else:
                        # A distant frame sleeps on the precise timer and only polls
                        # display ticks for its last two refreshes.
                        remaining -= 2 * self._period if frame else 0.0
                        # Relative due time uses 100 ns units; period zero is one-shot.
                        win32event.SetWaitableTimer(self._timer, -max(1, math.ceil(remaining * 10_000_000)),
                                                    0, None, None, False)
                else:
                    win32event.CancelWaitableTimer(self._timer)
            if near_frame:
                ticked = self._wait_display_tick()
                continue
            win32event.MsgWaitForMultipleObjects((self._wake, self._timer), False,
                                                 win32event.INFINITE, win32con.QS_ALLINPUT)

    def _wait_display_tick(self) -> bool:
        """Sleep to the next display tick; False when woken early by queued work.

        A missing tick (display off, locked session) times out after two
        refreshes and still releases frame work, so a final paint is never lost.
        """
        from ctypes import wintypes

        if self._tick_handles is None:
            self._tick_handles = (wintypes.HANDLE * 1)(int(self._wake))
        try:
            signal = self._tick_wait(1, self._tick_handles, max(2, math.ceil(self._period * 2000)))
        except OSError:
            with self._lock:
                self._tick_wait = None
                self._frame_tokens.clear()  # fall back to ordinary timer deadlines
            return False
        if signal != _TICK:
            self._last_tick = None  # a wake-up or timeout is not evidence of the refresh period
            return signal != 0
        now = time.perf_counter()
        if self._last_tick is not None and 0.5 * self._period < now - self._last_tick < 1.5 * self._period:
            self._period += (now - self._last_tick - self._period) * 0.2
        self._last_tick = now
        return True

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
            self._frame_tokens.clear()
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
