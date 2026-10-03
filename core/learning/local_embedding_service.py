"""One lazy local-embedding worker shared by every MO process.

The optional FastEmbed/ONNX stack is intentionally kept outside terminal,
Desktop, Telegram, and worker processes.  A small authenticated loopback helper
owns the one model copy, serves bounded embedding requests serially, and exits
after an idle period so native memory is returned to the operating system.

This module imports only the standard library on the client path.  ``fastembed``
is imported inside the helper process on its first real embedding request.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import socket
import socketserver
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


_PROTOCOL_VERSION = 1
_MAX_TEXT_CHARS = 8_000
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def _identity(state_dir: Path, model: str, threads: int) -> str:
    raw = f"{state_dir.resolve(strict=False)}\n{model}\n{threads}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:16]


class SharedLocalEmbedder:
    """Callable client for one profile/model-scoped local helper."""

    def __init__(
        self,
        *,
        model: str,
        state_dir: str | Path,
        threads: int = 1,
        idle_seconds: float = 60.0,
        startup_timeout: float = 12.0,
    ) -> None:
        self.model = str(model or "BAAI/bge-small-en-v1.5").strip()
        self.threads = max(1, min(4, int(threads or 1)))
        self.idle_seconds = max(5.0, min(3600.0, float(idle_seconds or 60.0)))
        self.startup_timeout = max(1.0, min(30.0, float(startup_timeout or 12.0)))
        self.state_dir = Path(state_dir).expanduser().resolve(strict=False)
        self.service_id = _identity(self.state_dir, self.model, self.threads)
        self.endpoint_path = self.state_dir / f"{self.service_id}.json"
        self.lock_name = f"mo-local-embeddings-{self.service_id}.lock"

    def __call__(self, text: str) -> list[float]:
        value = str(text or "")[:_MAX_TEXT_CHARS]
        if not value:
            return []
        endpoint = self._endpoint()
        if endpoint is None:
            endpoint = self._start_and_wait()
        if endpoint is None:
            return []
        try:
            payload = self._send(endpoint, {"op": "embed", "text": value})
        except Exception:
            # A helper may cross its idle boundary between endpoint discovery and
            # connect. Wait briefly for that exact owner to release its lock,
            # remove only stale metadata, then make one bounded restart. Starting
            # immediately would race the exiting owner and the replacement would
            # correctly lose the singleton lock without leaving a server behind.
            endpoint = self._replacement_after_failure(endpoint)
            if endpoint is None:
                return []
            try:
                payload = self._send(endpoint, {"op": "embed", "text": value})
            except Exception:
                return []
        vector = payload.get("vector") if isinstance(payload, dict) else None
        if not isinstance(vector, list):
            return []
        try:
            return [float(item) for item in vector]
        except (TypeError, ValueError):
            return []

    def _replacement_after_failure(self, failed: dict[str, Any]) -> dict[str, Any] | None:
        failed_pid = int(failed.get("pid") or 0)
        deadline = time.monotonic() + 1.0
        from core.runtime.lock import runtime_lock_owner

        while runtime_lock_owner(self.lock_name) == failed_pid and time.monotonic() < deadline:
            time.sleep(0.04)
        self._remove_if_stale(failed)
        current = self._endpoint()
        if current is not None and str(current.get("token") or "") != str(failed.get("token") or ""):
            return current
        if current is not None and runtime_lock_owner(self.lock_name) == failed_pid:
            return current
        return self._start_and_wait()

    def service_pid(self) -> int | None:
        endpoint = self._endpoint()
        return int(endpoint["pid"]) if endpoint else None

    def stop_for_test(self) -> None:
        """Stop the authenticated helper; intentionally test-only."""
        endpoint = self._endpoint()
        if endpoint is None:
            return
        try:
            self._send(endpoint, {"op": "stop"})
        except Exception:
            pass

    def _endpoint(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.endpoint_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or int(data.get("version") or 0) != _PROTOCOL_VERSION:
                return None
            if str(data.get("service_id") or "") != self.service_id:
                return None
            if str(data.get("host") or "") != "127.0.0.1":
                return None
            port = int(data.get("port") or 0)
            pid = int(data.get("pid") or 0)
            token = str(data.get("token") or "")
            if not 1 <= port <= 65535 or pid <= 0 or len(token) < 32:
                return None
            from core.runtime.lock import runtime_lock_owner

            if runtime_lock_owner(self.lock_name) != pid:
                return None
            return data
        except Exception:
            return None

    def _start_and_wait(self) -> dict[str, Any] | None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        from core.runtime.subprocess_flags import apply_windows_hidden_process_flags
        from core.state.paths import repo_root
        from core.tooling.sandbox import safe_env

        command = [
            sys.executable,
            "-m",
            "core.learning.local_embedding_service",
            "--serve",
            "--state-dir",
            str(self.state_dir),
            "--model",
            self.model,
            "--threads",
            str(self.threads),
            "--idle-seconds",
            str(self.idle_seconds),
        ]
        kwargs: dict[str, Any] = {
            "cwd": repo_root(),
            "env": safe_env(),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        apply_windows_hidden_process_flags(kwargs, detached=True, below_normal_priority=True)
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        try:
            subprocess.Popen(command, **kwargs)
        except Exception:
            return None

        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            endpoint = self._endpoint()
            if endpoint is not None:
                try:
                    self._send(endpoint, {"op": "ping"})
                    return endpoint
                except Exception:
                    pass
            time.sleep(0.04)
        return None

    def _send(self, endpoint: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request)
        payload["token"] = str(endpoint["token"])
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > _MAX_REQUEST_BYTES:
            raise ValueError("local embedding request exceeds the bounded transport limit")
        with socket.create_connection(
            (str(endpoint["host"]), int(endpoint["port"])),
            timeout=max(5.0, self.startup_timeout),
        ) as conn:
            conn.settimeout(max(5.0, self.startup_timeout))
            conn.sendall(encoded)
            response = _recv_line(conn, _MAX_RESPONSE_BYTES)
        data = json.loads(response.decode("utf-8"))
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise RuntimeError("local embedding helper rejected the request")
        return data

    def _remove_if_stale(self, endpoint: dict[str, Any]) -> None:
        try:
            from core.runtime.lock import runtime_lock_owner

            if runtime_lock_owner(self.lock_name) == int(endpoint.get("pid") or 0):
                return
            current = json.loads(self.endpoint_path.read_text(encoding="utf-8"))
            if isinstance(current, dict) and str(current.get("token") or "") == str(endpoint.get("token") or ""):
                self.endpoint_path.unlink(missing_ok=True)
        except Exception:
            return


def shared_local_embedder(
    model: str,
    *,
    config: dict[str, Any] | None = None,
    threads: int = 1,
    idle_seconds: float = 60.0,
) -> SharedLocalEmbedder:
    from core.state.paths import resolve_state_path

    state_dir = resolve_state_path("run/local_embeddings", config or {})
    return SharedLocalEmbedder(
        model=model,
        state_dir=state_dir,
        threads=threads,
        idle_seconds=idle_seconds,
    )


def _recv_line(conn: socket.socket, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        chunk = conn.recv(min(65536, limit + 1 - total))
        if not chunk:
            break
        newline = chunk.find(b"\n")
        if newline >= 0:
            chunks.append(chunk[:newline])
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
    raise ValueError("local embedding response exceeds the bounded transport limit")


class _EmbeddingState:
    def __init__(self, model: str, threads: int, token: str) -> None:
        self.model = model
        self.threads = threads
        self.token = token
        self.embedder: Any = None
        self.last_activity = time.monotonic()
        self.stop_requested = False

    def embed(self, text: str) -> list[float]:
        if self.embedder is None:
            from fastembed import TextEmbedding

            self.embedder = TextEmbedding(model_name=self.model, threads=self.threads)
        values = list(self.embedder.embed([str(text or "")[:_MAX_TEXT_CHARS]], parallel=1))
        self.last_activity = time.monotonic()
        return [float(item) for item in values[0]] if values else []


class _EmbeddingHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        state: _EmbeddingState = self.server.embedding_state  # type: ignore[attr-defined]
        try:
            raw = self.rfile.readline(_MAX_REQUEST_BYTES + 1)
            if not raw or len(raw) > _MAX_REQUEST_BYTES:
                return
            request = json.loads(raw.decode("utf-8"))
            supplied = str(request.get("token") or "") if isinstance(request, dict) else ""
            if not hmac.compare_digest(supplied, state.token):
                self._write({"ok": False})
                return
            state.last_activity = time.monotonic()
            op = str(request.get("op") or "")
            if op == "ping":
                self._write({"ok": True})
            elif op == "embed":
                self._write({"ok": True, "vector": state.embed(str(request.get("text") or ""))})
            elif op == "stop":
                state.stop_requested = True
                self._write({"ok": True})
            else:
                self._write({"ok": False})
        except Exception:
            try:
                self._write({"ok": False})
            except Exception:
                pass

    def _write(self, payload: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")


class _LoopbackServer(socketserver.TCPServer):
    allow_reuse_address = False


def serve(*, state_dir: str | Path, model: str, threads: int, idle_seconds: float) -> int:
    root = Path(state_dir).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    threads = max(1, min(4, int(threads or 1)))
    idle_seconds = max(5.0, min(3600.0, float(idle_seconds or 60.0)))
    service_id = _identity(root, model, threads)
    endpoint_path = root / f"{service_id}.json"
    lock_name = f"mo-local-embeddings-{service_id}.lock"

    from core.runtime.lock import acquire_runtime_lock, release_runtime_lock

    runtime_lock = acquire_runtime_lock(lock_name=lock_name, label="MO local embedding helper")
    if runtime_lock is None:
        return 0
    token = secrets.token_urlsafe(32)
    state = _EmbeddingState(model, threads, token)
    server: _LoopbackServer | None = None
    try:
        server = _LoopbackServer(("127.0.0.1", 0), _EmbeddingHandler)
        server.timeout = 0.4
        server.embedding_state = state  # type: ignore[attr-defined]
        endpoint = {
            "version": _PROTOCOL_VERSION,
            "service_id": service_id,
            "host": "127.0.0.1",
            "port": int(server.server_address[1]),
            "pid": os.getpid(),
            "token": token,
        }
        _write_endpoint(endpoint_path, endpoint)
        while not state.stop_requested and time.monotonic() - state.last_activity < idle_seconds:
            server.handle_request()
        return 0
    finally:
        if server is not None:
            server.server_close()
        _remove_owned_endpoint(endpoint_path, token)
        release_runtime_lock(runtime_lock)


def _write_endpoint(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _remove_owned_endpoint(path: Path, token: str) -> None:
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(current, dict) and hmac.compare_digest(str(current.get("token") or ""), token):
            path.unlink(missing_ok=True)
    except Exception:
        return


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--idle-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    if not args.serve:
        return 2
    return serve(
        state_dir=args.state_dir,
        model=args.model,
        threads=args.threads,
        idle_seconds=args.idle_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(_main())
