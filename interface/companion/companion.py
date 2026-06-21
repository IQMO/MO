"""MO Companion — the on-screen text-input surface.

Summon with Win+Alt+M (global hotkey) or `/companion` slash command. Type a
request, press Enter, and MO processes it via Ghost → Gateway with results
shown in a MO-branded overlay bubble. Runs as a daemon thread alongside the TUI.

Architecture
    [Companion tkinter window] → Gateway.run_turn(route_source="desktop")
                                 → Ghost shapes → MO executes → overlay bubble

No voice/tray yet — those are Phase 3 and 4.
Phase 3 (voice): push-to-talk STT/TTS via faster-whisper + piper-tts.
Phase 4 (tray, modes, log, panic): system-tray icon, Guide/Do modes,
    visible action log, run-at-startup, panic-stop.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from typing import Any

from core.sandbox import redact_sensitive_text
from interface.companion.voice import CompanionVoice, VoiceRecognizer, VoiceSpeaker
from interface.companion.tray import CompanionTray, start_tray_if_enabled

CYAN = "#00cccc"
CARD = "#04141a"
TEXT = "#dff6f6"
GLYPH = "◐"  # ◐ half-moon = MO mark
_ENTRY_BG = "#0a2028"


class CompanionSurface:
    """On-screen MO companion with text input and result display."""

    def __init__(self, agent: Any, gateway: Any, voice_config: dict | None = None) -> None:
        self._agent = agent
        self._gateway = gateway
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
        self._visible = False

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
        self._init_voice()
        self._init_tray()
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
    # Tray + startup + panic-stop (Phase 4)
    # ------------------------------------------------------------------

    def _init_tray(self) -> None:
        """Start system tray if configured."""
        self._tray = start_tray_if_enabled(self, self._voice_cfg)

    @property
    def mode(self) -> str:
        return self._tray.mode if self._tray else self._mode

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
        if not self._root:
            return
        try:
            self._root.event_generate("<<CompanionShowLog>>")
        except Exception:
            pass

    def _show_log_popup(self, root: Any) -> None:
        """Create and display the action log popup window."""
        import tkinter as tk

        popup = tk.Toplevel(root)
        popup.title("MO Companion — Action Log")
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try:
            popup.attributes("-alpha", 0.95)
        except Exception:
            pass

        border = tk.Frame(popup, bg=CYAN)
        border.pack()
        card = tk.Frame(border, bg=CARD)
        card.pack(padx=2, pady=2)

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
        if voice is None or not voice.stt_available:
            self._set_status("Voice input not available (install faster-whisper + sounddevice)", "#ffcc44")
            return
        if self._recording_voice:
            # second click → stop, transcribe, submit
            self._recording_voice = False
            self._set_status("Transcribing…", CYAN)

            def _finish() -> None:
                text = voice.stop_and_transcribe()
                if text and not text.startswith("[STT") and not text.startswith("[Voice"):
                    self._panic_stop_requested = False  # explicit action resumes after panic
                    self._turn_thread = threading.Thread(
                        target=self._run_turn, args=(text,), name="mo-companion-turn", daemon=True)
                    self._turn_thread.start()
                else:
                    self._set_status(text or "[No speech detected]", "#ff4444")

            threading.Thread(target=_finish, name="mo-companion-voice", daemon=True).start()
            return
        # first click → start (don't start over a running turn)
        if self._turn_thread is not None and self._turn_thread.is_alive():
            self._set_status("Still working on the previous request…", "#ffcc44")
            return
        if voice.start_recording():
            self._recording_voice = True
            self._set_status("Recording… click 🎤 again to stop", CYAN)
        else:
            self._set_status("Could not start the microphone.", "#ff4444")

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

        # text entry + voice button
        entry_frame = tk.Frame(card, bg=CARD)
        entry_frame.pack(fill="x", padx=10, pady=(0, 6))

        # voice mic button (🎤)
        self._voice_btn = tk.Label(entry_frame, text="🎤", fg=CYAN, bg=CARD,
                                   font=("Segoe UI", 14), cursor="hand2")
        self._voice_btn.pack(side="right", padx=(6, 0))
        self._voice_btn.bind("<Button-1>", lambda _e: self._on_voice_input())

        self._entry = tk.Entry(entry_frame, font=("Segoe UI", 11),
                               fg=TEXT, bg=_ENTRY_BG,
                               insertbackground=CYAN, relief="flat",
                               borderwidth=4)
        self._entry.pack(side="left", fill="x", expand=True)
        self._entry.bind("<Return>", self._on_submit)
        self._entry.bind("<Escape>", lambda _e: self.hide())

        # response label
        self._response = tk.Label(card, text="", fg=TEXT, bg=CARD,
                                  font=("Segoe UI", 10), anchor="w",
                                  justify="left", wraplength=380)
        self._response.pack(fill="both", padx=10, pady=(0, 8))

        # bottom hint
        tk.Label(card, text="Esc to hide  ·  Win+Alt+M to summon  ·  🎤 for voice",
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
        root.bind("<<CompanionShowLog>>", lambda _e: self._show_log_popup(root))

        while self._running:
            try:
                root.update()
                root.update_idletasks()
                time.sleep(0.05)  # ~20fps; blocking yield so the loop never busy-spins
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
            with self._agent.lane_scope(lane):
                result = self._gateway.run_turn(
                    user_input,
                    route_source="desktop",
                    on_activity=self._on_activity,
                    on_assistant_text=self._on_assistant_text,
                    on_board_event=self._on_board_event,
                    on_proposal=self._on_proposal,
                    cancel_event=self._cancel_event,
                )
            self._set_result(self._append_task_board(result))
            self._log_action("turn_complete", result[:200])
            # Speak result if TTS enabled
            if self._voice and self._voice.tts_available:
                self._voice.speak_result(result)
        except Exception as exc:
            self._set_status(f"Error: {exc}", "#ff4444")
            self._log_action("turn_error", str(exc)[:200])
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

    def _on_proposal(self, plan_text: str) -> None:
        # Show Ghost's plan before MO routes the work — same contract as the TUI /
        # Telegram. MO's structured reasoning must not be invisible on this surface.
        plan = " ".join(str(plan_text or "").split())
        if plan:
            self._set_response("Plan: " + plan[:500])
            self._log_action("ghost_plan", plan[:200])

    def _append_task_board(self, reply: str) -> str:
        # Taskboard is MO's product contract — every surface shows the same
        # evidence-gated board. Mirrors the Telegram surface (board.render()),
        # best-effort so a render glitch never swallows the answer.
        text = str(reply or "")
        try:
            board = getattr(self._gateway, "last_task_board", None)
            if board is None or not getattr(board, "tasks", None):
                return text
            rendered = str(board.render() or "").strip()
            if not rendered or rendered in text:
                return text
            return f"{text}\n\n{rendered}" if text else rendered
        except Exception:
            return text

    def _on_assistant_text(self, delta: str) -> None:
        # Called from the Gateway thread — tkinter is NOT thread-safe, so never
        # touch widgets here. Accumulate and schedule the update on the GUI thread.
        if not self._root:
            return
        self._stream_buf += str(delta or "")
        buf = self._stream_buf[:600]
        try:
            self._root.after(0, lambda: self._response.config(text=buf) if self._response else None)
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
            self._root.after(0, lambda: self._response.config(text=text[:1500]))
        except Exception:
            pass

    def _set_result(self, text: str) -> None:
        summary = (text or "").strip()
        if len(summary) > 1200:
            summary = summary[:1197] + "..."
        self._set_response(summary)
        self._set_status("Done — Esc to hide", "#44cc88")

    # ------------------------------------------------------------------
    # Global hotkey (optional)
    # ------------------------------------------------------------------

    def _try_register_hotkey(self) -> None:
        try:
            import keyboard
        except ImportError:
            # Don't fail silently — tell the operator why Win+Alt+M is dead and
            # how to reach the companion meanwhile.
            sys.stderr.write(
                "[companion] Win+Alt+M hotkey unavailable: `pip install keyboard`. "
                "Summon the companion with the /companion command in the meantime.\n")
            return
        try:
            keyboard.add_hotkey("win+alt+m", self.toggle)
            self._hotkey_listener = True
        except Exception:
            sys.stderr.write(
                "[companion] could not register Win+Alt+M (global hotkeys may need "
                "elevation). Use the /companion command to summon it.\n")
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
        companion = CompanionSurface(agent, gateway,
                                     voice_config=companion_cfg.get("voice", {}))
        if companion.start():
            return companion
    except Exception:
        traceback.print_exc()
    return None
