"""JSON-lines bridge between the native shell and one canonical MO terminal.

The bridge is intentionally small: it owns one existing ``mo.py`` process,
one ConPTY transport, and one bounded VT screen projection. It does not own
Agent/Gateway behavior or task truth. The native renderer can run this module
as a child process and keep all visual work outside Tk.
"""

from __future__ import annotations

import argparse
import base64
import codecs
from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import unicodedata
import uuid
from typing import Any, Mapping

from interface.terminal_host import mo_terminal_identity
from interface.workspace_screen import TerminalScreen
from interface.workspace_pty import workspace_terminal_environment
from prompt_toolkit.utils import get_cwidth


_WINDOW_VISUAL_ERROR = "canonical shell window effect failed"


class MoShellTerminal:
    """Run the canonical MO entry point inside a Windows pseudoconsole."""

    def __init__(
        self,
        *,
        cwd: str,
        config_path: str = "",
        columns: int = 120,
        rows: int = 36,
        on_change: Any = None,
    ) -> None:
        self.cwd = str(Path(cwd).expanduser().resolve(strict=False))
        self.config_path = str(config_path or "")
        self.columns = max(20, int(columns))
        self.rows = max(3, int(rows))
        self.on_change = on_change
        self.instance_id = uuid.uuid4().hex[:8]
        self._backend: Any = None
        self._screen: TerminalScreen | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

    @property
    def alive(self) -> bool:
        backend = self._backend
        return bool(backend is not None and backend.isalive())

    def start(self) -> None:
        if os.name != "nt":
            raise RuntimeError("MO Shell terminal bridge requires Windows ConPTY")
        if self._backend is not None:
            return
        from interface.workspace_conpty import NativeConPtyProcess

        entrypoint = Path(__file__).resolve().parents[1] / "mo.py"
        command = [sys.executable, str(entrypoint)]
        if self.config_path:
            command.extend(["--config", self.config_path])
        environment = workspace_terminal_environment()
        environment["MO_INSTANCE_ID"] = self.instance_id
        environment["MO_SHELL_TERMINAL"] = "1"
        environment["MO_PROJECT_CWD"] = self.cwd
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        self._screen = TerminalScreen(
            self.columns,
            self.rows,
            history=1_000,
            write_response=self._write,
        )
        self._backend = NativeConPtyProcess(
            command,
            cwd=self.cwd,
            env=environment,
            columns=self.columns,
            rows=self.rows,
        )
        self._reader = threading.Thread(
            target=self._read_loop,
            name=f"mo-shell-terminal-{self.instance_id}",
            daemon=True,
        )
        self._reader.start()
        self._notify()

    def _write(self, value: str) -> None:
        backend = self._backend
        if backend is not None and value:
            backend.write(str(value).encode("utf-8", errors="replace"))

    def write(self, value: str) -> None:
        self._write(str(value or ""))

    def resize(self, *, columns: int, rows: int) -> None:
        self.columns = max(20, int(columns))
        self.rows = max(3, int(rows))
        with self._lock:
            if self._screen is not None:
                self._screen.resize(columns=self.columns, rows=self.rows)
        if self._backend is not None:
            self._backend.resize(columns=self.columns, rows=self.rows)
        self._notify()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            screen = self._screen
            title = str(screen.title if screen is not None else "")[:128]
            identity = mo_terminal_identity(title)
            return {
                "type": "screen",
                "instance_id": self.instance_id,
                "alive": self.alive,
                "title": title,
                "busy": bool(identity and identity[2]),
                "columns": self.columns,
                "rows": self.rows,
                "fragments": [
                    [
                        fragment
                        for style, text in row
                        for fragment in _terminal_fragments(style, text)
                    ]
                    for row in screen.display_fragments()[-self.rows:]
                ] if screen is not None else [],
                "cursor": {
                    "x": int(screen.cursor_x) if screen is not None else 0,
                    "y": int(screen.cursor_y) if screen is not None else 0,
                    "visible": bool(screen.cursor_visible) if screen is not None else False,
                },
            }

    def _notify(self) -> None:
        callback = self.on_change
        if callable(callback):
            callback()

    def _read_loop(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while not self._stop.is_set():
                try:
                    chunk = self._backend.read(4096) if self._backend is not None else b""
                except (EOFError, OSError):
                    break
                if chunk:
                    text = decoder.decode(chunk)
                    with self._lock:
                        if text and self._screen is not None:
                            self._screen.feed(text)
                    self._notify()
                    continue
                if not self.alive:
                    break
        finally:
            tail = decoder.decode(b"", final=True)
            with self._lock:
                if tail and self._screen is not None:
                    self._screen.feed(tail)
            self._notify()

    def close(self) -> None:
        self._stop.set()
        backend = self._backend
        self._backend = None
        if backend is not None:
            backend.close()
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        self._reader = None


def _emit(payload: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _configure_output() -> None:
    """Keep Unicode terminal cells intact across Windows redirected pipes."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")


class ThemeWatcher:
    """Project the persisted canonical skin across the process boundary."""

    def __init__(self, *, config_path: str = "") -> None:
        from mo_desktop.visuals import load_desktop_config

        self._config = load_desktop_config(config_path=config_path)
        self._semantic_payload: dict[str, Any] | None = None

    def poll(self) -> dict[str, Any] | None:
        from mo_desktop.design import DEFAULT_CUBE_FORM
        from mo_desktop.visuals import desktop_visual_payload, load_desktop_visual_state
        from mo_desktop.settings import load_settings

        state = load_desktop_visual_state(self._config, refresh_skin=True)
        character = asdict(load_settings(self._config).character)
        character["edge_ratio"] = DEFAULT_CUBE_FORM.edge_ratio
        payload = desktop_visual_payload(
            state,
            character=character,
        )
        if payload == self._semantic_payload:
            return None
        self._semantic_payload = payload
        delivery = dict(payload)
        delivery["brand_icon"] = _brand_icon_payload(state)
        return delivery


def _terminal_fragment(style: str, text: str) -> dict[str, Any]:
    """Project TerminalScreen's existing styled fragment without another parser."""
    properties: dict[str, Any] = {
        "text": str(text),
        "columns": _terminal_columns(str(text)),
    }
    for token in str(style or "").split():
        if token.startswith("fg:"):
            color = _terminal_color(token[3:])
            if color:
                properties["foreground"] = color
        elif token in {"bold", "dim", "italic", "underline", "strike"}:
            properties[token] = True
    return properties


def _terminal_fragments(style: str, text: str) -> list[dict[str, Any]]:
    """Split only emoji runs so Windows can use its native emoji face."""
    runs: list[dict[str, Any]] = []
    current = ""
    current_is_emoji: bool | None = None
    for cluster in _terminal_text_clusters(str(text)):
        is_emoji = _is_emoji_cluster(cluster)
        if current and is_emoji != current_is_emoji:
            fragment = _terminal_fragment(style, current)
            if current_is_emoji:
                fragment["emoji"] = True
            runs.append(fragment)
            current = ""
        current += cluster
        current_is_emoji = is_emoji
    if current:
        fragment = _terminal_fragment(style, current)
        if current_is_emoji:
            fragment["emoji"] = True
        runs.append(fragment)
    return runs


def _terminal_text_clusters(text: str):
    cluster = ""
    for character in text:
        codepoint = ord(character)
        extends_cluster = bool(cluster) and (
            unicodedata.category(character).startswith("M")
            or character == "\u200d"
            or cluster.endswith("\u200d")
            or 0x1F3FB <= codepoint <= 0x1F3FF
            or 0xE0020 <= codepoint <= 0xE007F
            or (
                0x1F1E6 <= ord(cluster[0]) <= 0x1F1FF
                and len(cluster) == 1
                and 0x1F1E6 <= codepoint <= 0x1F1FF
            )
        )
        if cluster and not extends_cluster:
            yield cluster
            cluster = ""
        cluster += character
    if cluster:
        yield cluster


def _is_emoji_cluster(cluster: str) -> bool:
    return any(
        character == "\ufe0f"
        or ord(character) == 0x20E3
        or 0x1F000 <= ord(character) <= 0x1FAFF
        or 0x2600 <= ord(character) <= 0x27BF
        or 0x2B00 <= ord(character) <= 0x2BFF
        or ord(character) in {0x3030, 0x303D, 0x3297, 0x3299}
        for character in cluster
    )


def _terminal_color(value: str) -> str:
    value = str(value or "").strip().lower()
    if len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
        except ValueError:
            return ""
        return value
    from prompt_toolkit.output.vt100 import ANSI_COLORS_TO_RGB

    rgb = ANSI_COLORS_TO_RGB.get(value)
    if rgb is None or value == "ansidefault":
        return ""
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _terminal_columns(text: str) -> int:
    return max(0, int(get_cwidth(str(text or ""))))


def _brand_icon_payload(state: Any) -> str:
    """Serialize the canonical Desktop mark for the native taskbar identity."""
    from interface.desktop_brand import make_four_cube_icon

    stream = io.BytesIO()
    make_four_cube_icon(64, palette=state.palette).save(
        stream,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)],
    )
    return base64.b64encode(stream.getvalue()).decode("ascii")


