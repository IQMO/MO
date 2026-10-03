"""Small skin-aware Tk widgets shared by MO Desktop utility surfaces.

Tk imports remain lazy so importing interface helpers stays lightweight.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any, Callable, Iterable, TypeVar
import weakref

from interface.desktop_ui import (
    DESKTOP_SPACING as SPACING,
    DESKTOP_TYPOGRAPHY as TYPOGRAPHY,
    DesktopPalette,
    DesktopVisualAdapterError,
    DesktopWindowEffects,
    active_desktop_visual_state,
)
from interface.desktop_brand import WINDOW_CHROME

T = TypeVar("T")
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThemedMenuSubmenu:
    """A nested branch in :func:`themed_context_menu`."""

    items: tuple[tuple[str, Any], ...]


def apply_windows_rounded_region(
    hwnd: int,
    width: int,
    height: int,
    radius: int,
) -> str:
    """Apply one Windows HWND region and return ``applied``/``unsupported``.

    A supported Windows failure is explicit because silently showing a square
    fallback would violate the active Desktop visual state.
    """
    import os

    if os.name != "nt":
        return "unsupported"
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        user32.SetWindowRgn.restype = ctypes.c_int
        clean_radius = max(0, int(radius))
        if clean_radius == 0:
            if not user32.SetWindowRgn(int(hwnd), None, True):
                raise OSError(ctypes.get_last_error(), "SetWindowRgn(clear) failed")
            return "applied"
        gdi32.CreateRoundRectRgn.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL
        diameter = max(1, clean_radius * 2)
        region = gdi32.CreateRoundRectRgn(
            0, 0, max(1, int(width)) + 1, max(1, int(height)) + 1,
            diameter, diameter,
        )
        if not region:
            raise OSError(ctypes.get_last_error(), "CreateRoundRectRgn failed")
        if not user32.SetWindowRgn(int(hwnd), region, True):
            gdi32.DeleteObject(region)
            raise OSError(ctypes.get_last_error(), "SetWindowRgn failed")
        # A successful SetWindowRgn transfers region ownership to Windows.
        return "applied"
    except DesktopVisualAdapterError:
        raise
    except Exception as exc:
        raise DesktopVisualAdapterError(
            f"could not apply Desktop window radius to HWND {int(hwnd)}"
        ) from exc


def apply_windows_rounded_frame(
    hwnd: int,
    width: int,
    height: int,
    radius: int,
    color: str,
    *,
    thickness: int = 1,
) -> str:
    """Paint the skin border along an HWND's exact rounded region.

    Borderless Tk windows have no DWM non-client frame, so clipping their
    rectangular client border removes the four curved arcs.  Frame the same
    native region after Tk paints instead of asking each surface to approximate
    those arcs independently.
    """
    import os

    if os.name != "nt":
        return "unsupported"
    try:
        import ctypes
        from ctypes import wintypes

        clean_color = str(color or "").strip()
        if len(clean_color) != 7 or not clean_color.startswith("#"):
            raise ValueError("Desktop frame color must use #RRGGBB")
        red = int(clean_color[1:3], 16)
        green = int(clean_color[3:5], 16)
        blue = int(clean_color[5:7], 16)
        colorref = red | (green << 8) | (blue << 16)
        clean_width = max(1, int(width))
        clean_height = max(1, int(height))
        clean_radius = max(0, int(radius))
        clean_thickness = max(1, int(thickness))

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        user32.GetDCEx.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.DWORD]
        user32.GetDCEx.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        gdi32.CreateRectRgn.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        gdi32.CreateRectRgn.restype = wintypes.HRGN
        gdi32.CreateRoundRectRgn.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
        gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        gdi32.FrameRgn.argtypes = [
            wintypes.HDC,
            wintypes.HRGN,
            wintypes.HBRUSH,
            ctypes.c_int,
            ctypes.c_int,
        ]
        gdi32.FrameRgn.restype = wintypes.BOOL
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL

        region = None
        brush = None
        hdc = None
        try:
            if clean_radius == 0:
                region = gdi32.CreateRectRgn(
                    0, 0, clean_width + 1, clean_height + 1,
                )
            else:
                diameter = max(1, clean_radius * 2)
                region = gdi32.CreateRoundRectRgn(
                    0, 0, clean_width + 1, clean_height + 1,
                    diameter, diameter,
                )
            if not region:
                raise OSError(ctypes.get_last_error(), "Create frame region failed")
            brush = gdi32.CreateSolidBrush(colorref)
            if not brush:
                raise OSError(ctypes.get_last_error(), "CreateSolidBrush failed")
            # DCX_WINDOW | DCX_CACHE draws the outer frame after child controls
            # have painted, including the client-owned corners of Tk windows.
            hdc = user32.GetDCEx(int(hwnd), None, 0x00000001 | 0x00000002)
            if not hdc:
                raise OSError(ctypes.get_last_error(), "GetDCEx failed")
            if not gdi32.FrameRgn(
                hdc, region, brush, clean_thickness, clean_thickness,
            ):
                raise OSError(ctypes.get_last_error(), "FrameRgn failed")
            return "applied"
        finally:
            if hdc:
                user32.ReleaseDC(int(hwnd), hdc)
            if brush:
                gdi32.DeleteObject(brush)
            if region:
                gdi32.DeleteObject(region)
    except DesktopVisualAdapterError:
        raise
    except Exception as exc:
        raise DesktopVisualAdapterError(
            f"could not apply Desktop window frame to HWND {int(hwnd)}"
        ) from exc


def render_desktop_window_effect(
    width: int,
    height: int,
    radius: int,
    effects: DesktopWindowEffects,
    glow_color: str,
    *,
    hollow: bool = False,
) -> tuple[Any, int]:
    """Render one transparent, skin-derived outside treatment for an HWND."""
    if not isinstance(effects, DesktopWindowEffects):
        raise TypeError("Desktop window effect requires DesktopWindowEffects")
    style = effects.style
    level = max(0.0, min(1.0, effects.intensity / 100.0))
    if style == "none" or level <= 0:
        raise ValueError("an inactive Desktop window effect has no image")
    clean_glow = str(glow_color or "").strip()
    try:
        if len(clean_glow) != 7 or not clean_glow.startswith("#"):
            raise ValueError
        glow_rgb = tuple(int(clean_glow[index:index + 2], 16) for index in (1, 3, 5))
    except ValueError as exc:
        raise ValueError("Desktop window effect accent must use #RRGGBB") from exc

    from PIL import Image, ImageDraw, ImageFilter

    clean_width = max(1, int(width))
    clean_height = max(1, int(height))
    clean_radius = max(0, int(radius))
    shadow_blur = int(round(8 + 14 * level))
    glow_blur = int(round(7 + 17 * level))
    shadow_dy = int(round(2 + 6 * level))
    largest_blur = max(
        shadow_blur if style in {"shadow", "hybrid"} else 0,
        glow_blur if style in {"glow", "hybrid"} else 0,
    )
    pad = max(18, largest_blur * 3 + shadow_dy + 2)
    size = (clean_width + 2 * pad, clean_height + 2 * pad)
    box = (pad, pad, pad + clean_width - 1, pad + clean_height - 1)
    image = Image.new("RGBA", size, (0, 0, 0, 0))

    if style in {"shadow", "hybrid"}:
        shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (box[0], box[1] + shadow_dy, box[2], box[3] + shadow_dy),
            radius=clean_radius,
            fill=(0, 0, 0, int(round(45 + 120 * level))),
        )
        image = Image.alpha_composite(
            image,
            shadow.filter(ImageFilter.GaussianBlur(shadow_blur)),
        )
    if style in {"glow", "hybrid"}:
        glow = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle(
            box,
            radius=clean_radius,
            fill=(*glow_rgb, int(round(15 + 65 * level))),
        )
        image = Image.alpha_composite(
            image,
            glow.filter(ImageFilter.GaussianBlur(glow_blur)),
        )
    if hollow:
        ImageDraw.Draw(image).rounded_rectangle(
            box,
            radius=clean_radius,
            fill=(0, 0, 0, 0),
        )
    return image, pad


class DesktopWindowEffectLayer:
    """One passive native layer kept directly behind one MO-owned HWND."""

    def __init__(self) -> None:
        self._surface: Any = None
        self._owner_hwnd = 0
        self._render_key: tuple[Any, ...] | None = None
        self._pad = 0
        self._image_size = (0, 0)
        self._placement: tuple[int, int, int, int] | None = None
        self._opacity = -1

    def refresh_hwnd(
        self,
        hwnd: int,
        width: int,
        height: int,
        radius: int,
        effects: DesktopWindowEffects,
        glow_color: str,
        *,
        show: bool = True,
        content_rect: tuple[int, int, int, int] | None = None,
        hollow: bool = False,
    ) -> str:
        import os

        if os.name != "nt":
            return "unsupported"
        if not isinstance(effects, DesktopWindowEffects):
            raise TypeError("Desktop native effect requires DesktopWindowEffects")
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.IsWindow.argtypes = [wintypes.HWND]
            user32.IsWindow.restype = wintypes.BOOL
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsWindowVisible.restype = wintypes.BOOL
            user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            user32.GetWindowRect.restype = wintypes.BOOL
            clean_hwnd = int(hwnd)
            if not clean_hwnd or not user32.IsWindow(clean_hwnd):
                self.hide()
                return "deferred"
            if effects.style == "none" or effects.intensity <= 0:
                self.hide()
                self._opacity = 0
                return "hidden"
            if not user32.IsWindowVisible(clean_hwnd):
                self.hide()
                return "deferred"
            rect = wintypes.RECT()
            if not user32.GetWindowRect(clean_hwnd, ctypes.byref(rect)):
                raise OSError(ctypes.get_last_error(), "GetWindowRect failed")
            owner_width = max(1, int(rect.right - rect.left))
            owner_height = max(1, int(rect.bottom - rect.top))
            clean_left = int(rect.left)
            clean_top = int(rect.top)
            if content_rect is None:
                clean_width = max(owner_width, int(width))
                clean_height = max(owner_height, int(height))
            else:
                offset_x, offset_y, clean_width, clean_height = (
                    int(value) for value in content_rect
                )
                if (
                    offset_x < 0
                    or offset_y < 0
                    or clean_width < 1
                    or clean_height < 1
                    or offset_x + clean_width > owner_width
                    or offset_y + clean_height > owner_height
                ):
                    raise ValueError("Desktop effect content rectangle exceeds its owner")
                clean_left += offset_x
                clean_top += offset_y
            render_key = (
                clean_width,
                clean_height,
                max(0, int(radius)),
                effects.style,
                str(glow_color),
                bool(hollow),
            )
            opacity = max(1, min(255, int(round(255 * effects.intensity / 100.0))))
            placement = (
                clean_left,
                clean_top,
                clean_width,
                clean_height,
            )
            if self._surface is None or self._owner_hwnd != clean_hwnd:
                self.destroy()
                from mo_desktop.layered import NativeLayeredWindow

                self._surface = NativeLayeredWindow(clean_hwnd)
                self._owner_hwnd = clean_hwnd
            if self._render_key != render_key:
                render_options = {"hollow": True} if hollow else {}
                image, pad = render_desktop_window_effect(
                    clean_width,
                    clean_height,
                    radius,
                    DesktopWindowEffects(style=effects.style, intensity=100),
                    glow_color,
                    **render_options,
                )
                self._pad = pad
                self._image_size = image.size
                blit_options = {"opacity": opacity}
                if not show:
                    blit_options["show"] = False
                if not self._surface.blit(
                    image,
                    clean_left - pad,
                    clean_top - pad,
                    **blit_options,
                ):
                    self.destroy()
                    raise DesktopVisualAdapterError(
                        "Desktop native window effect could not be painted"
                    )
                self._render_key = render_key
                self._opacity = opacity
                self._placement = placement
                return "applied"
            changed = False
            if self._opacity != opacity:
                if not self._surface.set_opacity(opacity):
                    self.destroy()
                    raise DesktopVisualAdapterError(
                        "Desktop native window effect strength could not be applied"
                    )
                self._opacity = opacity
                changed = True
            self._placement = placement
            if not show:
                self._surface.hide()
                return "applied" if changed else "unchanged"
            if not self._surface.place_behind(
                clean_left - self._pad,
                clean_top - self._pad,
                self._image_size[0],
                self._image_size[1],
            ):
                self.destroy()
                raise DesktopVisualAdapterError(
                    "Desktop native window effect could not follow its owner"
                )
            return "applied" if changed else "unchanged"
        except DesktopVisualAdapterError:
            raise
        except Exception as exc:
            raise DesktopVisualAdapterError(
                f"could not apply Desktop window effect to HWND {int(hwnd)}"
            ) from exc

    def show(self) -> bool:
        """Show a prepared effect without re-rendering its cached bitmap."""
        if self._surface is None or self._placement is None:
            return False
        left, top, width, height = self._placement
        shown = bool(self._surface.place_behind(
            left - self._pad,
            top - self._pad,
            self._image_size[0] or width + 2 * self._pad,
            self._image_size[1] or height + 2 * self._pad,
        ))
        if not shown:
            raise DesktopVisualAdapterError(
                "Desktop native window effect could not be published"
            )
        return True

    def hide(self) -> None:
        if self._surface is not None:
            self._surface.hide()

    def destroy(self) -> None:
        surface = self._surface
        self._surface = None
        self._owner_hwnd = 0
        self._render_key = None
        self._pad = 0
        self._image_size = (0, 0)
        self._placement = None
        self._opacity = -1
        if surface is not None:
            surface.destroy()


def _windows_widget_geometry(
    widget: Any,
    *,
    top_level: bool,
) -> tuple[int, int, int] | None:
    """Resolve the real HWND and current size, deferring withdrawn Toplevels."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd = int(widget.winfo_id())
    width = max(1, int(widget.winfo_width()))
    height = max(1, int(widget.winfo_height()))
    if not top_level:
        return hwnd, width, height
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    wrapper = user32.GetParent(hwnd)
    if not wrapper:
        return None
    hwnd = int(wrapper)
    rect = wintypes.RECT()
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        width = max(1, int(rect.right - rect.left))
        height = max(1, int(rect.bottom - rect.top))
    return hwnd, width, height


