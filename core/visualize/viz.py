"""MO terminal visual primitives — comprehensive ANSI/character visuals.

This is MO's reusable visual library: it renders panels, key/value tables, bar
charts, sparklines and rules to ANSI truecolor strings that any surface can
paint (the operator-visual channel, print mode, or a real terminal). It exists
so MO's output can be *designed terminal visuals*, not plain report words — the
same character/ANSI idiom the terminal signal field uses, powered by ``rich`` (already
a MO dependency) plus a few hand-rolled block-glyph primitives.

Imported lazily (only when a visual is actually rendered), so it stays off the
light agent-startup path even though it pulls ``rich``.
"""
from __future__ import annotations

import io
import math
import shutil

# Cohesive fallback palette (MO brand teal family), used only if the skin can't
# be resolved. The live colours come from the active skin via interface.theming
# so every visual follows MO's theme instead of a generic rainbow.
_FALLBACK_PALETTE = ("#3fe0e0", "#66d9ef", "#7aa2ff", "#68d391", "#bb86fc", "#f6ad55")
_FALLBACK_ACCENT = "#00cccc"   # brand teal
_FALLBACK_TEXT = "#d7dee8"     # primary text
_FALLBACK_MUTED = "#7d8996"    # secondary / dim text
_SPARK = "▁▂▃▄▅▆▇█"
_BAR_GLYPH = "█"
_MAX_BARS = 12   # bars beyond this are a wall, not a chart → show the largest + summarize
_MAX_ROWS = 12   # same complexity budget for table/progress/compare rows


def _skin():
    """Resolve the active skin lazily; None if theming isn't available (e.g. a
    minimal/headless context). Kept lazy so viz stays off the startup path."""
    try:
        from interface.theming import get_skin
        return get_skin()
    except Exception:
        return None


def series_palette() -> tuple[str, ...]:
    """Theme-cohesive colours for chart data series (follows the active skin)."""
    try:
        from interface.theming import chart_palette
        pal = tuple(chart_palette())
        if pal:
            return pal
    except Exception:
        pass
    return _FALLBACK_PALETTE


def _accent() -> str:
    """Brand/border accent from the skin (teal by default)."""
    s = _skin()
    return getattr(s, "brand_primary", None) or _FALLBACK_ACCENT


def _text_color() -> str:
    s = _skin()
    return getattr(s, "text_primary", None) or _FALLBACK_TEXT


def _muted_color() -> str:
    s = _skin()
    return getattr(s, "response_subtle", None) or _FALLBACK_MUTED


# Back-compat: some callers/tests referenced PALETTE. Now skin-derived.
PALETTE = series_palette()


def terminal_width(fallback: int = 96, cap: int = 100) -> int:
    try:
        w = int(shutil.get_terminal_size((fallback, 24)).columns) or fallback
    except Exception:
        w = fallback
    return max(24, min(cap, w - 2))


def render_ansi(renderable, *, width: int | None = None) -> str:
    """Capture a rich renderable to a truecolor ANSI string."""
    from rich.console import Console

    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=width or terminal_width(),
        legacy_windows=False,
        highlight=False,
        soft_wrap=False,
    )
    console.print(renderable)
    return buf.getvalue()


def rule(label: str = "", *, style: str | None = None, width: int | None = None) -> str:
    from rich.rule import Rule

    return render_ansi(Rule(label, style=style or _accent()), width=width)


def panel(body, *, title: str = "", style: str | None = None, width: int | None = None) -> str:
    """A box-drawn panel around ``body`` (str or rich renderable)."""
    from rich.panel import Panel

    return render_ansi(
        Panel(body, title=title, border_style=style or _accent(), title_align="left"),
        width=width,
    )


