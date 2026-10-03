"""Native Live Control lane for one actually running MO terminal TUI."""
from __future__ import annotations

import json
import threading
from typing import Any, Callable


MAX_REMOTE_INPUT_CHARS = 12_000
MAX_TERMINAL_SNAPSHOT_BYTES = 12 * 1024
MAX_TERMINAL_LINES = 160
_TERMINAL_KEY_NAMES = frozenset({
    "backspace", "delete", "down", "end", "enter", "escape", "home",
    "left", "pagedown", "pageup", "right", "space", "tab", "up",
})
_TERMINAL_MODIFIERS = frozenset({"alt", "ctrl", "shift"})


class TerminalLiveLane:
    """Expose bounded transcript snapshots and the existing TUI input boundary."""

    command_menu = True

    def __init__(self, tui: Any):
        self.tui = tui
        self._session_id = ""
        self._send_event: Callable[[str, dict[str, Any]], bool] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_signature = ""

    def open(self, session_id: str, send_event: Callable[..., bool], _send_frame: Callable[..., bool]) -> None:
        self.close(self._session_id)
        self._session_id = session_id
        self._send_event = send_event
        self._last_signature = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, args=(self._stop,), name="mo-live-terminal", daemon=True)
        self._thread.start()

    def receive(self, session_id: str, payload: dict[str, Any]) -> None:
        if session_id != self._session_id:
            return
        kind = str(payload.get("type") or "")
        if kind == "client_connected":
            self._last_signature = ""
            self._emit_snapshot()
            return
        if kind == "terminal_command":
            def _command() -> None:
                # A queued request belongs only to the lease that admitted it.
                if self._session_id != session_id or self._send_event is None:
                    return
                result = self._command_result(payload)
                self._send_event(session_id, result)

            if not self._dispatch_ui(_command):
                self._notice("MO terminal is not ready for commands")
            return
        if kind == "text":
            text = str(payload.get("value") or "")
            if not _valid_input(text):
                self._notice("Input rejected")
                return
            if _is_local_command(text):
                self._notice("Local commands are not available remotely")
                return

            def _submit() -> None:
                try:
                    self.tui._handle_input(text)
                except Exception:
                    self._notice("Input could not be submitted")
                    return
                self._notice("Sent to this MO terminal")

            if not self._dispatch_ui(_submit):
                self._notice("MO terminal is not ready for input")
            return
        if kind == "key" and str(payload.get("key") or "").lower() == "escape" and not payload.get("modifiers"):

            def _escape() -> None:
                try:
                    handled = bool(self.tui._handle_busy_escape())
                except Exception:
                    handled = False
                notice = str(getattr(self.tui, "_last_busy_escape_notice", "") or "")
                self._notice(notice if handled and notice else "Escape handled" if handled else "Nothing to stop")

            if not self._dispatch_ui(_escape):
                self._notice("MO terminal is not ready for input")
            return
        if kind == "key":
            key = str(payload.get("key") or "").lower()
            action = str(payload.get("action") or "press").lower()
            raw_modifiers = payload.get("modifiers")
            modifiers = [str(value or "").lower() for value in raw_modifiers] if isinstance(raw_modifiers, list) else []
            presses = _terminal_key_presses(key, action, modifiers)
            if presses is None:
                self._notice("That key combination is not supported by this MO terminal")
                return

            def _press() -> None:
                processor = getattr(getattr(self.tui, "_app", None), "key_processor", None)
                if processor is None:
                    self._notice("MO terminal is not ready for key input")
                    return
                try:
                    for press in presses:
                        processor.feed(press)
                except Exception:
                    self._notice("Key input could not be submitted")
                    return
                self._notice("Key sent to this MO terminal")

            if not self._dispatch_ui(_press):
                self._notice("MO terminal is not ready for key input")

    def _command_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .command_palette import CommandPalette, PaletteItem
        from .command_registry import resolve_slash_command, slash_command_spec

        raw_value = str(payload["value"])
        value = raw_value.strip()
        action = payload["action"]
        rows = []
        title = value
        mode, text = "menu", ""
        try:
            # The host wire carries menu rows, not the local tab state. Help
            # uses its existing all-roots query so every host command remains
            # reachable without borrowing the client's catalog or a new protocol.
            host_help = resolve_slash_command(value.split()[0]) == "/help"
            if action == "query" or host_help:
                menu = CommandPalette()
                if host_help:
                    menu.set_query("/")
                    rows = menu._query_matches()
                elif any(char.isspace() for char in raw_value):
                    if self.tui._maybe_show_command_args(raw_value, palette=menu):
                        rows = menu._current_items()
                    else:
                        mode = "input"
                else:
                    menu.set_query(value)
                    rows = menu._query_matches()
            else:
                root = resolve_slash_command(value.split()[0])
                spec = slash_command_spec(root)
                if spec is None or not spec.palette:
                    raise ValueError("Command is not available in this host's menu")
                if action == "select":
                    rows = self.tui._palette_children_for_item(PaletteItem(value, value))
                    rows = [
                        PaletteItem(row.value, row.label, row.desc, "run" if row.value == value else row.kind)
                        for row in rows
                    ]
                if not rows:
                    self.tui._palette.close()
                    self.tui._notice_text = ""
                    self.tui._run_palette_command(value)
                    palette = self.tui._palette
                    if palette.result_active:
                        mode, text = palette._result_kind, palette._result_text
                        title = palette._result_title
                        rows = palette._result_actions
                    elif palette.in_submenu:
                        rows = palette._current_items()
                        title = palette._stack[-1][0]
                    else:
                        mode, text = "notice", str(getattr(self.tui, "_notice_text", "") or "Command activated")
        except Exception:
            mode, text = "error", "Host command could not be completed"
        items = []
        used = 0
        for row in rows[:128]:
            item = [row.value[:2048], row.label[:160], row.desc[:160], row.kind]
            cost = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if used + cost > 10_000:
                break
            items.append(item)
            used += cost
        return {
            "type": "terminal_command_result", "request_id": payload["request_id"],
            "mode": mode, "title": title[:160],
            "text": text.encode("utf-8")[:10_000].decode("utf-8", errors="ignore"),
            "items": items,
        }

    def close(self, session_id: str) -> None:
        if session_id and self._session_id and session_id != self._session_id:
            return
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None
        self._session_id = ""
        self._send_event = None
        self._last_signature = ""

    def _watch(self, stop: threading.Event) -> None:
        while not stop.wait(0.35):
            self._emit_snapshot(stop)

    def _emit_snapshot(self, stop: threading.Event | None = None) -> None:
        stop = self._stop if stop is None else stop
        session_id = self._session_id
        sender = self._send_event
        if not session_id or sender is None or stop is not self._stop or stop.is_set():
            return
        payload = _terminal_payload(self.tui)
        signature = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if signature == self._last_signature:
            return
        if sender(session_id, payload) and not stop.is_set():
            self._last_signature = signature

    def _notice(self, message: str) -> None:
        if self._session_id and self._send_event is not None:
            self._send_event(self._session_id, {"type": "notice", "message": str(message or "")[:120]})

    def _dispatch_ui(self, callback: Callable[[], None]) -> bool:
        app = getattr(self.tui, "_app", None)
        loop = getattr(app, "loop", None)
        if loop is None:
            return False
        try:
            loop.call_soon_threadsafe(callback)
            return True
        except Exception:
            return False