def _set_windows_rounded_region(
    widget: Any,
    radius: int,
    *,
    top_level: bool,
    frame_color: str | None = None,
    frame_thickness: int = 1,
) -> str:
    """Apply an idempotent rounded region to one Tk widget."""
    import os

    if os.name != "nt":
        return "unsupported"
    try:
        geometry = _windows_widget_geometry(widget, top_level=top_level)
        # Tk creates the client HWND before the native wrapper for a withdrawn
        # Toplevel. Wait for <Map>/<Configure> instead of clipping that client.
        if geometry is None:
            return "deferred"
        hwnd, width, height = geometry
        key = (hwnd, width, height, max(0, int(radius)))
        result = "unchanged"
        if getattr(widget, "_mo_rounded_region_key", None) != key:
            result = apply_windows_rounded_region(hwnd, width, height, radius)
            if result in {"applied", "unsupported"}:
                setattr(widget, "_mo_rounded_region_key", key)
        if frame_color is not None:
            apply_windows_rounded_frame(
                hwnd,
                width,
                height,
                radius,
                frame_color,
                thickness=max(1, int(frame_thickness)),
            )
        return result
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if getattr(widget, "_mo_rounded_region_error", "") != detail:
            setattr(widget, "_mo_rounded_region_error", detail)
            _LOG.error("Desktop rounded-window adapter failed: %s", detail)
        return "error"


class DesktopButtonCorners:
    """Attach the shared button-corner metric to a classic Tk button."""

    def __init__(self, button: Any) -> None:
        self.button = button
        self._pending: Any = None
        try:
            button.bind("<Configure>", self._queue, add="+")
            button.bind("<Destroy>", self._destroyed, add="+")
        except Exception:
            pass
        self.refresh()

    def _destroyed(self, event: Any = None) -> None:
        if event is not None and getattr(event, "widget", self.button) is not self.button:
            return
        self._pending = None

    def _queue(self, _event: Any = None) -> None:
        if self._pending is not None:
            return
        try:
            self._pending = self.button.after_idle(self.refresh)
        except Exception:
            self._pending = None

    def refresh(self) -> None:
        self._pending = None
        _set_windows_rounded_region(
            self.button,
            active_desktop_visual_state().metrics.button_corner_radius,
            top_level=False,
        )


