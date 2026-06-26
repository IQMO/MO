"""LSP manager — routes files to operator-configured language servers, lazily.

Config shape (mirrors `config.mcp.servers`):

    lsp:
      servers:
        python:     {command: pylsp,                       args: []}
        typescript: {command: typescript-language-server,  args: [--stdio]}
        go:         {command: gopls,                        args: []}

Off by default: no `lsp.servers` configured means every call is a clean no-op
returning ``[]``. Servers spawn lazily on the first file of their language and
are local, read-only analysis (MO never applies server-suggested edits).
"""
from __future__ import annotations

import threading
import traceback
from pathlib import Path

from .client import LspClient

# Filename extension -> LSP languageId. The configured server is keyed by language,
# so e.g. both .ts and .tsx route to the operator's `typescript` server if present.
_EXT_LANGUAGE = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".lua": "lua",
}

# A configured server is selected by the broad language family, so .tsx ->
# typescriptreact still uses the `typescript` server entry.
_LANGUAGE_SERVER_KEY = {
    "typescriptreact": "typescript",
    "javascriptreact": "javascript",
}

_SEVERITY = {1: "error", 2: "warning", 3: "information", 4: "hint"}


def language_for(path: str) -> str | None:
    return _EXT_LANGUAGE.get(Path(path).suffix.lower())


def summarize_diagnostics(diagnostics: list[dict]) -> dict[str, int]:
    """Count diagnostics by severity name (error/warning/information/hint)."""
    counts: dict[str, int] = {}
    for d in diagnostics or []:
        name = _SEVERITY.get(int(d.get("severity", 1) or 1), "error")
        counts[name] = counts.get(name, 0) + 1
    return counts


class LspManager:
    """Lazy, config-driven pool of language-server clients. Thread-safe."""

    def __init__(self, servers: dict | None = None, root_path: str | None = None, timeout: float = 30.0):
        self._servers = {str(k): dict(v) for k, v in (servers or {}).items() if isinstance(v, dict)}
        self._root_path = root_path
        self._timeout = float(timeout or 30.0)
        self._clients: dict[str, LspClient] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._servers)

    def _server_key(self, language: str) -> str:
        return _LANGUAGE_SERVER_KEY.get(language, language)

    def _client_for(self, language: str) -> LspClient | None:
        key = self._server_key(language)
        cfg = self._servers.get(key)
        if not cfg or not cfg.get("command"):
            return None
        with self._lock:
            client = self._clients.get(key)
            if client is not None:
                return client
            try:
                client = LspClient(
                    name=key,
                    command=cfg["command"],
                    args=cfg.get("args"),
                    root_path=self._root_path,
                    env=cfg.get("env"),
                    timeout=self._timeout,
                ).start()
            except Exception:
                traceback.print_exc()
                return None
            self._clients[key] = client
            return client

    def file_diagnostics(self, path: str, timeout: float = 5.0) -> list[dict]:
        """Open ``path`` in the matching server and return its diagnostics.

        Returns ``[]`` when no server is configured for the file's language, the
        server can't start, or the path isn't readable — never raises into a turn.
        """
        language = language_for(path)
        if not language:
            return []
        client = self._client_for(language)
        if client is None:
            return []
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        try:
            client.did_open(path, text, language)
            return client.wait_for_diagnostics(path, timeout=timeout)
        except Exception:
            traceback.print_exc()
            return []

    def stop_all(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.stop()
            except Exception:
                pass
