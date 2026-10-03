"""S4 — re-audit worker findings before they reach the user.

The mapper workers are cheap/fast models, so their single biggest failure mode is
citing files or lines that do not exist. This deterministic pass extracts every
`path` / `path:line` citation a worker made and checks it resolves to a real file
(and that the line is in range). It does not judge whether a *claim* is correct —
it verifies the *evidence* exists, which is the cheap, high-signal guard against a
fast model inventing references. Slices with many unresolved citations are flagged
with low citation resolution in the synthesis. This is not semantic confidence.
"""
from __future__ import annotations

import re
from pathlib import Path

# Backtick-wrapped path-ish tokens with an optional :line suffix — the exact shape
# the project-mapper role is told to cite in.
_CITE_RE = re.compile(r"`([\w./\\-]+?\.[A-Za-z0-9]{1,6})(?::(\d+))?`")


def verify_slice_map(text: str, base: str | Path) -> dict[str, object]:
    """Return citation stats for one slice-map: how many citations resolved to real
    files, and up to a few that did not."""
    base = Path(base)
    checked = 0
    resolved = 0
    unresolved: list[str] = []
    seen: set[str] = set()
    for match in _CITE_RE.finditer(str(text or "")):
        raw_path = match.group(1)
        line = match.group(2)
        key = f"{raw_path}:{line or ''}"
        if key in seen:
            continue
        seen.add(key)
        rel = raw_path.replace("\\", "/").lstrip("./")
        cand = (base / rel)
        checked += 1
        try:
            if cand.is_file():
                if line:
                    total = _line_count(cand)
                    if total and int(line) > total:
                        unresolved.append(f"{raw_path}:{line} (file has {total} lines)")
                        continue
                resolved += 1
            else:
                unresolved.append(raw_path)
        except OSError:
            unresolved.append(raw_path)
    return {
        "citations": checked,
        "resolved": resolved,
        "unresolved": unresolved[:20],
        "citation_resolution": _citation_resolution(checked, resolved),
    }


def _line_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _citation_resolution(checked: int, resolved: int) -> str:
    if checked == 0:
        return "unverified"  # the worker cited nothing to check
    ratio = resolved / checked
    if ratio >= 0.9:
        return "high"
    if ratio >= 0.6:
        return "medium"
    return "low"