def install_desktop_button_corners(button: Any) -> DesktopButtonCorners:
    controller = getattr(button, "_mo_button_corners", None)
    if isinstance(controller, DesktopButtonCorners):
        controller.refresh()
        return controller
    controller = DesktopButtonCorners(button)
    try:
        setattr(button, "_mo_button_corners", controller)
    except Exception:
        pass
    return controller


_DESKTOP_WINDOW_CONTROLLERS: Any = weakref.WeakSet()


class DesktopWindowCorners:
    """One rounded region, frame, and outside-effect owner for utility windows."""

    def __init__(self, window: Any) -> None:
        self.window = window
        self._pending: Any = None
        self._frame_pending: Any = None
        self._revealing = False
        self._effect = DesktopWindowEffectLayer()
        self._effect_prepared = False
        _DESKTOP_WINDOW_CONTROLLERS.add(self)
        try:
            window.bind("<Configure>", self._queue, add="+")
            window.bind("<Map>", self._queue, add="+")
            window.bind("<Unmap>", self._hidden, add="+")
            window.bind("<Expose>", self._queue_frame, add="+")
            window.bind("<Destroy>", self._destroyed, add="+")
        except Exception:
            pass
        self.refresh()

    def _destroyed(self, event: Any = None) -> None:
        if event is not None and getattr(event, "widget", self.window) is not self.window:
            return
        self._pending = None
        self._frame_pending = None
        self._effect_prepared = False
        self._effect.destroy()
        _DESKTOP_WINDOW_CONTROLLERS.discard(self)

    def _hidden(self, event: Any = None) -> None:
        if event is not None and getattr(event, "widget", self.window) is not self.window:
            return
        self._effect.hide()

    def _queue(self, event: Any = None) -> None:
        # A Toplevel pathname is also a bindtag on every descendant.  Child
        # Configure storms must not rescan or repaint the native outer frame.
        if event is not None and getattr(event, "widget", self.window) is not self.window:
            return
        if self._revealing or self._pending is not None:
            return
        try:
            self._pending = self.window.after_idle(self.refresh)
        except Exception:
            self._pending = None

    def _queue_frame(self, _event: Any = None) -> None:
        # Descendant Expose events arrive after Tk child widgets paint over the
        # shared HWND. Restore only the native outer stroke after that paint;
        # do not rescan descendants or reapply their geometry roles.
        if (
            self._revealing
            or self._pending is not None
            or self._frame_pending is not None
        ):
            return
        try:
            self._frame_pending = self.window.after_idle(self.refresh_frame)
        except Exception:
            self._frame_pending = None

    def refresh_frame(self) -> None:
        """Restore the native frame without touching descendant layout."""
        self._frame_pending = None
        visuals = active_desktop_visual_state()
        _set_windows_rounded_region(
            self.window,
            visuals.metrics.panel_corner_radius,
            top_level=True,
            frame_color=visuals.palette.border,
        )

    def refresh(self, *, effect_visible: bool = True) -> None:
        self._pending = None
        self.refresh_frame()
        self.refresh_effect(show=effect_visible)
        self._refresh_semantic_descendants()

    def refresh_effect(self, *, show: bool = True) -> None:
        """Apply only the outside effect, preserving every Tk child and layout."""
        import os

        visuals = active_desktop_visual_state()
        if os.name != "nt" or visuals.effects.style == "none" or visuals.effects.intensity <= 0:
            self._effect_prepared = False
            self._effect.hide()
            return
        try:
            geometry = _windows_widget_geometry(self.window, top_level=True)
            if geometry is None:
                self._effect_prepared = False
                self._effect.hide()
                return
            hwnd, width, height = geometry
            result = self._effect.refresh_hwnd(
                hwnd,
                width,
                height,
                visuals.metrics.panel_corner_radius,
                visuals.effects,
                str(visuals.token("_GLOW")),
                show=show,
            )
            self._effect_prepared = result in {"applied", "unchanged"}
            setattr(self.window, "_mo_window_effect_error", "")
        except Exception as exc:
            self._effect_prepared = False
            detail = f"{type(exc).__name__}: {exc}"
            if getattr(self.window, "_mo_window_effect_error", "") != detail:
                setattr(self.window, "_mo_window_effect_error", detail)
                _LOG.error("Desktop window-effect adapter failed: %s", detail)

    def begin_reveal(self) -> None:
        """Suppress Map/Expose retries during one explicit first-frame settle."""
        self._revealing = True
        for name in ("_pending", "_frame_pending"):
            pending = getattr(self, name)
            if pending is not None:
                try:
                    self.window.after_cancel(pending)
                except Exception:
                    pass
                setattr(self, name, None)

    def end_reveal(self) -> None:
        self._revealing = False

    def show_prepared_effect(self) -> None:
        """Publish the cached outside treatment after the Tk window is visible."""
        if not self._effect_prepared:
            return
        try:
            self._effect.show()
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if getattr(self.window, "_mo_window_effect_error", "") != detail:
                setattr(self.window, "_mo_window_effect_error", detail)
                _LOG.error("Desktop window-effect adapter failed: %s", detail)

    def _refresh_semantic_descendants(self) -> None:
        """Enroll native descendants whose semantic role is unambiguous.

        Frames stay opt-in because many are flat layout structure. Controls and
        table/tab containers have one role regardless of their call site, so the
        window owner can cover children added after its initial construction.
        """
        control_classes = {"Button", "Entry", "TEntry", "TCombobox", "Listbox"}
        panel_classes = {"Treeview", "TNotebook"}
        pending = [self.window]
        while pending:
            parent = pending.pop()
            try:
                children = list(parent.winfo_children())
            except Exception:
                continue
            pending.extend(children)
            for child in children:
                try:
                    widget_class = str(child.winfo_class())
                    if widget_class == "Button":
                        install_desktop_button_corners(child)
                    elif widget_class in control_classes:
                        install_desktop_role_corners(child, "button")
                    elif widget_class in panel_classes:
                        install_desktop_role_corners(child, "panel")
                except Exception:
                    continue


def install_desktop_window_corners(window: Any) -> DesktopWindowCorners:
    """Install or refresh the shared corner adapter on one Toplevel."""
    controller = getattr(window, "_mo_window_corners", None)
    if isinstance(controller, DesktopWindowCorners):
        _DESKTOP_WINDOW_CONTROLLERS.add(controller)
        controller.refresh()
        return controller
    controller = DesktopWindowCorners(window)
    try:
        setattr(window, "_mo_window_corners", controller)
    except Exception:
        pass
    return controller


def refresh_all_desktop_window_corners() -> None:
    """Refresh every live shared window controller after a full visual change."""
    for controller in tuple(_DESKTOP_WINDOW_CONTROLLERS):
        controller.refresh()


def refresh_all_desktop_window_effects() -> None:
    """Refresh only native outside effects after type or intensity changes."""
    for controller in tuple(_DESKTOP_WINDOW_CONTROLLERS):
        controller.refresh_effect()


def reveal_desktop_window(window: Any, *, focus: bool = False) -> None:
    """Publish one fully settled Desktop window without an intermediate frame."""
    controller = getattr(window, "_mo_window_corners", None)
    if not isinstance(controller, DesktopWindowCorners):
        controller = install_desktop_window_corners(window)
    else:
        _DESKTOP_WINDOW_CONTROLLERS.add(controller)

    try:
        was_viewable = bool(window.winfo_viewable())
    except Exception:
        was_viewable = False
    previous_alpha: Any = None
    alpha_hidden = False
    if not was_viewable:
        try:
            previous_alpha = window.attributes("-alpha")
            window.attributes("-alpha", 0.0)
            alpha_hidden = True
        except Exception:
            previous_alpha = None

    controller.begin_reveal()
    alpha_restored = False
    try:
        window.deiconify()
        if not was_viewable:
            # A withdrawn Tk Toplevel has only its temporary client HWND.
            # Let Windows create the real wrapper while it remains transparent.
            window.update_idletasks()
        controller.refresh(effect_visible=not alpha_hidden)
        window.lift()
        if alpha_hidden:
            window.attributes("-alpha", previous_alpha)
            alpha_restored = True
            controller.show_prepared_effect()
        if focus:
            window.focus_force()
    finally:
        if alpha_hidden and not alpha_restored:
            try:
                window.attributes("-alpha", previous_alpha)
            except Exception:
                pass
        controller.end_reveal()


