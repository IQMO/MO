"""Task-owned, expiring reference leases; no directory or control API is exposed.

Only prepared media crosses this boundary. A tunnel is optional and is started
on demand, never for text-only generation. It is not a remote deletion service.
"""
from __future__ import annotations

import atexit
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import sys

from core.runtime.subprocess_flags import apply_windows_hidden_process_flags, bind_windows_child_lifetime
from core.state.paths import resolve_state_path
from core.utils.atomic_write import atomic_write_text
from .catalog import reference_limits, settings
from .preparation import prepare

_LEASES: dict[str, "ReferenceLease"] = {}
_LOCK = threading.RLock()
REFERENCE_TTL = 1800
_PUBLISH_GRACE = 6.0     # seconds after cloudflared registers the tunnel before this computer's first DNS lookup


class _ReferenceServer(ThreadingHTTPServer):
    """Bound public fetch concurrency; never spawn unlimited request threads."""
    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(*args)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, *_args):
        pass  # Request errors must not print transfer metadata.


def helper_path(config: dict) -> str | None:
    import os

    name = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    managed = Path(resolve_state_path("bin/media/" + name, config))
    return str(managed) if managed.is_file() else shutil.which(name)


def lease_status(job_id: str) -> dict:
    with _LOCK:
        lease = _LEASES.get(job_id)
        return {"active": bool(lease and not lease.closed),
                "expires_at": lease.expires_at if lease and not lease.closed else None}


def revoke(job_id: str) -> None:
    with _LOCK:
        lease = _LEASES.get(job_id)
    if lease:
        lease.close()


