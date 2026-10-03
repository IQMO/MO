"""Where the cube belongs: MO's own terminal window.

Identifying that window is the whole problem. A terminal host owns *every* window it hosts
from one process — Windows Terminal was measured owning five — so process identity finds the
host, not MO's window. Instead MO hands its child the answer: ``set_terminal_title`` publishes
the name it gave its own window into the environment, and MO Desktop, spawned by MO, inherits
it. Nothing is hardcoded on either side; MO recognises MO.

So the question "is the cursor inside MO's terminal?" is answered by asking the window under
the cursor for its title. No enumeration, no guessing which of five windows was meant.

Launched from the tray rather than the terminal, the variable is absent and there is simply no
home — the cube keeps chasing the cursor.

Import stays light: ctypes is resolved on first use.
"""
from __future__ import annotations

import os
import sys

from interface.terminal_host import terminal_title_matches

Rect = tuple[int, int, int, int]

# Home is the terminal's right edge, lifted clear of the input box and footer.
_DOCK_INSET_PX = 20
# Distance from the window's bottom edge to the cube's BOTTOM edge: enough to clear the input
# box and its separator, with a small gap. Measured on the real window.
#
# It anchors the cube's edge, not its centre. A lift to the centre looked the same at one size
# and only at one size: a 140px cluster hung 28px lower than an 84px one and started to sit on
# the input region — and Size is a live slider now. The clearance is what has to stay constant.
#
# Being bottom-anchored, this is independent of the window's height; resizing cannot break it.
# It does assume the terminal's font size and DPI scaling, which is where it would need
# re-measuring, and neither process can ask the other for a cell height it can trust.
_DOCK_CLEAR_PX = 88

_GA_ROOT = 2
OWN_WINDOW = "\x00mo-desktop"     # sentinel: a window of ours, never a real title
_last_rect: Rect | None = None    # the terminal rect we last saw the cursor inside


def contains(rect: Rect | None, x: float, y: float) -> bool:
    if not rect:
        return False
    left, top, right, bottom = rect
    return left <= x <= right and top <= y <= bottom


def published_title() -> str:
    """The window title MO gave its terminal, inherited from the process that spawned us."""
    from core.runtime.instance import ENV_MO_TERMINAL_TITLE

    return os.environ.get(ENV_MO_TERMINAL_TITLE, "").strip()


def _root_window_of(hwnd: int) -> tuple[int, int]:
    """(top-level hwnd, owning pid). ``GA_ROOT`` is identity for a window already top-level."""
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.windll.user32
    root = u32.GetAncestor(hwnd, _GA_ROOT) or hwnd
    owner = wintypes.DWORD()
    u32.GetWindowThreadProcessId(root, ctypes.byref(owner))
    return int(root), int(owner.value)


def _window_title(hwnd: int) -> str:
    import ctypes

    u32 = ctypes.windll.user32
    length = u32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    u32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _root_window_title_at(x: float, y: float) -> tuple[int, str] | None:
    """(hwnd, title) of the top-level window under the screen point, or None.

    ``("", "")`` is returned as ``(hwnd, OWN_WINDOW)`` when the window belongs to us: the cube
    docks *inside* the terminal and is topmost, so it hides the very window we are asking about.
    """
    import ctypes
    from ctypes import wintypes

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    u32 = ctypes.windll.user32
    u32.WindowFromPoint.argtypes = [_POINT]
    u32.WindowFromPoint.restype = wintypes.HWND
    hwnd = u32.WindowFromPoint(_POINT(int(x), int(y)))
    if not hwnd:
        return None
    root, pid = _root_window_of(hwnd)
    if pid == os.getpid():
        return (root, OWN_WINDOW)
    return (root, _window_title(root))


def _rect_of(hwnd: int) -> Rect | None:
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.windll.user32
    rect = wintypes.RECT()
    if not u32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def terminal_rect_at(x: float, y: float) -> Rect | None:
    """Rect of MO's terminal window if the screen point is inside it, else None.

    The cube parks inside that window and is topmost, as is the panel it opens. Asking the OS
    what is under the cursor therefore answers "the cube" the moment you move onto it — and
    reading that as *left the terminal* is what made the cube bolt for the cursor and bounce.
    A window of ours is not an answer: fall back to the rect we last saw the cursor inside.
    """
    global _last_rect
    if sys.platform != "win32":
        return None
    title = published_title()
    if not title:
        return None
    try:
        found = _root_window_title_at(x, y)
        if found is None:
            return None
        if found[1] == OWN_WINDOW:
            return _last_rect if contains(_last_rect, x, y) else None
        if not terminal_title_matches(found[1], title):
            return None       # another app: not home — but REMEMBER the terminal. The cube may be
            #                   docked there, and clicking it must not read as clicking away.
        _last_rect = _rect_of(found[0])
        return _last_rect
    except Exception:
        return None


def last_rect() -> Rect | None:
    """The terminal rect the cursor was last seen inside. Valid while the cube is docked."""
    return _last_rect


def forget_terminal() -> None:
    global _last_rect
    _last_rect = None


def terminal_focused() -> bool:
    """Is MO's terminal the window you are actually working in?

    This is the honest form of "clicked away": clicking another app makes it the foreground
    window, and clicking inside the terminal keeps the terminal foreground — while merely moving
    the cursor changes nothing. It also answers a question a click-latch could not: whether the
    terminal is still the window on top. When another app covers it, home is not a place to be,
    and the cube must not sit recharging over an unrelated window.

    One of our own windows counts as focused: the panel and the cube are MO, not somewhere else.
    """
    if sys.platform != "win32":
        return False
    title = published_title()
    if not title:
        return False
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return False
        root, pid = _root_window_of(hwnd)
        if pid == os.getpid():
            return True
        return terminal_title_matches(_window_title(root), title)
    except Exception:
        return False


def fullscreen_foreground() -> bool:
    """Is a window covering the entire screen in front right now — a film, a game, a slideshow?

    Any full-screen window counts. There is no reliable way to tell a video from a game from a
    presentation, and MO should get out of the way of all three. One of our own windows never does.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        u32 = ctypes.windll.user32
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return False
        root, pid = _root_window_of(hwnd)
        if pid == os.getpid():
            return False
        rect = _rect_of(root)
        if rect is None:
            return False
        left, top, right, bottom = rect
        screen_w, screen_h = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
        return (left <= 0 and top <= 0
                and right - left >= screen_w
                and bottom - top >= screen_h)
    except Exception:
        return False


def dock_point(rect: Rect, cube_size: int) -> tuple[float, float]:
    """Home: clear of the input region inside the terminal's bottom-right."""
    left, top, right, bottom = rect
    half = cube_size / 2.0
    x = min(max(left + half, right - _DOCK_INSET_PX - half), right - half)
    y = min(max(top + half, bottom - _DOCK_CLEAR_PX - half), bottom - half)
    return (x, y)
