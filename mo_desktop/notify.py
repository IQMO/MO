"""MO Desktop's notice spine: one shape for every glance.

A *notice* is a short line the cube can show at a glance, plus a longer detail the operator only
sees if they look at it, plus the emote that announces it. Recharging is one category; Telegram,
email and worker events are others. Nothing here knows what any of them mean.

The pieces this ties together already existed:
- ``emotes.EMOTE_EVENTS`` already categorises notifications (``notify_email``, ``notify_telegram``…)
  and ``cube.react()`` is the single event -> emote choke point.
- ``cube.show_notice()`` is the glance surface — the same small label the volume readout uses.
- ``dashboard.register()`` is the pattern for pluggable providers; ``register_source`` mirrors it.

So adding a category later is a producer plus an emote entry, never a new window and never a branch
in the cube. Import stays light: no tkinter, no PIL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Source = Callable[[], "list[Notice]"]


@dataclass(frozen=True)
class Notice:
    """One thing worth a glance.

    ``title`` is what the cube says (keep it to a couple of words — "synced"). ``detail`` is the
    short summary revealed only if the operator glances at the bubble; it is never the full
    context, because the bubble has to stay in shape and in position.
    """

    key: str
    title: str
    detail: str = ""
    emote_event: str = "notice"
    seconds: float = 3.0
    activate: Callable[[], None] | None = field(default=None, repr=False, compare=False)


_SOURCES: list[tuple[int, str, Source]] = []


def register_source(source: Source, *, order: int = 50, key: str = "") -> None:
    """Register a producer of notices. Same contract as ``dashboard.register``."""
    name = key or getattr(source, "__name__", "notice")
    _SOURCES[:] = [item for item in _SOURCES if item[1] != name]
    _SOURCES.append((int(order), name, source))
    _SOURCES.sort(key=lambda item: (item[0], item[1]))


def collect() -> list[Notice]:
    """Every notice every source currently has. A failing source is skipped, never fatal."""
    out: list[Notice] = []
    for _order, _key, source in list(_SOURCES):
        try:
            out.extend(source() or [])
        except Exception:
            continue
    return out


def emit(cube: Any, notice: Notice) -> bool:
    """Announce a notice on the cube: the emote, then the glance. True if anything was shown."""
    shown = False
    react = getattr(cube, "react", None)
    if callable(react) and notice.emote_event:
        try:
            react(notice.emote_event)
        except Exception:
            pass
    show = getattr(cube, "show_notice", None)
    if callable(show):
        try:
            if notice.activate is None:
                show(notice.title, notice.detail, notice.seconds)
            else:
                show(notice.title, notice.detail, notice.seconds, activate=notice.activate)
            shown = True
        except Exception:
            pass
    return shown
