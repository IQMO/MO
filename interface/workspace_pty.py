"""Bounded local PTY process used by MO's split-terminal workspace.

The native backend and MO-owned VT screen are loaded only when a pane is
created, so ordinary MO startup keeps its existing light import path.
"""
from __future__ import annotations

import codecs
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .internal_terminal import _default_shell
from .workspace_transport import WorkspaceChangeNotifier

_DEFAULT_COLUMNS = 80
_DEFAULT_ROWS = 24
_HISTORY_LINES = 1_000
_MO_ROUTING_ENV = frozenset({
    "MO_PROJECT_CWD",
    "MO_INSTANCE_ID",
    "MO_PARENT_INSTANCE_ID",
    "MO_SESSION_ID",
    "MO_SESSION_SLOT",
    "MO_TRACE_SESSION_ID",
    "MO_WORKSPACE_INDEX",
})


def workspace_terminal_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Inherit shell preferences while letting a new MO bind its actual cwd."""
    env = {str(key): str(value) for key, value in os.environ.items()}
    for key, value in (extra or {}).items():
        env[str(key)] = str(value)
    for key in list(env):
        if key in _MO_ROUTING_ENV or key.startswith("MO_WORKSPACE_"):
            env.pop(key, None)
    return env


class LocalTerminalProcess(WorkspaceChangeNotifier):
    """One ordinary local shell connected to a real PTY/ConPTY."""

    def __init__(
        self,
        *,
        pane_id: str,
        cwd: str,
        on_change: Callable[[], None] | None = None,
        env: dict[str, str] | None = None,
        columns: int = _DEFAULT_COLUMNS,
        rows: int = _DEFAULT_ROWS,
    ) -> None:
        self.pane_id = str(pane_id)
        self.cwd = str(Path(cwd or os.getcwd()).resolve())
        self.cwd_label = Path(self.cwd).name or self.cwd
        self.on_change = on_change
        self.env = workspace_terminal_environment(env)
        self.columns = max(20, int(columns or _DEFAULT_COLUMNS))
        self.rows = max(3, int(rows or _DEFAULT_ROWS))
        self.state = "starting"
        self.error = ""
        self.exit_code: int | None = None
        self._backend: Any = None
        self._screen: Any = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._closed = False

    @property
    def alive(self) -> bool:
        backend = self._backend
        if backend is None:
            return False
        try:
            if os.name == "nt":
                return bool(backend.isalive())
            return backend.poll() is None
        except Exception:
            return False

    @property
    def root_pid(self) -> int | None:
        """Return the exact shell PID that owns this local terminal tree."""
        backend = self._backend
        if backend is None:
            return None
        try:
            pid = int(getattr(backend, "pid", 0) or 0)
        except (TypeError, ValueError):
            return None
        return pid if pid > 0 else None

    @property
    def terminal_title(self) -> str:
        """Expose the child terminal's OSC title without duplicating identity state."""
        with self._lock:
            return str(getattr(self._screen, "title", "") or "")[:128]

    def start(self) -> None:
        """Spawn the platform PTY once and start its bounded reader thread."""
        with self._lock:
            if self._backend is not None:
                return
            try:
                from .workspace_screen import TerminalScreen

                self._screen = TerminalScreen(
                    self.columns,
                    self.rows,
                    history=_HISTORY_LINES,
                    write_response=self._write_terminal_response,
                )
                if os.name == "nt":
                    from .workspace_conpty import NativeConPtyProcess

                    self._backend = NativeConPtyProcess(
                        _default_shell(),
                        cwd=self.cwd,
                        env=self.env,
                        columns=self.columns,
                        rows=self.rows,
                    )
                else:
                    self._backend = self._spawn_posix()
                self.state = "running"
            except Exception as exc:
                self.state = "error"
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                self._notify()
                raise
        self._reader = threading.Thread(
            target=self._read_loop,
            name=f"mo-workspace-pty-{self.pane_id}",
            daemon=True,
        )
        self._reader.start()
        self._notify()

    def _spawn_posix(self):
        import fcntl
        import pty
        import struct
        import termios

        master_fd, slave_fd = pty.openpty()
        try:
            fcntl.ioctl(
                slave_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", self.rows, self.columns, 0, 0),
            )
            process = subprocess.Popen(
                _default_shell(),
                cwd=self.cwd,
                env=self.env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        process._mo_workspace_master_fd = master_fd  # type: ignore[attr-defined]
        return process

    def _read_once(self) -> bytes:
        if os.name == "nt":
            return bytes(self._backend.read(4096) or b"")
        master_fd = int(getattr(self._backend, "_mo_workspace_master_fd"))
        return os.read(master_fd, 4096)

    def _read_loop(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while not self._stop.is_set():
                try:
                    chunk = self._read_once()
                except (EOFError, OSError):
                    break
                except Exception as exc:
                    if not self._stop.is_set():
                        with self._lock:
                            self.error = f"{type(exc).__name__}: {exc}"[:300]
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
                time.sleep(0.01)
        finally:
            tail = decoder.decode(b"", final=True)
            with self._lock:
                if tail and self._screen is not None:
                    self._screen.feed(tail)
                if self.state not in {"closing", "error"}:
                    self.state = "exited"
                self.exit_code = self._read_exit_code()
            self._notify()

    def _read_exit_code(self) -> int | None:
        try:
            if os.name == "nt":
                value = self._backend.exitstatus
                return int(value) if value is not None else None
            value = self._backend.poll()
            return int(value) if value is not None else None
        except Exception:
            return None

    def screen_lines(self) -> tuple[str, ...]:
        """Return only the current VT screen, with trailing empty rows removed."""
        with self._lock:
            if self._screen is None:
                return ()
            return tuple(self._screen.display_lines())[-self.rows:]

    def screen_fragments(self) -> tuple[tuple[tuple[str, str], ...], ...]:
        """Return prompt-toolkit fragments for the visible screen."""
        with self._lock:
            if self._screen is None:
                return ()
            return tuple(self._screen.display_fragments())[-self.rows:]

    def scrollback_fragments(self) -> tuple[tuple[tuple[str, str], ...], ...]:
        """Return the terminal model's bounded history for workspace scrolling."""
        with self._lock:
            if self._screen is None:
                return ()
            return tuple(self._screen.scrollback_fragments())

    def scroll_view(self, delta: int) -> bool:
        """Ask a full-screen child to move its own view by one terminal page."""
        with self._lock:
            alternate_active = bool(
                self._screen is not None
                and getattr(self._screen, "alternate_active", False)
            )
        if not alternate_active or not self.alive:
            return False
        if not int(delta):
            return True
        sequence = b"\x1b[5~" if int(delta) > 0 else b"\x1b[6~"
        try:
            self._write_bytes(sequence * min(20, abs(int(delta))))
            return True
        except Exception as exc:
            with self._lock:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
            self._notify()
            return False

    def scroll_view_to_edge(self, *, oldest: bool) -> bool:
        """Delegate an edge jump to a full-screen child's own viewport."""
        with self._lock:
            alternate_active = bool(
                self._screen is not None
                and getattr(self._screen, "alternate_active", False)
            )
        if not alternate_active or not self.alive:
            return False
        # Ctrl+Home/Ctrl+End are the conventional terminal edge sequences. The
        # child remains authoritative for interpreting its own transcript.
        sequence = b"\x1b[1;5H" if oldest else b"\x1b[1;5F"
        try:
            self._write_bytes(sequence)
            return True
        except Exception as exc:
            with self._lock:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
            self._notify()
            return False

    def _write_bytes(self, payload: bytes) -> None:
        if os.name == "nt":
            self._backend.write(payload)
        else:
            os.write(
                int(getattr(self._backend, "_mo_workspace_master_fd")),
                payload,
            )

    def _write_terminal_response(self, text: str) -> None:
        if self.alive:
            self._write_bytes(str(text).encode("utf-8", errors="replace"))

    def send_line(self, text: str) -> bool:
        if not self.alive:
            return False
        payload = (str(text or "") + "\r").encode("utf-8", errors="replace")
        try:
            self._write_bytes(payload)
            return True
        except Exception as exc:
            with self._lock:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
            self._notify()
            return False

    def send_input(self, text: str) -> bool:
        """Send exact bounded terminal input without adding a newline."""
        if not self.alive:
            return False
        payload = str(text or "").encode("utf-8", errors="replace")[:12000]
        if not payload:
            return True
        try:
            self._write_bytes(payload)
            return True
        except Exception as exc:
            with self._lock:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
            self._notify()
            return False

    def send_interrupt(self) -> bool:
        if not self.alive:
            return False
        try:
            self._write_bytes(b"\x03")
            return True
        except Exception:
            return False

    def resize(self, *, columns: int, rows: int) -> None:
        columns = max(20, int(columns or _DEFAULT_COLUMNS))
        rows = max(3, int(rows or _DEFAULT_ROWS))
        with self._lock:
            if (columns, rows) == (self.columns, self.rows):
                return
            self.columns, self.rows = columns, rows
            try:
                if self._screen is not None:
                    self._screen.resize(rows=rows, columns=columns)
                if self._backend is not None:
                    if os.name == "nt":
                        self._backend.resize(columns=columns, rows=rows)
                    else:
                        import fcntl
                        import struct
                        import termios

                        fcntl.ioctl(
                            int(getattr(self._backend, "_mo_workspace_master_fd")),
                            termios.TIOCSWINSZ,
                            struct.pack("HHHH", rows, columns, 0, 0),
                        )
            except Exception:
                pass

    def _terminate_posix_backend(self, *, timeout: float) -> int | None:
        """Reap the exact POSIX shell, escalating only after a bounded wait."""
        backend = self._backend
        if backend is None:
            return None
        wait_timeout = max(0.05, float(timeout))
        try:
            if backend.poll() is None:
                backend.terminate()
                try:
                    backend.wait(timeout=wait_timeout)
                except subprocess.TimeoutExpired:
                    backend.kill()
                    try:
                        backend.wait(timeout=wait_timeout)
                    except subprocess.TimeoutExpired:
                        pass
            value = backend.poll()
            return int(value) if value is not None else None
        except Exception:
            return self._read_exit_code()

    def close(self, *, timeout: float = 0.75) -> None:
        """Ask the shell to exit, then force only this exact PTY if it remains."""
        with self._lock:
            if self._closed or self._backend is None:
                return
            self._closed = True
            self.state = "closing"
        self._notify()
        try:
            self.send_line("exit")
        except Exception:
            pass
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self.alive and time.monotonic() < deadline:
            time.sleep(0.02)
        observed_exit = self._read_exit_code()
        try:
            if os.name == "nt":
                self._backend.close()
            else:
                observed_exit = self._terminate_posix_backend(
                    timeout=min(0.5, max(0.1, float(timeout)))
                )
        except Exception:
            pass
        if os.name != "nt":
            try:
                os.close(int(getattr(self._backend, "_mo_workspace_master_fd")))
            except Exception:
                pass
        self._stop.set()
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=0.5)
        with self._lock:
            self.state = "exited"
            self.exit_code = observed_exit if observed_exit is not None else self.exit_code
        self._notify()