def kv_table(rows, *, title: str = "", style: str | None = None, width: int | None = None) -> str:
    """A two-column key/value table. ``rows`` is a dict or list of (key, value)."""
    from rich.table import Table
    from rich.text import Text as _T

    items = list(rows.items()) if hasattr(rows, "items") else list(rows)
    overflow = 0
    if len(items) > _MAX_ROWS:
        overflow = len(items) - _MAX_ROWS
        items = items[:_MAX_ROWS]
    table = Table(show_header=False, box=None, expand=False, pad_edge=False)
    table.add_column(justify="right", style=f"bold {_accent()}")  # keys in brand accent
    table.add_column(style=_text_color())                          # values in theme text
    for key, value in items:
        table.add_row(str(key), str(value))
    if overflow:
        table.add_row(_T("…", style=_muted_color()), _T(f"+{overflow} more", style=_muted_color()))
    return panel(table, title=title, style=style, width=width) if title else render_ansi(table, width=width)


def bar_chart(data, *, title: str = "", unit: str = "", width: int | None = None, style: str | None = None) -> str:
    """Horizontal char bar chart. ``data`` is a dict/list of (label, number).

    Magnitude is encoded by bar LENGTH, so every bar shares ONE brand accent —
    colour is reserved for meaning, never a per-bar rainbow (MO dropped the
    rainbow; a sequential palette here would be noise). Too many bars is a wall,
    not a chart, so it shows the largest ``_MAX_BARS`` and summarizes the rest.
    """
    from rich.text import Text

    items = [(str(k), float(v)) for k, v in (data.items() if hasattr(data, "items") else data)]
    if any(not math.isfinite(value) for _, value in items):
        raise ValueError("bar values must be finite numbers")
    inner = (width or terminal_width()) - 4
    if not items:
        return panel(Text("(no data)", style=_muted_color()), title=title, style=style, width=width)
    overflow = 0
    if len(items) > _MAX_BARS:
        items = sorted(items, key=lambda kv: abs(kv[1]), reverse=True)
        overflow = len(items) - _MAX_BARS
        items = items[:_MAX_BARS]
    accent = _accent()
    text_col = _text_color()
    label_w = min(28, max(len(label) for label, _ in items))
    peak = max((abs(v) for _, v in items), default=0.0) or 1.0
    val_w = max(len(f"{v:,.0f}{unit}") for _, v in items)
    bar_area = max(4, inner - label_w - val_w - 3)
    body = Text()
    for label, value in items:
        filled = min(bar_area, max(0, int(round(bar_area * (abs(value) / peak)))))
        clipped = label if len(label) <= label_w else label[: label_w - 1] + "…"
        body.append(f"{clipped:>{label_w}} ", style=text_col)
        body.append(_BAR_GLYPH * filled or "▏", style=accent)
        body.append(f" {value:,.0f}{unit}\n", style=text_col)
    if overflow:
        body.append(f"{'…':>{label_w}} +{overflow} more\n", style=_muted_color())
    return panel(body, title=title, style=style, width=width)


def sparkline(values, *, label: str = "", style: str | None = None) -> str:
    """A single-line block sparkline for a sequence of numbers (ANSI Text)."""
    from rich.text import Text

    nums = [float(v) for v in values if isinstance(v, (int, float))]
    if not nums:
        return render_ansi(Text("", style=style or _accent()))
    lo, hi = min(nums), max(nums)
    span = (hi - lo) or 1.0
    chars = "".join(_SPARK[min(len(_SPARK) - 1, int((v - lo) / span * (len(_SPARK) - 1)))] for v in nums)
    text = Text()
    if label:
        text.append(f"{label}  ", style=_muted_color())
    text.append(chars, style=style or _accent())
    return render_ansi(text)


def _status_colors():
    """(done, active, blocked) colours from the skin for threshold shading."""
    s = _skin()
    return (
        getattr(s, "status_done", None) or "#00cc88",
        getattr(s, "status_active", None) or "#ddaa00",
        getattr(s, "status_blocked", None) or "#cc4444",
    )


