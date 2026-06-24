"""Owner-only protocol activation gates.

Owner protocol files and the owner token live under the per-user MO profile. This
module exposes only the product-side detection seam; it does not ship protocol content.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

from .path_defaults import mo_home, operator_pack_root

def _pack_present() -> bool:
    """True when the untracked owner protocol files are on disk."""
    try:
        devmode = operator_pack_root() / "devmode"
        return (
            (devmode / "DEVMODE05.md").exists()
            or (devmode / "VS05.md").exists()
            or (devmode / "IFDEV05.md").exists()
            or (devmode / "IAM05.md").exists()
        )
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

    DEVMODE05/VS05 are personal operator protocols, not product features. They
    require BOTH the untracked ``~/.mo/operator/devmode/`` files AND a private owner
    token in ``~/.mo`` (``operator.token``) that a user clone never has — so the
    copyable protocol files alone cannot fake operator mode. On a user clone both are
    absent, so the activation terms are inert by absence — no config, nothing to
    leak. ``MO_OPERATOR_PROTOCOLS=1`` forces installed-state for tests.
    """
    if os.environ.get("MO_OPERATOR_PROTOCOLS") == "1":
        return True
    return _pack_present() and _owner_token_present()


def is_devmode05_activation(user_input: str) -> bool:
    """Return True when the operator has activated DEVMODE05."""
    text = " ".join(str(user_input or "").strip().lower().split())
    if not text:
        return False
    if not re.search(r"\b(?:start\s+)?devmode\s*05\b", text):
        return False
    return operator_protocols_installed()


def is_vs05_activation(user_input: str) -> bool:
    """Return True when the operator has activated VS05 comparison mode."""
    text = " ".join(str(user_input or "").strip().lower().split())
    if not text:
        return False
    if not re.search(r"\b(?:start\s+)?vs\s*05\b", text):
        return False
    return operator_protocols_installed()


def is_ifdev05_activation(user_input: str) -> bool:
    """Return True when the operator has activated IFDEV05 interface-diagnosis mode."""
    text = " ".join(str(user_input or "").strip().lower().split())
    if not text:
        return False
    if not re.search(r"\b(?:start\s+)?ifdev\s*05\b", text):
        return False
    return operator_protocols_installed()


def is_iam05_activation(user_input: str) -> bool:
    """Return True when the operator has activated IAM05 expert-honesty audit mode."""
    text = " ".join(str(user_input or "").strip().lower().split())
    if not text:
        return False
    if not re.search(r"\b(?:start\s+)?(?:iam\s*05|expert\s+audit)\b", text):
        return False
    return operator_protocols_installed()


def is_owner_protocol_activation(user_input: str) -> bool:
    """True when ANY owner-only protocol (DEVMODE05/VS05/IFDEV05/IAM05) is activated.

    Lets the gateway scope protocol turns uniformly. The original gateway check covered
    only DEVMODE05/VS05, so IFDEV05/IAM05 fell through to the generic ghost board-seeding —
    and IAM05 then inherited a DEVMODE05-flavored ghost board (context-bled from a prior
    DEVMODE05 turn in the same session) that its own closeout never advances, leaving a
    stale open board that tripped the generic done-claim gate (observed live mo-1782300201)."""
    return (is_devmode05_activation(user_input) or is_vs05_activation(user_input)
            or is_ifdev05_activation(user_input) or is_iam05_activation(user_input))


def owner_protocol_name(user_input: str) -> str:
    """Return the active owner-protocol name (DEVMODE05/VS05/IFDEV05/IAM05), or ''."""
    if is_devmode05_activation(user_input):
        return "DEVMODE05"
    if is_vs05_activation(user_input):
        return "VS05"
    if is_ifdev05_activation(user_input):
        return "IFDEV05"
    if is_iam05_activation(user_input):
        return "IAM05"
    return ""


def vs05_readonly_source_roots(user_input: str) -> list[str]:
    """Return existing local source roots explicitly supplied to a VS05 turn.

    VS05 compares MO against operator-named references. Those references may
    live outside the active project root, but they are source-intake roots only:
    callers must still keep mutating tools on the normal project sandbox roots.
    """
    if not is_vs05_activation(user_input):
        return []
    tokens = re.findall(r'"([^"]+)"|\'([^\']+)\'|([^\s,;]+)', str(user_input or ""))
    roots: list[str] = []
    seen: set[str] = set()
    for groups in tokens:
        raw = next((value for value in groups if value), "").strip()
        if not raw:
            continue
        lowered = raw.lower().lstrip("/")
        if lowered in {"start", "vs05", "vs", "05"}:
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