class ShellWindowVisual:
    """Apply the canonical Desktop effect to one bounded Shell-owned surface."""

    def __init__(self) -> None:
        self._theme: dict[str, Any] | None = None
        self._geometry: dict[str, Any] | None = None
        self._effect: Any = None
        self._entrance: dict[str, Any] | None = None

    @property
    def entrance_active(self) -> bool:
        return bool(self._entrance and not self._entrance["finished"])

    def start_entrance(self, message: Mapping[str, Any]) -> None:
        from mo_desktop.layered import NativeLayeredWindow
        from mo_desktop.mo_renderer import _app_entrance_art

        art = _app_entrance_art(message["source"], message["target"])
        self._close_entrance()
        layer = NativeLayeredWindow(int(message["hwnd"]))
        self._entrance = {"art": art, "layer": layer, "started": time.monotonic(), "finished": False,
                          "published": False}
        self.pump()

    def _close_entrance(self) -> None:
        entrance, self._entrance = self._entrance, None
        if entrance is not None:
            entrance["layer"].destroy()

    def set_theme(self, payload: Mapping[str, Any]) -> None:
        self._theme = dict(payload)
        self._apply()

    def update(self, message: Mapping[str, Any]) -> None:
        geometry = {
            "hwnd": int(message.get("hwnd", 0)),
            "owner_width": int(message.get("owner_width", 0)),
            "owner_height": int(message.get("owner_height", 0)),
            "x": int(message.get("x", -1)),
            "y": int(message.get("y", -1)),
            "width": int(message.get("width", 0)),
            "height": int(message.get("height", 0)),
            "radius": int(message.get("radius", -1)),
            "visible": bool(message.get("visible", False)),
        }
        if (
            geometry["hwnd"] <= 0
            or not (1 <= geometry["owner_width"] <= 32_768)
            or not (1 <= geometry["owner_height"] <= 32_768)
            or geometry["x"] < 0
            or geometry["y"] < 0
            or geometry["width"] < 1
            or geometry["height"] < 1
            or geometry["x"] + geometry["width"] > geometry["owner_width"]
            or geometry["y"] + geometry["height"] > geometry["owner_height"]
            or geometry["radius"] < 0
            or geometry["radius"] > min(geometry["width"], geometry["height"]) // 2
        ):
            raise ValueError("invalid MO Shell surface geometry")
        self._geometry = geometry
        if geometry["visible"]:
            self._close_entrance()
        self._apply()

    def _apply(self) -> None:
        geometry = self._geometry
        if geometry is None:
            return
        if not geometry["visible"]:
            if self._effect is not None:
                self._effect.hide()
            return
        theme = self._theme
        if theme is None:
            return
        panel = theme.get("panel")
        tokens = theme.get("tokens")
        if not isinstance(panel, Mapping) or not isinstance(tokens, Mapping):
            raise ValueError("MO Shell visual payload is incomplete")
        from interface.desktop_ui import desktop_window_effects
        from interface.desktop_widgets import DesktopWindowEffectLayer

        if self._effect is None:
            self._effect = DesktopWindowEffectLayer()
        self._effect.refresh_hwnd(
            geometry["hwnd"],
            geometry["width"],
            geometry["height"],
            geometry["radius"],
            desktop_window_effects(panel),
            str(tokens["glow"]),
            content_rect=(
                geometry["x"],
                geometry["y"],
                geometry["width"],
                geometry["height"],
            ),
            hollow=True,
        )

    def pump(self) -> None:
        entrance = self._entrance
        if entrance and not entrance["finished"]:
            from mo_desktop.mo_renderer import _app_entrance_frame

            elapsed = time.monotonic() - entrance["started"]
            try:
                frame = _app_entrance_frame(entrance["art"], elapsed)
                confirmed = entrance["layer"].blit(frame, *entrance["art"]["position"])
            except (OSError, RuntimeError, TypeError, ValueError):
                confirmed = False
            if confirmed and not entrance["published"]:
                entrance["published"] = True
                _emit({"type": "entrance_started"})
            if not confirmed or elapsed >= entrance["art"]["duration"]:
                entrance["finished"] = True
                _emit({"type": "entrance_finished", "confirmed": bool(confirmed)})
                if not confirmed:
                    self._close_entrance()
        if self._effect is None and self._entrance is None:
            return
        from mo_desktop.layered import pump_native_window_messages

        pump_native_window_messages()

    def close(self) -> None:
        self._close_entrance()
        effect, self._effect = self._effect, None
        if effect is not None:
            effect.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MO Shell ConPTY bridge")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--config", default="")
    parser.add_argument("--columns", type=int, default=120)
    parser.add_argument("--rows", type=int, default=36)
    args = parser.parse_args(argv)
    _configure_output()
    screen_changed = threading.Event()
    terminal = MoShellTerminal(
        cwd=args.cwd,
        config_path=args.config,
        columns=args.columns,
        rows=args.rows,
        on_change=screen_changed.set,
    )
    command_stream: queue.Queue[dict[str, Any]] = queue.Queue()
    window_visual = ShellWindowVisual()

    def read_commands() -> None:
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except (TypeError, ValueError):
                command_stream.put({"type": "invalid"})
                continue
            if isinstance(message, dict):
                command_stream.put(message)
            else:
                command_stream.put({"type": "invalid"})
        command_stream.put({"type": "close"})

    try:
        theme_watcher = ThemeWatcher(config_path=args.config)
        initial_theme = theme_watcher.poll()
        if initial_theme is not None:
            window_visual.set_theme(initial_theme)
            _emit({"type": "theme", "payload": initial_theme})
        terminal.start()
        _emit({"type": "ready", "instance_id": terminal.instance_id})
        _emit(terminal.snapshot())
        screen_changed.clear()
        threading.Thread(target=read_commands, name="mo-shell-bridge-input", daemon=True).start()
        running = True
        next_theme_poll = time.monotonic() + 0.25
        theme_error_reported = False
        window_visual_error_reported = False
        while running:
            try:
                message = command_stream.get(timeout=0.016 if window_visual.entrance_active else 0.05)
                kind = str(message.get("type") or "")
                if kind == "input":
                    terminal.write(str(message.get("text") or ""))
                elif kind == "resize":
                    terminal.resize(
                        columns=int(message.get("columns", terminal.columns)),
                        rows=int(message.get("rows", terminal.rows)),
                    )
                elif kind == "snapshot":
                    _emit(terminal.snapshot())
                elif kind == "window_visual":
                    try:
                        window_visual.update(message)
                    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                        if not window_visual_error_reported:
                            _emit({"type": "error", "message": _WINDOW_VISUAL_ERROR})
                            window_visual_error_reported = True
                    else:
                        if window_visual_error_reported and bool(message.get("visible", False)):
                            _emit({"type": "recovered", "message": _WINDOW_VISUAL_ERROR})
                            window_visual_error_reported = False
                elif kind == "window_entrance":
                    try:
                        window_visual.start_entrance(message)
                    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError):
                        window_visual._close_entrance()
                        _emit({"type": "entrance_finished", "confirmed": False})
                elif kind == "invalid":
                    _emit({"type": "error", "message": "invalid shell bridge message"})
                elif kind == "close":
                    running = False
            except queue.Empty:
                pass
            now = time.monotonic()
            if now >= next_theme_poll:
                next_theme_poll = now + 0.25
                try:
                    changed_theme = theme_watcher.poll()
                    theme_error_reported = False
                    if changed_theme is not None:
                        window_visual.set_theme(changed_theme)
                        _emit({"type": "theme", "payload": changed_theme})
                except (OSError, RuntimeError, TypeError, ValueError):
                    if not theme_error_reported:
                        _emit({"type": "error", "message": "canonical theme refresh failed"})
                        theme_error_reported = True
            if screen_changed.is_set():
                screen_changed.clear()
                _emit(terminal.snapshot())
            window_visual.pump()
    finally:
        window_visual.close()
        terminal.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MoShellTerminal", "ShellWindowVisual", "ThemeWatcher", "main"]
