"""MO version identity, derived from the running git checkout.

`mo --version` shipped a hardcoded string with no tie to the actual build. This
reads the short git HEAD of the checkout (AGENT_ROOT) so the version reflects what
is really running. Cached; falls back to the base string outside a git checkout.
"""
from __future__ import annotations

import time

from ._git_utils import _git

_BASE = "MO v1.0"
_cache: dict[str, object] = {"text": None, "at": 0.0}


def current_version() -> str:
    """Return e.g. ``MO v1.0 (ab12cd3)`` using the short git HEAD, cached for 30s.

    Falls back to ``MO v1.0`` when not in a git checkout (zip download)."""
    now = time.time()
    cached = _cache.get("text")
    if isinstance(cached, str) and now - float(_cache.get("at") or 0.0) < 30:
        return cached
    out = _git(["rev-parse", "--short", "HEAD"], timeout=5)
    short = out.stdout.strip() if out and out.returncode == 0 else ""
    text = f"{_BASE} ({short})" if short else _BASE
    _cache["text"] = text
    _cache["at"] = now
    return text
