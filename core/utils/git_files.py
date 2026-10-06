"""Read a checkout's git HEAD from its files: no git process, so cheap enough for startup.

Handles a normal ``.git`` directory, a linked worktree (``.git`` file with ``gitdir:``),
symbolic and detached HEAD, loose and packed refs. Returns "" when it cannot tell."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def git_dirs(root: Path) -> tuple[Path | None, Path | None]:
    marker = root / ".git"
    if marker.is_dir():
        return marker, marker
    try:
        raw = marker.read_text(encoding="utf-8", errors="strict").strip()
    except OSError:
        return None, None
    if not raw.lower().startswith("gitdir:"):
        return None, None
    git_dir = Path(raw.split(":", 1)[1].strip()).expanduser()
    if not git_dir.is_absolute():
        git_dir = (root / git_dir).resolve(strict=False)
    try:
        relative = (git_dir / "commondir").read_text(encoding="utf-8", errors="strict").strip()
        common_dir = (git_dir / relative).resolve(strict=False)
    except OSError:
        common_dir = git_dir
    return git_dir, common_dir


def head_from_dirs(git_dir: Path, common_dir: Path) -> str:
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="strict").strip()
    except OSError:
        return ""
    if not head.startswith("ref: "):
        return clean_commit(head)
    ref = head[5:].strip()
    for base in (git_dir, common_dir):
        try:
            value = (base / ref).read_text(encoding="utf-8", errors="strict").strip()
        except OSError:
            continue
        clean = clean_commit(value)
        if clean:
            return clean
    try:
        packed = (common_dir / "packed-refs").read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError:
        return ""
    for line in packed:
        value, _, name = line.partition(" ")
        if name.strip() == ref:
            return clean_commit(value)
    return ""


def clean_commit(value: Any) -> str:
    commit = str(value or "").strip().lower()
    if len(commit) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in commit):
        return ""
    return commit


def head_commit(root: str | Path) -> str:
    """The full commit id the checkout at ``root`` is on, or ""."""
    git_dir, common_dir = git_dirs(Path(root))
    return head_from_dirs(git_dir, common_dir) if git_dir is not None and common_dir is not None else ""
