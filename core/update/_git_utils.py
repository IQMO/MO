"""Shared git helpers for core.update — one source of truth for repo-root
resolution, git subprocess calls, and checkout detection."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _repo_root() -> Path:
    """MO checkout root (contains .git, mo.py, requirements.txt, core/...).

    From `core/update/_git_utils.py` the root is three levels up:
    core/update/ -> core/ -> repo root."""
    return Path(__file__).resolve().parent.parent.parent


def _git(args: list[str], *, timeout: float) -> subprocess.CompletedProcess | None:
    """Run a git command in the MO checkout root. Returns the process on
    success, ``None`` on failure or timeout."""
    try:
        return subprocess.run(
            ["git", "-C", str(_repo_root()), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return None


def _is_git_checkout() -> bool:
    """True when the MO checkout root is inside a git working tree."""
    out = _git(["rev-parse", "--is-inside-work-tree"], timeout=5)
    return bool(out and out.returncode == 0 and "true" in (out.stdout or "").lower())
