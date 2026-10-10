"""Design specs for MO Desktop's visual surfaces.

The runtime defaults live here so the owner-only studio can preview and generate
product patches without poking renderer private fields or keeping a duplicate
renderer. These objects are lightweight dataclasses only; importing this module
must not pull in tkinter, PIL, voice, tray, OCR, or provider SDKs.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from math import ceil, sqrt
from typing import Any

from interface.desktop_brand import FOUR_CUBE_CELLS

Position = tuple[float, float]

_CLUSTER4: tuple[Position, ...] = tuple(
    (float(column * 2 - 1), float(row * 2 - 1))
    for column, row in FOUR_CUBE_CELLS
)


def split_token_to_width(token: str, max_width: int, measure: Any) -> list[str]:
    """Split one over-wide UI token without changing normal word wrapping."""
    if not token or measure(token) <= max_width:
        return [token]
    parts: list[str] = []
    current = ""
    for char in token:
        candidate = current + char
        if current and measure(candidate) > max_width:
            parts.append(current)
            current = char
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


@dataclass(frozen=True)
class CubeFormSpec:
    """Normalized cube positions plus renderer ratios.

    ``positions`` are multiplied by ``(cube_edge + gap) / 2`` and centered in
    the cube window. The default reproduces the original 2x2 companion exactly.
    """

    positions: tuple[Position, ...] = _CLUSTER4
    edge_ratio: float = 0.22
    gap_ratio: float = 0.10
    bob_ratio: float = 0.035
    glow_blur_ratio: float = 0.10
    row_step_ratio: float = 0.22
    # Deliberately unlike the default 2x2 cluster: wide eyes over a compact,
    # nearly joined mouth.  Smaller offsets made the reviewer state visually
    # indistinguishable from the normal companion at production cube sizes.
    face_eye_x_ratio: float = 0.23
    face_eye_y_ratio: float = 0.18
    face_mouth_x_ratio: float = 0.11
    face_mouth_y_ratio: float = 0.24
    default_formation: str = "cluster"
    emote_gain: float = 1.0

    @property
    def cube_count(self) -> int:
        return len(self.positions)


@dataclass(frozen=True)
class BubbleDesign:
    """Reply/input card layout defaults extracted from ``reply_bubble.py``.

    ``shadow_pad`` is the transparent margin the shadow is blurred into. It must hold the
    whole falloff — roughly ``shadow_dy + 3 * shadow_blur`` — or the layered window ends
    while the shadow is still opaque, drawing a hard straight line around the card. At
    pad 16 the bottom edge still carried alpha 24/255; 28 takes it to zero.
    """

    shadow_pad: int = 28
    content_width: int = 262
    # Final conversational replies need enough horizontal measure to read as a
    # card instead of a narrow scrolling column. Input and specialized panels
    # keep ``content_width``; only plain reply/footerless states opt into this.
    reply_content_width: int | None = None
    accent_top: int = 13
    line_height: int = 21
    min_text_height: int = 22
    max_text_height: int = 230
    footer_height: int = 30
    dock_overlap: int = 8
    shadow_blur: int = 8
    shadow_alpha: int = 150


@dataclass(frozen=True)
class LabelBubbleDesign:
    """Small activity/volume label beside the cube.

    ``shadow_pad`` obeys the same rule as ``BubbleDesign``: it was cutting the shadow off
    at alpha 48/255, the hardest edge of the two surfaces.
    """

    # Quieter than the cube it hangs off: the skin's muted tone, a smaller face, a whisper border.
    font_size: int = 12
    # The border is a whisper, not a frame: the card reads by its fill and shadow.
    edge_alpha: int = 46
    shadow_pad: int = 16
    shadow_blur: int = 5
    shadow_alpha: int = 130
    max_chars: int = 96
    max_width: int = 280
    line_height: int = 17
    # Gap from the cube centre to the CARD's near edge. Placement anchors on the card, not
    # the image, so the shadow margin can grow without sliding the label sideways. 0.64 is
    # the old 0.52 image-anchored ratio plus the 10px pad it used to inherit.
    offset_x_ratio: float = 0.64
    offset_y_ratio: float = 0.0


class PanelState(str, Enum):
    """States rendered by the shared cube-attached Desktop panel."""

    COMPACT = "compact"
    REPLY = "reply"
    INPUT = "input"
    # A reply rendered with no footer controls — a genuine walkthrough, OR transient
    # tool-streaming prose. It is NOT a walkthrough by itself; the walkthrough feature
    # (point_on_screen sequences) is a separate concept. Named FOOTERLESS so tool-use
    # replies are never mistaken for walkthroughs.
    FOOTERLESS = "footerless"
    FILE = "file"
    IMAGE = "image"
    MEDIA = "media"
    HISTORY = "history"
    DASHBOARD = "dashboard"


@dataclass(frozen=True)
class DesktopPanelDesign:
    """Unified defaults shared by the cube-attached panel renderers."""

    bubble: BubbleDesign = field(default_factory=lambda: DEFAULT_BUBBLE_DESIGN)
    label: LabelBubbleDesign = field(default_factory=lambda: DEFAULT_LABEL_BUBBLE_DESIGN)
    min_width: int = 84
    max_width: int = 360
    transition_ms: int = 200
    pin_icon_size: int = 14
    trace_alpha: int = 110


@dataclass(frozen=True)
class DesktopVisualProfile:
    """Visual reactions for desktop events, separate from Gateway/session logic."""

    left_click_action: str = "input"       # left-click opens the text input
    right_click_action: str = "dashboard"  # right-click opens the dashboard
    double_click_action: str = "launcher"  # four-cube MO app launcher; chase/free is Ctrl-Ctrl
    lock_emote: str = "jump"


DEFAULT_CUBE_FORM = CubeFormSpec()
DEFAULT_BUBBLE_DESIGN = BubbleDesign(reply_content_width=340)
DEFAULT_LABEL_BUBBLE_DESIGN = LabelBubbleDesign()
DEFAULT_DESKTOP_PANEL_DESIGN = DesktopPanelDesign()
DEFAULT_VISUAL_PROFILE = DesktopVisualProfile()


def guidance_label_design(base: LabelBubbleDesign | None = None) -> LabelBubbleDesign:
    """A clearer variant of the existing cube label for pointed guidance.

    Activity, volume, sync, and thinking remain compact glances. Walkthrough
    points need longer reading time and stronger hierarchy, but they still use
    the same label renderer and skin rather than creating another surface.
    """
    current = base or DEFAULT_LABEL_BUBBLE_DESIGN
    return replace(
        current,
        font_size=max(15, int(current.font_size)),
        edge_alpha=max(60, int(current.edge_alpha)),
        max_chars=max(160, int(current.max_chars)),
        max_width=max(360, int(current.max_width)),
        line_height=max(22, int(current.line_height)),
    )


def cluster4() -> CubeFormSpec:
    return DEFAULT_CUBE_FORM


def center5() -> CubeFormSpec:
    return replace(DEFAULT_CUBE_FORM, positions=_CLUSTER4 + ((0.0, 0.0),))


def balanced_square(count: int) -> CubeFormSpec:
    """Return a centered, square-like form for ``count`` cubes.

    The 4- and 5-cube cases are intentional: 4 stays the current square, and 5
    keeps that square with the new cube in the center.
    """
    try:
        n = max(1, int(count))
    except Exception:
        n = 4
    if n == 4:
        return cluster4()
    if n == 5:
        return center5()
    side = max(1, ceil(sqrt(n)))
    coords: list[Position] = []
    offset = (side - 1) / 2.0
    for row in range(side):
        for col in range(side):
            coords.append((float(col - offset), float(row - offset)))
            if len(coords) >= n:
                return replace(DEFAULT_CUBE_FORM, positions=tuple(coords))
    return replace(DEFAULT_CUBE_FORM, positions=tuple(coords[:n]))


def coerce_cube_form(value: Any, *, cube_count: int | None = None) -> CubeFormSpec:
    if isinstance(value, CubeFormSpec):
        return value
    if isinstance(value, dict):
        allowed = {name for name in CubeFormSpec.__dataclass_fields__}
        data = {k: v for k, v in value.items() if k in allowed}
        if "positions" in data:
            try:
                data["positions"] = tuple((float(x), float(y)) for x, y in data["positions"])
            except Exception:
                data.pop("positions", None)
        try:
            return CubeFormSpec(**data)
        except Exception:
            pass
    if cube_count is not None and int(cube_count or 4) != 4:
        return balanced_square(int(cube_count))
    return DEFAULT_CUBE_FORM


def coerce_panel_state(value: Any) -> PanelState:
    if isinstance(value, PanelState):
        return value
    text = str(value or "").strip().lower()
    for state in PanelState:
        if text == state.value:
            return state
    return PanelState.COMPACT