class _DesktopWidgetProxy:
    """Small composition proxy that keeps tkinter imports off module import."""

    widget: Any

    def __getattr__(self, name: str) -> Any:
        return getattr(self.widget, name)


class _DesktopRoleCorners:
    def __init__(self, widget: Any, role: str) -> None:
        self.widget = widget
        self.role = role
        self._pending: Any = None
        try:
            widget.bind("<Configure>", self._queue, add="+")
            widget.bind("<Expose>", self._queue, add="+")
        except Exception:
            pass
        self.refresh()

    def _queue(self, _event: Any = None) -> None:
        if self._pending is None:
            try:
                self._pending = self.widget.after_idle(self.refresh)
            except Exception:
                self._pending = None

    def refresh(self) -> None:
        self._pending = None
        metrics = active_desktop_visual_state().metrics
        radius = (
            metrics.panel_corner_radius
            if self.role == "panel"
            else metrics.button_corner_radius
        )
        frame_color: str | None = None
        frame_thickness = 1
        try:
            configured_thickness = int(float(self.widget.cget("highlightthickness")))
            configured_color = str(self.widget.cget("highlightbackground")).strip()
            if (
                configured_thickness > 0
                and len(configured_color) == 7
                and configured_color.startswith("#")
            ):
                int(configured_color[1:], 16)
                frame_color = configured_color
                frame_thickness = configured_thickness
        except Exception:
            frame_color = None
        _set_windows_rounded_region(
            self.widget,
            radius,
            top_level=False,
            frame_color=frame_color,
            frame_thickness=frame_thickness,
        )


def install_desktop_role_corners(widget: Any, role: str) -> Any:
    """Enroll an existing bounded widget as a panel or control role."""
    clean = str(role or "").strip().lower()
    if clean not in {"panel", "button"}:
        raise ValueError("Desktop visual role must be panel or button")
    controller = getattr(widget, "_mo_role_corners", None)
    if isinstance(controller, _DesktopRoleCorners):
        controller.role = clean
        controller.refresh()
        return controller
    controller = _DesktopRoleCorners(widget, clean)
    setattr(widget, "_mo_role_corners", controller)
    return controller


class DesktopRoundedPanel(_DesktopWidgetProxy):
    """Bounded panel whose padding and radius come only from visual state."""

    def __init__(self, parent: Any, *, palette: DesktopPalette | None = None, **kwargs: Any) -> None:
        import tkinter as tk

        state = active_desktop_visual_state()
        p = palette or state.palette
        options = {"bg": p.card, "padx": state.metrics.panel_padding,
                   "pady": state.metrics.panel_padding, "highlightthickness": 1,
                   "highlightbackground": p.border}
        options.update(kwargs)
        self.widget = tk.Frame(parent, **options)
        self.content = self.widget
        self._corners = _DesktopRoleCorners(self.widget, "panel")


class DesktopButton(_DesktopWidgetProxy):
    """Classic Tk action using the one button padding/radius role."""

    def __init__(
        self,
        parent: Any,
        *,
        text: str = "",
        command: Callable[[], Any] | None = None,
        palette: DesktopPalette | None = None,
        danger: bool = False,
        **kwargs: Any,
    ) -> None:
        import tkinter as tk

        state = active_desktop_visual_state()
        p = palette or state.palette
        background = p.error if danger else p.entry
        options = {
            "text": text,
            "command": command,
            "bg": background,
            "fg": p.text,
            "activebackground": p.accent,
            "activeforeground": p.card,
            "relief": "flat",
            "bd": 0,
            "padx": state.metrics.button_padding,
            "pady": state.metrics.button_padding,
            "cursor": "hand2",
            "font": TYPOGRAPHY.body,
        }
        options.update(kwargs)
        self.widget = tk.Button(parent, **options)
        self._corners = install_desktop_button_corners(self.widget)


class DesktopFieldShell(DesktopRoundedPanel):
    """Rounded input/editor shell using the control geometry role."""

    def __init__(self, parent: Any, *, palette: DesktopPalette | None = None, **kwargs: Any) -> None:
        p = palette or active_desktop_visual_state().palette
        super().__init__(
            parent,
            palette=p,
            bg=p.entry,
            padx=active_desktop_visual_state().metrics.button_padding,
            pady=active_desktop_visual_state().metrics.button_padding,
            **kwargs,
        )
        self._corners.role = "button"
        self._corners.refresh()

    def apply_palette(self, palette: DesktopPalette) -> None:
        self.widget.configure(
            bg=palette.entry,
            highlightbackground=palette.border,
        )
        self._corners.refresh()


class DesktopSelect(_DesktopWidgetProxy):
    """Readonly selection control enrolled in shared styles and geometry."""

    def __init__(self, parent: Any, *, style: str, **kwargs: Any) -> None:
        from tkinter import ttk

        self.widget = ttk.Combobox(parent, state="readonly", style=style, **kwargs)
        self._corners = _DesktopRoleCorners(self.widget, "button")


class DesktopTabs(_DesktopWidgetProxy):
    """Keyboard-accessible Notebook enrolled in the shared tab/panel roles."""

    def __init__(self, parent: Any, *, style: str, **kwargs: Any) -> None:
        from tkinter import ttk

        self.widget = ttk.Notebook(parent, style=style, **kwargs)
        self._corners = _DesktopRoleCorners(self.widget, "panel")


class DesktopSelectableRow(DesktopButton):
    """Full-width standalone row with the button hit/radius role."""

    def __init__(self, parent: Any, **kwargs: Any) -> None:
        kwargs.setdefault("anchor", "w")
        super().__init__(parent, **kwargs)


class DesktopTableShell(DesktopRoundedPanel):
    """Rounded table container; its grouped rows intentionally remain flat."""

    pass


