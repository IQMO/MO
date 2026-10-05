"""Small Gmail REST and installed-app OAuth client; imported only on use."""
from __future__ import annotations

import base64
from email.message import EmailMessage
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
import hashlib
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

SCOPE = "https://www.googleapis.com/auth/gmail.modify"
API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
# Google ended the refresh authorization (an OAuth app in Testing status does so after 7 days).
# The text tells MO to recover through the existing connect action rather than report it.
_EXPIRED = ("Gmail authorization expired (Google ended it). Run mail action=connect now: it reopens "
            "Google consent in the browser, the operator approves once, and the request can continue")


class GmailError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0):
        super().__init__(message)
        self.status = status


def _http():
    import httpx

    return httpx


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _token_request(values: dict[str, str]) -> dict:
    try:
        response = _http().post(TOKEN_URL, data=values, timeout=20.0)
        payload = response.json()
    except (OSError, ValueError, _http().HTTPError) as exc:
        raise GmailError("Google authorization did not complete") from exc
    if response.status_code != 200 or not isinstance(payload, dict):
        reason = str(payload.get("error") or "") if isinstance(payload, dict) else ""
        raise GmailError(_EXPIRED if reason == "invalid_grant" else "Google authorization failed", status=401 if reason == "invalid_grant" else response.status_code)
    if not payload.get("access_token"):
        raise GmailError("Google authorization returned no access token")
    payload["expires_at"] = time.time() + max(60, int(payload.get("expires_in") or 3600)) - 60
    return payload


def connect(client_id: str, client_secret: str) -> dict:
    """Run one loopback+PKCE consent operation from a trusted local surface."""
    if not str(client_id or "").strip():
        raise GmailError("Gmail OAuth client ID is missing")
    if not str(client_secret or "").strip():
        raise GmailError("Gmail OAuth client secret is missing")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64(hashlib.sha256(verifier.encode("ascii")).digest())
    receipt: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_GET(self):
            parsed = urlparse(self.path)
            values = parse_qs(parsed.query)
            if parsed.path != "/oauth2callback":
                self.send_error(404)
                return
            receipt["state"] = str((values.get("state") or [""])[0])
            receipt["code"] = str((values.get("code") or [""])[0])
            receipt["error"] = str((values.get("error") or [""])[0])
            body = b"MO Gmail authorization received. You can close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Callback)
    server.timeout = 180
    redirect = f"http://127.0.0.1:{server.server_port}/oauth2callback"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    try:
        if not webbrowser.open(f"{AUTH_URL}?{urlencode(params)}"):
            raise GmailError("MO could not open the browser for Gmail consent")
        server.handle_request()
    finally:
        server.server_close()
    if receipt.get("state") != state or not receipt.get("code") or receipt.get("error"):
        raise GmailError("Gmail consent was cancelled, expired, or did not match this request")
    token = _token_request({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": receipt["code"],
        "code_verifier": verifier,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    })
    if SCOPE not in str(token.get("scope") or "").split():
        raise GmailError("Gmail did not grant the requested scope")
    if not token.get("refresh_token"):
        raise GmailError("Gmail did not return a refresh token; reconnect")
    return token


def refresh(client_id: str, client_secret: str, refresh_token: str) -> dict:
    return _token_request({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })


def request(token: str, method: str, path: str, *, params: dict | None = None, body: dict | None = None,
            timeout: float = 25.0) -> dict:
    """One Gmail request. Mutation retries are intentionally the caller's decision."""
    if not path.startswith("/") or ".." in path:
        raise ValueError("invalid Gmail API path")
    try:
        response = _http().request(
            method, f"{API_ROOT}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params, json=body, timeout=timeout,
        )
    except _http().HTTPError as exc:
        raise GmailError("Gmail request outcome is unknown; inspect the mailbox before retrying") from exc
    if response.status_code >= 400:
        if response.status_code in {401, 403}:
            message = "Gmail authorization is unavailable; reconnect the account"
        elif response.status_code in {429, 500, 502, 503, 504}:
            message = "Gmail is temporarily unavailable"
        else:
            message = f"Gmail request failed (HTTP {response.status_code})"
        raise GmailError(message, status=response.status_code)
    try:
        result = response.json()
    except ValueError as exc:
        raise GmailError("Gmail returned an unreadable response") from exc
    if not isinstance(result, dict):
        raise GmailError("Gmail returned an unexpected response")
    return result


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, _attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        elif tag in {"br", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1
        elif tag in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _body_text(payload: dict) -> str:
    candidates: dict[str, str] = {}

    visited = 0

    def visit(part: dict, depth: int = 0):
        nonlocal visited
        if depth > 12 or visited >= 100:
            return
        visited += 1
        mime = str(part.get("mimeType") or "").lower()
        data = str((part.get("body") or {}).get("data") or "")
        if mime in {"text/plain", "text/html"} and data and mime not in candidates:
            try:
                candidates[mime] = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            except ValueError:
                pass
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                visit(child, depth + 1)

    visit(payload)
    if "text/plain" in candidates:
        return candidates["text/plain"][:20000]
    parser = _PlainHTML()
    parser.feed(candidates.get("text/html", "")[:100000])
    return "".join(parser.parts).strip()[:20000]


def message_view(value: dict, *, include_body: bool = False) -> dict:
    payload = value.get("payload") or {}
    headers = {
        str(item.get("name") or "").lower(): str(item.get("value") or "")[:500]
        for item in payload.get("headers") or [] if isinstance(item, dict)
    }
    view = {
        "id": str(value.get("id") or ""),
        "thread_id": str(value.get("threadId") or ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "labels": list(value.get("labelIds") or [])[:30],
        "snippet": str(value.get("snippet") or "")[:250],
    }
    if include_body:
        view["body"] = _body_text(payload)
    return view


def draft_review(raw: str) -> dict[str, str]:
    """Extract bounded send-review headers; reject drafts this client cannot inspect."""
    if not raw or len(raw) > 1_400_000:
        raise GmailError("Draft is too large for MO to review; send it in Gmail")
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        message = BytesParser(policy=policy.default).parsebytes(decoded)
    except (ValueError, TypeError) as exc:
        raise GmailError("Draft cannot be decoded for review; it was not sent") from exc
    if any(part.get_content_disposition() == "attachment" or part.get_filename()
           for part in message.walk()):
        raise GmailError("Draft has attachments MO cannot review; send it in Gmail")
    def review_header(header: str, limit: int) -> str:
        values = message.get_all(header, [])
        if len(values) > 1:
            raise GmailError("Draft has repeated headers MO cannot review; send it in Gmail")
        clean = " ".join(str(values[0]).split()) if values else ""
        if len(clean) > limit:
            raise GmailError("Draft headers are too long for MO to review; send it in Gmail")
        return clean

    recipients = "; ".join(
        f"{header}: {value}" for header in ("To", "Cc", "Bcc")
        if (value := review_header(header, 500))
    )
    if not recipients:
        raise GmailError("Draft has no reviewable recipient; it was not sent")
    return {"recipients": recipients, "subject": review_header("Subject", 300)}


def draft_raw(*, sender: str, to: str, subject: str, body: str) -> str:
    if any("\r" in item or "\n" in item for item in (sender, to, subject)):
        raise ValueError("mail headers cannot contain newlines")
    parsed = parseaddr(to)[1]
    if not parsed or parsed != to.strip() or len(to) > 320:
        raise ValueError("draft needs one exact recipient address")
    if not body.strip() or len(body) > 100000:
        raise ValueError("draft body must contain 1 to 100000 characters")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject[:500]
    msg.set_content(body)
    return _b64(msg.as_bytes())
