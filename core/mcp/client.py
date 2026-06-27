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
import time
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
        self._inbox: "queue.Queue[dict]" = queue.Queue()
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
            "text": True,
            "bufsize": 1,
            "env": full_env,
            "cwd": self._cwd,
        }
        apply_windows_hidden_process_flags(popen_kwargs)
        self._proc = subprocess.Popen(self._command, **popen_kwargs)
        threading.Thread(target=self._read_loop, daemon=True).start()
        self._initialize()
        self.tools = self._list_tools()
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
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            # Drop oversized frames: a buggy/hostile MCP server emitting a giant
            # line must not be buffered whole into memory before parsing.
            if len(line) > _MCP_MAX_LINE_BYTES:
                continue
            try:
                self._inbox.put(json.loads(line))
            except Exception:
                continue

    def _send(self, payload: dict) -> None:
        proc = self._proc
        if not proc or not proc.stdin:
            raise McpError(f"MCP server '{self.name}' is not running")
        with self._write_lock:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()

    def _request(self, method: str, params: dict | None = None) -> dict:
        with self._id_lock:
            self._next_id += 1
            rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.monotonic() + self._timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError(f"MCP '{self.name}' {method} timed out after {self._timeout}s")
            try:
                msg = self._inbox.get(timeout=remaining)
            except queue.Empty:
                raise McpError(f"MCP '{self.name}' {method} timed out")
            if msg.get("id") != rid:
                continue  # notification or out-of-order response — ignore
            if "error" in msg:
                raise McpError(f"MCP '{self.name}' {method}: {msg['error']}")
            return msg.get("result") or {}

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
