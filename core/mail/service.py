"""One Gmail account owner shared by MO tools, commands, and Desktop status."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any

from core.runtime.lock import file_byte_lock
from core.state.paths import resolve_state_path
from core.state.secrets import canonical_secret_file, resolve_secret
from core.utils.atomic_write import atomic_write_text

from . import gmail, secure_store

_STATE_LOCK = threading.RLock()
_PENDING_LOCK = threading.RLock()
_PENDING: dict[str, tuple[str, int, float, str]] = {}
_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_APPROVE = re.compile(r"\bapprove\s+([0-9a-f]{10})\b", re.IGNORECASE)
_ACTION_WORDS = {
    "connect": re.compile(r"\b(?:connect|link|set\s*up|setup|authori[sz]e|sign\s*in|verbind\w*|koppel\w*|aanmeld\w*)\b", re.I),
    "draft": re.compile(r"\b(?:draft|compose|write|reply|respond)\b", re.I),
    "send": re.compile(r"\b(?:send|deliver)\b", re.I),
    "archive": re.compile(r"\barchive\b", re.I),
    "mark_read": re.compile(r"\bmark\b.{0,40}\b(?:read|seen)\b", re.I),
    "label": re.compile(r"\b(?:label|tag|categorize|classify)\b", re.I),
    "trash": re.compile(r"\b(?:trash|delete|discard)\b|\bmove\b.{0,40}\b(?:bin|trash)\b", re.I),
    "move": re.compile(r"\b(?:move|file|organize|sort)\b", re.I),
}


def operator_requested_action(action: str, user_input: str) -> bool:
    pattern = _ACTION_WORDS.get(action)
    if pattern is None:
        return True
    text = str(user_input or "")
    for match in pattern.finditer(text):
        preceding = text[max(0, match.start() - 30):match.start()]
        if not re.search(r"(?i)\b(?:do not|don't|never|without|avoid)\s+(?:\w+\s+){0,2}$", preceding):
            return True
    return False


def has_pending_confirmation(session_id: str, user_input: str) -> bool:
    """Only a live pending Gmail code can make a short approval reply a mail turn."""
    supplied = _APPROVE.fullmatch(str(user_input or "").strip())
    if not supplied or not session_id:
        return False
    with _PENDING_LOCK:
        return any(
            key.startswith(f"{session_id}:") and value[2] >= time.time()
            and value[0][:10] == supplied.group(1).lower()
            for key, value in _PENDING.items()
        )


def complete_pending_confirmation(agent: Any, user_input: str) -> str | None:
    """Execute an exact later-turn user approval without asking the model to recall IDs."""
    text = str(user_input or "").strip()
    session_id = str(getattr(getattr(agent, "session", None), "session_id", "") or "")
    if not has_pending_confirmation(session_id, text):
        return None
    code = _APPROVE.fullmatch(text).group(1).lower()
    with _PENDING_LOCK:
        match = next((
            (key, value) for key, value in _PENDING.items()
            if key.startswith(f"{session_id}:") and value[0][:10] == code
        ), None)
    if match is None:
        return None
    key, pending = match
    if int(getattr(getattr(agent, "session", None), "turn_count", 0) or 0) <= pending[1]:
        return None
    action = key.rsplit(":", 1)[-1]
    ident = pending[3]
    if action in {"outlook_send", "outlook_trash"}:
        from core.browser_bridge import BrowserBridgeError
        from . import outlook_browser

        with _PENDING_LOCK:
            _PENDING.pop(key, None)
        try:
            if action == "outlook_send":
                outlook_browser.confirm_send(agent, ident)
            else:
                outlook_browser.confirm_trash(agent, ident)
        except (ValueError, BrowserBridgeError) as exc:
            return f"Outlook {'send' if action == 'outlook_send' else 'delete'} was not confirmed: {exc}"
        return ("Outlook Send was clicked. Check Sent Items to confirm the result."
                if action == "outlook_send" else "Outlook Delete was clicked. Check Deleted Items to confirm the result.")
    service = MailService(getattr(agent, "config", {}) or {})
    try:
        result = service.send_draft(ident, agent=agent) if action == "send" else service.modify(ident, "trash", agent=agent)
    except (gmail.GmailError, secure_store.SecureStoreUnavailable, ValueError) as exc:
        return f"Gmail {action} was not confirmed: {exc}"
    return json.dumps(result, ensure_ascii=False)


def _path(relative: str, config: dict) -> Path:
    return Path(resolve_state_path(relative, config))


def _read_json(path: Path) -> dict:
    raw = secure_store.read(path)
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise secure_store.SecureStoreUnavailable("Gmail protected state is unreadable") from exc
    if not isinstance(value, dict):
        raise secure_store.SecureStoreUnavailable("Gmail protected state is invalid")
    return value


def _write_json(path: Path, value: dict) -> None:
    secure_store.write(path, json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


class MailService:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.settings = self.config.get("mail", {}) or {}
        self.token_path = _path("credentials/gmail-token.bin", self.config)
        self.state_path = _path("memory/surfaces/mail-state.bin", self.config)
        self.lock_path = _path("run/mail/sync.lock", self.config)

    def _client_id(self) -> str:
        return resolve_secret("GMAIL_CLIENT_ID", service="gmail", config=self.config).strip()

    def _client_secret(self) -> str:
        return resolve_secret("GMAIL_CLIENT_SECRET", service="gmail", config=self.config).strip()

    def _enabled(self) -> bool:
        return bool(self.settings.get("enabled", False))

    def status(self) -> dict:
        if not self._enabled():
            return {"state": "disabled", "next_action": "Ask MO to connect Gmail, or open Dashboard → Email to enable it."}
        if not secure_store.available():
            return {"state": "secure_storage_unavailable", "next_action": "Gmail requires a supported encrypted local store."}
        if not self._client_id() or not self._client_secret():
            return {"state": "client_missing", "next_action": "Create your Google Desktop OAuth client, then save its ID and secret in Dashboard → Email."}
        try:
            token = _read_json(self.token_path)
            state = _read_json(self.state_path)
        except secure_store.SecureStoreUnavailable:
            return {"state": "reconnect_required", "next_action": "Reconnect Gmail from a trusted local surface."}
        if not token.get("refresh_token") or gmail.SCOPE not in str(token.get("scope") or "").split():
            return {"state": "disconnected", "next_action": "Ask MO to connect Gmail, or choose Continue with Google in Dashboard → Email."}
        if state.get("auth_error"):
            return {"state": "reconnect_required", "next_action": "Ask MO to reconnect Gmail, or choose Continue with Google in Dashboard → Email."}
        return {
            "state": "connected" if state.get("sync_known") else "sync_unknown",
            "unread": max(0, int(state.get("unread") or 0)) if state.get("sync_known") else None,
            "last_sync": float(state.get("last_sync") or 0),
            "next_action": "" if state.get("sync_known") else "Run /mail sync or wait for Desktop sync.",
        }

    def notifications_enabled(self) -> bool:
        """Return the operator's encrypted Gmail notice preference."""
        return _read_json(self.state_path).get("notifications_enabled", True) is not False

    def set_notifications_enabled(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            raise ValueError("Gmail notifications must be on or off")
        with file_byte_lock(self.lock_path, _STATE_LOCK):
            state = _read_json(self.state_path)
            state["notifications_enabled"] = enabled
            if not enabled:
                state["notices"] = []
            _write_json(self.state_path, state)
        return enabled

    def save_client_credentials(self, client_id: str, client_secret: str) -> dict:
        """Store the operator's Desktop OAuth client in the canonical private file."""
        if self.status().get("state") in {"connected", "sync_unknown"}:
            raise ValueError("Disconnect Gmail before changing its OAuth client")
        values = {"GMAIL_CLIENT_ID": str(client_id or "").strip(),
                  "GMAIL_CLIENT_SECRET": str(client_secret or "").strip()}
        if (not values["GMAIL_CLIENT_ID"].endswith(".apps.googleusercontent.com")
                or len(values["GMAIL_CLIENT_ID"]) > 300):
            raise ValueError("Enter a Google Desktop OAuth client ID")
        if (not values["GMAIL_CLIENT_SECRET"] or len(values["GMAIL_CLIENT_SECRET"]) > 300
                or any(not value.isprintable() or "\n" in value or "\r" in value
                       for value in values.values())):
            raise ValueError("Enter the Desktop OAuth client secret")
        path = canonical_secret_file("gmail", self.config)
        if path is None:
            raise ValueError("Gmail's canonical credential location is unavailable")
        lock = Path(resolve_state_path("run/mail/client.lock", self.config))
        with file_byte_lock(lock, _STATE_LOCK):
            try:
                existing = path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                existing = []
            kept = [line for line in existing
                    if line.partition("=")[0].strip() not in values]
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, "\n".join([*kept,
                *(f"{key}={value}" for key, value in values.items())]) + "\n", encoding="utf-8")
            if os.name != "nt":
                os.chmod(path, 0o600)
        return {"state": "client_ready"}

    def connect(self) -> dict:
        if not self._enabled():
            raise gmail.GmailError("Enable mail in private MO config before connecting")
        if not secure_store.available():
            raise gmail.GmailError("Gmail secure storage is unavailable on this host")
        client_id = self._client_id()
        token = gmail.connect(client_id, self._client_secret())
        profile = gmail.request(str(token["access_token"]), "GET", "/profile")
        email = str(profile.get("emailAddress") or "").strip()
        if not email:
            raise gmail.GmailError("Gmail did not identify the connected account")
        token["account"] = email
        with file_byte_lock(self.lock_path, _STATE_LOCK):
            with file_byte_lock(self.token_path.with_suffix(".lock"), _STATE_LOCK):
                _write_json(self.state_path, {})
                _write_json(self.token_path, token)
        return {"state": "connected", "next_action": "Run /mail sync to establish the inbox baseline."}

    def disconnect(self) -> dict:
        with file_byte_lock(self.lock_path, _STATE_LOCK):
            with file_byte_lock(self.token_path.with_suffix(".lock"), _STATE_LOCK):
                self.token_path.unlink(missing_ok=True)
                self.state_path.unlink(missing_ok=True)
        return {"state": "disconnected", "note": "Local Gmail authority and sync state removed. Revoke MO in Google Account settings if desired."}

    def _token(self) -> dict:
        if not self._enabled():
            raise gmail.GmailError("Gmail is disabled")
        if not secure_store.available():
            raise gmail.GmailError("Gmail secure storage is unavailable on this host")
        with file_byte_lock(self.token_path.with_suffix(".lock"), _STATE_LOCK):
            token = _read_json(self.token_path)
            if not token.get("refresh_token") or gmail.SCOPE not in str(token.get("scope") or "").split():
                raise gmail.GmailError("Gmail is disconnected; use /mail connect")
            if float(token.get("expires_at") or 0) <= time.time():
                updated = gmail.refresh(self._client_id(), self._client_secret(), str(token["refresh_token"]))
                token.update(updated)
                if gmail.SCOPE not in str(token.get("scope") or "").split():
                    raise gmail.GmailError("Gmail scope changed; reconnect")
                _write_json(self.token_path, token)
            return token

    def _request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None,
                 timeout: float = 25.0) -> dict:
        token = self._token()
        return gmail.request(str(token["access_token"]), method, path, params=params, body=body, timeout=timeout)

    @staticmethod
    def _id(value: str) -> str:
        item = str(value or "").strip()
        if not _ID.fullmatch(item):
            raise ValueError("one exact Gmail ID is required")
        return item

    def list_messages(self, *, query: str = "", limit: int = 10) -> dict:
        limit = min(20, max(1, int(limit)))
        search = str(query or "").strip()[:300]
        params = {"maxResults": limit}
        if search:
            params["q"] = search
        else:
            params["labelIds"] = ["INBOX"]
        listing = self._request("GET", "/messages", params=params)
        rows = []
        for item in listing.get("messages") or []:
            ident = self._id(item.get("id"))
            try:
                value = self._request("GET", f"/messages/{ident}", params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
            except gmail.GmailError as exc:
                if exc.status != 404:
                    raise
                # A message can disappear between the inbox list and metadata read.
                continue
            rows.append(gmail.message_view(value))
        return {"messages": rows, "estimate": int(listing.get("resultSizeEstimate") or 0)}

    def read_message(self, ident: str) -> dict:
        value = self._request("GET", f"/messages/{self._id(ident)}", params={"format": "full"})
        return gmail.message_view(value, include_body=True)

    def create_draft(self, *, to: str, subject: str, body: str) -> dict:
        account = str(self._token().get("account") or "")
        raw = gmail.draft_raw(sender=account, to=to, subject=subject, body=body)
        result = self._request("POST", "/drafts", body={"message": {"raw": raw}})
        return {"draft_id": str(result.get("id") or ""), "state": "saved_in_gmail", "to": to, "subject": subject[:200]}

    def move(self, ident: str, folder: str) -> dict:
        """Move Inbox mail into one existing user label, using Gmail's native modify."""
        ident = self._id(ident)
        name = str(folder or "").strip()
        if not name or len(name) > 120 or any(ch in name for ch in "\r\n"):
            raise ValueError("Move needs one existing Gmail label name")
        labels = self._request("GET", "/labels").get("labels") or []
        matches = [item for item in labels if item.get("type") == "user"
                   and str(item.get("name") or "").casefold() == name.casefold()]
        if len(matches) != 1:
            raise ValueError("Choose one exact existing Gmail label name")
        label_id = self._id(matches[0].get("id"))
        result = self._request("POST", f"/messages/{ident}/modify",
                               body={"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]})
        return {"state": "move", "message_id": str(result.get("id") or ""), "folder": str(matches[0]["name"])}

    def move_destinations(self) -> list[str]:
        """Names of existing user labels accepted by move()."""
        labels = self._request("GET", "/labels").get("labels") or []
        names = set()
        for item in labels:
            name = str(item.get("name") or "").strip()
            if item.get("type") == "user" and name and len(name) <= 120 and not any(ch in name for ch in "\r\n"):
                names.add(name)
        return sorted(names, key=str.casefold)

    def _confirmation(self, *, action: str, ident: str, fingerprint: str, agent: Any,
                      review: str = "") -> str | None:
        session = getattr(agent, "session", None)
        owner = str(getattr(session, "session_id", "") or "")
        turn = int(getattr(session, "turn_count", 0) or 0)
        user_conversation = getattr(agent, "_is_user_conversation", None)
        if not owner or not turn or not callable(user_conversation) or not user_conversation():
            return "[APPROVAL REQUIRED] This mail action needs a live user conversation."
        text = str(getattr(agent, "_current_user_input", "") or "")
        conversation_input = getattr(agent, "_conversation_user_input", None)
        if callable(conversation_input):
            text = str(conversation_input(text) or "")
        supplied = _APPROVE.fullmatch(text.strip())
        key = f"{owner}:{action}"
        now = time.time()
        with _PENDING_LOCK:
            pending = _PENDING.get(key)
            if pending and pending[2] < now:
                _PENDING.pop(key, None)
                pending = None
            account = str(self._token().get("account") or "")
            expected = hashlib.sha256(f"{owner}:{action}:{account}:{ident}:{fingerprint}".encode()).hexdigest()
            code = expected[:10]
            if pending and pending[0] == expected and pending[1] < turn and supplied and supplied.group(1).lower() == code:
                _PENDING.pop(key, None)
                return None
            _PENDING[key] = (expected, turn, now + 180, ident)
        detail = f" {review}" if review else ""
        notice = f"[APPROVAL REQUIRED id={code}] {action} for exact Gmail item {ident} is paused.{detail} Review it, then reply 'approve {code}' in a new turn. Approval expires in 3 minutes and is single-use."
        agent._mail_approval_notice = notice
        return notice

    def send_draft(self, ident: str, *, agent: Any) -> dict:
        ident = self._id(ident)
        draft = self._request("GET", f"/drafts/{ident}", params={"format": "raw"})
        message = draft.get("message") or {}
        raw = str(message.get("raw") or "")
        if not raw:
            raise gmail.GmailError("Draft cannot be verified; it was not sent")
        review = gmail.draft_review(raw)
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        reason = self._confirmation(
            action="send", ident=ident, fingerprint=fingerprint, agent=agent,
            review=f"{review['recipients']}; Subject: {review['subject'] or '(none)'}. No attachments.",
        )
        if reason:
            return {"state": "approval_required", "detail": reason}
        result = self._request("POST", "/drafts/send", body={"id": ident})
        return {"state": "sent", "message_id": str(result.get("id") or "")}

    def modify(self, ident: str, action: str, *, label: str = "", agent: Any = None) -> dict:
        ident = self._id(ident)
        labels: dict[str, list[str]] = {}
        if action == "archive":
            labels["removeLabelIds"] = ["INBOX"]
        elif action == "mark_read":
            labels["removeLabelIds"] = ["UNREAD"]
        elif action == "label":
            labels["addLabelIds"] = [self._id(label)]
        elif action == "trash":
            value = self._request("GET", f"/messages/{ident}", params={"format": "minimal"})
            fingerprint = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            reason = self._confirmation(action="trash", ident=ident, fingerprint=fingerprint, agent=agent)
            if reason:
                return {"state": "approval_required", "detail": reason}
            result = self._request("POST", f"/messages/{ident}/trash")
            return {"state": "trashed", "message_id": str(result.get("id") or "")}
        else:
            raise ValueError("unsupported Gmail action")
        result = self._request("POST", f"/messages/{ident}/modify", body=labels)
        return {"state": action, "message_id": str(result.get("id") or "")}

    def sync(self) -> dict:
        with file_byte_lock(self.lock_path, _STATE_LOCK):
            state = _read_json(self.state_path)
            now = time.time()
            if float(state.get("retry_after") or 0) > now:
                raise gmail.GmailError("Gmail sync is waiting after a temporary failure", status=429)
            try:
                deadline = time.monotonic() + 30
                profile = self._request("GET", "/profile", timeout=8)
                account = str(profile.get("emailAddress") or "")
                current = str(profile.get("historyId") or "")
                if not account or not current:
                    raise gmail.GmailError("Gmail sync lacks account history")
                token = self._token()
                if account.casefold() != str(token.get("account") or "").casefold():
                    raise gmail.GmailError("Gmail account changed; reconnect")
                old = str(state.get("history_id") or "")
                added: list[str] = []
                resynced = False
                if old:
                    page = ""
                    for _ in range(5):
                        if time.monotonic() >= deadline:
                            old = ""
                            added.clear()
                            resynced = True
                            break
                        params = {"startHistoryId": old, "historyTypes": ["messageAdded"], "maxResults": 100}
                        if page:
                            params["pageToken"] = page
                        try:
                            history = self._request("GET", "/history", params=params, timeout=8)
                        except gmail.GmailError as exc:
                            if exc.status == 404:
                                old = ""
                                added.clear()
                                resynced = True
                                break
                            raise
                        for row in history.get("history") or []:
                            for item in row.get("messagesAdded") or []:
                                ident = str((item.get("message") or {}).get("id") or "")
                                if _ID.fullmatch(ident):
                                    added.append(ident)
                        page = str(history.get("nextPageToken") or "")
                        if not page:
                            break
                    if page or len(set(added)) > 20:
                        old = ""
                        added.clear()
                        resynced = True
                unread = self._request("GET", "/messages", params={"labelIds": ["INBOX", "UNREAD"], "maxResults": 1}, timeout=8)
                seen = list(state.get("seen") or [])[-100:]
                queue = list(state.get("notices") or [])[-20:]
                if old:
                    for ident in added[:20]:
                        if time.monotonic() >= deadline:
                            resynced = True
                            queue = list(state.get("notices") or [])[-20:]
                            break
                        if ident in seen:
                            continue
                        try:
                            item = self._request("GET", f"/messages/{ident}", params={"format": "minimal"}, timeout=8)
                        except gmail.GmailError as exc:
                            if exc.status != 404:
                                raise
                            # History can outlive a deleted message. Its absence
                            # does not make the fresh inbox count unavailable.
                            seen.append(ident)
                            continue
                        if {"INBOX", "UNREAD"}.issubset(set(item.get("labelIds") or [])):
                            queue.append(ident)
                        seen.append(ident)
                state.update({
                    "history_id": current,
                    "unread": max(0, int(unread.get("resultSizeEstimate") or 0)),
                    "last_sync": time.time(),
                    "sync_known": True,
                    "auth_error": False,
                    "retry_after": 0,
                    "retry_count": 0,
                    "seen": seen[-100:],
                    "notices": queue[-20:] if state.get("notifications_enabled", True) is not False else [],
                })
                _write_json(self.state_path, state)
                return {"state": "connected", "unread": state["unread"], "new": len(queue), "resynced": resynced}
            except (gmail.GmailError, secure_store.SecureStoreUnavailable) as exc:
                state["sync_known"] = False
                if isinstance(exc, gmail.GmailError):
                    state["auth_error"] = exc.status in {401, 403}
                    if exc.status in {429, 500, 502, 503, 504}:
                        failures = min(6, int(state.get("retry_count") or 0) + 1)
                        state["retry_count"] = failures
                        state["retry_after"] = time.time() + min(900, 30 * (2 ** (failures - 1)))
                _write_json(self.state_path, state)
                raise

    def claim_notices(self) -> int:
        with file_byte_lock(self.lock_path, _STATE_LOCK):
            state = _read_json(self.state_path)
            if state.get("notifications_enabled", True) is False:
                return 0
            queued = list(state.get("notices") or [])
            if queued:
                state["notices"] = []
                _write_json(self.state_path, state)
            return len(queued)


def dashboard_glance(provider: str, *, config: dict | None = None, agent: Any = None,
                     limit: int = 3, query: str = "") -> dict:
    """Read bounded recent mail for local Desktop displays, without model or persistence."""
    from ..life.candidates import visible_mail_signals

    if provider not in {"gmail", "outlook"}:
        raise ValueError("Unknown mail provider")
    limit = min(20, max(1, int(limit)))
    if provider == "gmail":
        service = MailService(config)
        listing = service.list_messages(limit=limit, query=query)
        try:
            from ..life.items import list_items

            tracked = {item["source_id"] for item in list_items(config=config)
                       if item.get("source_provider") == "gmail" and item.get("source_id")}
        except (OSError, ValueError):
            tracked = None
        rows = [{
            "id": str(item.get("id") or ""),
            "title": " ".join(str(item.get("subject") or "").split())[:140] or "(no subject)",
            "detail": " ".join(str(item.get("from") or "").split())[:100] or "Unknown sender",
            "excerpt": " ".join(str(item.get("snippet") or "").split())[:160],
            "provider_important": "IMPORTANT" in (item.get("labels") or []),
            "provider_unread": "UNREAD" in (item.get("labels") or []),
            "provider_spam": "SPAM" in (item.get("labels") or []),
            "tracked": str(item.get("id") or "") in tracked if tracked is not None else None,
        } for item in listing["messages"]]
        for row in rows:
            row.update(visible_mail_signals(f"{row['title']} {row['excerpt']}"))
        return {"provider": provider, "messages": rows,
                "inbox_estimate": listing["estimate"], "unread": service.status().get("unread"),
                "notifications_enabled": service.notifications_enabled()}

    from . import outlook_browser

    listing = outlook_browser.execute("search" if query else "list", {"limit": limit, "query": query}, agent=agent)
    rows = []
    for item in listing.get("messages") or []:
        lines = [" ".join(line.split()) for line in str(item.get("preview") or "").splitlines() if line.strip()]
        row = {"id": str(item.get("id") or ""),
                     "identity": hashlib.sha256(str(item.get("preview") or "").encode()).hexdigest(),
                     "title": (lines[0] if lines else "Outlook message")[:140],
                     "detail": (lines[1] if len(lines) > 1 else "")[:100],
                     "excerpt": " ".join(lines[2:])[:160]}
        row.update(visible_mail_signals(str(item.get("preview") or "")))
        rows.append(row)
    return {"provider": provider, "messages": rows}


def execute(arguments: dict, *, agent: Any = None, config: dict | None = None) -> str:
    """Action-aware adapter; returns bounded, user-safe errors without secrets."""
    action = str(arguments.get("action") or "status").strip().lower()
    provider = str(arguments.get("provider") or "gmail").strip().lower()
    if action == "review":
        from core.browser_bridge import BrowserBridgeError

        limit = arguments.get("limit") or 10
        query = str(arguments.get("query") or "")[:300]
        if provider == "both":
            accounts = []
            for account in ("gmail", "outlook"):
                try:
                    accounts.append(dashboard_glance(account, config=config, agent=agent,
                                                     limit=limit, query=query))
                except (gmail.GmailError, secure_store.SecureStoreUnavailable,
                        BrowserBridgeError, OSError, TypeError, ValueError) as exc:
                    accounts.append({"provider": account, "error": str(exc)[:240]})
            return json.dumps({"provider": "both", "accounts": accounts}, ensure_ascii=False)
        try:
            return json.dumps(dashboard_glance(provider, config=config, agent=agent,
                limit=limit, query=query), ensure_ascii=False)
        except (gmail.GmailError, secure_store.SecureStoreUnavailable,
                BrowserBridgeError, TypeError, ValueError) as exc:
            return f"Error: {exc}"
    if provider == "both":
        return "Error: Both accounts are available only for read-only review"
    if provider == "outlook" and action in {"status", "connect"}:
        from core import browser_bridge
        from . import outlook_browser

        try:
            if action == "connect":
                browser_bridge.ensure_installed()
            bridge = browser_bridge.status()
            if not bridge.get("installed") or not bridge.get("extension_connected"):
                return json.dumps({"state": "extension_required",
                    "bridge_ready": bridge.get("installed") is True,
                    "extension_dir": str(bridge.get("extension_dir") or "")}, ensure_ascii=False)
            try:
                result = outlook_browser.execute("status", {}, agent=agent)
            except (ValueError, browser_bridge.BrowserBridgeError) as exc:
                result = {"state": "browser_attention", "detail": str(exc)[:240]}
            return json.dumps(result, ensure_ascii=False)
        except (browser_bridge.BrowserBridgeError, OSError, ValueError) as exc:
            return f"Error: {exc}"
    if provider == "outlook":
        from core.browser_bridge import BrowserBridgeError
        from . import outlook_browser

        try:
            result = outlook_browser.execute(action, arguments, agent=agent)
            if action in {"send", "trash"} and result.get("state") == "ready_to_approve":
                session = getattr(agent, "session", None)
                owner = str(getattr(session, "session_id", "") or "")
                turn = int(getattr(session, "turn_count", 0) or 0)
                if not owner or not turn:
                    raise ValueError(f"Outlook {action} needs a live user conversation")
                fingerprint = str(result["fingerprint"])
                pending_action = "outlook_send" if action == "send" else "outlook_trash"
                expected = hashlib.sha256(f"{owner}:{pending_action}:{fingerprint}".encode()).hexdigest()
                code = expected[:10]
                with _PENDING_LOCK:
                    _PENDING[f"{owner}:{pending_action}"] = (expected, turn, time.time() + 180, fingerprint)
                if action == "send":
                    review = (f"Send the open Outlook draft to {result['to']} "
                              f"with subject {result['subject']!r}? Review the full draft in Outlook")
                else:
                    review = (f"Delete the open Outlook message with subject {result['subject']!r}? "
                              "Review the message in Outlook")
                detail = (f"[APPROVAL REQUIRED id={code}] {review}, then reply 'approve {code}' "
                          "in a new turn. Approval expires in 3 minutes and is single-use.")
                agent._mail_approval_notice = detail
                result = {"state": "approval_required", "detail": detail}
            return json.dumps(result, ensure_ascii=False)
        except (ValueError, TypeError, BrowserBridgeError) as exc:
            return f"Error: {exc}"
    service = MailService(config)
    try:
        if action == "status":
            result = service.status()
        elif action == "connect":
            if service.status().get("state") == "disabled":
                from ..state.preferences import RuntimePreferenceError, persist_mail_enabled

                try:
                    persist_mail_enabled(service.config, True)
                except RuntimePreferenceError as exc:
                    return f"Error: {exc}"
                service = MailService(service.config)
            state = service.status().get("state")
            if state in {"disconnected", "reconnect_required"}:
                service.connect()
                service.sync()
            result = service.status()
        elif action == "sync":
            result = service.sync()
        elif action in {"list", "search"}:
            result = service.list_messages(query=str(arguments.get("query") or "") if action == "search" else "", limit=arguments.get("limit") or 10)
        elif action == "read_latest":
            listing = service.list_messages(query=str(arguments.get("query") or ""), limit=1)
            rows = list(listing.get("messages") or [])
            result = service.read_message(str(rows[0].get("id") or "")) if rows else {"state": "not_found"}
        elif action == "read":
            result = service.read_message(str(arguments.get("id") or ""))
        elif action == "draft":
            result = service.create_draft(to=str(arguments.get("to") or ""), subject=str(arguments.get("subject") or ""), body=str(arguments.get("body") or ""))
        elif action == "send":
            result = service.send_draft(str(arguments.get("id") or ""), agent=agent)
        elif action == "move":
            result = service.move(str(arguments.get("id") or ""), str(arguments.get("folder") or ""))
        elif action in {"archive", "mark_read", "label", "trash"}:
            result = service.modify(str(arguments.get("id") or ""), action, label=str(arguments.get("label") or ""), agent=agent)
        else:
            raise ValueError("unsupported mail action")
        return json.dumps(result, ensure_ascii=False)
    except (gmail.GmailError, secure_store.SecureStoreUnavailable, ValueError, OSError) as exc:
        return f"Error: {exc}"


def operator_result(action: str, value: dict, *, provider: str = "gmail") -> str:
    """Render connection and change receipts for the live operator."""
    action = str(action or "").strip().lower()
    def field(item: Any, fallback: str = "") -> str:
        # Untrusted headers stay on their intended transcript line.
        clean = re.sub(r"[\r\n\t\x00-\x1f]+", " ", str(item or "")).strip()
        return clean or fallback

    if provider == "outlook":
        if value.get("state") == "approval_required":
            return str(value.get("detail") or "Outlook send requires approval.")
        if action in {"status", "connect"}:
            state = value.get("state")
            if state == "connected":
                return "Outlook is connected through the signed-in MO Connected Tab."
            if state == "extension_required":
                if not value.get("bridge_ready"):
                    return "Ask MO to connect Outlook to prepare the local browser bridge."
                folder = field(value.get("extension_dir"))
                return ("MO prepared its local browser bridge. In Chrome, open chrome://extensions, "
                        "enable Developer mode, choose Load unpacked, and select this MO Connected Tab folder: "
                        f"{folder}. Then sign in at https://outlook.live.com/mail/ and ask MO to connect Outlook again.")
            if state == "browser_attention":
                return ("MO Connected Tab is available. Open the Outlook Mail tab and sign in if needed, "
                        "then ask MO to connect Outlook again. " + field(value.get("detail")))
            return "Outlook connection status is unavailable; check Dashboard → Email."
        if action == "draft":
            return f"Outlook draft open in Chrome.\n**To:** {field(value.get('to'))}\n**Subject:** {field(value.get('subject'))}"
        if action == "archive":
            return f"Outlook Archive was clicked for **{field(value.get('subject'))}**."
        if action == "mark_read":
            return "Outlook message was already read." if value.get("already_read") else "Outlook Mark as read was clicked."
        if action == "move":
            return (f"Outlook Move was clicked for **{field(value.get('subject'))}** "
                    f"to **{field(value.get('folder'))}**.")
        return "Outlook action completed."

    if value.get("state") == "approval_required":
        return str(value.get("detail") or "Gmail action requires approval.")
    if action in {"status", "connect"}:
        state = str(value.get("state") or "unknown")
        if state == "connected":
            unread = value.get("unread")
            return "Gmail is connected" + (f" · {int(unread)} unread" if unread is not None else "") + "."
        if state == "sync_unknown":
            return "Gmail authorization succeeded. Its first inbox sync is pending; use Dashboard → Email or /mail sync."
        if state == "client_missing":
            return ("Gmail is enabled. In your Google Cloud project, enable Gmail API, configure "
                    "the OAuth consent screen, and create a Desktop app OAuth client for gmail.modify. "
                    "Open Dashboard → Email → Gmail and save its client ID and secret there, not in chat. "
                    "Then choose Continue with Google to approve access in your browser.")
        if state == "secure_storage_unavailable":
            return "Gmail needs a supported encrypted local store on this device before it can connect."
        return "Gmail: " + field(value.get("next_action"), state)
    if action == "draft":
        return "\n".join((
            "Gmail draft saved.",
            f"Draft ID: {value.get('draft_id') or ''}",
            f"To: {value.get('to') or ''}",
            f"Subject: {value.get('subject') or '(no subject)'}",
        ))
    return "Gmail: " + json.dumps(value, ensure_ascii=False)
