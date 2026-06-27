"""Owner-only protocol activation gates.

Owner protocol files and the owner token live under the per-user MO profile. This
module exposes only the product-side detection seam; it does not ship protocol content.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from .path_defaults import mo_home, operator_pack_root

OWNER_MAINTENANCE = "maintenance"
OWNER_COMPARISON = "comparison"
OWNER_INTERFACE_AUDIT = "interface_audit"
OWNER_INTEGRITY_AUDIT = "integrity_audit"
OWNER_DEDUP = "deduplication"

_PROTOCOL_KEYS = (
    OWNER_MAINTENANCE,
    OWNER_COMPARISON,
    OWNER_INTERFACE_AUDIT,
    OWNER_INTEGRITY_AUDIT,
    OWNER_DEDUP,
)

_DEFAULT_ALIASES = {
    OWNER_MAINTENANCE: ("owner maintenance", "self maintenance", "maintenance audit"),
    OWNER_COMPARISON: ("owner comparison", "reference comparison", "comparison audit"),
    OWNER_INTERFACE_AUDIT: ("owner interface audit", "interface audit"),
    OWNER_INTEGRITY_AUDIT: ("owner integrity audit", "integrity audit", "expert audit"),
    OWNER_DEDUP: ("owner deduplication", "deduplication audit", "consolidation audit"),
}

_DEFAULT_LABELS = {
    OWNER_MAINTENANCE: "owner maintenance",
    OWNER_COMPARISON: "owner comparison",
    OWNER_INTERFACE_AUDIT: "owner interface audit",
    OWNER_INTEGRITY_AUDIT: "owner integrity audit",
    OWNER_DEDUP: "owner deduplication",
}


def _protocol_config() -> dict:
    """Load private owner protocol aliases/labels from the profile pack.

    The public product carries only generic protocol slots. Private aliases, legacy
    trigger words, and exact operator-facing labels belong in the owner profile under
    ``operator/devmode/protocols.json`` or ``operator/protocols.json``.
    """
    for path in (
        operator_pack_root() / "devmode" / "protocols.json",
        operator_pack_root() / "protocols.json",
    ):
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                protocols = data.get("protocols", data)
                return protocols if isinstance(protocols, dict) else {}
        except Exception:
            continue
    return {}


def _protocol_entry(key: str) -> dict:
    entry = _protocol_config().get(key, {})
    return entry if isinstance(entry, dict) else {}


def _protocol_aliases(key: str) -> tuple[str, ...]:
    aliases = list(_DEFAULT_ALIASES.get(key, ()))
    extra = _protocol_entry(key).get("aliases", ())
    if isinstance(extra, str):
        extra = (extra,)
    aliases.extend(str(item) for item in extra if str(item).strip())
    return tuple(dict.fromkeys(alias.strip() for alias in aliases if alias.strip()))


def _normalize_trigger(text: str) -> str:
    return re.sub(r"[\s_-]+", " ", str(text or "").strip().lower()).lstrip("/")


def _alias_matches(text: str, alias: str) -> bool:
    normalized = _normalize_trigger(text)
    phrase = _normalize_trigger(alias)
    if not normalized or not phrase:
        return False
    candidates = (phrase, f"start {phrase}", f"run {phrase}")
    if any(normalized == item or normalized.startswith(item + " ") for item in candidates):
        return True
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", normalized) is not None


def _is_alias_token(text: str, alias: str) -> bool:
    """True only when one token is itself a protocol alias.

    Path extraction uses this narrower check so a temp path such as
    ``test_owner_comparison_readonly`` is not mistaken for the command phrase.
    """
    normalized = _normalize_trigger(text)
    phrase = _normalize_trigger(alias)
    return bool(normalized and phrase and normalized == phrase)


def _pack_present() -> bool:
    """True when the untracked owner protocol files are on disk."""
    try:
        devmode = operator_pack_root() / "devmode"
        return bool(_protocol_config()) or any(devmode.glob("*.md"))
    except Exception:
        return False


def _owner_token_present() -> bool:
    """True when the operator's private owner token exists in the runtime home.

    The token (``~/.mo/operator.token``) lives only in the operator's private
    runtime home — never in any repo, never shipped. Copying the public repo, or
    even the protocol pack files, does not grant it; a fresh user clone's ``~/.mo``
    has no such token. This is what makes operator mode owner-bound rather than
    unlocked by mere file presence.
    """
    try:
        token = mo_home() / "operator.token"
        return token.is_file() and bool(token.read_text(encoding="utf-8").strip())
    except Exception:
        return False


def operator_protocols_installed() -> bool:
    """True only for the real operator: owner protocol files AND the owner token.

    Owner protocol aliases are personal operator profile data, not product features. They
    require BOTH the untracked ``~/.mo/operator/`` files AND a private owner
    token in ``~/.mo`` (``operator.token``) that a user clone never has — so the
    copyable protocol files alone cannot fake operator mode. On a user clone both are
    absent, so the activation terms are inert by absence — no config, nothing to
    leak.
    """
    return _pack_present() and _owner_token_present()


def is_owner_maintenance_activation(user_input: str) -> bool:
    """Return True when the owner maintenance protocol is active."""
    return _is_protocol_activation(user_input, OWNER_MAINTENANCE)


def is_owner_comparison_activation(user_input: str) -> bool:
    """Return True when the owner comparison protocol is active."""
    return _is_protocol_activation(user_input, OWNER_COMPARISON)


def is_owner_interface_audit_activation(user_input: str) -> bool:
    """Return True when the owner interface-audit protocol is active."""
    return _is_protocol_activation(user_input, OWNER_INTERFACE_AUDIT)


def is_owner_integrity_audit_activation(user_input: str) -> bool:
    """Return True when the owner integrity-audit protocol is active."""
    return _is_protocol_activation(user_input, OWNER_INTEGRITY_AUDIT)


def is_owner_dedup_activation(user_input: str) -> bool:
    """Return True when the owner deduplication/consolidation protocol is active."""
    return _is_protocol_activation(user_input, OWNER_DEDUP)


def _is_protocol_activation(user_input: str, key: str) -> bool:
    if not operator_protocols_installed():
        return False
    return any(_alias_matches(user_input, alias) for alias in _protocol_aliases(key))


def is_owner_protocol_activation(user_input: str) -> bool:
    """True when any owner-only protocol is active."""
    return (is_owner_maintenance_activation(user_input) or is_owner_comparison_activation(user_input)
            or is_owner_interface_audit_activation(user_input) or is_owner_integrity_audit_activation(user_input)
            or is_owner_dedup_activation(user_input))


def owner_protocol_name(user_input: str) -> str:
    """Return the active generic owner-protocol key, or an empty string."""
    for key in _PROTOCOL_KEYS:
        if _is_protocol_activation(user_input, key):
            return key
    return ""


def owner_protocol_label(key: str) -> str:
    value = _protocol_entry(key).get("label")
    return str(value).strip() if value else _DEFAULT_LABELS.get(key, key.replace("_", " "))


def owner_comparison_readonly_source_roots(user_input: str) -> list[str]:
    """Return existing local source roots explicitly supplied to an owner comparison turn.

    Owner comparison compares MO against operator-named references. Those references may
    live outside the active project root, but they are source-intake roots only:
    callers must still keep mutating tools on the normal project sandbox roots.
    """
    if not is_owner_comparison_activation(user_input):
        return []
    tokens = re.findall(r'"([^"]+)"|\'([^\']+)\'|([^\s,;]+)', str(user_input or ""))
    roots: list[str] = []
    seen: set[str] = set()
    for groups in tokens:
        raw = next((value for value in groups if value), "").strip()
        if not raw:
            continue
        lowered = raw.lower().lstrip("/")
        if lowered in {"start", "run"} or any(_is_alias_token(raw, alias) for alias in _protocol_aliases(OWNER_COMPARISON)):
            continue
        windows_abs = bool(re.match(r"^[A-Za-z]:[\\/]", raw)) or raw.startswith("\\\\")
        candidate = Path(raw).expanduser()
        if not (windows_abs or candidate.is_absolute()):
            continue
        try:
            resolved = candidate.resolve(strict=False)
            if resolved.is_file():
                resolved = resolved.parent
            if not resolved.is_dir():
                continue
        except OSError:
            continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        roots.append(str(resolved))
        seen.add(key)
    return roots
