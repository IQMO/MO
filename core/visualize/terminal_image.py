"""MO in-terminal raster rendering — turn an image into ANSI for the OPERATOR.

MO's default model (DeepSeek) is text-only: canonical screen observation feeds the model's
vision channel, but MO's *generated* visuals and any image the operator should
look at have no viewer without a browser. This renders a raster to a truecolor
ANSI string so the operator — the one consumer with eyes — sees it inline.

Technique: the unicode upper-half block ``▀`` with 24-bit truecolor —
foreground = the top pixel, background = the bottom pixel, i.e. two vertical
pixels per character cell. Works in any truecolor terminal (including Windows
Terminal). Reuses Pillow (already a MO computer-use dependency), imported lazily
so this stays off the light startup path.

The rendered ANSI is painted to the operator through the shared
``core.visualize.operator_visual`` channel — the same path dashboards and
animations use.
"""
from __future__ import annotations

import shutil

# Raster rendering is possible exactly when Pillow is importable; the edit leg
# (core.imageedit) already owns that one probe.
from core.imageedit import available

UPPER_HALF_BLOCK = "▀"  # ▀
DEFAULT_MAX_COLS = 100
MIN_COLS = 8


def terminal_cols(fallback: int = 80) -> int:
    try:
        return int(shutil.get_terminal_size((fallback, 24)).columns) or fallback
    except Exception:
        return fallback


def _target_cols(cols: int | None, image_width: int, max_cols: int) -> int:
    avail = max(MIN_COLS, terminal_cols() - 2)
    cap = max(MIN_COLS, min(int(max_cols or DEFAULT_MAX_COLS), avail))
    if cols and int(cols) > 0:
        return max(MIN_COLS, min(int(cols), cap))
    return max(MIN_COLS, min(int(image_width or cap), cap))


def image_dimensions(source) -> tuple[int, int] | None:
    """Return (width, height) for a path or PIL image, or None if unreadable."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        if hasattr(source, "size"):
            return tuple(source.size)  # type: ignore[return-value]
        with Image.open(str(source)) as im:
            return im.size
    except Exception:
        return None


def render(source, *, cols: int | None = None, max_cols: int = DEFAULT_MAX_COLS) -> str:
    """Return an ANSI truecolor half-block render of an image.

    ``source`` is a file path (str/Path) or a ``PIL.Image.Image``. Never raises:
    returns a one-line ``[image render …]`` error string on any failure so a tool
    turn can't crash on it.
    """
    try:
        from PIL import Image
    except Exception:
        return "[image render unavailable: Pillow not installed]"
    try:
        img = source.convert("RGB") if hasattr(source, "convert") else Image.open(str(source)).convert("RGB")
    except Exception as exc:
        return f"[image render error: {type(exc).__name__}: {exc}]"
    w, h = img.size
    if not w or not h:
        return "[image render error: empty image]"
    cols_n = _target_cols(cols, w, max_cols)
    px_h = max(2, round(cols_n * h / w))
    if px_h % 2:
        px_h += 1
    small = img.resize((cols_n, px_h), Image.LANCZOS)
    px = small.load()
    lines = []
    for r in range(px_h // 2):
        cell = []
        for c in range(cols_n):
            tr, tg, tb = px[c, 2 * r][:3]
            br, bg, bb = px[c, 2 * r + 1][:3]
            cell.append(f"\x1b[38;2;{tr};{tg};{tb};48;2;{br};{bg};{bb}m{UPPER_HALF_BLOCK}")
        cell.append("\x1b[0m")
        lines.append("".join(cell))
    return "\n".join(lines)
