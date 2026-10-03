"""LSP manager — routes files to operator-configured language servers, lazily.

Config shape (mirrors `config.mcp.servers`):

    lsp:
      servers:
        python:     {command: pylsp,                       args: []}
        typescript: {command: typescript-language-server,  args: [--stdio]}
        go:         {command: gopls,                        args: []}

Off by default: no `lsp.servers` configured means every call returns typed
``disabled``/``unsupported`` truth. Servers spawn lazily on the first file of their language and
are local, read-only analysis (MO never applies server-suggested edits).
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import threading
import os
from dataclasses import dataclass
from pathlib import Path

from .client import LspClient, LspError

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


@dataclass(frozen=True)
class LspDiagnosticResult:
    path: str
    status: str
    language: str = ""
    server: str = ""
    diagnostics: tuple[dict, ...] = ()
    reason: str = ""

    @property
    def counts(self) -> dict[str, int]:
        return summarize_diagnostics(list(self.diagnostics))

    @property
    def clean(self) -> bool:
        return self.status == "clean"


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
        self._config: dict = {}
        self._timeout = float(timeout or 30.0)
        self._clients: dict[tuple[str, str], LspClient] = {}
        self._client_failures: dict[tuple[str, str], tuple[str, str]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config: dict | None, root_path: str | None = None) -> "LspManager":
        """Use normal config plus the existing private per-project overlay.

        No configured servers means disabled, never evidence of clean files.
        """
        lsp_cfg = ((config or {}).get("lsp") or {}) if isinstance(config, dict) else {}
        servers = lsp_cfg.get("servers") if isinstance(lsp_cfg.get("servers"), dict) else {}
        manager = cls(servers, root_path=root_path, timeout=float(lsp_cfg.get("timeout", DEFAULT_PREFERENCES["lsp.timeout"]) or DEFAULT_PREFERENCES["lsp.timeout"]))
        manager._config = config or {}
        return manager

    @property
    def enabled(self) -> bool:
        return self.enabled_for()

    def _root(self, root_path: str | None = None) -> str:
        return os.path.normcase(str(Path(root_path or self._root_path or ".").resolve(strict=False)))

    def status(self, root_path: str | None = None) -> dict:
        """Read-only configuration/lifecycle facts; readiness is not test evidence."""
        from ..state.preferences import project_lsp_preference

        root = self._root(root_path)
        override = project_lsp_preference(self._config, root)
        selected = override if override is not None else (self._config.get("lsp") or {}).get("enabled", DEFAULT_PREFERENCES["lsp.enabled"])
        enabled = bool(selected and self._servers)
        with self._lock:
            running = sum(key[0] == root for key in self._clients)
        return {
            "enabled": enabled, "configured": bool(self._servers),
            "selection": "default" if override is None else "on" if override else "off",
            "state": "not configured" if not self._servers else "ready" if enabled else "off",
            "running": running, "languages": sorted(self._servers),
        }

    def enabled_for(self, root_path: str | None = None) -> bool:
        return self.status(root_path)["enabled"]

    def set_project_selection(self, choice: str, root_path: str | None = None) -> dict:
        """Use the same private policy and client lifecycle from every local UI."""
        from ..state.preferences import persist_project_lsp_preference

        if choice not in {"on", "off", "default"}:
            raise ValueError("LSP selection must be on, off or default")
        root = self._root(root_path)
        if choice == "on" and not self._servers:
            raise ValueError("No LSP servers configured. Set lsp.servers in your normal MO config, then /reload. No server is installed automatically.")
        persist_project_lsp_preference(self._config, root, {"on": True, "off": False, "default": None}[choice])
        self.stop_all(root)
        return self.status(root)

    def _server_key(self, language: str) -> str:
        return _LANGUAGE_SERVER_KEY.get(language, language)

    def _client_for(self, language: str, root_path: str | None = None) -> tuple[LspClient | None, str, str]:
        key = self._server_key(language)
        root = self._root(root_path)
        client_key = (root, key)
        if not self.enabled_for(root):
            self.stop_all(root)
            return None, "disabled", "project_disabled" if self._servers else "server_not_configured"
        cfg = self._servers.get(key)
        if not cfg:
            return None, "disabled", "server_not_configured"
        if not cfg.get("command"):
            return None, "unavailable", "command_not_configured"
        with self._lock:
            client = self._clients.get(client_key)
            if client is not None:
                return client, "ready", ""
            failure = self._client_failures.get(client_key)
            if failure is not None:
                return None, failure[0], failure[1]
            try:
                client = LspClient(
                    name=key,
                    command=cfg["command"],
                    args=cfg.get("args"),
                    root_path=root,
                    env=cfg.get("env"),
                    timeout=self._timeout,
                ).start()
            except FileNotFoundError:
                self._client_failures[client_key] = ("unavailable", "command_not_found")
                return None, "unavailable", "command_not_found"
            except LspError as exc:
                status = "timeout" if "timed out" in str(exc).casefold() else "unavailable"
                reason = "initialize_timeout" if status == "timeout" else "initialize_failed"
                self._client_failures[client_key] = (status, reason)
                return None, status, reason
            except Exception:
                self._client_failures[client_key] = ("unavailable", "start_failed")
                return None, "unavailable", "start_failed"
            self._clients[client_key] = client
            return client, "ready", ""

    def file_diagnostics(self, path: str, timeout: float = 5.0, *, root_path: str | None = None) -> LspDiagnosticResult:
        """Return diagnostics plus the actual configured/no-op/failure status."""
        path = str(Path(self._root(root_path)) / path)
        language = language_for(path)
        if not language:
            return LspDiagnosticResult(str(path), "unsupported", reason="unsupported_language")
        server = self._server_key(language)
        client, status, reason = self._client_for(language, root_path)
        if client is None:
            return LspDiagnosticResult(str(path), status, language, server, reason=reason)
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return LspDiagnosticResult(str(path), "read_error", language, server, reason="path_unreadable")
        try:
            client.did_open(path, text, language)
            diagnostics = client.wait_for_diagnostics(path, timeout=timeout)
            if not client.diagnostics_seen(path):
                return LspDiagnosticResult(str(path), "timeout", language, server, reason="diagnostics_timeout")
            rows = tuple(dict(item) for item in diagnostics if isinstance(item, dict))
            return LspDiagnosticResult(
                str(path),
                "diagnostics" if rows else "clean",
                language,
                server,
                diagnostics=rows,
            )
        except LspError as exc:
            status = "timeout" if "timed out" in str(exc).casefold() else "unavailable"
            reason = "request_timeout" if status == "timeout" else "request_failed"
            return LspDiagnosticResult(str(path), status, language, server, reason=reason)
        except Exception:
            return LspDiagnosticResult(str(path), "unavailable", language, server, reason="request_failed")

    def stop_all(self, root_path: str | None = None) -> None:
        """Stop this manager's clients, optionally only one project's clients."""
        root = self._root(root_path) if root_path is not None else None
        with self._lock:
            keys = [key for key in self._clients if root is None or key[0] == root]
            clients = [self._clients.pop(key) for key in keys]
            for key in list(self._client_failures):
                if root is None or key[0] == root:
                    self._client_failures.pop(key)
        for client in clients:
            try:
                client.stop()
            except Exception:
                pass
