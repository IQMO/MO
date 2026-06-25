"""Local-only IPC transport for the warm-daemon client/server split (Phase 1).

Transport + framing ONLY — no agent, no Gateway yet. A server accepts local
connections, reads newline-framed JSON requests, streams newline-framed JSON
events, then a terminal response. A client connects, authenticates, sends one
request, and iterates the streamed events/response.

Transport is loopback TCP (127.0.0.1) on an ephemeral port. The port and a random
auth token are written to ``$MO_HOME/run/<name>-ipc.json`` with owner-only perms.
Loopback is reachable by any local process, so the TOKEN handshake — not the bind
address — is the access boundary. Nothing is ever bound to a non-loopback address;
there is no network listener.

Why loopback+token instead of a unix socket / named pipe: it is stdlib-only and
behaves identically on Windows and POSIX, so a single code path is exercised on
every platform (no pywin32 dependency, which MO does not ship). POSIX ``AF_UNIX``
with 0600 is a defensible later hardening — defense in depth on top of the token —
not a Phase-1 requirement.
"""
from __future__ import annotations

import json
import os
import socket
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

from .path_defaults import mo_home

LOOPBACK = "127.0.0.1"
_HANDSHAKE_OK = {"ok": True}

# handler(request, emit) -> response|None. ``emit(event)`` streams zero or more
# events before the handler returns its terminal result.
Handler = Callable[[dict, "Callable[[dict], None]"], "dict | None"]


class IpcError(RuntimeError):
    """Base IPC error."""


class IpcUnavailable(IpcError):
    """No reachable server (missing endpoint file, or connect/auth failed). Callers
    fall back to the in-process path so the daemon is never worse than its absence."""


class IpcAuthError(IpcError):
    """Token handshake rejected by the server."""


def endpoint_dir(mo_home_path: str | None = None) -> Path:
    """Return (creating) the owner-only ``$MO_HOME/run`` directory."""
    root = Path(mo_home_path or mo_home()) / "run"
    root.mkdir(parents=True, exist_ok=True)
    _lock_down(root)
    return root


def _endpoint_file(name: str, mo_home_path: str | None = None) -> Path:
    return endpoint_dir(mo_home_path) / f"{name}-ipc.json"


def _lock_down(path: Path) -> None:
    """Best-effort owner-only perms. Effective on POSIX; on Windows the token is
    the real guard (chmod there only toggles the read-only bit)."""
    try:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    except OSError:
        pass


def _send_line(conn: socket.socket, obj: dict) -> None:
    # default=str so a stray non-serializable value in a streamed event (e.g. a
    # rich renderable or a board object) degrades to its string form instead of
    # raising inside a turn callback and aborting the turn. ensure_ascii=False
    # keeps MO's UTF-8 text intact across the wire.
    conn.sendall((json.dumps(obj, default=str, ensure_ascii=False) + "\n").encode("utf-8"))


def _read_lines(conn: socket.socket) -> Iterator[str]:
    """Yield complete newline-delimited frames from a stream socket until it closes."""
    buf = b""
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            tail = buf.strip()
            if tail:
                yield tail.decode("utf-8")
            return
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                yield line.decode("utf-8")


