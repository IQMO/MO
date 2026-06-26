"""MO Companion — the desktop Ghost surface (on-screen text/voice input).

This IS the desktop "Ghost" (the transitional name "MO Companion" / `/companion`
is being unified to Ghost — see the multi-instance/Ghost-separation proposal). It
is a distinct surface from both the always-on "Ghost planning" step (which seeds
the taskboard via Gateway.propose_work) and the TUI side-panel coordinator.

Summon with Win+Alt+M (global hotkey) or `/companion`. Type/speak a request and
the turn runs through the Gateway on Ghost's OWN isolated session (so the desktop
conversation never bleeds into Main MO), gated by the Guide/Do lane; results show
in a MO-branded overlay bubble. Runs as a daemon thread alongside the TUI.

Architecture
    [Companion tkinter window]
      → Gateway.run_turn(route_source="ghost") on an isolated Ghost session
      → Ghost planning seeds the board → MO executes (Guide/Do lane) → overlay bubble
"""
from __future__ import annotations

import queue
import sys
import threading
import time
import traceback
from typing import Any, Callable

from core.ghost.desktop_pointer import set_desktop_pointer
from core.runtime_lock import acquire_runtime_lock, release_runtime_lock
from core.sandbox import redact_sensitive_text
from core.tasking.task_board import attach_taskboard_to_text
from interface.ghost_desktop.orb import GhostOrb
from interface.ghost_desktop.voice import CompanionVoice, VoiceRecognizer, VoiceSpeaker
from interface.ghost_desktop.tray import CompanionTray, start_tray_if_enabled

CYAN = "#00cccc"
CARD = "#0b1418"
TEXT = "#dff6f6"
GLYPH = "◐"  # ◐ half-moon = MO mark
_ENTRY_BG = "#10242b"
_MUTED = "#5a8899"
_BORDER = "#123038"
_LISTEN = "#bb86fc"  # voice-listening accent (matches the orb)
WINDOW_WIDTH = 400
WINDOW_HEIGHT = 268
WINDOW_OFFSET = 24

# Desktop Ghost keeps its OWN persisted session slot (never "main"): isolated from
# Main MO, but durable across restarts so Ghost stays concise about what it already
# did on the desktop in earlier sessions.
GHOST_DESKTOP_SESSION_SLOT = "ghost-desktop"


def companion_geometry_near_pointer(
    pointer_x: int,
    pointer_y: int,
    screen_width: int,
    screen_height: int,
    *,
    width: int = WINDOW_WIDTH,
    height: int = WINDOW_HEIGHT,
    offset: int = WINDOW_OFFSET,
) -> str:
    """Return a Tk geometry string that places the Companion near the pointer."""
    x = int(pointer_x) + offset
    y = int(pointer_y) + offset
    if x + width > screen_width:
        x = int(pointer_x) - width - offset
    if y + height > screen_height:
        y = int(pointer_y) - height - offset
    x = max(0, min(x, max(0, int(screen_width) - width)))
    y = max(0, min(y, max(0, int(screen_height) - height)))
    return f"{width}x{height}+{x}+{y}"