def progress(rows, *, title: str = "", width: int | None = None, style: str | None = None) -> str:
    """Labelled ratio/coverage bars. ``rows`` is a dict or list of either
    (label, fraction) with fraction in 0..1 (or 0..100), or (label, value, total).

    Each bar is shaded by the skin's status colours — high=done, mid=active,
    low=blocked — so coverage/progress reads at a glance without a legend."""
    from rich.text import Text

    items = list(rows.items()) if hasattr(rows, "items") else list(rows)
    if not items:
        return panel(Text("(no data)", style=_muted_color()), title=title, style=style, width=width)
    overflow = 0
    if len(items) > _MAX_ROWS:
        overflow = len(items) - _MAX_ROWS
        items = items[:_MAX_ROWS]
    norm: list[tuple[str, float, str]] = []
    for row in items:
        row = tuple(row) if not isinstance(row, tuple) else row
        if isinstance(row[1], (list, tuple)):  # (label, (value, total))
            label, (value, total) = row[0], row[1]
            frac = (float(value) / float(total)) if total else 0.0
            cap = f"{float(value):,.0f}/{float(total):,.0f}"
        elif len(row) == 3:                     # (label, value, total)
            label, value, total = row
            frac = (float(value) / float(total)) if total else 0.0
            cap = f"{float(value):,.0f}/{float(total):,.0f}"
        else:                                   # (label, fraction)
            label, value = row[0], float(row[1])
            frac = value / 100.0 if value > 1 else value
            cap = f"{frac * 100:.0f}%"
        norm.append((str(label), max(0.0, min(1.0, frac)), cap))

    done, active, blocked = _status_colors()
    text_col = _text_color()
    inner = (width or terminal_width()) - 4
    label_w = min(24, max(len(l) for l, _, _ in norm))
    cap_w = max(len(c) for _, _, c in norm)
    track = max(8, inner - label_w - cap_w - 4)
    body = Text()
    for label, frac, cap in norm:
        colour = done if frac >= 0.8 else active if frac >= 0.5 else blocked
        filled = int(round(track * frac))
        clipped = label if len(label) <= label_w else label[: label_w - 1] + "…"
        body.append(f"{clipped:>{label_w}} ", style=text_col)
        body.append(_BAR_GLYPH * filled, style=colour)
        body.append("░" * (track - filled), style=_muted_color())
        body.append(f" {cap}\n", style=text_col)
    if overflow:
        body.append(f"{'…':>{label_w}} +{overflow} more\n", style=_muted_color())
    return panel(body, title=title, style=style, width=width)


def compare(rows, *, title: str = "", unit: str = "", width: int | None = None, style: str | None = None) -> str:
    """Before→after with delta. ``rows`` is a dict {label: (before, after)} or a
    list of (label, before, after). The delta is signed and coloured by direction
    (up green / down red) so a change reads instantly."""
    from rich.table import Table

    items = list(rows.items()) if hasattr(rows, "items") else list(rows)
    overflow = 0
    if len(items) > _MAX_ROWS:
        overflow = len(items) - _MAX_ROWS
        items = items[:_MAX_ROWS]
    up, _, down = _status_colors()
    table = Table(show_header=True, box=None, expand=False, pad_edge=False, header_style=_muted_color())
    table.add_column("", justify="right", style=f"bold {_accent()}")
    table.add_column("before", justify="right", style=_text_color())
    table.add_column("", justify="center", style=_muted_color())
    table.add_column("after", justify="right", style=_text_color())
    table.add_column("Δ", justify="right")
    for row in items:
        if isinstance(row, tuple) and len(row) == 2 and isinstance(row[1], (list, tuple)):
            label, (before, after) = row[0], row[1]
        else:
            label, before, after = row
        before, after = float(before), float(after)
        delta = after - before
        dcol = up if delta > 0 else down if delta < 0 else _muted_color()
        sign = "+" if delta > 0 else ""
        from rich.text import Text as _T
        table.add_row(
            str(label), f"{before:,.0f}{unit}", "→", f"{after:,.0f}{unit}",
            _T(f"{sign}{delta:,.0f}{unit}", style=dcol),
        )
    if overflow:
        from rich.text import Text as _TT
        table.add_row(_TT("…", style=_muted_color()), "", "", "", _TT(f"+{overflow} more", style=_muted_color()))
    return panel(table, title=title, style=style, width=width)


def stack(*blocks: str) -> str:
    """Join rendered ANSI blocks vertically, dropping empties."""
    return "\n".join(b.rstrip("\n") for b in blocks if b and b.strip())