def start_terminal_live_control(tui: Any) -> Any | None:
    config = getattr(getattr(tui, "agent", None), "config", None) or {}
    try:
        from core.runtime.instance import get_instance_id
        from core.files.host import FileHostLane, WORKSTATION_FILES_LANE
        from mo_everywhere.live_host import LiveControlHost

        instance_id = get_instance_id()
        file_lane = FileHostLane(config)
        handlers = {"mo_session": TerminalLiveLane(tui)}
        if file_lane.enabled:
            handlers[WORKSTATION_FILES_LANE] = file_lane
        host = LiveControlHost(
            config,
            instance_key=f"terminal:{instance_id}",
            label=f"MO Terminal · {instance_id}",
            handlers=handlers,
        )
        return host if host.start() else None
    except Exception:
        return None


def _terminal_payload(tui: Any) -> dict[str, Any]:
    try:
        logical = tui._logical_transcript_lines()
    except Exception:
        logical = []
    lines = ["".join(str(text) for _style, text in line) for line in logical]
    lines = lines[-MAX_TERMINAL_LINES:]
    busy = bool(getattr(tui, "busy", False))
    activity = " ".join(str(getattr(tui, "activity_text", "") or "").split())[:160]
    kept: list[str] = []
    used = 128
    for line in reversed(lines):
        clean = line.replace("\x00", "")[:2_000]
        cost = len(clean.encode("utf-8")) + 8
        if kept and used + cost > MAX_TERMINAL_SNAPSHOT_BYTES:
            break
        kept.append(clean)
        used += cost
    kept.reverse()
    attention = any(
        "[APPROVAL REQUIRED" in line.upper() or "[PIXEL APPROVAL REQUIRED" in line.upper()
        for line in kept[-8:]
    )
    return {
        "type": "terminal_snapshot",
        "lines": kept,
        "busy": busy,
        "activity": activity,
        "attention": attention,
    }


