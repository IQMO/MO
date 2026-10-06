"""MO Desktop companion — behavior state machine (free / lock + click semantics).

Two modes drive the cube's motion; the click meanings are the SAME in both so
they never conflict:

  FREE  — the cube idles in place.
  LOCK  — the cube follows the cursor.

Click meanings come from the ``DesktopVisualProfile`` (so the mapping is data, not
hard-code). The defaults:

  left-click   = open the text input.
  right-click  = open the dashboard.
  double-click = open the four-cube MO app launcher.
  Ctrl-Ctrl    = toggle FREE <-> LOCK (chase); handled by the companion hotkey, not a
                 mouse click, so a click never fights the mode toggle.
  Alt,Alt+hold = push-to-talk voice, and Win+Alt+M = summon. Both live on the companion's
                 global key hook, never on a click — one gesture, one meaning.

This module owns only the *state machine*. It reuses the companion's capabilities
(``_display_input_dialog`` / ``_display_dashboard``) and the
cube's controls (``enable_follow`` / ``summon_to`` / ``play_emote``) — no duplicate
machinery. Single vs double click is resolved with a short delayed-single timer on
the resident GUI loop, so a single action never fires as half of a double.
"""
from __future__ import annotations

from typing import Any

from mo_desktop.design import DEFAULT_VISUAL_PROFILE, DesktopVisualProfile

_DOUBLE_MS = 260  # wait this long for a second click before treating one as a single


class CompanionModes:
    """Free/Lock click-and-mode controller for the cube companion."""

    FREE = "free"
    LOCK = "lock"

    def __init__(self, companion: Any, cube: Any, *, default_mode: str = "free",
                 visual_profile: DesktopVisualProfile | None = None) -> None:
        self._c = companion
        self._cube = cube
        self._visual_profile = visual_profile or DEFAULT_VISUAL_PROFILE
        self._mode = self.LOCK if str(default_mode or "").lower() == "lock" else self.FREE
        self._pending: Any = None
        self._cube.set_click_handlers(
            left=self._on_left, right=self._on_right, left_double=self._on_left_double,
            escape=lambda: self._c.hide(),
        )
        try:
            self._cube.set_resident(True)  # the companion is always on screen, not a transient pointer
        except Exception:
            pass
        self._apply_mode()

    @property
    def mode(self) -> str:
        return self._mode

    # ---- click plumbing (single vs double) ----
    def _win_after(self, ms: int, fn: Any) -> Any:
        try:
            return self._cube._gui.schedule(ms, fn)
        except Exception:
            fn()
            return None

    def _cancel_pending(self) -> None:
        if self._pending is not None:
            try:
                self._cube._gui.cancel(self._pending)
            except Exception:
                pass
            self._pending = None

    # click action name (from the visual profile) -> handler method
    _ACTIONS = {
        "input": "_open_input",
        "dashboard": "_open_dashboard",
        "launcher": "_open_launcher",
        "toggle_mode": "toggle_mode",
    }

    def _dispatch(self, action: str) -> None:
        name = self._ACTIONS.get(str(action or "").strip().lower())
        fn = getattr(self, name, None) if name else None
        if callable(fn):
            fn()

    def _on_left(self) -> None:
        self._cancel_pending()
        self._pending = self._win_after(_DOUBLE_MS, self._left_single)

    def _on_left_double(self) -> None:
        self._cancel_pending()
        if self._show_game_session():
            return
        self._dispatch(self._visual_profile.double_click_action)

    def _on_right(self) -> None:
        self._dispatch(self._visual_profile.right_click_action)

    def _left_single(self) -> None:
        self._pending = None
        if self._show_game_session():
            return
        activate = getattr(self._c, "_activate_notice", None)
        if callable(activate):
            try:
                if activate():
                    return
            except Exception:
                pass
        # Focus changes the cube's surroundings, never its established click contract.
        # Single-click still opens the configured composer; double-click owns the launcher.
        self._dispatch(self._visual_profile.left_click_action)

    def _show_game_session(self) -> bool:
        show = getattr(self._c, "show_game_session_panel", None)
        if not callable(show):
            return False
        try:
            return show() is True
        except Exception:
            return False

    # ---- transitions ----
    def toggle_mode(self) -> None:
        self._mode = self.FREE if self._mode == self.LOCK else self.LOCK
        self._apply_mode()

    def toggle_chase(self) -> None:
        """Ctrl-Ctrl, three steps on the keyboard (runs on the GUI thread):
          1. FREE  -> dash to the cursor with the glide trace (``summon_to``) and chase.
          2. LOCK (chasing near the cursor) -> open the dashboard.
          3. dashboard already open -> release: close it and return to FREE.
        Same intent as before (summon), now with the dashboard reachable without the mouse.
        """
        if self._show_game_session():
            return
        try:
            if self._dashboard_open():
                self._close_panel()
                self._mode = self.FREE
                self._cube.enable_follow(False)
                return
            if self._mode == self.FREE:
                summon = getattr(self._cube, "summon_to", None)
                summoned = bool(summon()) if callable(summon) else False
                if not summoned:
                    self._cube.enable_follow(True)
                    self._cube.wake()
                self._mode = self.LOCK
                self._play(self._visual_profile.lock_emote)
            else:                                   # already chasing near the cursor -> dashboard
                self._open_dashboard()
        except Exception:
            pass

    def _dashboard_open(self) -> bool:
        bubble = getattr(self._c, "_bubble", None)
        if not bubble or bubble is False:
            return False
        try:
            from mo_desktop.design import PanelState
            return bool(bubble.visible()) and getattr(bubble, "_panel_state", None) == PanelState.DASHBOARD
        except Exception:
            return False

    def _composer_open(self) -> bool:
        bubble = getattr(self._c, "_bubble", None)
        if not bubble or bubble is False:
            return False
        try:
            from mo_desktop.design import PanelState
            return bool(bubble.visible()) and getattr(bubble, "_panel_state", None) == PanelState.INPUT
        except Exception:
            return False

    def _close_panel(self) -> None:
        bubble = getattr(self._c, "_bubble", None)
        if bubble and bubble is not False:
            try:
                bubble.hide()
            except Exception:
                pass

    def set_mode(self, mode: str) -> None:
        """Set the mode explicitly (settings panel: Free vs Chase)."""
        self._mode = self.LOCK if str(mode or "").lower() in ("lock", "chase") else self.FREE
        self._apply_mode()

    def _apply_mode(self) -> None:
        follow = self._mode == self.LOCK
        try:
            self._cube.enable_follow(follow)
            if follow:
                self._cube.wake()
                self._play(self._visual_profile.lock_emote)
        except Exception:
            pass

    def _play(self, name: str) -> None:
        fn = getattr(self._cube, "play_emote", None)
        if callable(fn):
            try:
                fn(name)
            except Exception:
                pass

    def _open_input(self) -> None:
        # Clicking the cube while it recharges at home asks it to catch up on what the terminal
        # is doing, rather than opening an input over the terminal you are already typing in.
        if getattr(self._cube, "_charging", False):
            self._call(self._c, "_sync_with_terminal")
            return
        # A second click on the cubes closes the composer it opened (the draft is kept).
        if self._composer_open():
            self._call(self._c._bubble, "collapse_to_cube")
            return
        self._call(self._c, "_display_input_dialog")

    def _open_dashboard(self) -> None:
        self._call(self._c, "_display_dashboard")

    def _open_launcher(self) -> None:
        self._call(self._c, "show_cube_launcher")

    @staticmethod
    def _call(obj: Any, name: str) -> None:
        fn = getattr(obj, name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
