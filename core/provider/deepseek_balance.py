"""DeepSeek official-API account balance — for the TUI footer.

Only the official DeepSeek endpoint (``api.deepseek.com``) exposes ``/user/balance``
(OpenCode and other OpenAI-compatible relays do not). The balance is fetched in a
throttled background thread and cached, so the footer render NEVER blocks on the
network. Best-effort: any error → no balance shown. The key is read from the live
provider's client and is never logged.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Any
from urllib.parse import urlsplit

_DEEPSEEK_HOST = "api.deepseek.com"
_TTL_SECONDS = 120.0  # refresh at most once every 2 minutes
_TIMEOUT_SECONDS = 8.0
_CURRENCY_SYMBOL = {"USD": "$", "CNY": "¥"}

_lock = threading.Lock()
_state: dict[str, Any] = {"text": None, "fetched_at": 0.0, "fetching": False}


def is_official_deepseek(provider: Any) -> bool:
    """True only for the official DeepSeek API host (not OpenCode/relays)."""
    base = str(getattr(provider, "base_url", "") or "").lower()
    return _DEEPSEEK_HOST in urlsplit(base).netloc


def balance_endpoint(base_url: str) -> str:
    """``/user/balance`` lives at the host ROOT, not under ``/v1``."""
    parts = urlsplit(str(base_url or ""))
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}/user/balance"
    return f"https://{_DEEPSEEK_HOST}/user/balance"


def _provider_api_key(provider: Any) -> str:
    client = getattr(provider, "client", None)
    return str(getattr(client, "api_key", "") or "")


def format_balance(payload: dict[str, Any]) -> str | None:
    """Render the /user/balance JSON into a compact footer string, or None."""
    infos = payload.get("balance_infos") or []
    if not isinstance(infos, list) or not infos:
        return None
    info = infos[0] if isinstance(infos[0], dict) else {}
    currency = str(info.get("currency") or "").strip()
    total = str(info.get("total_balance") or "").strip()
    if not total:
        return None
    symbol = _CURRENCY_SYMBOL.get(currency, "")
    amount = f"{symbol}{total}" if symbol else f"{total} {currency}".strip()
    low = "" if payload.get("is_available", True) else " ⚠low"
    return f"DeepSeek {amount}{low}"


def _fetch(url: str, api_key: str) -> str | None:
    try:
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return format_balance(payload)


def _refresh(url: str, api_key: str) -> None:
    text = _fetch(url, api_key) if api_key else None
    with _lock:
        _state["text"] = text
        _state["fetched_at"] = time.time()
        _state["fetching"] = False


def balance_text(provider: Any) -> str | None:
    """Cached balance string for the official DeepSeek provider, or None.

    Triggers a throttled background refresh when stale; never blocks the caller, so
    it is safe to call from the footer render path on every frame.
    """
    if not is_official_deepseek(provider):
        return None
    api_key = _provider_api_key(provider)
    if not api_key:
        return None
    now = time.time()
    with _lock:
        stale = (now - float(_state["fetched_at"] or 0.0)) > _TTL_SECONDS
        if stale and not _state["fetching"]:
            _state["fetching"] = True
            url = balance_endpoint(getattr(provider, "base_url", ""))
            threading.Thread(target=_refresh, args=(url, api_key), daemon=True).start()
        return _state["text"]
