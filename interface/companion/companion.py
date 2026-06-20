"""MO Companion — the on-screen text-input surface.

Summon with Win+Alt+M (global hotkey) or `/companion` slash command. Type a
request, press Enter, and MO processes it via Ghost → Gateway with results
shown in a MO-branded overlay bubble. Runs as a daemon thread alongside the TUI.

Architecture
    [Companion tkinter window] → Gateway.run_turn(route_source="desktop")
                                 → Ghost shapes → MO executes → overlay bubble

No voice/tray yet — those are Phase 3 and 4.
"""
from __future__ import annotations

import threading
import traceback
from typing import Any

CYAN = "#00cccc"
CARD = "#04141a"
TEXT = "#dff6f6"
GLYPH = "◐"  # ◐ half-moon = MO mark
_ENTRY_BG = "#0a2028"


class CompanionSurface:
    """On-screen MO companion with text input and result display."""

    def __init__(self, agent: Any, gateway: Any) -> None:
        self._agent = agent
        self._gateway = gateway
        self._root: Any = None
        self._entry: Any = None
        self._response: Any = None
        self._status_label: Any = None
        self._running = False
        self._turn_thread: threading.Thread | None = None
        self._hotkey_listener: Any = None
        self._visible = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Start the companion in a daemon GUI thread. Returns True on success."""
        if self._running:
            return True
        try:
            import tkinter as tk
        except ImportError:
            return False  # tkinter missing (unusual but possible on headless)

        self._running = True
        thread = threading.Thread(target=self._gui_loop, name="mo-companion", daemon=True)
        thread.start()
        self._try_register_hotkey()
        return True

    def stop(self) -> None:
        """Shut down the companion GUI and unregister the hotkey."""
        self._running = False
        self._unregister_hotkey()
        if self._root:
            try:
                self._root.event_generate("<<CompanionStop>>")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def show(self) -> None:
        """Show the companion text-input window (summon)."""
        if not self._root:
            return
        try:
            self._root.event_generate("<<CompanionShow>>")
        except Exception:
            pass

    def hide(self) -> None:
        """Hide the companion window."""
        if not self._root:
            return
        try:
            self._root.event_generate("<<CompanionHide>>")
        except Exception:
            pass

    def toggle(self) -> None:
        """Toggle the companion window visibility."""
        self.hide() if self._visible else self.show()

    # ------------------------------------------------------------------
    # GUI
    # ------------------------------------------------------------------

    def _gui_loop(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()  # hidden until summoned
        self._root = root

        # --- build the window ---
        win = tk.Toplevel(root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.95)
        except Exception:
            pass

        # cyan border = outer frame; dark card = inner
        border = tk.Frame(win, bg=CYAN)
        border.pack()
        card = tk.Frame(border, bg=CARD)
        card.pack(padx=2, pady=2)

        # header: glyph + title
        header = tk.Frame(card, bg=CARD)
        header.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(header, text=GLYPH, fg=CYAN, bg=CARD,
                 font=("Segoe UI", 16, "bold")).pack(side="left", padx=(0, 8))
        tk.Label(header, text="MO Companion", fg=CYAN, bg=CARD,
                 font=("Segoe UI", 12, "bold")).pack(side="left")

        # status line
        self._status_label = tk.Label(card, text="Ask anything — press Enter",
                                      fg="#5a8899", bg=CARD, font=("Segoe UI", 9),
                                      anchor="w")
        self._status_label.pack(fill="x", padx=10, pady=(0, 4))

        # text entry
        self._entry = tk.Entry(card, font=("Segoe UI", 11),
                               fg=TEXT, bg=_ENTRY_BG,
                               insertbackground=CYAN, relief="flat",
                               borderwidth=4)
        self._entry.pack(fill="x", padx=10, pady=(0, 6))
        self._entry.bind("<Return>", self._on_submit)
        self._entry.bind("<Escape>", lambda _e: self.hide())

        # response label
        self._response = tk.Label(card, text="", fg=TEXT, bg=CARD,
                                  font=("Segoe UI", 10), anchor="w",
                                  justify="left", wraplength=380)
        self._response.pack(fill="both", padx=10, pady=(0, 8))

        # bottom hint
        tk.Label(card, text="Esc to hide  ·  Win+Alt+M to summon",
                 fg="#3a6677", bg=CARD, font=("Segoe UI", 8)).pack(
                     fill="x", padx=10, pady=(0, 6))

        # --- geometry (centre of screen) ---
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        ww, wh = 440, 200
        wx = max(0, (sw - ww) // 2)
        wy = max(0, (sh - wh) // 3)
        win.geometry(f"{ww}x{wh}+{wx}+{wy}")
        win.minsize(360, 140)

        def _do_show(*_args: Any) -> None:
            if not self._running:
                return
            win.deiconify()
            self._entry.focus_set()
            self._visible = True

        def _do_hide(*_args: Any) -> None:
            win.withdraw()
            self._visible = False

        def _do_stop(*_args: Any) -> None:
            win.destroy()
            root.destroy()

        root.bind("<<CompanionShow>>", _do_show)
        root.bind("<<CompanionHide>>", _do_hide)
        root.bind("<<CompanionStop>>", _do_stop)

        while self._running:
            try:
                root.update()
                root.update_idletasks()
                root.after(50)
            except Exception:
                if not self._running:
                    break
                traceback.print_exc()

    # ------------------------------------------------------------------
    # Turn submission
    # ------------------------------------------------------------------

    def _on_submit(self, _event: Any) -> None:
        text = (self._entry.get() or "").strip()
        if not text:
            return
        self._entry.delete(0, "end")
        self._set_status("Thinking…", CYAN)
        self._set_response("")

        self._turn_thread = threading.Thread(
            target=self._run_turn, args=(text,), name="mo-companion-turn", daemon=True
        )
        self._turn_thread.start()

    def _run_turn(self, user_input: str) -> None:
        try:
            result = self._gateway.run_turn(
                user_input,
                route_source="desktop",
                on_activity=self._on_activity,
                on_assistant_text=self._on_assistant_text,
                on_board_event=self._on_board_event,
            )
            self._set_result(result)
        except Exception as exc:
            self._set_status(f"Error: {exc}", "#ff4444")
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Callbacks (called from Gateway thread)
    # ------------------------------------------------------------------

    def _on_activity(self, label: str) -> None:
        self._set_status(label, "#5a8899")

    def _on_board_event(self, event: dict) -> None:
        kind = event.get("kind", "")
        text = event.get("text", "")
        if kind in ("task_started", "task_completed", "task_blocked"):
            self._set_status(text, CYAN if kind == "task_completed" else "#5a8899")

    def _on_assistant_text(self, delta: str) -> None:
        # append streaming text to response label
        if self._root and self._response:
            try:
                current = self._response.cget("text")
                self._response.config(text=current + delta)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # UI helpers (thread-safe via tkinter event queue)
    # ------------------------------------------------------------------

    def _set_status(self, text: str, color: str) -> None:
        if not self._root or not self._status_label:
            return
        try:
            self._root.after(0, lambda: self._status_label.config(text=text[:120], fg=color))
        except Exception:
            pass

    def _set_response(self, text: str) -> None:
        if not self._root or not self._response:
            return
        try:
            self._root.after(0, lambda: self._response.config(text=text[:600]))
        except Exception:
            pass

    def _set_result(self, text: str) -> None:
        summary = (text or "").strip()
        if len(summary) > 400:
            summary = summary[:397] + "..."
        self._set_response(summary)
        self._set_status("Done — Esc to hide", "#44cc88")

    # ------------------------------------------------------------------
    # Global hotkey (optional)
    # ------------------------------------------------------------------

    def _try_register_hotkey(self) -> None:
        try:
            import keyboard
        except ImportError:
            return
        try:
            keyboard.add_hotkey("win+alt+m", self.toggle)
            self._hotkey_listener = True
        except Exception:
            traceback.print_exc()

    def _unregister_hotkey(self) -> None:
        if self._hotkey_listener:
            try:
                import keyboard
                keyboard.remove_hotkey("win+alt+m")
            except Exception:
                pass
            self._hotkey_listener = None


# ------------------------------------------------------------------
# Service starter (follows start_*_if_enabled pattern)
# ------------------------------------------------------------------

def start_companion_if_enabled(agent: Any, gateway: Any) -> CompanionSurface | None:
    """Start the desktop companion if config says so and deps are present.

    Mirrors start_telegram_gateway_if_enabled / start_heartbeat_service_if_enabled.
    Returns the CompanionSurface instance if started, None otherwise.
    """
    try:
        config = getattr(agent, "config", None) or {}
        companion_cfg = config.get("desktop_companion", {}) if isinstance(config, dict) else {}
    except Exception:
        companion_cfg = {}

    if not companion_cfg.get("enabled", False) if isinstance(companion_cfg, dict) else False:
        return None

    try:
        companion = CompanionSurface(agent, gateway)
        if companion.start():
            return companion
    except Exception:
        traceback.print_exc()
    return None
