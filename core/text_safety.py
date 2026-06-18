"""Text hygiene helpers for provider/session/console boundaries."""

import re
import sys
from typing import Any
import traceback

SECRET_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"bearer\s+[a-z0-9._\-+/=]{8,}"
    # name = value / "name": value — the optional quote before the separator
    # covers JSON/dict secret keys like {"api_key": "..."}.
    r"|(?:api[_-]?key|access[_-]?key|access[_-]?token|refresh[_-]?token|token|"
    r"secret[_-]?key|client[_-]?secret|secret|password|passwd|private[_-]?key|"
    r"session[_-]?cookie)[\"']?\s*[:=]\s*[^\s,;]{3,}"
    # High-confidence standalone tokens (parity with the answer-critic).
    r"|sk-[a-z0-9_\-]{16,}"                          # OpenAI-style keys
    r"|gh[pousr]_[a-z0-9]{20,}"                      # GitHub tokens
    r"|xox[baprs]-[a-z0-9-]{10,}"                    # Slack tokens
    r"|akia[0-9a-z]{16}"                             # AWS access key id
    r"|aiza[0-9a-z_\-]{20,}"                         # Google API key
    r"|(?:sk|pk|rk)_(?:live|test)_[a-z0-9]{10,}"     # Stripe keys
    r"|-----begin[a-z0-9 ]*private\s+key-----"       # PEM private key block
    r")"
)
_UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def contains_secret_value(value: Any) -> bool:
    """Return True when text looks like an inline secret-bearing value."""
    return bool(SECRET_VALUE_RE.search(str(value or "")))


def configure_utf8_stdio() -> None:
    """Best-effort UTF-8 stdio for Windows/direct helper scripts.

    Console encodings such as cp1252 cannot print MO's Unicode status glyphs.
    Entrypoints and small diagnostic helpers can call this once at startup; it is
    intentionally safe/no-op for streams without ``reconfigure``.
    """
    for stream_name in ("stdout", "stderr", "stdin"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            traceback.print_exc()


def sanitize_unicode_text(value: Any) -> str:
    """Return text safe for UTF-8/log/provider boundaries.

    Folds surrogate pairs, replaces unpaired surrogates, strips BOM/null/unsafe
    C0 controls, and preserves tab/newline/carriage return for readable text.
    """
    text = "" if value is None else str(value)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return sanitize_control_text(text)


def sanitize_control_text(value: Any) -> str:
    """Strip control bytes that are unsafe in logs/provider/session text."""
    text = "" if value is None else str(value)
    text = text.replace("\ufeff", "")
    return _UNSAFE_CONTROL_RE.sub("", text)


def sanitize_jsonish(value: Any) -> Any:
    """Recursively sanitize JSON-like provider/session payloads."""
    if isinstance(value, str):
        return sanitize_unicode_text(value)
    if value is None:
        return None
    if isinstance(value, dict):
        return {sanitize_unicode_text(k): sanitize_jsonish(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_jsonish(v) for v in value]
    if isinstance(value, tuple):
        return [sanitize_jsonish(v) for v in value]
    return value
