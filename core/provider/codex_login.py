"""Sign MO in to Codex (ChatGPT) with a device code.

The operator opens one link on any device and enters a short code; MO receives a session of its
own. A machine signed in this way is never broken by another machine's token refresh (a copied
``auth.json`` shares one refresh chain, so whichever side refreshes first kills the other's copy).
Same protocol as ``codex login --device-auth``.

    python -m core.provider.codex_login start  [--auth-path PATH]   # prints the link and code
    python -m core.provider.codex_login finish [--auth-path PATH]   # waits until approved, saves
"""
from __future__ import annotations

import argparse
import base64
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..state.paths import codex_auth_path
from ..utils.atomic_write import atomic_write_json

ISSUER = "https://auth.openai.com"
VERIFICATION_URL = f"{ISSUER}/codex/device"
LOGIN_SECONDS = 15 * 60


@dataclass(frozen=True)
class DeviceLogin:
    device_auth_id: str
    user_code: str
    interval: float
    expires_at: float
    verification_url: str = VERIFICATION_URL


def _client_id() -> str:
    from .provider import CodexOAuthProvider

    return CodexOAuthProvider.oauth_client_id


def _http() -> Any:
    from .provider import _httpx

    return _httpx()


def start_device_login(*, timeout: float = 20.0) -> DeviceLogin:
    """Ask for a user code; the operator enters it at ``VERIFICATION_URL``."""
    response = _http().post(f"{ISSUER}/api/accounts/deviceauth/usercode", json={"client_id": _client_id()},
                            timeout=timeout, follow_redirects=True)
    if response.status_code == 404:
        raise RuntimeError("Device sign-in is not enabled for this account.")
    if response.status_code >= 400:
        raise RuntimeError(f"Device sign-in could not start (HTTP {response.status_code}).")
    body = response.json()
    code = str(body.get("user_code") or body.get("usercode") or "").strip()
    device = str(body.get("device_auth_id") or "").strip()
    if not code or not device:
        raise RuntimeError("Device sign-in returned no code.")
    try:
        interval = max(1.0, float(str(body.get("interval") or "5").strip()))
    except ValueError:
        interval = 5.0
    return DeviceLogin(device, code, interval, time.time() + LOGIN_SECONDS)


def finish_device_login(login: DeviceLogin, *, auth_path: str | Path | None = None, cancel_event: Any = None,
                        sleep: Any = time.sleep, timeout: float = 20.0) -> str:
    """Wait until the operator approves the code, then save MO's own session. "" when signed in,
    otherwise why not (expired, cancelled, refused)."""
    http = _http()
    while time.time() < login.expires_at:
        if cancel_event is not None and cancel_event.is_set():
            return "Sign-in cancelled."
        response = http.post(f"{ISSUER}/api/accounts/deviceauth/token",
                             json={"device_auth_id": login.device_auth_id, "user_code": login.user_code},
                             timeout=timeout, follow_redirects=True)
        if response.status_code in (403, 404):                # not approved yet
            sleep(min(login.interval, max(0.0, login.expires_at - time.time())))
            continue
        if response.status_code >= 400:
            return f"Sign-in was refused (HTTP {response.status_code})."
        grant = response.json()
        tokens = http.post(f"{ISSUER}/oauth/token", data={
            "grant_type": "authorization_code",
            "code": str(grant.get("authorization_code") or ""),
            "redirect_uri": f"{ISSUER}/deviceauth/callback",
            "client_id": _client_id(),
            "code_verifier": str(grant.get("code_verifier") or ""),
        }, timeout=timeout, follow_redirects=True)
        if tokens.status_code >= 400:
            return f"Sign-in could not be completed (HTTP {tokens.status_code})."
        save_session(tokens.json(), auth_path=auth_path)
        return ""
    return "The sign-in code expired before it was used; start again."


def save_session(tokens: dict[str, Any], *, auth_path: str | Path | None = None) -> Path:
    """Write the session in the Codex ``auth.json`` shape MO's provider reads."""
    for key in ("id_token", "access_token", "refresh_token"):
        if not str(tokens.get(key) or "").strip():
            raise RuntimeError(f"Sign-in returned no {key}.")
    path = Path(auth_path or codex_auth_path(None)).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    session = {
        "OPENAI_API_KEY": None,
        "tokens": {"id_token": tokens["id_token"], "access_token": tokens["access_token"],
                   "refresh_token": tokens["refresh_token"], "account_id": _account_id(tokens["id_token"])},
        "last_refresh": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    atomic_write_json(path, session, indent=2)
    return path


def _account_id(id_token: str) -> str:
    try:
        part = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return str((claims.get("https://api.openai.com/auth") or {}).get("chatgpt_account_id") or "")
    except Exception:
        return ""


def _state_path(auth_path: Path) -> Path:
    return auth_path.with_name(auth_path.name + ".device-login.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sign MO in to Codex with a device code.")
    parser.add_argument("step", choices=("start", "finish"))
    parser.add_argument("--auth-path", default="", help="Where to save the session (default: the Codex auth file).")
    args = parser.parse_args(argv)
    auth_path = Path(args.auth_path or codex_auth_path(None)).expanduser()
    state = _state_path(auth_path)
    if args.step == "start":
        login = start_device_login()
        state.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(state, asdict(login), indent=2)
        print(f"Open {login.verification_url} and enter the code {login.user_code} (valid 15 minutes).")
        return 0
    try:
        login = DeviceLogin(**json.loads(state.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        print("No sign-in in progress; run the start step first.")
        return 2
    problem = finish_device_login(login, auth_path=auth_path)
    state.unlink(missing_ok=True)
    print(problem or f"Signed in. Session saved to {auth_path}.")
    return 1 if problem else 0


if __name__ == "__main__":
    raise SystemExit(main())
