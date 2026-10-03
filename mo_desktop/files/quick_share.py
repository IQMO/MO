"""Short-lived browser transfer session for MO Files on one local interface."""
from __future__ import annotations

import base64
import hashlib
import html
import io
import ipaddress
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

from core.files.service import FileManagerService


MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_FILES = 8
SESSION_SECONDS = 300


def local_addresses() -> list[dict[str, str]]:
    """Offer bindable private IPv4 addresses without optional system packages."""
    try:
        rows = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return []
    found: list[dict[str, str]] = []
    for address in sorted({row[4][0] for row in rows}):
        try:
            ip = ipaddress.ip_address(address)
            if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local:
                continue
            # DNS can retain a disconnected adapter's address. Bind without
            # listening to confirm this computer still owns the offered IP.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((address, 0))
        except (ValueError, OSError):
            continue
        found.append({"address": str(ip), "label": "Local network"})
    return found


class QuickShareSession:
    """Expose exact selected files and one allowed destination for five minutes."""

    def __init__(
        self,
        files: FileManagerService,
        *,
        address: str,
        location_id: str,
        parent_path: str,
        selected: list[dict[str, Any]],
        can_upload: bool,
    ) -> None:
        self.files = files
        self.location_id = location_id
        self.parent_path = parent_path
        self.can_upload = can_upload
        self.selected = selected
        self.uploaded: list[str] = []
        self.connected = False
        self.upload_state = "waiting"
        self._inflight = 0
        self._uploads: dict[threading.Thread, socket.socket] = {}
        self.token = secrets.token_urlsafe(32)
        self.expires_at = time.monotonic() + SESSION_SECONDS
        self._lock = threading.RLock()
        self._closed = False
        self._server = ThreadingHTTPServer((address, 0), self._handler())
        self._server.daemon_threads = True
        self.url = f"http://{address}:{self._server.server_port}/s/{self.token}"
        self._thread = threading.Thread(target=self._server.serve_forever, name="mo-files-quick-share", daemon=True)
        self._thread.start()
        self._expiry = threading.Timer(SESSION_SECONDS, self.stop)
        self._expiry.daemon = True
        self._expiry.start()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        session = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "MOFiles/1"
            protocol_version = "HTTP/1.1"
            rbufsize = 0  # Read available upload bytes without waiting for a full block.

            def log_message(self, _format: str, *_args: Any) -> None:
                # The bearer URL never enters process output or the normal log.
                return

            def _reply(self, status: int, body: bytes, mime: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def _authorized(self) -> tuple[bool, str]:
                path = urlsplit(self.path).path
                host = f"{self.server.server_address[0]}:{self.server.server_port}"
                with session._lock:
                    active = not session._closed and time.monotonic() < session.expires_at
                return active and self.headers.get("Host") == host and path.startswith(f"/s/{session.token}"), path

            def do_GET(self) -> None:
                self.connection.settimeout(20)
                allowed, path = self._authorized()
                if not allowed:
                    self._reply(404, b"Unavailable", "text/plain; charset=utf-8")
                    return
                base = f"/s/{session.token}"
                if path == base:
                    with session._lock:
                        session.connected = True
                    self._reply(200, session._page(), "text/html; charset=utf-8")
                    return
                if path.startswith(base + "/file/"):
                    handle = None
                    try:
                        index = int(path.removeprefix(base + "/file/"))
                        if index < 0:
                            raise ValueError("Invalid file")
                        selected = session.selected[index]
                        source = session.files.source_path(session.location_id, selected["path"])
                        handle = source.open("rb")
                        if source.stat().st_size != selected["bytes"]:
                            raise ValueError("File changed")
                        digest = hashlib.sha256()
                        for block in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(block)
                        if digest.hexdigest() != selected["sha256"]:
                            raise ValueError("File changed")
                        handle.seek(0)
                        with session._lock:
                            if session._closed or time.monotonic() >= session.expires_at:
                                raise RuntimeError("Quick transfer ended")
                    except (IndexError, ValueError, OSError, KeyError, RuntimeError):
                        if handle is not None:
                            handle.close()
                        self._reply(409, b"File changed. Create a new QR.", "text/plain; charset=utf-8")
                        return
                    try:
                        with handle:
                            self.send_response(200)
                            self.send_header("Content-Type", "application/octet-stream")
                            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{_quoted_name(selected['name'])}")
                            self.send_header("Content-Length", str(selected["bytes"]))
                            self.send_header("Cache-Control", "no-store")
                            self.send_header("Referrer-Policy", "no-referrer")
                            self.send_header("X-Content-Type-Options", "nosniff")
                            self.send_header("Connection", "close")
                            self.end_headers()
                            remaining = selected["bytes"]
                            while remaining:
                                with session._lock:
                                    if session._closed or time.monotonic() >= session.expires_at:
                                        break
                                chunk = handle.read(min(1024 * 1024, remaining))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                            self.close_connection = True
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                self._reply(404, b"Unavailable", "text/plain; charset=utf-8")

            def do_POST(self) -> None:
                self.connection.settimeout(1)
                allowed, path = self._authorized()
                if not allowed or path != f"/s/{session.token}/upload" or not session.can_upload:
                    self._reply(404, b"Unavailable", "text/plain; charset=utf-8")
                    return
                origin = self.headers.get("Origin")
                if origin and origin != session.url.rsplit("/s/", 1)[0]:
                    with session._lock:
                        session.upload_state = "rejected"
                    self._reply(403, b"Origin denied", "text/plain; charset=utf-8")
                    return
                reserved = False
                try:
                    size = int(self.headers.get("Content-Length", "-1"))
                    name = unquote(self.headers.get("X-File-Name", ""))
                    if size < 0 or size > MAX_FILE_BYTES:
                        raise ValueError("File exceeds the 64 MiB limit")
                    with session._lock:
                        if session._closed or len(session.uploaded) + session._inflight >= MAX_FILES:
                            raise ValueError("This QR has reached its file limit")
                        session._inflight += 1
                        session.upload_state = "receiving"
                        session._uploads[threading.current_thread()] = self.connection
                        reserved = True

                    class SessionReader:
                        def read(self, amount: int) -> bytes:
                            while True:
                                with session._lock:
                                    if session._closed or time.monotonic() >= session.expires_at:
                                        raise RuntimeError("Quick transfer ended")
                                if time.monotonic() >= deadline:
                                    raise RuntimeError("Upload timed out")
                                try:
                                    chunk = handler.rfile.read(amount)
                                except socket.timeout:
                                    continue
                                with session._lock:
                                    if session._closed or time.monotonic() >= session.expires_at:
                                        raise RuntimeError("Quick transfer ended")
                                return chunk

                    deadline = time.monotonic() + 120
                    handler = self
                    entry = session.files.import_stream(
                        session.location_id, session.parent_path, name, SessionReader(), size,
                        cancelled=lambda: session._closed or time.monotonic() >= session.expires_at,
                    )
                    with session._lock:
                        session.uploaded.append(entry["name"])
                        session.upload_state = "saved"
                except (ValueError, OSError, RuntimeError) as exc:
                    with session._lock:
                        session.upload_state = "rejected"
                    try:
                        self._reply(400, str(exc).encode("utf-8")[:300], "text/plain; charset=utf-8")
                    except OSError:
                        pass
                    return
                finally:
                    if reserved:
                        with session._lock:
                            session._inflight -= 1
                            session._uploads.pop(threading.current_thread(), None)
                self._reply(201, b"Received", "text/plain; charset=utf-8")

        return Handler

    def _page(self) -> bytes:
        from interface.desktop_brand import cube_mark_css, cube_mark_html

        rows = "".join(
            f'<a class="file" href="/s/{self.token}/file/{index}" download><span>{html.escape(row["name"])}</span><small>{row["bytes"]:,} bytes ↓</small></a>'
            for index, row in enumerate(self.selected)
        )
        upload = (
            '<label class="drop">Send files to MO<input id="files" type="file" multiple></label>'
            '<p>Choose up to eight files, 64 MiB each. Files go to the folder selected in MO Files.</p>'
            if self.can_upload else '<p>This MO folder accepts downloads only.</p>'
        )
        page = f'''<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'">
<title>MO Files · Quick transfer</title><style>
:root{{font-family:system-ui,sans-serif;color-scheme:dark;--bg:#091017;--surface:#132530;--edge:#335160;--text:#eef5fa;--muted:#9cafbd;--brand:#6bcee9;background:var(--bg);color:var(--text)}}
@media(prefers-color-scheme:light){{:root{{color-scheme:light;--bg:#f4f8fa;--surface:#fff;--edge:#b5cbd5;--text:#19303d;--muted:#526d7a;--brand:#197897}}}}
body{{max-width:530px;margin:0 auto;padding:30px 20px}}header{{display:flex;align-items:center;gap:12px;margin-bottom:28px}}
{cube_mark_css('.mo-mark')}
.mo-mark{{--mark-edge:11px;--mark-gap:3px;--mark-fill:var(--brand);--mark-glow:var(--brand);--mark-glow-scale:1}}
h1{{font-size:23px;margin:0}}h2{{font-size:15px;margin:28px 0 9px}}p{{color:var(--muted);line-height:1.5;font-size:13px}}
.file,.drop{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px;margin:7px 0;border:1px solid var(--edge);border-radius:10px;background:var(--surface);color:var(--text);text-decoration:none}}
.file small{{color:var(--brand);white-space:nowrap}}.drop{{cursor:pointer;border-style:dashed;border-color:var(--brand);justify-content:center;font-weight:700}}
input{{display:none}}#status{{min-height:22px;color:var(--brand)}}footer{{margin-top:30px;border-top:1px solid var(--edge);padding-top:12px}}
</style></head><body><header>{cube_mark_html(label='MO')}<h1>MO Files</h1></header>
<p>Quick transfer over this Wi‑Fi network. Use a trusted private network: this local connection is not encrypted. This QR expires in five minutes or when MO Files closes.</p>
<h2>Take from MO</h2>{rows or '<p>No files selected to download.</p>'}<h2>Send to MO</h2>{upload}<p id="status" role="status"></p>
<footer><p>Only the selected files and destination folder are available in this session.</p></footer>
<script>const input=document.getElementById('files'),status=document.getElementById('status');
function send(file){{return new Promise((resolve,reject)=>{{
const request=new XMLHttpRequest();request.open('POST',location.pathname+'/upload');
request.setRequestHeader('X-File-Name',encodeURIComponent(file.name));request.timeout=120000;
request.upload.onprogress=event=>{{if(event.lengthComputable)status.textContent='Sending '+file.name+' · '+Math.round(event.loaded/event.total*100)+'%';}};
request.onload=()=>request.status===201?resolve():reject(new Error(request.responseText||'Upload rejected'));
request.onerror=()=>reject(new Error('Connection lost. Check that MO Files is open and both devices use the same Wi-Fi.'));
request.ontimeout=()=>reject(new Error('Upload timed out. Start a fresh QR and try again.'));
request.send(file);
}});}}
if(input)input.onchange=async()=>{{for(const file of [...input.files].slice(0,8)){{
if(file.size>{MAX_FILE_BYTES}){{status.textContent='Could not send '+file.name+': file exceeds 64 MiB';break;}}
status.textContent='Sending '+file.name+'…';
try{{await send(file);status.textContent='Received '+file.name;}}
catch(error){{status.textContent='Could not send '+file.name+': '+error.message;break;}}}}}};</script></body></html>'''
        return page.encode("utf-8")

    def public(self) -> dict[str, Any]:
        import segno

        image = io.BytesIO()
        segno.make(self.url, error="m", micro=False).save(image, kind="png", scale=9, border=4)
        return {
            "image": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode("ascii"),
            "address": self.url.rsplit("/s/", 1)[0],
            "expires_seconds": max(0, int(self.expires_at - time.monotonic())),
            "offered": [row["name"] for row in self.selected],
            "received": list(self.uploaded),
            "can_upload": self.can_upload,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active": not self._closed and time.monotonic() < self.expires_at,
                "expires_seconds": max(0, int(self.expires_at - time.monotonic())),
                "received": list(self.uploaded),
                "connected": self.connected,
                "upload_state": self.upload_state,
            }

    def stop(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            uploads = list(self._uploads.items())
        for _, connection in uploads:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self._expiry.cancel()
        self._server.shutdown()
        self._server.server_close()
        for thread, _ in uploads:
            if thread is not threading.current_thread():
                thread.join(timeout=5)


def _quoted_name(name: str) -> str:
    from urllib.parse import quote

    return quote(name, safe="")