def _is_local_command(value: str) -> bool:
    """True when remote text would reach the local slash-command dispatcher.

    Ordinary messages remain separate from explicit terminal-command requests.
    The latter use this exact authenticated terminal lease and the host's own
    command menu/handlers; text must not accidentally enter that control path.
    """
    return str(value or "").strip().startswith("/")


def _valid_input(value: str) -> bool:
    return (
        1 <= len(value) <= MAX_REMOTE_INPUT_CHARS
        and bool(value.strip())
        and all(ord(char) >= 0x20 or char in "\t\r\n" for char in value)
        and "\x7f" not in value
    )


def _terminal_key_presses(key: str, action: str, modifiers: list[str]) -> list[Any] | None:
    normalized = str(key or "").lower()
    values = [str(value or "").lower() for value in modifiers]
    if (
        action != "press"
        or len(values) > 4
        or len(set(values)) != len(values)
        or any(value not in _TERMINAL_MODIFIERS for value in values)
        or (
            normalized not in _TERMINAL_KEY_NAMES
            and (len(normalized) != 1 or not 0x21 <= ord(normalized) <= 0x7E)
        )
    ):
        return None
    try:
        from prompt_toolkit.key_binding.key_processor import KeyPress
        from prompt_toolkit.keys import Keys
    except Exception:
        return None
    special = {
        "backspace": Keys.Backspace,
        "delete": Keys.Delete,
        "down": Keys.Down,
        "end": Keys.End,
        "enter": Keys.Enter,
        "escape": Keys.Escape,
        "home": Keys.Home,
        "left": Keys.Left,
        "pagedown": Keys.PageDown,
        "pageup": Keys.PageUp,
        "right": Keys.Right,
        "space": " ",
        "tab": Keys.Tab,
        "up": Keys.Up,
    }
    if "ctrl" in values:
        if "shift" in values:
            control_shift_name = {
                "left": "ControlShiftLeft",
                "right": "ControlShiftRight",
                "up": "ControlShiftUp",
                "down": "ControlShiftDown",
                "home": "ControlShiftHome",
                "end": "ControlShiftEnd",
                "pageup": "ControlShiftPageUp",
                "pagedown": "ControlShiftPageDown",
                "delete": "ControlShiftDelete",
            }.get(normalized)
            base = getattr(Keys, control_shift_name, None) if control_shift_name else None
        elif len(normalized) == 1 and normalized.isalpha():
            base = getattr(Keys, f"Control{normalized.upper()}", None)
        else:
            control_name = {
                "enter": "ControlM",
                "tab": "ControlI",
                "space": "ControlAt",
                "backspace": "ControlH",
                "left": "ControlLeft",
                "right": "ControlRight",
                "up": "ControlUp",
                "down": "ControlDown",
                "home": "ControlHome",
                "end": "ControlEnd",
                "pageup": "ControlPageUp",
                "pagedown": "ControlPageDown",
                "delete": "ControlDelete",
            }.get(normalized)
            base = getattr(Keys, control_name, None) if control_name else None
        if base is None:
            return None
    elif "shift" in values:
        if normalized == "tab":
            base = Keys.BackTab
        elif len(normalized) == 1:
            base = normalized.upper()
        else:
            shift_name = {
                "left": "ShiftLeft",
                "right": "ShiftRight",
                "up": "ShiftUp",
                "down": "ShiftDown",
                "home": "ShiftHome",
                "end": "ShiftEnd",
                "pageup": "ShiftPageUp",
                "pagedown": "ShiftPageDown",
                "delete": "ShiftDelete",
                "escape": "ShiftEscape",
            }.get(normalized)
            base = getattr(Keys, shift_name, None) if shift_name else None
            if base is None:
                return None
    else:
        base = special.get(normalized, normalized)
    data = base if isinstance(base, str) else None
    presses = [KeyPress(base, data=data)]
    if "alt" in values:
        presses.insert(0, KeyPress(Keys.Escape))
    return presses
