"""Text hygiene helpers for provider/session/console boundaries."""

import re
import sys
from typing import Any
import traceback

# ── Canonical secret pattern fragments ─────────────────────────────────
# Single-sourced here (lowest-level, stdlib-only module) so the answer-critic
# (core/review/critic.py), the tool/web/audit redactor (core/tooling/sandbox.py), and this
# detector stay in coverage lockstep. SEC-1 was caused by these diverging.
# Used inside IGNORECASE patterns; written lowercase.
SECRET_NAME_PATTERN = (
    r"api[_-]?key|x-api-key|access[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|auth[_-]?token|token|secret[_-]?key|client[_-]?secret|secret|"
    r"password|passwd|private[_-]?key|session[_-]?cookie"
)
PROVIDER_TOKEN_PATTERN = (
    r"gh[pousr]_[a-z0-9]{16,}"                       # GitHub tokens
    r"|xox[baprs]-[a-z0-9-]{10,}"                    # Slack tokens
    r"|akia[0-9a-z]{12,}"                            # AWS access key id
    r"|aiza[0-9a-z_\-]{20,}"                         # Google API key
    r"|(?:sk|pk|rk)_(?:live|test)_[a-z0-9]{10,}"     # Stripe keys
)

SECRET_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"bearer\s+[a-z0-9._\-+/=]{8,}"
    # name = value / "name": value — the optional quote before the separator
    # covers JSON/dict secret keys like {"api_key": "..."}.
    r"|(?:" + SECRET_NAME_PATTERN + r")[\"']?\s*[:=]\s*[^\s,;]{3,}"
    r"|sk-[a-z0-9_\-]{16,}"                          # OpenAI-style keys
    r"|" + PROVIDER_TOKEN_PATTERN +                  # GitHub/Slack/AWS/Google/Stripe
    r"|-----begin[a-z0-9 ]*private\s+key-----"       # PEM private key block
    r")"
)
_UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def contains_secret_value(value: Any) -> bool:
    """Return True when text looks like an inline secret-bearing value.

    NOTE: deliberately over-broad (the ``name=value`` arm matches assignments to
    *any* RHS) because its job is REDACTION of output, where over-matching is
    harmless. Do NOT use it to block a file write — it fires on ordinary code like
    ``api_key = os.environ["KEY"]``. Use ``contains_hardcoded_secret_literal``.
    """
    return bool(SECRET_VALUE_RE.search(str(value or "")))


# ── Precise hardcoded-secret-LITERAL detector (for blocking writes) ─────
# Distinct from SECRET_VALUE_RE: this must be precise enough to BLOCK a file
# write without firing on legitimate code, so it matches only unambiguous
# key-shaped literals plus high-entropy *quoted* assignments, and exempts
# placeholders / env-refs / expression RHS.
_PLACEHOLDER_LITERALS = frozenset({
    "...", "xxx", "xxxx", "xxxxx", "xxxxxxxx", "changeme", "change_me",
    "replace_me", "[redacted]", "<redacted>", "redacted", "none", "null",
})
_PLACEHOLDER_SECRET_WORDS = ("key", "token", "password", "secret", "credential")
_PLACEHOLDER_HINT_WORDS = ("your", "example", "sample", "dummy", "fake", "test", "placeholder", "replace", "todo")


def is_placeholder_secret_value(value: Any) -> bool:
    """True when a secret-shaped value is an obvious placeholder, not a real secret.

    Single-sourced here so the write-time guard and the answer-critic stay aligned.
    """
    clean = str(value or "").strip().strip("`'\"")
    lower = clean.lower()
    if not clean or lower in _PLACEHOLDER_LITERALS:
        return True
    if lower.startswith("<") and lower.endswith(">"):
        return True
    # Env / config / expression references are not literal secrets.
    if lower.startswith(("${", "os.environ", "process.env", "env.", "getenv", "config.", "settings.")):
        return True
    if any(w in lower for w in _PLACEHOLDER_SECRET_WORDS) and any(w in lower for w in _PLACEHOLDER_HINT_WORDS):
        return True
    if lower.endswith("_here") and any(w in lower for w in _PLACEHOLDER_SECRET_WORDS):
        return True
    return False


# Unambiguous standalone key-shaped literals — no assignment context needed.
_HARDCODED_LITERAL_RE = re.compile(
    r"(?i)(?:sk-[a-z0-9_\-]{16,}|" + PROVIDER_TOKEN_PATTERN + r"|-----begin[a-z0-9 ]*private\s+key-----)"
)
# A high-entropy quoted value assigned to a secret-named key:
#   API_KEY = "AKIA…", "token": "eyJhbGci…"  (quotes required → excludes os.environ refs)
_HARDCODED_ASSIGN_RE = re.compile(
    r"(?i)\b(?:" + SECRET_NAME_PATTERN + r")\b[\"']?\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']"
)


def _looks_high_entropy(value: str) -> bool:
    v = str(value or "")
    if len(v) < 16 or " " in v:
        return False
    has_alpha = any(c.isalpha() for c in v)
    has_digit = any(c.isdigit() for c in v)
    return has_alpha and has_digit


def contains_hardcoded_secret_literal(content: Any) -> bool:
    """Precise detector for BLOCKING file writes: unambiguous secret literals only.

    Matches provider key shapes (GitHub/AWS/Slack/Google/Stripe/OpenAI-style), PEM
    private keys, and high-entropy quoted values assigned to a secret-named key.
    Exempts placeholders and env/expression references so legitimate code such as
    ``token = os.environ["X"]`` or ``password = input()`` is never blocked.
    """
    text = str(content or "")
    if not text:
        return False
    if _HARDCODED_LITERAL_RE.search(text):
        return True
    for match in _HARDCODED_ASSIGN_RE.finditer(text):
        value = match.group(1)
        if is_placeholder_secret_value(value):
            continue
        if _looks_high_entropy(value):
            return True
    return False


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