class DesktopTitleBar:
    """Cube-branded title row with consistent utility-window controls."""

    def __init__(
        self,
        parent: Any,
        *,
        window: Any,
        palette: DesktopPalette,
        title: str,
        subtitle: str = "",
        show_pin: bool = False,
        pinned: bool = False,
        show_minimize: bool = True,
        on_close: Callable[[], Any] | None = None,
    ) -> None:
        import tkinter as tk
        from PIL import ImageTk
        from interface.desktop_brand import make_four_cube_icon

        self.window = window
        self.palette = palette
        install_desktop_window_corners(window)
        self._pinned = bool(pinned)
        self._icon_photos: dict[tuple[Any, ...], Any] = {}
        self._controls: list[dict[str, Any]] = []
        self._hover_after: Any = None
        try:
            self._borderless = bool(window.overrideredirect())
        except Exception:
            self._borderless = False
        self._drag_origin: tuple[int, int, int, int] | None = None
        self.frame = tk.Frame(parent, bg=palette.card, height=WINDOW_CHROME["height"])
        self.frame.pack_propagate(False)
        self.frame.bind("<Destroy>", self._stop_controls)

        self._brand_photo = ImageTk.PhotoImage(
            make_four_cube_icon(28, palette=palette),
            master=window,
        )
        brand = tk.Label(
            self.frame,
            image=self._brand_photo,
            bg=palette.card,
            bd=0,
        )
        brand.pack(side="left", padx=(WINDOW_CHROME["inset"], SPACING.control))
        self._brand_label = brand
        labels = tk.Frame(self.frame, bg=palette.card)
        self._labels_frame = labels
        labels.pack(side="left", fill="y")
        self.title_label = tk.Label(
            labels,
            text=title,
            bg=palette.card,
            fg=palette.text,
            font=TYPOGRAPHY.section,
            anchor="w",
        )
        self.title_label.pack(side="left")
        self.status_label = tk.Label(
            labels,
            text=subtitle,
            bg=palette.card,
            fg=palette.muted,
            font=TYPOGRAPHY.small,
            anchor="w",
        )
        if subtitle:
            self.status_label.pack(side="left", padx=(SPACING.control, 0))

        controls = tk.Frame(self.frame, bg=palette.card)
        self._controls_frame = controls
        controls.pack(
            side="right",
            padx=(0, WINDOW_CHROME["inset"]),
        )
        self.pin_button = None
        if show_pin:
            self.pin_button = self._control(
                controls, "pin", self._toggle_pin
            )
        if show_minimize:
            self._control(controls, "minimize", self._minimize)
        self._control(controls, "close", on_close or self._close)

        for widget in (self.frame, brand, labels, self.title_label, self.status_label):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)

    def _paint_control(self, control: dict[str, Any]) -> None:
        from PIL import Image, ImageColor, ImageDraw, ImageTk
        from interface.desktop_brand import make_glyph_icon

        name = control["icon"]
        pinned = name == "pin" and self._pinned
        level = control["level"]
        key = (name, pinned, level, control["pressed"], control["focused"])
        photo = self._icon_photos.get(key)
        if photo is None:
            side = WINDOW_CHROME["button"]
            scale = 4
            palette = self.palette
            base = ImageColor.getrgb(palette.card)
            ink = palette.error if name == "close" else palette.accent if pinned else palette.text
            amount = WINDOW_CHROME["close_mix" if name == "close" else "hover_mix"] / 100
            amount *= max(level / 10, 1 if pinned else 0)
            target = ImageColor.getrgb(ink)
            fill = tuple(round(a + (b-a) * amount) for a, b in zip(base, target))
            if control["pressed"]:
                fill = ImageColor.getrgb(palette.entry)
            radius = active_desktop_visual_state().metrics.button_corner_radius * scale
            image = Image.new("RGBA", (side*scale, side*scale), palette.card)
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((scale, scale, (side-1)*scale, (side-1)*scale), radius=radius,
                                   fill=fill, outline=palette.accent if control["focused"] else None,
                                   width=2*scale)
            glyph = make_glyph_icon("pin_filled" if pinned else name, WINDOW_CHROME["icon"]*scale,
                                   color=ink if level or pinned else palette.muted)
            offset = (side*scale - glyph.width)//2
            image.alpha_composite(glyph, (offset, offset))
            photo = ImageTk.PhotoImage(
                image.resize((side, side), Image.Resampling.LANCZOS), master=self.window
            )
            self._icon_photos[key] = photo
        control["button"].configure(image=photo)

    def _animate_controls(self) -> None:
        self._hover_after = None
        moving = False
        for control in self._controls:
            target = 10 if control["hovered"] else 0
            progress = min(1, (time.monotonic() - control["started"]) * 1000 / WINDOW_CHROME["hover_ms"])
            level = round(control["origin"] + (target - control["origin"]) * progress)
            if control["level"] != level:
                control["level"] = level
                self._paint_control(control)
            moving |= progress < 1 and control["origin"] != target
        if moving:
            self._hover_after = self.frame.after(WINDOW_CHROME["hover_ms"]//10, self._animate_controls)

    def _stop_controls(self, _event: Any = None) -> None:
        if self._hover_after is not None:
            self.frame.after_cancel(self._hover_after)
            self._hover_after = None

    def _control(
        self,
        parent: Any,
        icon: str,
        command: Callable[[], Any],
    ) -> Any:
        import tkinter as tk

        button = tk.Button(
            parent,
            command=command,
            bg=self.palette.card,
            activebackground=self.palette.card,
            relief="flat",
            bd=0, highlightthickness=0, padx=0, pady=0,
            width=WINDOW_CHROME["button"], height=WINDOW_CHROME["button"], takefocus=True,
            cursor="hand2",
        )
        control = dict(button=button, icon=icon, level=0, hovered=False, pressed=False, focused=False,
                       started=0.0, origin=0)
        self._controls.append(control)
        def change(name: str, value: bool) -> None:
            if name == "hovered":
                control["started"] = time.monotonic()
                control["origin"] = control["level"]
            control[name] = value
            if name == "hovered" and not value:
                control["pressed"] = False
            self._paint_control(control)
            if self._hover_after is None:
                self._animate_controls()
        for event, name, value in (("<Enter>", "hovered", True), ("<Leave>", "hovered", False),
                                    ("<ButtonPress-1>", "pressed", True), ("<ButtonRelease-1>", "pressed", False),
                                    ("<FocusIn>", "focused", True), ("<FocusOut>", "focused", False)):
            button.bind(event, lambda _event, key=name, state=value: change(key, state))
        button.pack(side="left", padx=(WINDOW_CHROME["gap"] if len(self._controls) > 1 else 0, 0))
        self._paint_control(control)
        return button

    def apply_palette(self, palette: DesktopPalette) -> None:
        """Retheme the existing title bar without replacing its window owner."""
        from PIL import ImageTk
        from interface.desktop_brand import make_four_cube_icon

        self.palette = palette
        self._icon_photos.clear()
        self._brand_photo = ImageTk.PhotoImage(
            make_four_cube_icon(28, palette=palette),
            master=self.window,
        )
        self._brand_label.configure(image=self._brand_photo, bg=palette.card)
        for frame in (self.frame, self._labels_frame, self._controls_frame):
            frame.configure(bg=palette.card)
        self.title_label.configure(bg=palette.card, fg=palette.text)
        self.status_label.configure(bg=palette.card, fg=palette.muted)
        for control in self._controls:
            control["button"].configure(
                bg=palette.card,
                activebackground=palette.card,
            )
            self._paint_control(control)

    def _toggle_pin(self) -> None:
        self._pinned = not self._pinned
        try:
            self.window.attributes("-topmost", self._pinned)
        except Exception:
            pass
        for control in self._controls:
            if control["icon"] == "pin":
                self._paint_control(control)

    @property
    def pinned(self) -> bool:
        return self._pinned

    def toggle_pin(self) -> None:
        self._toggle_pin()

    def _minimize(self) -> None:
        if not self._borderless:
            self.window.iconify()
            return
        try:
            self.window.overrideredirect(False)
            self.window.update_idletasks()
            self.window.iconify()

            def restore(_event: Any = None) -> None:
                try:
                    if str(self.window.state()) != "iconic":
                        self.window.after_idle(
                            lambda: self.window.overrideredirect(True)
                        )
                except Exception:
                    pass

            self.window.bind("<Map>", restore, add="+")
        except Exception:
            self.window.iconify()

    def _close(self) -> None:
        self.window.withdraw()

    def _start_drag(self, event: Any) -> None:
        self._drag_origin = (
            int(event.x_root),
            int(event.y_root),
            int(self.window.winfo_x()),
            int(self.window.winfo_y()),
        )

    def _drag(self, event: Any) -> None:
        if self._drag_origin is None:
            return
        pointer_x, pointer_y, window_x, window_y = self._drag_origin
        x = window_x + int(event.x_root) - pointer_x
        y = window_y + int(event.y_root) - pointer_y
        self.window.geometry(f"+{x}+{y}")

    def set_status(self, text: str, *, color: str | None = None) -> None:
        self.status_label.configure(text=text, fg=color or self.palette.muted)
        if text and not self.status_label.winfo_manager():
            self.status_label.pack(side="left", padx=(SPACING.control, 0))

    def pack(self, *args: Any, **kwargs: Any) -> Any:
        return self.frame.pack(*args, **kwargs)


class DesktopResizeGrip:
    """Bottom-right resize affordance for borderless Desktop utility windows."""

    def __init__(self, parent: Any, *, window: Any, palette: DesktopPalette) -> None:
        import tkinter as tk

        from PIL import ImageTk
        from interface.desktop_brand import make_glyph_icon

        self.window = window
        self._origin: tuple[int, int, int, int] | None = None
        self._photo = ImageTk.PhotoImage(
            make_glyph_icon("resize", 10, color=palette.muted), master=window
        )
        self.label = tk.Label(
            parent,
            image=self._photo,
            bg=palette.card,
            cursor="size_nw_se",
            bd=0,
            padx=SPACING.compact,
            pady=SPACING.micro,
        )
        self.label.bind("<ButtonPress-1>", self._start)
        self.label.bind("<B1-Motion>", self._resize)

    def apply_palette(self, palette: DesktopPalette) -> None:
        from PIL import ImageTk
        from interface.desktop_brand import make_glyph_icon

        self._photo = ImageTk.PhotoImage(
            make_glyph_icon("resize", 10, color=palette.muted), master=self.window
        )
        self.label.configure(image=self._photo, bg=palette.card)

    def _start(self, event: Any) -> None:
        self._origin = (
            int(event.x_root),
            int(event.y_root),
            int(self.window.winfo_width()),
            int(self.window.winfo_height()),
        )

    def _resize(self, event: Any) -> None:
        if self._origin is None:
            return
        pointer_x, pointer_y, width, height = self._origin
        try:
            minimum_width, minimum_height = self.window.minsize()
        except Exception:
            minimum_width, minimum_height = (320, 240)
        target_width = max(minimum_width, width + int(event.x_root) - pointer_x)
        target_height = max(minimum_height, height + int(event.y_root) - pointer_y)
        self.window.geometry(f"{target_width}x{target_height}")

    def place(self, *args: Any, **kwargs: Any) -> Any:
        return self.label.place(*args, **kwargs)


class DesktopSwitch:
    """A real two-state switch with one callback and no native-theme escape."""

    def __init__(
        self,
        parent: Any,
        *,
        value: bool,
        command: Callable[[bool], Any],
        palette: DesktopPalette,
        text: str = "",
        enabled: bool = True,
    ) -> None:
        import tkinter as tk

        self._palette = palette
        self._command = command
        self._value = bool(value)
        self._enabled = bool(enabled)
        self._amount = float(self._value)
        self._after: Any = None
        self._started = 0.0
        self._source = self._amount
        self.frame = tk.Frame(parent, bg=palette.card)
        if text:
            self.label = tk.Label(
                self.frame,
                text=text,
                bg=palette.card,
                fg=palette.text,
                font=TYPOGRAPHY.body_large,
            )
            self.label.pack(side="left")
            self.label.bind("<Button-1>", self._clicked)
        else:
            self.label = None
        self.canvas = tk.Canvas(
            self.frame,
            width=42,
            height=22,
            bg=palette.card,
            bd=0,
            highlightthickness=0,
        )
        self.canvas.pack(side="right" if text else "left", padx=(SPACING.content if text else SPACING.none, SPACING.none))
        self.canvas.bind("<Button-1>", self._clicked)
        self.canvas.bind("<space>", self._clicked)
        self.canvas.bind("<Destroy>", self._cancel_animation)
        self.canvas.configure(takefocus=True)
        self._paint()

    def _clicked(self, _event: Any = None) -> None:
        self.toggle()

    def _paint(self) -> None:
        from PIL import ImageTk

        p = self._palette
        self.canvas.delete("all")
        self._photo = ImageTk.PhotoImage(self._image(p, self._amount, self._enabled), master=self.canvas)
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        cursor = "hand2" if self._enabled else "arrow"
        self.canvas.configure(cursor=cursor)
        if self.label is not None:
            self.label.configure(cursor=cursor, fg=p.text if self._enabled else p.muted)

    @staticmethod
    def _image(palette: DesktopPalette, amount: float, enabled: bool) -> Any:
        """One antialiased switch silhouette for every native Desktop consumer."""
        from PIL import Image, ImageColor, ImageDraw

        scale = 4
        image = Image.new("RGBA", (42*scale, 22*scale))
        draw = ImageDraw.Draw(image)
        def blend(first: str, second: str) -> tuple[int, int, int]:
            return tuple(round(a+(b-a)*amount) for a, b in
                         zip(ImageColor.getrgb(first), ImageColor.getrgb(second)))
        track = blend(palette.border, palette.accent) if enabled else palette.entry
        # A switch's capsule and circular thumb express its two-state travel;
        # they are structural geometry, independent of panel corner preferences.
        radius = 9*scale
        draw.rounded_rectangle((scale, 2*scale, 41*scale, 20*scale), radius=radius, fill=track)
        x = (3+21*amount)*scale
        draw.ellipse((x, 4*scale, x+14*scale, 18*scale),
                     fill=palette.text if enabled else palette.muted)
        return image.resize((42, 22), Image.Resampling.LANCZOS)

    def _cancel_animation(self, _event: Any = None) -> None:
        if self._after is not None:
            self.canvas.after_cancel(self._after)
            self._after = None

    def _animate(self) -> None:
        import time
        from mo_desktop.cube_motion import _ease_out
        from mo_desktop.design import DEFAULT_DESKTOP_PANEL_DESIGN

        self._after = None
        progress = min(1.0, (time.monotonic()-self._started)/(DEFAULT_DESKTOP_PANEL_DESIGN.transition_ms/1000))
        self._amount = self._source+(float(self._value)-self._source)*_ease_out(progress)
        self._paint()
        if progress < 1.0:
            self._after = self.canvas.after(16, self._animate)

    def get(self) -> bool:
        return self._value

    def set(self, value: bool, *, notify: bool = False) -> None:
        import time
        changed = self._value != bool(value)
        if not changed:
            return
        self._value = bool(value)
        self._cancel_animation()
        self._source, self._started = self._amount, time.monotonic()
        self._animate()
        if notify:
            self._command(self._value)

    def toggle(self) -> None:
        if not self._enabled:
            return
        self.set(not self._value, notify=True)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._paint()

    def pack(self, *args: Any, **kwargs: Any) -> Any:
        return self.frame.pack(*args, **kwargs)

    def grid(self, *args: Any, **kwargs: Any) -> Any:
        return self.frame.grid(*args, **kwargs)

    def destroy(self) -> None:
        self.frame.destroy()


def themed_input_dialog(
    parent: Any,
    *,
    title: str,
    prompt: str,
    palette: DesktopPalette,
    initial: str = "",
    allow_blank: bool = False,
) -> str | None:
    """Ask for one string in a fully themed modal owned by ``parent``."""
    import tkinter as tk

    result: dict[str, str | None] = {"value": None}
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title(title)
    dialog.configure(bg=palette.card)
    dialog.resizable(False, False)
    dialog.transient(parent)
    try:
        dialog.attributes("-topmost", True)
    except Exception:
        pass

    body = tk.Frame(
        dialog,
        bg=palette.card,
        highlightthickness=1,
        highlightbackground=palette.border,
    )
    body.pack(fill="both", expand=True)
    tk.Label(
        body,
        text=prompt,
        bg=palette.card,
        fg=palette.text,
        anchor="w",
        justify="left",
        font=TYPOGRAPHY.body_large,
    ).pack(fill="x", padx=SPACING.expanded, pady=(SPACING.full_screen, SPACING.inset))
    entry = tk.Entry(
        body,
        bg=palette.entry,
        fg=palette.text,
        insertbackground=palette.text,
        selectbackground=palette.accent,
        selectforeground=palette.card,
        relief="flat",
        highlightthickness=1,
        highlightbackground=palette.border,
        highlightcolor=palette.accent,
        font=TYPOGRAPHY.body_large,
    )
    entry.pack(fill="x", padx=SPACING.expanded, ipady=SPACING.control)
    entry.insert(0, initial)
    entry.select_range(0, "end")

    buttons = tk.Frame(body, bg=palette.card)
    buttons.pack(fill="x", padx=SPACING.expanded, pady=SPACING.full_screen)

    def close(value: str | None) -> None:
        if value is not None and not allow_blank and not value.strip():
            entry.focus_set()
            return
        result["value"] = value
        try:
            dialog.grab_release()
        except Exception:
            pass
        dialog.destroy()

    tk.Button(
        buttons,
        text="Cancel",
        command=lambda: close(None),
        bg=palette.entry,
        fg=palette.text,
        activebackground=palette.border,
        activeforeground=palette.text,
        relief="flat",
        bd=0,
        padx=SPACING.roomy,
        pady=SPACING.item,
    ).pack(side="right")
    tk.Button(
        buttons,
        text="OK",
        command=lambda: close(entry.get()),
        bg=palette.accent,
        fg=palette.card,
        activebackground=palette.text,
        activeforeground=palette.card,
        relief="flat",
        bd=0,
        padx=SPACING.spacious,
        pady=SPACING.item,
    ).pack(side="right", padx=(SPACING.none, SPACING.inset))

    dialog.bind("<Escape>", lambda _event: close(None))
    dialog.bind("<Return>", lambda _event: close(entry.get()))
    dialog.protocol("WM_DELETE_WINDOW", lambda: close(None))
    _centre_dialog(dialog, parent)
    reveal_desktop_window(dialog)
    try:
        dialog.grab_set()
    except Exception:
        pass
    entry.focus_set()
    parent.wait_window(dialog)
    return result["value"]


def _create_themed_modal(parent: Any, title: str, palette: DesktopPalette) -> Any:
    """Create the shared native modal shell used by choice/confirm dialogs."""
    import tkinter as tk

    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title(title)
    dialog.configure(bg=palette.card)
    dialog.resizable(False, False)
    dialog.transient(parent)
    try:
        dialog.attributes("-topmost", True)
    except Exception:
        pass
    return dialog


def _close_themed_modal(dialog: Any) -> None:
    try:
        dialog.grab_release()
    except Exception:
        pass
    dialog.destroy()


def _add_modal_action_buttons(
    dialog: Any,
    palette: DesktopPalette,
    *,
    cancel_label: str,
    confirm_label: str,
    on_cancel: Callable[[], None],
    on_confirm: Callable[[], None],
) -> None:
    import tkinter as tk

    buttons = tk.Frame(dialog, bg=palette.card)
    buttons.pack(fill="x", padx=SPACING.expanded, pady=SPACING.full_screen)
    tk.Button(
        buttons,
        text=cancel_label,
        command=on_cancel,
        bg=palette.entry,
        fg=palette.text,
        relief="flat",
        bd=0,
        padx=SPACING.roomy,
        pady=SPACING.item,
    ).pack(side="right")
    tk.Button(
        buttons,
        text=confirm_label,
        command=on_confirm,
        bg=palette.accent,
        fg=palette.card,
        relief="flat",
        bd=0,
        padx=SPACING.spacious,
        pady=SPACING.item,
    ).pack(side="right", padx=(SPACING.none, SPACING.inset))


def _run_themed_modal(dialog: Any, parent: Any, focus: Any) -> None:
    _centre_dialog(dialog, parent)
    reveal_desktop_window(dialog)
    try:
        dialog.grab_set()
    except Exception:
        pass
    focus.focus_set()
    parent.wait_window(dialog)


def themed_choice_dialog(
    parent: Any,
    *,
    title: str,
    prompt: str,
    choices: Iterable[tuple[str, T]],
    palette: DesktopPalette,
    initial: T | None = None,
) -> T | None:
    """Choose one value from a themed list without a native menu."""
    import tkinter as tk

    options = list(choices)
    if not options:
        return None
    result: dict[str, T | None] = {"value": None}
    dialog = _create_themed_modal(parent, title, palette)

    tk.Label(
        dialog,
        text=prompt,
        bg=palette.card,
        fg=palette.text,
        anchor="w",
        font=TYPOGRAPHY.body_large,
    ).pack(fill="x", padx=SPACING.expanded, pady=(SPACING.full_screen, SPACING.inset))
    choices_box = tk.Listbox(
        dialog,
        bg=palette.entry,
        fg=palette.text,
        selectbackground=palette.accent,
        selectforeground=palette.card,
        highlightthickness=1,
        highlightbackground=palette.border,
        relief="flat",
        activestyle="none",
        width=42,
        height=min(8, max(3, len(options))),
        exportselection=False,
        font=TYPOGRAPHY.body_large,
    )
    choices_box.pack(fill="both", expand=True, padx=SPACING.expanded)
    selected = 0
    for index, (label, value) in enumerate(options):
        choices_box.insert("end", label)
        if value == initial:
            selected = index
    choices_box.selection_set(selected)
    choices_box.activate(selected)

    def close(accept: bool) -> None:
        if accept and choices_box.curselection():
            result["value"] = options[int(choices_box.curselection()[0])][1]
        _close_themed_modal(dialog)

    _add_modal_action_buttons(
        dialog,
        palette,
        cancel_label="Cancel",
        confirm_label="Choose",
        on_cancel=lambda: close(False),
        on_confirm=lambda: close(True),
    )
    dialog.bind("<Escape>", lambda _event: close(False))
    dialog.bind("<Return>", lambda _event: close(True))
    choices_box.bind("<Double-Button-1>", lambda _event: close(True))
    dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))
    _run_themed_modal(dialog, parent, choices_box)
    return result["value"]


