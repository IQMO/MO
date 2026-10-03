"""Minimal MCP (Model Context Protocol) stdio client — hand-rolled, no SDK dep.

Speaks JSON-RPC 2.0 over a local subprocess's stdin/stdout using newline-delimited
JSON (MCP's stdio transport). Local-first and operator-configured: MO only spawns
servers the operator listed in `config.mcp.servers`. Every tool call still passes
MO's sandbox gate before it reaches here.
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
from typing import Any

from ..runtime.subprocess_flags import apply_windows_hidden_process_flags
from ..tooling.sandbox import safe_env

PROTOCOL_VERSION = "2024-11-05"
# Cap a single JSON-RPC frame so a buggy/hostile server can't OOM MO with one
# enormous line. 8 MiB is far above any legitimate tool response.
_MCP_MAX_LINE_BYTES = 8 * 1024 * 1024


class McpError(Exception):
    pass


class McpClient:
    """One local MCP server over stdio. Cross-platform (uses a reader thread)."""

    def __init__(self, name, command, args=None, env=None, timeout=30.0, cwd=None):
        self.name = str(name)
        self._command = [str(command), *[str(a) for a in (args or [])]]
        self._env = env or {}
        self._timeout = float(timeout or 30.0)
        self._cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._pending: dict[int, "queue.Queue[dict]"] = {}
        self._pending_lock = threading.Lock()
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.tools: list[dict[str, Any]] = []

    # ---- lifecycle ----------------------------------------------------------
    def start(self) -> "McpClient":
        full_env = safe_env()
        full_env.update({str(k): str(v) for k, v in self._env.items()})
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            # Binary bounded reads enforce the frame cap before Python's text
            # iterator can allocate an arbitrarily large server-controlled line.
            "text": False,
            "bufsize": 0,
            "env": full_env,
            "cwd": self._cwd,
        }
        apply_windows_hidden_process_flags(popen_kwargs)
        self._proc = subprocess.Popen(self._command, **popen_kwargs)
        threading.Thread(target=self._read_loop, daemon=True).start()
        try:
            self._initialize()
            self.tools = self._list_tools()
        except Exception:
            self.stop()
            raise
        return self

    def stop(self) -> None:
        proc = self._proc
        if not proc:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        except Exception:
            pass
        self._proc = None

    # ---- transport ----------------------------------------------------------
    def _read_loop(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        try:
            while True:
                line = proc.stdout.readline(_MCP_MAX_LINE_BYTES + 1)
                if not line:
                    break
                oversized = len(line) > _MCP_MAX_LINE_BYTES
                if oversized:
                    # Drain the remainder without ever joining it into one object,
                    # then continue at the next newline-delimited frame.
                    while line and not line.endswith(b"\n"):
                        line = proc.stdout.readline(_MCP_MAX_LINE_BYTES + 1)
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except Exception:
                    continue
                response_id = message.get("id") if isinstance(message, dict) else None
                with self._pending_lock:
                    response_queue = self._pending.get(response_id)
                if response_queue is not None:
                    response_queue.put(message)
        finally:
            poll = getattr(proc, "poll", None)
            code = poll() if callable(poll) else None
            detail = f"exit code {code}" if code is not None else "closed stdout"
            signal = {"__transport_error__": f"MCP server '{self.name}' transport closed ({detail})"}
            with self._pending_lock:
                waiting = list(self._pending.values())
            for response_queue in waiting:
                try:
                    response_queue.put_nowait(signal)
                except queue.Full:
                    pass

    def _send(self, payload: dict) -> None:
        proc = self._proc
        if not proc or not proc.stdin:
            raise McpError(f"MCP server '{self.name}' is not running")
        poll = getattr(proc, "poll", None)
        if callable(poll) and poll() is not None:
            raise McpError(f"MCP server '{self.name}' transport is closed")
        with self._write_lock:
            try:
                proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise McpError(f"MCP server '{self.name}' transport is closed") from exc

    def _request(self, method: str, params: dict | None = None) -> dict:
        with self._id_lock:
            self._next_id += 1
            rid = self._next_id
        response_queue: "queue.Queue[dict]" = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[rid] = response_queue
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            try:
                msg = response_queue.get(timeout=self._timeout)
            except queue.Empty:
                raise McpError(f"MCP '{self.name}' {method} timed out after {self._timeout}s")
            if "__transport_error__" in msg:
                raise McpError(str(msg["__transport_error__"]))
            if "error" in msg:
                raise McpError(f"MCP '{self.name}' {method}: {msg['error']}")
            return msg.get("result") or {}
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)

    # ---- protocol -----------------------------------------------------------
    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "MO", "version": "1"},
            },
        )
        # post-init notification (no response expected)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def _list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list")
        tools = result.get("tools")
        return list(tools) if isinstance(tools, list) else []

    def call_tool(self, tool_name: str, arguments: dict | None = None) -> dict:
        return self._request("tools/call", {"name": tool_name, "arguments": arguments or {}})