class IpcServer:
    """Accept local connections and dispatch each request to ``handler``.

    ``handler(request, emit)`` may call ``emit(event_dict)`` zero or more times to
    stream events, then return a response dict (or ``None`` for an empty ack).
    A handler that raises sends a terminal ``{"type": "error"}`` to that one client
    and keeps the server alive for everyone else.
    """

    def __init__(self, handler: Handler, *, name: str = "mo", mo_home_path: str | None = None):
        self._handler = handler
        self._name = name
        self._mo_home = mo_home_path
        self._token = os.urandom(32).hex()
        self._sock: socket.socket | None = None
        self._conns: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self.address: tuple[str, int] | None = None

    def start(self) -> "IpcServer":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((LOOPBACK, 0))
        sock.listen(16)
        self._sock = sock
        self.address = sock.getsockname()
        self._write_endpoint()
        threading.Thread(target=self._accept_loop, name=f"ipc-{self._name}", daemon=True).start()
        return self

    def _write_endpoint(self) -> None:
        assert self.address is not None
        host, port = self.address
        path = _endpoint_file(self._name, self._mo_home)
        payload = {"host": host, "port": port, "token": self._token, "pid": os.getpid()}
        # Write-then-replace so a concurrent reader never sees a partial file.
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        _lock_down(tmp)
        os.replace(tmp, path)
        _lock_down(path)

    def _accept_loop(self) -> None:
        assert self._sock is not None
        while not self._closed.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return  # socket closed by stop()
            with self._lock:
                self._conns.add(conn)
            threading.Thread(target=self._serve, args=(conn,), name=f"ipc-{self._name}-conn", daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            lines = _read_lines(conn)
            try:
                hello = json.loads(next(lines))
            except StopIteration:
                return
            if not isinstance(hello, dict) or hello.get("token") != self._token:
                _send_line(conn, {"type": "error", "message": "auth"})
                return
            _send_line(conn, _HANDSHAKE_OK)
            for raw in lines:
                try:
                    request = json.loads(raw)
                except json.JSONDecodeError:
                    _send_line(conn, {"type": "error", "message": "bad-json"})
                    continue
                self._dispatch(conn, request)
        except OSError:
            pass  # client vanished mid-stream; drop the connection quietly
        finally:
            with self._lock:
                self._conns.discard(conn)
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch(self, conn: socket.socket, request: dict) -> None:
        rid = request.get("id")

        def emit(event: dict) -> None:
            _send_line(conn, {"type": "event", "id": rid, **event})

        try:
            result = self._handler(request, emit)
        except Exception as exc:  # a handler bug must never take down the server
            _send_line(conn, {"type": "error", "id": rid, "message": str(exc)})
            return
        _send_line(conn, {"type": "response", "id": rid, "result": result})

    def stop(self) -> None:
        self._closed.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        with self._lock:
            conns = list(self._conns)
            self._conns.clear()
        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass
        try:
            _endpoint_file(self._name, self._mo_home).unlink()
        except OSError:
            pass


class IpcClient:
    """Connect to a local :class:`IpcServer`, authenticate, and stream requests."""

    def __init__(self, conn: socket.socket):
        self._conn = conn
        self._lines = _read_lines(conn)
        self._id = 0

    @classmethod
    def connect(cls, *, name: str = "mo", mo_home_path: str | None = None, timeout: float = 5.0) -> "IpcClient":
        path = _endpoint_file(name, mo_home_path)
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IpcUnavailable(f"no endpoint: {exc}") from exc
        try:
            conn = socket.create_connection((info["host"], info["port"]), timeout=timeout)
        except OSError as exc:
            raise IpcUnavailable(f"connect failed: {exc}") from exc
        conn.settimeout(None)
        _send_line(conn, {"token": info.get("token")})
        client = cls(conn)
        try:
            hello = next(client._lines)
        except StopIteration as exc:
            conn.close()
            raise IpcUnavailable("server closed before handshake") from exc
        if json.loads(hello) != _HANDSHAKE_OK:
            conn.close()
            raise IpcAuthError("token rejected")
        return client

    def request(self, payload: dict) -> Iterator[dict]:
        """Send one request; yield each streamed event, then the terminal frame
        (``type`` of ``response`` or ``error``) which is yielded last and ends the
        iteration. The underlying connection is reusable for the next request."""
        self._id += 1
        _send_line(self._conn, {"id": self._id, **payload})
        for raw in self._lines:
            frame = json.loads(raw)
            yield frame
            if frame.get("type") in ("response", "error"):
                return

    def close(self) -> None:
        try:
            self._conn.close()
        except OSError:
            pass