class ReferenceLease:
    def __init__(self, config: dict, job_id: str, references: list[dict], operation: str, model: str):
        from .kie import check_network

        if sys.platform != "win32":
            raise ValueError("Crash-safe temporary reference sharing currently requires Windows; no files were shared.")
        check_network(config, "https://api.trycloudflare.com")
        self.config = config
        if settings(config).get("reference_sharing") is not True:
            raise ValueError("Reference sharing is off. Enable it in Generate after reading the privacy notice.")
        executable = helper_path(config)
        if not executable:
            raise ValueError("The optional reference helper is missing. Choose Install reference helper in Generate.")
        root = Path(resolve_state_path("run/media", config))
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="references-", dir=root)
        self.closed = False
        self.lock = threading.RLock()
        self.server = None
        self.process = None
        self.child_job = None
        self.job_id = job_id
        self.expires_at = time.time() + REFERENCE_TTL
        self.files: dict[str, dict] = {}
        self.references: list[dict] = []
        self.base_url = ""
        self.ready_at = 0.0
        self.registered_at = 0.0
        self.ready = threading.Event()
        self.health_path = "/ready/" + secrets.token_hex(32)
        try:
            total = {"audio": 0.0, "video": 0.0}
            for index, ref in enumerate(references):
                path, info = prepare(Path(ref["path"]), Path(self.temporary.name), index, operation, model)
                if info["kind"] != ref["kind"]:
                    raise ValueError("The selected reference kind does not match its decoded content.")
                if info["kind"] in total:
                    total[info["kind"]] += info["duration"]
                route = "/reference/" + secrets.token_hex(32) + path.suffix
                self.files[route] = {"path": path, "size": info["bytes"], "remaining": 250,
                                     "mime": {"image": "image/png", "audio": "audio/wav", "video": "video/mp4"}[info["kind"]]}
                self.references.append({"kind": info["kind"], "role": ref.get("role", "reference"), "route": route})
            seconds = reference_limits(operation, model).get("seconds")
            if seconds and any(v > seconds for v in total.values()):
                raise ValueError("Combined motion/audio reference duration exceeds the selected model's limit.")
            self.server = _ReferenceServer(("127.0.0.1", 0), self._handler())
            self.server.daemon_threads = False
            threading.Thread(target=self.server.serve_forever, name="mo-media-references", daemon=True).start()
            kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace", cwd=self.temporary.name)
            from core.tooling.sandbox import safe_env
            kwargs["env"] = {k: v for k, v in safe_env().items() if not k.startswith("TUNNEL_")}
            # Ignore the operator's unrelated named-tunnel/configuration state.
            helper_config = Path(self.temporary.name) / "helper.yml"
            atomic_write_text(helper_config, "{}\n")
            apply_windows_hidden_process_flags(kwargs)
            self.process = subprocess.Popen(
                [executable, "tunnel", "--config", str(helper_config), "--url", f"http://127.0.0.1:{self.server.server_port}", "--no-autoupdate"],
                **kwargs)
            self.child_job = bind_windows_child_lifetime(self.process, required=True)
            with _LOCK:
                if job_id in _LEASES:
                    raise ValueError("A reference lease already belongs to this job.")
                _LEASES[job_id] = self
            self.expiry = threading.Timer(REFERENCE_TTL, self.close)
            self.expiry.daemon = True
            self.expiry.start()
            threading.Thread(target=self._read_tunnel, name="mo-media-tunnel", daemon=True).start()
        except BaseException:
            self.close()
            raise

    def urls(self, cancel=None) -> list[dict]:
        from .kie import _connection, check_network

        deadline = time.monotonic() + 60
        failure = "Temporary reference sharing could not be reached; nothing was submitted."
        while time.monotonic() < deadline:
            if self.closed or (cancel is not None and cancel.is_set()):
                self.close()
                raise InterruptedError("Reference preparation stopped before submission.")
            if not self.ready.wait(.5):
                continue
            if not self._lookup_allowed():
                if cancel is not None:
                    cancel.wait(.25)
                else:
                    time.sleep(.25)
                continue
            failure = "Temporary reference sharing could not be reached; nothing was submitted."
            try:
                check_network(self.config, self.base_url)
                conn, target = _connection(self.base_url + self.health_path)
                try:
                    conn.request("GET", target)
                    response = conn.getresponse()
                    if response.status == 200 and response.read(16) == b"ready":
                        return [{"kind": r["kind"], "role": r["role"], "url": self.base_url + r["route"]}
                                for r in self.references]
                finally:
                    conn.close()
            except socket.gaierror:
                failure = "This computer could not resolve the temporary reference hostname. Check DNS/network availability; nothing was submitted."
            except (OSError, ValueError, http.client.HTTPException):
                pass
            if cancel is not None:
                cancel.wait(1)
            else:
                time.sleep(1)
        self.close()
        raise ValueError(failure)

    def _lookup_allowed(self) -> bool:
        """A quick tunnel's hostname is published a moment after cloudflared registers the tunnel, and how long that
        takes varies. Asked earlier, DNS answers 'does not exist' and a home router keeps that answer for minutes
        (measured 2026-10-08 on his PC: asked at once, every lookup failed for 75 s; Cloudflare's public DNS answered
        2.4 s after the address appeared; seven first lookups 3-4 s after registration all resolved). So this
        computer's first lookup waits twice that after registration, or after the address if no registration line
        is seen."""
        start = float(getattr(self, "registered_at", 0.0) or 0.0) or float(getattr(self, "ready_at", 0.0) or 0.0)
        return bool(start) and time.monotonic() >= start + _PUBLISH_GRACE

    def _read_tunnel(self):
        try:
            for line in self.process.stdout:
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com\b", line)
                if match and not self.ready.is_set():
                    self.base_url = match.group(0)
                    self.ready_at = time.monotonic()
                    self.ready.set()
                if "Registered tunnel connection" in line and not self.registered_at:
                    self.registered_at = time.monotonic()
                # Always drain, but never retain or log capability URLs.
        finally:
            self.close()

    def _handler(self):
        lease = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def do_HEAD(self):
                self._serve(head=True)

            def do_GET(self):
                self._serve(head=False)

            def _serve(self, *, head):
                # No redirects, directory listings, query tokens, uploads or local controls.
                with lease.lock:
                    if lease.closed or time.time() >= lease.expires_at:
                        self.send_error(410)
                        return
                    if self.path == lease.health_path:
                        self.send_response(200)
                        self.send_header("Content-Length", "5")
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        if not head:
                            self.wfile.write(b"ready")
                        return
                    item = lease.files.get(self.path)
                    if not item or item["remaining"] <= 0:
                        self.send_error(404)
                        return
                    start, end = 0, item["size"] - 1
                    range_value = self.headers.get("Range")
                    if range_value:
                        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_value)
                        if not match or not any(match.groups()):
                            self.send_error(416)
                            return
                        a, b = match.groups()
                        if a:
                            start = int(a)
                            end = min(int(b), end) if b else end
                        else:
                            start = max(0, item["size"] - int(b))
                        if not 0 <= start <= end < item["size"]:
                            self.send_error(416)
                            return
                    if not head:
                        item["remaining"] -= 1
                    path, mime, size = item["path"], item["mime"], item["size"]
                try:
                    with path.open("rb") as source:
                        self.send_response(206 if range_value else 200)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(end - start + 1))
                        self.send_header("Cache-Control", "no-store, private, max-age=0")
                        self.send_header("Referrer-Policy", "no-referrer")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.send_header("Accept-Ranges", "bytes")
                        if range_value:
                            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                        self.end_headers()
                        if head:
                            return
                        source.seek(start)
                        remaining = end - start + 1
                        while remaining and not lease.closed and time.time() < lease.expires_at:
                            data = source.read(min(65536, remaining))
                            if not data:
                                break
                            self.wfile.write(data)
                            remaining -= len(data)
                except OSError:
                    pass

        return Handler

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.files.clear()
        with _LOCK:
            if _LEASES.get(self.job_id) is self:
                _LEASES.pop(self.job_id, None)
        if hasattr(self, "expiry"):
            self.expiry.cancel()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.child_job:
            import ctypes
            from ctypes import wintypes
            ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self.child_job))
            self.child_job = None
        self.temporary.cleanup()


@atexit.register
def _close_owned_leases():
    with _LOCK:
        leases = list(_LEASES.values())
    for lease in leases:
        lease.close()
