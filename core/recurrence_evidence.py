"""Factual recurrence evidence — what keeps coming back, measured from git history.

This module is a neutral product capability: pure, deterministic facts about which
areas of a repository get patched again and again. It makes NO interpretation — it
does not guess *why* something recurs, only surfaces *that* it does, so a later
human/model review has real data to reason over instead of impression.

The signal it answers: "have we patched the same thing twice?" — the band-aid-loop
detector CLAUDE.md asks for ("Patching the same area twice means stop and fix the cause").

Two signals, both from `git log`:
- **Repeat-patched files** — files touched in >=2 distinct recent commits.
- **Recurring fix areas** — conventional-commit scopes (``fix(devmode)`` -> ``devmode``)
  that appear >=2 times, catching thematic recurrence even across different files.

Git invocation is a thin wrapper that degrades to empty on any failure; the parsing is
pure and unit-tested on synthetic input (no temp repo needed).
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

_DEFAULT_WINDOW = 20
_MIN_FILE_COMMITS = 2
_MIN_SCOPE_COUNT = 2
_TOP = 10
# Conventional-commit subject: type(scope)!: summary  -> capture type and optional scope.
_SCOPE_RE = re.compile(r"^([a-z]+)(?:\(([^)]+)\))?!?:", re.IGNORECASE)
# git --pretty=format:%x1f emits a unit-separator as each commit's header line.
_COMMIT_SEP = "\x1f"


# --- pure parsers (unit-tested directly) -------------------------------------

def _commit_file_blocks(raw: str) -> list[list[str]]:
    """Split `git log --name-only --pretty=format:%x1f` output into per-commit file
    lists. Each commit's chunk is the unit-separator followed by its changed paths."""
    blocks: list[list[str]] = []
    for chunk in str(raw or "").split(_COMMIT_SEP):
        files = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if files:
            blocks.append(files)
    return blocks


def _repeat_patched_files(
    blocks: list[list[str]], *, min_commits: int = _MIN_FILE_COMMITS, top: int = _TOP
) -> list[dict]:
    """Files that appear in at least ``min_commits`` distinct commits. A file listed
    twice in one commit still counts once (distinct-commit semantics)."""
    counter: Counter[str] = Counter()
    for block in blocks:
        for path in set(block):  # distinct per commit
            counter[path] += 1
    ranked = [
        {"path": path, "commits": count}
        for path, count in counter.most_common()
        if count >= min_commits
    ]
    return ranked[:top]


def _recurring_scopes(
    subjects: list[str], *, min_count: int = _MIN_SCOPE_COUNT, top: int = _TOP
) -> list[dict]:
    """Conventional-commit scopes (or bare type when no scope) seen >= min_count times.
    A scope appearing repeatedly means that area keeps getting re-touched."""
    counter: Counter[str] = Counter()
    for subject in subjects:
        match = _SCOPE_RE.match(str(subject or "").strip())
        if not match:
            continue
        scope = match.group(2) or match.group(1)
        if scope:
            counter[scope.strip().lower()] += 1
    ranked = [
        {"scope": scope, "count": count}
        for scope, count in counter.most_common()
        if count >= min_count
    ]
    return ranked[:top]


# --- thin git wrappers (degrade to empty) ------------------------------------

def _git(root: Path, args: list[str]) -> str | None:
    try:
        res = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True, timeout=15
        )
        return res.stdout if res.returncode == 0 else None
    except Exception:
        return None


# --- public API --------------------------------------------------------------

def build_recurrence_evidence(root: str = ".", *, window: int = _DEFAULT_WINDOW) -> dict:
    """Return factual recurrence signals over the last ``window`` commits.

    Shape: {window, available, repeat_patched: [{path, commits}], recurring_scopes: [{scope, count}]}.
    All-empty lists mean a clean (non-repetitive) recent history only when available=True.
    """
    base = Path(root or ".").resolve()
    name_only = _git(base, ["log", f"-n{int(window)}", "--name-only", "--pretty=format:%x1f"])
    subjects_raw = _git(base, ["log", f"-n{int(window)}", "--pretty=format:%s"])
    available = name_only is not None and subjects_raw is not None
    subjects = [ln for ln in (subjects_raw or "").splitlines() if ln.strip()]
    return {
        "window": int(window),
        "available": available,
        "repeat_patched": _repeat_patched_files(_commit_file_blocks(name_only or "")),
        "recurring_scopes": _recurring_scopes(subjects),
    }


def render_recurrence_evidence(evidence: dict) -> str:
    """Compact, human-readable recurrence block. Facts only."""
    window = evidence.get("window", _DEFAULT_WINDOW)
    available = bool(evidence.get("available", True))
    files = evidence.get("repeat_patched") or []
    scopes = evidence.get("recurring_scopes") or []
    lines = [f"### Recurrence Evidence (last {window} commits — facts, not diagnosis)"]
    if not available:
        lines.append("- Recurrence evidence unavailable: git history could not be read for this root.")
        return "\n".join(lines)
    if not files and not scopes:
        lines.append("- No recurrence detected: no file or area was patched twice. Clean history.")
        return "\n".join(lines)
    if files:
        lines.append("Repeat-patched files (touched in >=2 commits — candidate band-aid loops):")
        lines.extend(f"    - {f['path']}: {f['commits']} commits" for f in files)
    if scopes:
        lines.append("Recurring fix areas (commit scope seen >=2):")
        lines.extend(f"    - {s['scope']}: {s['count']} commits" for s in scopes)
    lines.append(
        "Note: this is evidence of WHAT recurs, not WHY. A repeat-patched area is a "
        "candidate for 'fix the cause, not the symptom' — confirm before concluding."
    )
    return "\n".join(lines)