def themed_confirm_dialog(
    parent: Any,
    *,
    title: str,
    prompt: str,
    palette: DesktopPalette,
    confirm_label: str = "OK",
    cancel_label: str = "Cancel",
) -> bool:
    """Ask a yes/no question with the active skin instead of a native dialog."""
    import tkinter as tk

    result = {"value": False}
    dialog = _create_themed_modal(parent, title, palette)

    tk.Label(
        dialog,
        text=prompt,
        bg=palette.card,
        fg=palette.text,
        anchor="w",
        justify="left",
        wraplength=380,
        font=TYPOGRAPHY.body_large,
    ).pack(fill="x", padx=SPACING.expanded, pady=(SPACING.full_screen, SPACING.inset))

    def close(accept: bool) -> None:
        result["value"] = accept
        _close_themed_modal(dialog)

    _add_modal_action_buttons(
        dialog,
        palette,
        cancel_label=cancel_label,
        confirm_label=confirm_label,
        on_cancel=lambda: close(False),
        on_confirm=lambda: close(True),
    )
    dialog.bind("<Escape>", lambda _event: close(False))
    dialog.bind("<Return>", lambda _event: close(True))
    dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))
    _run_themed_modal(dialog, parent, dialog)
    return bool(result["value"])


def themed_message_dialog(
    parent: Any,
    *,
    title: str,
    message: str,
    palette: DesktopPalette,
    button_label: str = "OK",
) -> None:
    """Show a themed notice/error message without a native light dialog."""
    import tkinter as tk

    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title(title)
    dialog.configure(bg=palette.card)
    dialog.resizable(False, False)
    dialog.transient(parent)
    try:
        dialog.attributes("-topmost", True)
    except Exception:
        pass

    tk.Label(
        dialog,
        text=message,
        bg=palette.card,
        fg=palette.text,
        anchor="w",
        justify="left",
        wraplength=380,
        font=TYPOGRAPHY.body_large,
    ).pack(fill="x", padx=SPACING.expanded, pady=(SPACING.full_screen, SPACING.inset))

    def close() -> None:
        try:
            dialog.grab_release()
        except Exception:
            pass
        dialog.destroy()

    buttons = tk.Frame(dialog, bg=palette.card)
    buttons.pack(fill="x", padx=SPACING.expanded, pady=SPACING.full_screen)
    tk.Button(
        buttons,
        text=button_label,
        command=close,
        bg=palette.accent,
        fg=palette.card,
        relief="flat",
        bd=0,
        padx=SPACING.spacious,
        pady=SPACING.item,
    ).pack(side="right")
    dialog.bind("<Escape>", lambda _event: close())
    dialog.bind("<Return>", lambda _event: close())
    dialog.protocol("WM_DELETE_WINDOW", close)
    _centre_dialog(dialog, parent)
    reveal_desktop_window(dialog)
    try:
        dialog.grab_set()
    except Exception:
        pass
    dialog.focus_set()
    parent.wait_window(dialog)


