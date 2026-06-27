"""MO version identity, derived from the running git checkout.

`mo --version` shipped a hardcoded string with no tie to the actual build. This
reads the short git HEAD of the checkout (AGENT_ROOT) so the version reflects what
is really running. Cached; falls back to the base string outside a git checkout.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

_BASE = "MO v1.0"
_cache: dict[str, object] = {"text": None, "at": 0.0}


def _repo_root() -> Path:
    # core/update/version.py -> repo root is two levels up (the checkout the shim runs).
    return Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(_repo_root()), *args],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return (out.stdout or "").strip()
    except Exception:
        pass
    return ""


def current_version() -> str:
    """Return e.g. ``MO v1.0 (ab12cd3)`` using the short git HEAD, cached for 30s.

    Falls back to ``MO v1.0`` when not in a git checkout (zip download)."""
    now = time.time()
    cached = _cache.get("text")
    if isinstance(cached, str) and now - float(_cache.get("at") or 0.0) < 30:
        return cached
    short = _git("rev-parse", "--short", "HEAD")
    text = f"{_BASE} ({short})" if short else _BASE
    _cache["text"] = text
    _cache["at"] = now
    return text