class CompanionSurface:
    """On-screen MO companion with text input and result display."""

    def __init__(
        self,
        agent: Any,
        gateway: Any,
        voice_config: dict | None = None,
        companion_config: dict | None = None,
    ) -> None:
        self._agent = agent
        self._gateway = gateway
        self._companion_cfg = companion_config or {}
        self._voice_cfg = voice_config or {}
        self._voice: CompanionVoice | None = None
        self._tray: CompanionTray | None = None
        self._action_log: list[dict[str, Any]] = []
        self._panic_stop_requested = False
        self._cancel_event: threading.Event | None = None
        self._stream_buf = ""
        self._recording_voice = False
        self._mode: str = "guide"  # "guide" or "do"
        self._root: Any = None
        self._entry: Any = None
        self._response: Any = None
        self._status_label: Any = None
        self._running = False
        self._turn_thread: threading.Thread | None = None
        self._hotkey_listener: Any = None
        self._voice_btn: Any = None
        self._mode_label: Any = None
        self._listen_label: Any = None  # voice-listening indicator in the window header
        self._orb: GhostOrb | None = None  # MO's on-screen moon pointer (clicky-style)
        self._tts_warned = False  # surface a TTS-unavailable note at most once per session
        self._stopped = False  # set once the GUI loop has torn down — no late queueing
        self._visible = False
        self._gui_events: queue.Queue[str | Callable[[], None]] = queue.Queue()
        self._gui_ready = threading.Event()
        # Ghost runs in its OWN session so a desktop turn never appends into Main MO's
        # conversation (the live bug: desktop messages merged into a running DEVMODE
        # session). Created lazily; thread-local via agent.isolated_session at turn time.
        self._ghost_session: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Start the companion in a daemon GUI thread. Returns True on success."""
        if self._running:
            return True
        import importlib.util
        if importlib.util.find_spec("tkinter") is None:
            return False  # tkinter missing (unusual but possible on headless)

        self._running = True
        self._gui_ready.clear()
        self._init_voice()
        self._init_tray()
        thread = threading.Thread(target=self._gui_loop, name="mo-companion", daemon=True)
        thread.start()
        if self._gui_ready.wait(timeout=5.0) and self._root is not None:
            self._try_register_hotkey()
            return True
        self._running = False
        if self._tray:
            self._tray.stop()
        sys.stderr.write("[companion] GUI did not become ready; companion was not started.\n")
        return False

    def stop(self) -> None:
        """Shut down the companion GUI and unregister the hotkey."""
        self._unregister_hotkey()
        self._persist_ghost_session()
        if self._tray:
            self._tray.stop()
        if not self._post_gui_event("<<CompanionStop>>"):
            self._running = False
        release_runtime_lock(getattr(self, "_runtime_lock", None))
        self._runtime_lock = None

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def show(self) -> None:
        """Show the companion text-input window (summon)."""
        self._post_gui_event("<<CompanionShow>>")

    def hide(self) -> None:
        """Hide the companion window."""
        self._post_gui_event("<<CompanionHide>>")

    def toggle(self) -> None:
        """Toggle the companion window visibility."""
        self.hide() if self._visible else self.show()

    # ------------------------------------------------------------------
    # Tray + startup + panic-stop (Phase 4)
    # ------------------------------------------------------------------

    def _init_tray(self) -> None:
        """Start system tray if configured."""
        self._tray = start_tray_if_enabled(
            self,
            companion_config=self._companion_cfg,
            voice_config=self._voice_cfg,
        )

    @property
    def mode(self) -> str:
        return self._tray.mode if self._tray else self._mode

    def _mode_indicator(self) -> tuple[str, str]:
        """(label, color) for the header mode badge. Do uses an alert color
        because that mode lets MO actuate the desktop; Guide stays calm."""
        if self.mode == "guide":
            return ("● Guide — MO explains", "#5a8899")
        return ("● Do — MO can act", "#ffaa33")

    def _refresh_mode_indicator(self) -> None:
        self._post_gui_call(self._apply_mode_indicator)

    def _apply_mode_indicator(self) -> None:
        if self._mode_label is not None:
            text, color = self._mode_indicator()
            self._mode_label.config(text=text, fg=color)

    def panic_stop(self) -> None:
        """Emergency stop: interrupt the in-flight turn and block the next one."""
        self._panic_stop_requested = True
        # Actually interrupt a running turn (the Gateway loop checks cancel_event).
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._set_status("PANIC STOP — turn interrupted, desktop blocked", "#ff4444")
        self._log_action("panic_stop", "Emergency stop triggered")

    def show_action_log(self) -> None:
        """Show the action log in a popup."""
        self._post_gui_event("<<CompanionShowLog>>")

    def _show_log_popup(self, root: Any) -> None:
        """Create and display the action log popup window."""
        import tkinter as tk

        popup = tk.Toplevel(root)
        popup.title("Ghost — Action Log")
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=CYAN)
        try:
            popup.attributes("-alpha", 0.95)
        except Exception:
            pass

        border = tk.Frame(popup, bg=CYAN)
        border.pack(fill="both", expand=True)
        card = tk.Frame(border, bg=CARD)
        card.pack(fill="both", expand=True, padx=2, pady=2)

        # Header
        header = tk.Frame(card, bg=CARD)
        header.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(header, text=GLYPH, fg=CYAN, bg=CARD,
                 font=("Segoe UI", 16, "bold")).pack(side="left", padx=(0, 8))
        tk.Label(header, text="Action Log", fg=CYAN, bg=CARD,
                 font=("Segoe UI", 12, "bold")).pack(side="left")

        # Scrollable log text
        text_frame = tk.Frame(card, bg=CARD)
        text_frame.pack(fill="both", expand=True, padx=10, pady=(4, 6))
        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side="right", fill="y")
        log_text = tk.Text(text_frame, fg=TEXT, bg=_ENTRY_BG,
                           font=("Consolas", 9), wrap="word",
                           yscrollcommand=scrollbar.set,
                           relief="flat", borderwidth=4,
                           width=60, height=16)
        log_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=log_text.yview)

        # Populate log entries
        if self._action_log:
            for entry in reversed(self._action_log):
                log_text.insert("end", f"[{entry['time']}] {entry['kind']}: {entry['detail']}\n")
        else:
            log_text.insert("end", "(no actions logged yet)\n")
        log_text.config(state="disabled")

        # Close button
        btn_frame = tk.Frame(card, bg=CARD)
        btn_frame.pack(fill="x", padx=10, pady=(0, 8))
        tk.Button(btn_frame, text="Close", command=popup.destroy,
                  fg=CARD, bg=CYAN, font=("Segoe UI", 9, "bold"),
                  relief="flat", padx=12, pady=2).pack(side="right")

        # Geometry — right-side of screen
        popup.update_idletasks()
        sw = popup.winfo_screenwidth()
        sh = popup.winfo_screenheight()
        ww, wh = 480, 360
        wx = max(0, sw - ww - 40)
        wy = max(0, (sh - wh) // 3)
        popup.geometry(f"{ww}x{wh}+{wx}+{wy}")
        popup.minsize(360, 200)

        # Close on Escape
        popup.bind("<Escape>", lambda _e: popup.destroy())

    def _log_action(self, kind: str, detail: str) -> None:
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "kind": kind,
            "detail": redact_sensitive_text(str(detail or ""))[:200],
        }
        self._action_log.append(entry)
        # Keep last 50 entries
        if len(self._action_log) > 50:
            self._action_log = self._action_log[-50:]

    # ------------------------------------------------------------------
    # Voice (Phase 3)
    # ------------------------------------------------------------------

    def _init_voice(self) -> None:
        """Initialize STT + TTS if configured."""
        if not self._voice_cfg:
            self._voice = None
            return
        stt_enabled = self._voice_cfg.get("stt_enabled", False)
        tts_enabled = self._voice_cfg.get("tts_enabled", False)
        if not stt_enabled and not tts_enabled:
            self._voice = None
            return
        recognizer = VoiceRecognizer(
            model_size=self._voice_cfg.get("stt_model", "base"),
        ) if stt_enabled else None
        speaker = VoiceSpeaker(
            voice_model_path=self._voice_cfg.get("tts_model", ""),
            voice_name=self._voice_cfg.get("tts_voice", "en_US-lessac-medium"),
        ) if tts_enabled else None
        self._voice = CompanionVoice(recognizer=recognizer, speaker=speaker)

    def _on_voice_input(self) -> None:
        """Push-to-talk TOGGLE (GUI-driven): click to record, click again to stop.
        No blocking input(); the mic is closed on the second click — never left open."""
        voice = self._voice
        if not self._voice_input_configured():
            self._set_status("Voice input is off in ghost.voice.stt_enabled", "#ffcc44")
            return
        if voice is None or not voice.stt_available:
            self._set_status(self._voice_input_unavailable_message(), "#ffcc44")
            return
        if self._recording_voice:
            # second tap → stop, transcribe, submit
            self._recording_voice = False
            self._orb_set_listening(False)
            self._set_voice_btn_color(CYAN)  # mic no longer hot
            self._finish_voice_capture(voice)
            return
        # first tap → start (don't start over a running turn)
        if self._turn_thread is not None and self._turn_thread.is_alive():
            self._set_status("Still working on the previous request…", "#ffcc44")
            return
        if voice.start_recording():
            self._recording_voice = True
            self._orb_wake()               # pop the moon at the cursor immediately
            self._orb_set_listening(True)  # then keep it up and reactive while hearing
            self._set_voice_btn_color("#ff4444")  # visible hot-mic cue
            self._set_status("Listening… Win+Alt+M again to stop", _LISTEN)
        else:
            rec = getattr(voice, "recorder", None)
            reason = (getattr(rec, "last_error", "") or "").strip() if rec else ""
            msg = f"Could not start the microphone — {reason}" if reason \
                else "Could not start the microphone — check OS mic permissions."
            self._set_status(msg, "#ff4444")

    def _set_voice_btn_color(self, color: str) -> None:
        if not self._root or self._voice_btn is None:
            return
        self._post_gui_call(
            lambda: self._voice_btn.config(fg=color) if self._voice_btn else None)

    def _finish_voice_capture(self, voice: Any) -> None:
        """Stop capture, transcribe, and submit — shared by the manual second click
        and the max-seconds auto-stop, so both land in the same place."""
        self._set_status("Transcribing…", CYAN)

        def _finish() -> None:
            text = voice.stop_and_transcribe()
            if text and not text.startswith("[STT") and not text.startswith("[Voice"):
                if self._turn_thread is not None and self._turn_thread.is_alive():
                    self._set_status("Still working on the previous request…", "#ffcc44")
                    return
                self._panic_stop_requested = False  # explicit action resumes after panic
                self._log_action("voice", text)  # log spoken requests too
                self._turn_thread = threading.Thread(
                    target=self._run_turn, args=(text,), name="mo-companion-turn", daemon=True)
                self._turn_thread.start()
            else:
                self._set_status(text or "[No speech detected]", "#ff4444")

        threading.Thread(target=_finish, name="mo-companion-voice", daemon=True).start()

    def _on_tts_error(self, reason: str) -> None:
        if self._tts_warned:
            return
        self._tts_warned = True
        self._set_status(f"Voice output unavailable — {reason}", "#ffcc44")

    def _poll_voice_autostop(self) -> None:
        """The recorder can self-stop at the max-seconds cap from its audio thread.
        Reflect that on the GUI instead of leaving a stale 'Recording…' hint with the
        mic already released — finish the buffered capture exactly like a 2nd click."""
        if not self._recording_voice or self._voice is None:
            return
        rec = getattr(self._voice, "recorder", None)
        if rec is None or getattr(rec, "_recording", False):
            return
        self._recording_voice = False
        self._orb_set_listening(False)
        self._set_voice_btn_color(CYAN)
        self._finish_voice_capture(self._voice)

    # ------------------------------------------------------------------
    # GUI
    # ------------------------------------------------------------------

    def _gui_loop(self) -> None:
        import tkinter as tk

        dnd_ok = False
        try:
            try:
                from tkinterdnd2 import TkinterDnD
                root = TkinterDnD.Tk()  # enables OS file drag-and-drop
                dnd_ok = True
            except Exception:
                root = tk.Tk()
        except Exception:
            self._running = False
            self._gui_ready.set()
            traceback.print_exc()
            return
        root.withdraw()  # hidden until summoned
        self._root = root

        # MO's on-screen moon pointer. Register it as the desktop pointer so
        # point_on_screen drives the moon (not the bare Windows cursor). Best-effort:
        # if the orb can't build (odd Tk backend) the surface still works text-first.
        try:
            self._orb = GhostOrb(root)
            set_desktop_pointer(self._point_with_orb)
        except Exception:
            self._orb = None
            traceback.print_exc()

        # --- build the window ---
        win = tk.Toplevel(root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.95)
        except Exception:
            pass

        # Modern, voice-first one-sided surface: a hairline accent border, a dark
        # card, Ghost's reply as the primary area, and a slim manual input bar.
        win.configure(bg=_BORDER)
        border = tk.Frame(win, bg=_BORDER)
        border.pack(fill="both", expand=True)
        card = tk.Frame(border, bg=CARD)
        card.pack(fill="both", expand=True, padx=1, pady=1)

        # header (drag handle): glyph + title on the left; listening dot + mode badge
        header = tk.Frame(card, bg=CARD)
        header.pack(fill="x", padx=14, pady=(10, 6))
        glyph_lbl = tk.Label(header, text=GLYPH, fg=CYAN, bg=CARD,
                             font=("Segoe UI", 15, "bold"))
        glyph_lbl.pack(side="left", padx=(0, 8))
        title_lbl = tk.Label(header, text="Ghost", fg=TEXT, bg=CARD,
                             font=("Segoe UI Semibold", 12))
        title_lbl.pack(side="left")
        # Mode indicator: the user must be able to SEE whether MO will drive the
        # desktop (Do) or only explain (Guide) — mode was previously tray-only.
        self._mode_label = tk.Label(header, text="", bg=CARD,
                                    font=("Segoe UI", 8, "bold"))
        self._mode_label.pack(side="right")
        # Listening dot — lights up while the mic is hot (orb is the primary cue).
        self._listen_label = tk.Label(header, text="", fg=_LISTEN, bg=CARD,
                                      font=("Segoe UI", 8, "bold"))
        self._listen_label.pack(side="right", padx=(0, 10))

        # The input + controls are packed to the BOTTOM first so they are ALWAYS
        # visible — the reply area then fills whatever space is left above them.
        # (Previously the reply expanded and pushed the input off a short window.)
        hint = "Win+Alt+M speak  ·  type + Enter  ·  drop files to attach  ·  Esc hide"
        tk.Label(card, text=hint, fg="#3a6677", bg=CARD, font=("Segoe UI", 8)).pack(
            side="bottom", fill="x", padx=14, pady=(0, 8))

        # manual input bar (mic fallback) with leading mark + Run/Stop buttons
        entry_frame = tk.Frame(card, bg=_ENTRY_BG)
        entry_frame.pack(side="bottom", fill="x", padx=14, pady=(0, 6))
        tk.Label(entry_frame, text="›", fg=CYAN, bg=_ENTRY_BG,
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=(8, 2))
        # Run (▶) and Stop (⏹) — explicit clickable controls, not just Enter.
        tk.Button(entry_frame, text="⏹", command=self._on_stop_click,
                  fg="#ff6b6b", bg=_ENTRY_BG, activebackground=_ENTRY_BG,
                  activeforeground="#ff9b9b", relief="flat", bd=0,
                  font=("Segoe UI", 11), cursor="hand2").pack(side="right", padx=(2, 6))
        tk.Button(entry_frame, text="▶", command=lambda: self._on_submit(None),
                  fg=CYAN, bg=_ENTRY_BG, activebackground=_ENTRY_BG,
                  activeforeground="#7fffff", relief="flat", bd=0,
                  font=("Segoe UI", 11), cursor="hand2").pack(side="right", padx=(2, 2))
        self._entry = tk.Entry(entry_frame, font=("Segoe UI", 10),
                               fg=TEXT, bg=_ENTRY_BG,
                               insertbackground=CYAN, relief="flat",
                               borderwidth=6)
        self._entry.pack(side="left", fill="x", expand=True)
        self._entry.bind("<Return>", self._on_submit)
        self._entry.bind("<Escape>", lambda _e: self.hide())

        # status line (thin, just above the input)
        self._status_label = tk.Label(card, text="Win+Alt+M to speak", anchor="w",
                                      fg=_MUTED, bg=CARD, font=("Segoe UI", 8))
        self._status_label.pack(side="bottom", fill="x", padx=14, pady=(0, 2))

        # Ghost's reply / explanation — the primary area, fills the space above.
        resp_frame = tk.Frame(card, bg=CARD)
        resp_frame.pack(side="top", fill="both", expand=True, padx=14, pady=(0, 6))
        resp_scroll = tk.Scrollbar(resp_frame)
        resp_scroll.pack(side="right", fill="y")
        self._response = tk.Text(resp_frame, fg=TEXT, bg=CARD,
                                 font=("Segoe UI", 10), wrap="word",
                                 height=4, relief="flat", borderwidth=0,
                                 highlightthickness=0, cursor="arrow",
                                 spacing1=2, spacing3=3,
                                 yscrollcommand=resp_scroll.set, state="disabled")
        self._response.pack(side="left", fill="both", expand=True)
        resp_scroll.config(command=self._response.yview)

        # Drag-and-drop files anywhere on the card → attach + remember in the session.
        if dnd_ok:
            try:
                from tkinterdnd2 import DND_FILES
                for _dt in (card, resp_frame, self._response):
                    _dt.drop_target_register(DND_FILES)
                    _dt.dnd_bind("<<Drop>>", self._on_files_dropped)
            except Exception:
                traceback.print_exc()

        # Drag-to-move from the header (borderless windows have no titlebar).
        def _start_drag(event: Any) -> None:
            self._drag_origin = (event.x_root, event.y_root)
            try:
                self._drag_win_origin = (win.winfo_x(), win.winfo_y())
            except Exception:
                self._drag_win_origin = (0, 0)

        def _on_drag(event: Any) -> None:
            ox, oy = getattr(self, "_drag_origin", (event.x_root, event.y_root))
            wx, wy = getattr(self, "_drag_win_origin", (0, 0))
            try:
                win.geometry(f"+{wx + (event.x_root - ox)}+{wy + (event.y_root - oy)}")
            except Exception:
                pass

        for _drag_widget in (header, glyph_lbl, title_lbl):
            _drag_widget.bind("<Button-1>", _start_drag)
            _drag_widget.bind("<B1-Motion>", _on_drag)

        # --- geometry (near pointer; recomputed on every summon) ---
        win.update_idletasks()
        win.geometry(companion_geometry_near_pointer(
            *win.winfo_pointerxy(),
            win.winfo_screenwidth(),
            win.winfo_screenheight(),
        ))
        win.minsize(360, 140)

        def _do_show(*_args: Any) -> None:
            if not self._running:
                return
            win.geometry(companion_geometry_near_pointer(
                *win.winfo_pointerxy(),
                win.winfo_screenwidth(),
                win.winfo_screenheight(),
            ))
            self._apply_mode_indicator()
            win.deiconify()
            self._entry.focus_set()
            self._visible = True

        def _do_hide(*_args: Any) -> None:
            win.withdraw()
            self._visible = False

        def _do_stop(*_args: Any) -> None:
            self._running = False
            self._stopped = True
            win.destroy()
            root.destroy()

        def _do_show_log(*_args: Any) -> None:
            self._show_log_popup(root)

        handlers = {
            "<<CompanionShow>>": _do_show,
            "<<CompanionHide>>": _do_hide,
            "<<CompanionStop>>": _do_stop,
            "<<CompanionShowLog>>": _do_show_log,
        }

        root.bind("<<CompanionShow>>", _do_show)
        root.bind("<<CompanionHide>>", _do_hide)
        root.bind("<<CompanionStop>>", _do_stop)
        root.bind("<<CompanionShowLog>>", _do_show_log)

        self._gui_ready.set()
        try:
            while self._running:
                self._drain_gui_events(handlers)
                if not self._running:
                    break
                self._poll_voice_autostop()
                if self._orb is not None:
                    try:
                        if self._recording_voice and self._voice is not None:
                            rec = getattr(self._voice, "recorder", None)
                            self._orb.set_level(float(getattr(rec, "level", 0.0) or 0.0))
                        self._orb.tick()
                    except Exception:
                        pass
                root.update()
                root.update_idletasks()
                time.sleep(0.03)  # ~33fps; smooth orb glide, still a blocking yield
        except Exception:
            if self._running:
                traceback.print_exc()
        finally:
            self._running = False
            set_desktop_pointer(None)
            if self._orb is not None:
                try:
                    self._orb.destroy()
                except Exception:
                    pass
                self._orb = None
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass
            try:
                if root.winfo_exists():
                    root.destroy()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Turn submission
    # ------------------------------------------------------------------

    def _on_submit(self, _event: Any) -> None:
        text = (self._entry.get() or "").strip()
        if not text:
            return
        # C6: don't overlap turns — one in-flight turn at a time.
        if self._turn_thread is not None and self._turn_thread.is_alive():
            self._set_status("Still working on the previous request…", "#ffcc44")
            return
        self._entry.delete(0, "end")
        # C2: an explicit new request resumes after a panic-stop (the operator
        # acting again is the in-app reset — no permanent block, no restart).
        self._panic_stop_requested = False
        self._set_status("Thinking…", CYAN)
        self._set_response("")
        self._log_action("submit", text)

        self._turn_thread = threading.Thread(
            target=self._run_turn, args=(text,), name="mo-companion-turn", daemon=True
        )
        self._turn_thread.start()

    def _ensure_ghost_session(self) -> Any:
        """Ghost's own conversation session, created lazily. Isolates the desktop
        transcript from Main MO's `_session` so the two never cross-contaminate."""
        if self._ghost_session is None:
            from core.session.session import Session
            from interface.ghost import ghost_desktop_system_message
            base_sys = (
                getattr(self._agent, "system_message", None)
                or getattr(getattr(self._agent, "_session", None), "system_message", "")
                or "system"
            )
            # The desktop Ghost runs the Main-MO agent on its OWN isolated session, but
            # with the Ghost desktop-presence persona so it speaks/acts AS Ghost.
            self._ghost_session = Session(ghost_desktop_system_message(base_sys))
            self._load_ghost_session(self._ghost_session)
        return self._ghost_session

    def _load_ghost_session(self, session: Any) -> None:
        """Restore the persisted desktop transcript into ``session`` so Ghost keeps
        continuity across restarts. No-op when nothing was saved yet."""
        sessions = getattr(self._agent, "_sessions", None)
        if not sessions:
            return
        try:
            data = sessions.load(GHOST_DESKTOP_SESSION_SLOT)
        except Exception:
            data = None
        if not isinstance(data, dict):
            return
        try:
            session.session_id = data.get("session_id", session.session_id)
            session.turn_count = int(data.get("turn_count", 0) or 0)
            session.messages = data.get("messages", []) or []
            session.total_tokens = int(data.get("total_tokens", 0) or 0)
            session.output_tokens = int(data.get("output_tokens", 0) or 0)
            session.token_log = list(data.get("token_log", []) or [])
            session.sanitize_for_provider()
        except Exception:
            traceback.print_exc()

    def _persist_ghost_session(self) -> None:
        """Save the desktop transcript to its own slot (never touches Main MO's
        current slot — uses ``save_snapshot``)."""
        session = self._ghost_session
        sessions = getattr(self._agent, "_sessions", None)
        if session is None or not sessions or not getattr(session, "messages", None):
            return
        try:
            sessions.save_snapshot(GHOST_DESKTOP_SESSION_SLOT, session)
        except Exception:
            traceback.print_exc()

    def _on_stop_click(self) -> None:
        """⏹ — cancel a live recording, else interrupt the running turn."""
        if self._recording_voice:
            self._recording_voice = False
            self._orb_set_listening(False)
            rec = getattr(self._voice, "recorder", None)
            if rec is not None:
                try:
                    rec.stop()  # discard the capture, don't transcribe
                except Exception:
                    pass
            self._set_status("Stopped listening.", _MUTED)
            return
        if self._turn_thread is not None and self._turn_thread.is_alive():
            self.panic_stop()
            return
        self._set_status("Nothing running.", _MUTED)

    def _attachments_dir(self):
        """~/.mo/gdesktop/attachments — Ghost Desktop's own file home (created lazily)."""
        from pathlib import Path
        base = None
        sessions = getattr(self._agent, "_sessions", None)
        try:
            if sessions is not None and getattr(sessions, "dir", None):
                base = Path(sessions.dir).parent.parent  # ~/.mo/memory/sessions → ~/.mo
        except Exception:
            base = None
        if base is None:
            base = Path.home() / ".mo"
        dest = base / "gdesktop" / "attachments"
        dest.mkdir(parents=True, exist_ok=True)
        return dest

    def _on_files_dropped(self, event: Any) -> None:
        """Files dropped on the window: copy into the attachments home and record a
        note in the persisted desktop session so MO can act on them and remember
        them across restarts."""
        import shutil
        from pathlib import Path
        try:
            raw = self._root.tk.splitlist(event.data)
        except Exception:
            raw = str(getattr(event, "data", "") or "").split()
        try:
            dest_dir = self._attachments_dir()
        except Exception:
            self._set_status("Could not open attachments folder", "#ff6b6b")
            return
        saved: list = []
        for item in raw:
            try:
                src = Path(str(item).strip("{}").strip())
                if not src.exists() or not src.is_file():
                    continue
                dest = dest_dir / src.name
                stem, suffix, i = dest.stem, dest.suffix, 1
                while dest.exists():
                    dest = dest_dir / f"{stem}-{i}{suffix}"
                    i += 1
                shutil.copy2(src, dest)
                saved.append(dest)
            except Exception:
                traceback.print_exc()
        if not saved:
            self._set_status("No files attached", "#ffcc44")
            return
        names = ", ".join(s.name for s in saved)
        try:
            session = self._ensure_ghost_session()
            session.add_user(
                f"[Operator attached {len(saved)} file(s) to Ghost Desktop: {names}. "
                f"Saved under {saved[0].parent}. Read them with your file tools when relevant.]"
            )
            self._persist_ghost_session()
        except Exception:
            traceback.print_exc()
        self._log_action("attach", names)
        self._set_status(f"Attached & remembered: {names}", "#44cc88")
        self._set_response("📎 Attached " + str(len(saved)) + " file(s):\n"
                           + "\n".join(f"  • {s.name}" for s in saved))
        self.show()

    def _run_turn(self, user_input: str) -> None:
        if self._panic_stop_requested:
            self._set_status("Stopped (panic). Type a new request to resume.", "#ff4444")
            return
        # Fresh cancel signal per turn so panic_stop can interrupt an in-flight turn.
        self._cancel_event = threading.Event()
        self._stream_buf = ""
        try:
            # Guide mode = point/explain, don't take control: scope a per-turn
            # lane so the sandbox blocks actuation (thread-local — never races the
            # TUI). Do mode runs normally.
            lane = "companion-guide" if self.mode == "guide" else None
            # Run on Ghost's OWN session (thread-local) so the desktop conversation can
            # never bleed into Main MO's session/transcript. The gateway turn-mutex
            # separately guarantees a desktop turn is rejected while a Main turn (e.g. a
            # whole DEVMODE run) is in flight, so the two never interleave.
            ghost_session = self._ensure_ghost_session()
            with self._agent.lane_scope(lane), self._agent.isolated_session(ghost_session):
                result = self._gateway.run_turn(
                    user_input,
                    route_source="ghost",
                    on_activity=self._on_activity,
                    on_assistant_text=self._on_assistant_text,
                    on_board_event=self._on_board_event,
                    on_proposal=self._on_proposal,
                    on_action=self._on_action,
                    cancel_event=self._cancel_event,
                )
            self._set_result(self._append_task_board(result))
            self._log_action("turn_complete", result[:200])
            # Speak result if TTS enabled; surface a one-time note when the backend
            # claims availability (piper imports) but can't actually speak (no model,
            # no audio device) so the user isn't met with silent dead air.
            if self._voice and self._voice.tts_available:
                self._voice.speak_result(result, on_error=self._on_tts_error)
        except Exception as exc:
            safe_error = redact_sensitive_text(str(exc) or type(exc).__name__)
            self._set_status(f"Error: {safe_error}", "#ff4444")
            self._log_action("turn_error", safe_error[:200])
            sys.stderr.write(f"[ghost-desktop] turn error: {safe_error}\n")
        finally:
            self._persist_ghost_session()

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
            # Record task transitions too — the action log should show how the
            # work progressed, not just the tools.
            self._log_action(kind, text or kind)

    def _on_action(self, action: dict) -> None:
        # Every tool MO runs, with a sanitized arg summary (click coords, typed
        # text, command, file path). This is what makes the action log reflect
        # what MO actually DID on the desktop — the whole point of the log.
        tool = str(action.get("tool", "") or "tool")
        summary = str(action.get("summary", "") or "")
        detail = f"{tool}: {summary}" if summary and summary != tool else tool
        if action.get("blocked"):
            self._log_action("blocked", detail)
        elif action.get("error"):
            self._log_action("action_error", detail)
        else:
            self._log_action("action", detail)

    def _on_proposal(self, plan_text: str) -> None:
        # Show Ghost's plan before MO routes the work — same contract as the TUI /
        # Telegram. MO's structured reasoning must not be invisible on this surface.
        plan = " ".join(str(plan_text or "").split())
        if plan:
            self._set_response("Plan: " + plan[:500])
            self._log_action("ghost_plan", plan[:200])

    def _append_task_board(self, reply: str) -> str:
        return attach_taskboard_to_text(self._gateway, reply)

    def _on_assistant_text(self, delta: str) -> None:
        # Called from the Gateway thread — tkinter is NOT thread-safe, so never
        # touch widgets here. Accumulate and queue the update on the GUI thread.
        if not self._root:
            return
        self._stream_buf += str(delta or "")
        self._render_response(self._stream_buf, follow_tail=True)

    # ------------------------------------------------------------------
    # UI helpers (thread-safe via tkinter event queue)
    # ------------------------------------------------------------------

    def _set_status(self, text: str, color: str) -> None:
        if not self._root or not self._status_label:
            return
        msg = str(text or "")
        if len(msg) > 120:
            msg = msg[:117] + "…"  # signal truncation rather than chop silently
        self._post_gui_call(lambda: self._status_label.config(text=msg, fg=color))

    def _set_response(self, text: str) -> None:
        self._render_response(text, follow_tail=False)

    def _render_response(self, text: str, *, follow_tail: bool) -> None:
        # Single render-layer chokepoint for the response Text. Redacts (covers
        # _set_result, _on_proposal, streaming, taskboard) so nothing secret shows
        # on-screen, and scrolls to the tail while streaming / to the top for a
        # finished answer.
        if not self._root or self._response is None:
            return
        safe = redact_sensitive_text(str(text or ""))[:4000]

        def _apply() -> None:
            widget = self._response
            if widget is None:
                return
            widget.config(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", safe)
            widget.config(state="disabled")
            widget.see("end" if follow_tail else "1.0")

        self._post_gui_call(_apply)

    def _set_result(self, text: str) -> None:
        summary = (text or "").strip()
        if len(summary) > 4000:  # the Text scrolls; only mark a genuinely huge clip
            summary = summary[:3997] + "..."
        self._set_response(summary)
        self._set_status("Done — Esc to hide", "#44cc88")
        # Surface the reply: a voice turn keeps the window hidden while listening,
        # so reveal Ghost's answer when it lands (the one-sided reply is the point).
        self.show()

    def _voice_input_configured(self) -> bool:
        return bool(self._voice_cfg.get("stt_enabled", False))

    def _voice_input_unavailable_message(self) -> str:
        voice = self._voice
        if voice is None:
            return "Voice input unavailable: STT is enabled but voice did not initialize"

        missing: list[str] = []
        recognizer = getattr(voice, "recognizer", None)
        recorder = getattr(voice, "recorder", None)
        if recognizer is not None and not getattr(recognizer, "available", False):
            reason = getattr(recognizer, "_load_error", None) or "faster-whisper not installed"
            missing.append(f"transcription ({reason})")
        if recorder is not None and not getattr(recorder, "available", False):
            missing.append("microphone capture (sounddevice not installed)")
        if missing:
            return "Voice input unavailable: " + "; ".join(missing)
        return "Voice input unavailable: STT backend is not ready"

    def _post_gui_event(self, event_name: str) -> bool:
        return self._post_gui_call(event_name)

    def _post_gui_call(self, callback: str | Callable[[], None]) -> bool:
        # Once stopped, the drain loop has exited and nothing will run a queued
        # callback — report False rather than a misleading "queued" success.
        if self._root is None or self._stopped:
            return False
        try:
            self._gui_events.put_nowait(callback)
            return True
        except Exception:
            return False

    def _drain_gui_events(self, handlers: dict[str, Any]) -> None:
        while True:
            try:
                item = self._gui_events.get_nowait()
            except queue.Empty:
                return
            if callable(item):
                try:
                    item()
                except Exception:
                    if self._running:
                        traceback.print_exc()
                continue
            handler = handlers.get(item)
            if handler is None:
                continue
            try:
                handler()
            except Exception:
                if self._running:
                    traceback.print_exc()

    # ------------------------------------------------------------------
    # Global hotkey (optional)
    # ------------------------------------------------------------------

    def _on_hotkey(self) -> None:
        """Win+Alt+M is voice-first: tap to start hearing (orb pulses, no text
        window), tap again to stop and run the spoken request. If voice isn't
        available, fall back to toggling the reply window so the key is never dead."""
        if self._voice_input_configured() and self._voice is not None and self._voice.stt_available:
            self._on_voice_input()
            return
        # No voice backend: still wake the moon at the cursor so the orb is the face
        # of the summon (not a bare window), then open the manual text input.
        self._orb_wake()
        self.toggle()

    def _orb_wake(self) -> None:
        """Show the breathing moon at the current pointer (GUI-thread marshalled)."""
        orb = self._orb
        if orb is None:
            return

        def _do() -> None:
            try:
                x, y = self._root.winfo_pointerxy()
            except Exception:
                x = y = None
            orb.wake(x, y)

        self._post_gui_call(_do)

    def _orb_set_listening(self, on: bool) -> None:
        orb = self._orb
        if orb is not None:
            self._post_gui_call(lambda: orb.set_listening(on))
        self._post_gui_call(lambda: self._apply_listening_indicator(on))

    def _apply_listening_indicator(self, on: bool) -> None:
        if self._listen_label is not None:
            self._listen_label.config(text="● hearing" if on else "")

    def _point_with_orb(self, x: int, y: int, label: str = "here", seconds: float = 4.0) -> bool:
        """MO's desktop pointer: glide the moon to a target. Called from the
        Gateway/tool thread, so it marshals the move onto the GUI thread. Returns
        True once queued (the orb owns the point); False lets the caller fall back
        to the one-shot overlay bubble."""
        orb = self._orb
        if orb is None:
            return False
        return self._post_gui_call(lambda: orb.point_to(x, y, label, seconds))

    def _try_register_hotkey(self) -> None:
        try:
            import keyboard
        except ImportError:
            # Don't fail silently — tell the operator why Win+Alt+M is dead and
            # how to reach Desktop Ghost meanwhile.
            sys.stderr.write(
                "[ghost-desktop] Win+Alt+M hotkey unavailable: `pip install keyboard`. "
                "Summon Ghost with `/ghost window` in the meantime.\n")
            return
        try:
            self._hotkey_listener = keyboard.add_hotkey("win+alt+m", self._on_hotkey)
            sys.stderr.write("[companion] ready: Win+Alt+M registered (voice-first).\n")
        except Exception:
            sys.stderr.write(
                "[ghost-desktop] could not register Win+Alt+M (global hotkeys may need "
                "elevation). Use `/ghost window` to summon it.\n")
            traceback.print_exc()

    def _unregister_hotkey(self) -> None:
        if self._hotkey_listener:
            try:
                import keyboard
                keyboard.remove_hotkey(self._hotkey_listener)
            except Exception:
                pass
            self._hotkey_listener = None


# ------------------------------------------------------------------
# Service starter (follows start_*_if_enabled pattern)
# ------------------------------------------------------------------

def ghost_surface_config(config: Any) -> dict:
    """Read the desktop Ghost config block, preferring the new ``ghost`` key and
    falling back to the legacy ``desktop_companion`` key for back-compat."""
    if not isinstance(config, dict):
        return {}
    block = config.get("ghost")
    if not isinstance(block, dict):
        block = config.get("desktop_companion")
    return block if isinstance(block, dict) else {}


def start_companion_if_enabled(agent: Any, gateway: Any) -> CompanionSurface | None:
    """Start the desktop companion if config says so and deps are present.

    Mirrors start_telegram_gateway_if_enabled / start_heartbeat_service_if_enabled.
    Returns the CompanionSurface instance if started, None otherwise.
    """
    try:
        companion_cfg = ghost_surface_config(getattr(agent, "config", None) or {})
    except Exception:
        companion_cfg = {}

    if not isinstance(companion_cfg, dict) or not companion_cfg.get("enabled", False):
        return None

    # Ghost Desktop runs as its OWN process by default — `python -m interface.ghost_desktop`
    # (or the run-at-startup shortcut). That way it survives terminal restarts, doesn't
    # launch with the terminal, and never blocks terminal boot. The terminal only co-hosts
    # it in-thread when explicitly opted in with `run_in_terminal: true`.
    if not companion_cfg.get("run_in_terminal", False):
        return None

    resource_lock = acquire_runtime_lock(lock_name="ghost.lock", legacy_lock_names=("mo-companion.lock",), label="MO Ghost")
    if resource_lock is None:
        return None

    try:
        companion = CompanionSurface(
            agent,
            gateway,
            voice_config=companion_cfg.get("voice", {}),
            companion_config=companion_cfg,
        )
        companion._runtime_lock = resource_lock
        if companion.start():
            return companion
        release_runtime_lock(resource_lock)
    except Exception:
        release_runtime_lock(resource_lock)
        traceback.print_exc()
    return None