def themed_context_menu(
    parent: Any,
    palette: Any,
    items: list[tuple[str, Any]],
    *,
    x: int,
    y: int,
) -> Any:
    """Show one skin-themed right-click menu at the pointer.

    Tk's own ``tk.Menu`` is an OS-native widget that ignores the skin, so this
    reuses the tray popup's language instead: a borderless topmost Toplevel of
    flat rows with explicit hover paint, dismissed on focus-out or Escape.
    Items are ``(label, command)``; a blank label draws a separator, a
    ``None`` command draws a disabled row that states why it is unavailable,
    and :class:`ThemedMenuSubmenu` opens a nested themed menu.
    """
    import tkinter as tk

    menus: list[Any] = []

    def dismiss(_event: Any = None) -> None:
        for popup in reversed(menus):
            try:
                popup.destroy()
            except Exception:
                pass
        menus.clear()

    def close_after(menu: Any) -> None:
        try:
            index = menus.index(menu)
        except ValueError:
            return
        for popup in reversed(menus[index + 1 :]):
            try:
                popup.destroy()
            except Exception:
                pass
        del menus[index + 1 :]

    def has_menu_focus(widget: Any) -> bool:
        while widget is not None:
            if widget in menus:
                return True
            widget = getattr(widget, "master", None)
        return False

    def dismiss_if_focus_left(_event: Any = None) -> None:
        def check() -> None:
            try:
                focused = parent.focus_displayof()
            except Exception:
                focused = None
            if not has_menu_focus(focused):
                dismiss()

        try:
            parent.after(20, check)
        except Exception:
            dismiss()

    def build_menu(
        entries: list[tuple[str, Any]],
        *,
        menu_x: int,
        menu_y: int,
        left_x: int | None = None,
    ) -> Any:
        menu = tk.Toplevel(parent)
        menus.append(menu)
        menu.withdraw()
        menu.overrideredirect(True)
        try:
            menu.attributes("-topmost", True)
        except Exception:
            pass
        menu.configure(bg=palette.border)
        body = tk.Frame(menu, bg=palette.card)
        body.pack(
            fill="both",
            expand=True,
            padx=SPACING.hairline,
            pady=SPACING.hairline,
        )

        def open_submenu(button: Any, submenu: ThemedMenuSubmenu) -> None:
            close_after(menu)
            button.update_idletasks()
            build_menu(
                list(submenu.items),
                menu_x=button.winfo_rootx() + button.winfo_width() - 2,
                menu_y=button.winfo_rooty(),
                left_x=button.winfo_rootx() + 2,
            )

        for label, command in entries:
            if not label:
                tk.Frame(body, bg=palette.border, height=1).pack(
                    fill="x", pady=SPACING.compact
                )
                continue
            danger = label.casefold().startswith(("delete", "trash"))
            if command is None:
                tk.Label(
                    body,
                    text=label,
                    anchor="w",
                    bg=palette.card,
                    fg=palette.muted,
                    padx=SPACING.spacious,
                    pady=SPACING.item,
                    font=TYPOGRAPHY.body,
                ).pack(fill="x")
                continue
            submenu = command if isinstance(command, ThemedMenuSubmenu) else None
            foreground = palette.error if danger else palette.text
            hover_fg = palette.error if danger else palette.accent

            def run(action: Any = command) -> None:
                dismiss()
                action()

            button = tk.Button(
                body,
                text=f"{label}   ›" if submenu is not None else label,
                command=(lambda: None) if submenu is not None else run,
                anchor="w",
                bg=palette.card,
                fg=foreground,
                activebackground=palette.entry,
                activeforeground=hover_fg,
                relief="flat",
                bd=0,
                padx=SPACING.spacious,
                pady=SPACING.item,
                cursor="hand2",
                font=TYPOGRAPHY.body,
            )
            if submenu is not None:
                button.configure(
                    command=lambda b=button, branch=submenu: open_submenu(b, branch)
                )

            def enter(
                _event: Any,
                *,
                b: Any = button,
                h: str = hover_fg,
                branch: ThemedMenuSubmenu | None = submenu,
            ) -> None:
                b.configure(bg=palette.entry, fg=h)
                if branch is None:
                    close_after(menu)
                else:
                    open_submenu(b, branch)

            button.bind("<Enter>", enter)
            button.bind(
                "<Leave>",
                lambda _event, b=button, f=foreground: b.configure(
                    bg=palette.card, fg=f
                ),
            )
            button.pack(fill="x")

        menu.update_idletasks()
        install_desktop_window_corners(menu).refresh()
        width = max(170, menu.winfo_reqwidth())
        height = menu.winfo_reqheight()
        screen_w, screen_h = menu.winfo_screenwidth(), menu.winfo_screenheight()
        target_x = int(menu_x)
        if left_x is not None and target_x + width > screen_w - 4:
            target_x = int(left_x) - width
        target_x = min(max(4, target_x), max(4, screen_w - width - 4))
        target_y = min(max(4, int(menu_y)), max(4, screen_h - height - 4))
        menu.geometry(f"{width}x{height}+{target_x}+{target_y}")
        reveal_desktop_window(menu)
        menu.bind("<FocusOut>", dismiss_if_focus_left)
        menu.bind("<Escape>", dismiss)
        return menu

    root_menu = build_menu(list(items), menu_x=int(x), menu_y=int(y))
    root_menu.focus_force()
    return root_menu


