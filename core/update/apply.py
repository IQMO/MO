"""Apply a MO update: ``git pull --ff-only`` in the checkout, re-install deps only
if requirements changed. Safe by construction — refuses on a dirty tree or a
non-git checkout, and ``--ff-only`` can never clobber local history.

Used by ``/update`` and ``mo --update``; also runnable directly:
    python -m core.update.apply
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ._git_utils import _git, _is_git_checkout, _repo_root
from ..runtime.subprocess_flags import apply_windows_hidden_process_flags


def _head() -> str:
    out = _git(["rev-parse", "HEAD"], timeout=10)
    return (out.stdout.strip() if out and out.returncode == 0 else "")


def _requirements_text(root: Path) -> str:
    try:
        return (root / "requirements.txt").read_text(encoding="utf-8")
    except Exception:
        return ""


def _upstream_history_is_unrelated() -> bool:
    """Return true only when HEAD and the configured upstream share no base."""
    common_base = _git(["merge-base", "HEAD", "@{u}"], timeout=10)
    return bool(common_base and common_base.returncode == 1)


def apply_update() -> str:
    """Pull the latest checkout fast-forward-only and refresh deps if needed.
    Returns a human-readable result string. Never raises."""
    root = _repo_root()
    if not _is_git_checkout():
        return "MO update: not a git checkout - re-download from the repo to update."
    status = _git(["status", "--porcelain"], timeout=15)
    if status and status.returncode == 0 and (status.stdout or "").strip():
        return "MO update: you have local changes - commit or stash them first, then retry."

    before = _head()
    req_before = _requirements_text(root)
    pull = _git(["pull", "--ff-only"], timeout=180)
    if not pull or pull.returncode != 0:
        if _upstream_history_is_unrelated():
            return (
                "MO update: this checkout predates the fresh public repository "
                "history. Make a fresh clone, then run `mo.py --init` with its "
                "Python interpreter. Your separate private MO home is preserved."
            )
        return (
            "MO update failed (git pull --ff-only). Check network access, the "
            "checkout's upstream, and repository state, then retry."
        )
    after = _head()
    if before and before == after:
        return "MO is already up to date."

    lines = [f"MO updated: {before[:7]} -> {after[:7]}."]
    if _requirements_text(root) != req_before:
        run_kwargs = {"capture_output": True, "text": True, "timeout": 600}
        apply_windows_hidden_process_flags(run_kwargs)
        pip = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(root / "requirements.txt")],
            **run_kwargs,
        )
        lines.append(
            "Dependencies updated."
            if pip.returncode == 0
            else "Dependency update FAILED — run `pip install -r requirements.txt` manually."
        )
    lines.append("Restart MO to load the update.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(apply_update())
