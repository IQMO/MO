"""Minimal Language Server Protocol client — hand-rolled, no SDK dep.

Speaks JSON-RPC 2.0 over a local subprocess's stdin/stdout using LSP's
**Content-Length framed** transport (unlike MCP's newline-delimited JSON). Local-
first and operator-configured: MO only spawns servers the operator listed in
`config.lsp.servers`, and treats them as read-only analysis (no edits applied).

Why this exists: it captures live, language-server **diagnostics** so MO can see
real compile/type errors as it edits — the one capability MO's Python-AST code
graph cannot provide. Diagnostics arrive asynchronously via the
``textDocument/publishDiagnostics`` notification, so the read loop routes
responses by id and stores diagnostics keyed by file URI.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..sandbox import safe_env

# Cap a single frame so a buggy/hostile server can't OOM MO with one huge message.
_LSP_MAX_BODY_BYTES = 16 * 1024 * 1024


class LspError(Exception):
    pass


def path_to_uri(path: str) -> str:
    """Convert a filesystem path to a ``file://`` URI (cross-platform)."""
    return Path(path).resolve().as_uri()


class LspClient:
    """One local language server over stdio, framed per the LSP spec."""

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 root_path: str | None = None, env: dict | None = None, timeout: float = 30.0):
        self.name = str(name)
        self._command = [str(command), *[str(a) for a in (args or [])]]
        self._root_path = root_path or os.getcwd()
        self._env = env or {}
        self._timeout = float(timeout or 30.0)
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._write_lock = threading.Lock()
        # id -> Queue for that request's response (avoids dropping interleaved responses)
        self._pending: dict[int, "queue.Queue[dict]"] = {}
        self._pending_lock = threading.Lock()
        # uri -> diagnostics list; _diag_seen marks uris the server has published for
        # (so a clean file's empty list is distinguishable from "not yet analyzed").
        self._diagnostics: dict[str, list[dict]] = {}
        self._diag_seen: set[str] = set()
        self._diag_lock = threading.Lock()
        self._closed = threading.Event()

    # ---- lifecycle ----------------------------------------------------------
    def start(self) -> "LspClient":
        full_env = safe_env()
        full_env.update({str(k): str(v) for k, v in self._env.items()})
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=full_env,
            cwd=self._root_path,
        )
        threading.Thread(target=self._read_loop, name=f"lsp-{self.name}", daemon=True).start()
        self._initialize()
        return self

    def stop(self) -> None:
        self._closed.set()
        proc = self._proc
        if not proc:
            return
        try:
            self._notify("exit", None)
        except Exception:
            pass
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        except Exception:
            pass
        self._proc = None

    # ---- transport (Content-Length framing) ---------------------------------
    def _read_message(self) -> dict | None:
        proc = self._proc
        if not proc or not proc.stdout:
            return None
        length = 0
        while True:  # headers, terminated by a blank line
            line = proc.stdout.readline()
            if not line:
                return None  # EOF
            line = line.strip()
            if not line:
                break
            key, _, value = line.partition(b":")
            if key.strip().lower() == b"content-length":
                try:
                    length = int(value.strip())
                except ValueError:
                    length = 0
        if length <= 0 or length > _LSP_MAX_BODY_BYTES:
            # Drain an oversized/garbage frame's body if we know its size, else skip.
            if 0 < length <= _LSP_MAX_BODY_BYTES * 4:
                proc.stdout.read(length)
            return {}
        body = proc.stdout.read(length)
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            return {}

    def _read_loop(self) -> None:
        while not self._closed.is_set():
            msg = self._read_message()
            if msg is None:
                return  # EOF
            if not msg:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                self._route_response(msg)
            elif msg.get("method"):
                self._handle_server_message(msg)

    def _route_response(self, msg: dict) -> None:
        with self._pending_lock:
            box = self._pending.pop(msg.get("id"), None)
        if box is not None:
            box.put(msg)

    def _handle_server_message(self, msg: dict) -> None:
        method = msg.get("method")
        if method == "textDocument/publishDiagnostics":
            params = msg.get("params") or {}
            uri = params.get("uri")
            if uri:
                with self._diag_lock:
                    self._diagnostics[uri] = list(params.get("diagnostics") or [])
                    self._diag_seen.add(uri)
            return
        # Server-to-client REQUESTS (have an id) need a minimal response so the
        # server doesn't block. MO never applies server edits or config.
        if "id" in msg:
            if method == "workspace/configuration":
                items = (msg.get("params") or {}).get("items") or []
                self._respond(msg["id"], [None for _ in items])
            elif method == "workspace/applyEdit":
                self._respond(msg["id"], {"applied": False})
            else:
                self._respond(msg["id"], None)

    def _frame(self, payload: dict) -> None:
        proc = self._proc
        if not proc or not proc.stdin:
            raise LspError(f"LSP server '{self.name}' is not running")
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        with self._write_lock:
            proc.stdin.write(header + body)
            proc.stdin.flush()

    def _respond(self, rid: Any, result: Any) -> None:
        try:
            self._frame({"jsonrpc": "2.0", "id": rid, "result": result})
        except Exception:
            pass

    def _notify(self, method: str, params: dict | None) -> None:
        self._frame({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _request(self, method: str, params: dict | None = None) -> dict:
        with self._id_lock:
            self._next_id += 1
            rid = self._next_id
        box: "queue.Queue[dict]" = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[rid] = box
        self._frame({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        try:
            msg = box.get(timeout=self._timeout)
        except queue.Empty:
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise LspError(f"LSP '{self.name}' {method} timed out after {self._timeout}s")
        if "error" in msg:
            raise LspError(f"LSP '{self.name}' {method}: {msg['error']}")
        return msg.get("result") or {}

    # ---- protocol -----------------------------------------------------------
    def _initialize(self) -> None:
        self._request("initialize", {
            "processId": os.getpid(),
            "rootUri": path_to_uri(self._root_path),
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {"relatedInformation": True},
                    "synchronization": {"didSave": False},
                },
            },
            "clientInfo": {"name": "MO", "version": "1"},
        })
        self._notify("initialized", {})

    def did_open(self, path: str, text: str, language_id: str) -> None:
        self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": path_to_uri(path),
                "languageId": language_id,
                "version": 1,
                "text": text,
            },
        })

    def did_close(self, path: str) -> None:
        self._notify("textDocument/didClose", {"textDocument": {"uri": path_to_uri(path)}})

    def wait_for_diagnostics(self, path: str, timeout: float = 5.0) -> list[dict]:
        """Block until the server publishes diagnostics for ``path`` (or timeout).

        A clean file yields an empty list once analyzed; the empty-vs-not-yet
        distinction uses ``_diag_seen`` so a clean result isn't a false 'pending'.
        """
        uri = path_to_uri(path)
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            with self._diag_lock:
                if uri in self._diag_seen:
                    return list(self._diagnostics.get(uri, []))
            time.sleep(0.02)
        with self._diag_lock:
            return list(self._diagnostics.get(uri, []))
