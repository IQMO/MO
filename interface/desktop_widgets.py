"""Shared Desktop native geometry, effects and switch artwork.

Window adapters retain lazy imports for the resident Tk input/event lifecycle.
"""
from __future__ import annotations

import logging
from typing import Any
import weakref

from interface.desktop_ui import (
    DesktopPalette,
    DesktopVisualAdapterError,
    DesktopWindowEffects,
    active_desktop_visual_state,
)

_LOG = logging.getLogger(__name__)


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


def _cancel_widget_after(widget: Any, *handles: Any) -> None:
    """Cancel while Tcl still owns the widget's registered commands."""
    for handle in handles:
        if handle is not None:
            widget.after_cancel(handle)


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
        _cancel_widget_after(self.button, self._pending)
        self._pending = None
        self.button = None

    def _queue(self, _event: Any = None) -> None:
        if self.button is None or self._pending is not None:
            return
        try:
            self._pending = self.button.after_idle(self.refresh)
        except Exception:
            self._pending = None

    def refresh(self) -> None:
        if self.button is None:
            return
        _cancel_widget_after(self.button, self._pending)
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
        _cancel_widget_after(self.window, self._pending, self._frame_pending)
        self._pending = None
        self._frame_pending = None
        self.window = None
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
        if self.window is None or self._revealing or self._pending is not None:
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
            self.window is None
            or self._revealing
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
        if self.window is None:
            return
        _cancel_widget_after(self.window, self._frame_pending)
        self._frame_pending = None
        visuals = active_desktop_visual_state()
        _set_windows_rounded_region(
            self.window,
            visuals.metrics.panel_corner_radius,
            top_level=True,
            frame_color=visuals.palette.border,
        )

    def refresh(self, *, effect_visible: bool = True) -> None:
        if self.window is None:
            return
        _cancel_widget_after(self.window, self._pending)
        self._pending = None
        self.refresh_frame()
        self.refresh_effect(show=effect_visible)
        self._refresh_semantic_descendants()

    def refresh_effect(self, *, show: bool = True) -> None:
        """Apply only the outside effect, preserving every Tk child and layout."""
        import os

        if self.window is None:
            return
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
        _cancel_widget_after(self.window, self._pending, self._frame_pending)
        self._pending = self._frame_pending = None

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


class _DesktopRoleCorners:
    def __init__(self, widget: Any, role: str) -> None:
        self.widget = widget
        self.role = role
        self._pending: Any = None
        try:
            widget.bind("<Configure>", self._queue, add="+")
            widget.bind("<Expose>", self._queue, add="+")
            widget.bind("<Destroy>", self._destroyed, add="+")
        except Exception:
            pass
        self.refresh()

    def _destroyed(self, event: Any = None) -> None:
        if event is not None and getattr(event, "widget", self.widget) is not self.widget:
            return
        _cancel_widget_after(self.widget, self._pending)
        self._pending = None
        self.widget = None

    def _queue(self, _event: Any = None) -> None:
        if self.widget is not None and self._pending is None:
            try:
                self._pending = self.widget.after_idle(self.refresh)
            except Exception:
                self._pending = None

    def refresh(self) -> None:
        if self.widget is None:
            return
        _cancel_widget_after(self.widget, self._pending)
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
        hwnd = anchor if isinstance(anchor, int) else (anchor.hwnd if hasattr(anchor, "hwnd") else anchor.winfo_id())
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


def render_desktop_switch(palette: DesktopPalette, amount: float, enabled: bool) -> Any:
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


__all__ = [
    "render_desktop_switch",
    "DesktopButtonCorners",
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
]