def _monitor_work_area(anchor: Any) -> tuple[int, int, int, int] | None:
    """Return the Windows work area containing a Tk window or native HWND."""
    if anchor is None:
        return None
    try:
        import ctypes
        import sys
        from ctypes import wintypes

        if sys.platform != "win32":
            return None

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        hwnd = anchor if isinstance(anchor, int) else anchor.winfo_id()
        monitor = user32.MonitorFromWindow(int(hwnd), 2)  # nearest monitor
        if not monitor:
            return None
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        area = info.rcWork
        if area.right <= area.left or area.bottom <= area.top:
            return None
        return int(area.left), int(area.top), int(area.right), int(area.bottom)
    except Exception:
        return None


def centre_desktop_window(
    window: Any,
    parent: Any = None,
    *,
    monitor_anchor: Any = None,
    work_area: tuple[int, int, int, int] | None = None,
) -> None:
    """Centre a Tk or native Desktop window on its parent or monitor work area.

    The Desktop's hidden Tk root reports a phantom 200x200 geometry and cannot
    identify the monitor where its visible Cube lives. Child dialogs therefore
    use their viewable parent, while independent utility windows use the Cube's
    native window only as a monitor anchor. ``winfo_screenwidth`` remains the
    safe fallback when no monitor-specific work area is available.
    """
    native = not callable(getattr(window, "winfo_width", None))
    if native:
        width, height = int(window.Width), int(window.Height)
        if work_area is None:
            work_area = _monitor_work_area(int(window.Handle.ToInt64()))
    else:
        window.update_idletasks()
        width, height = window.winfo_width(), window.winfo_height()
    if width <= 1:
        width = window.winfo_reqwidth()
    if height <= 1:
        height = window.winfo_reqheight()
    x = y = None
    if parent is not None:
        try:
            parent_width = parent.winfo_width()
            parent_height = parent.winfo_height()
            if bool(parent.winfo_viewable()) and parent_width > 1 and parent_height > 1:
                x = parent.winfo_rootx() + (parent_width - width) // 2
                y = parent.winfo_rooty() + (parent_height - height) // 2
        except Exception:
            x = y = None
    if x is None or y is None:
        if work_area is None:
            work_area = _monitor_work_area(monitor_anchor)
        if work_area is not None:
            if (len(work_area) != 4 or any(type(value) is not int for value in work_area)
                    or work_area[2] <= work_area[0] or work_area[3] <= work_area[1]):
                raise ValueError("Invalid monitor work area")
            left, top, right, bottom = work_area
            x = left + max(0, (right - left - width) // 2)
            y = top + max(0, (bottom - top - height) // 2)
    if x is None or y is None:
        if native:
            return
        try:
            x = max(0, (window.winfo_screenwidth() - width) // 2)
            y = max(0, (window.winfo_screenheight() - height) // 2)
        except Exception:
            return
    # Tk uses ``+-1190`` for an absolute negative virtual-desktop coordinate;
    # ``-1190`` means an offset from the right edge and lands on another monitor.
    if native:
        window.Left, window.Top = x, y
    else:
        window.geometry(f"+{x}+{y}")


def _centre_dialog(dialog: Any, parent: Any) -> None:
    centre_desktop_window(dialog, parent)


__all__ = [
    "DesktopButtonCorners",
    "DesktopResizeGrip",
    "DesktopWindowCorners",
    "DesktopWindowEffectLayer",
    "apply_windows_rounded_frame",
    "centre_desktop_window",
    "install_desktop_button_corners",
    "install_desktop_window_corners",
    "refresh_all_desktop_window_corners",
    "refresh_all_desktop_window_effects",
    "render_desktop_window_effect",
    "reveal_desktop_window",
    "themed_context_menu",
    "DesktopSwitch",
    "DesktopTitleBar",
    "themed_choice_dialog",
    "themed_input_dialog",
]
