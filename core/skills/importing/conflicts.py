"""Structural-only conflict signal for imported material (constraint C5).

MO's graph is structural, so this flags DOC claims that cite identifiers absent
from the fetched source code. It CANNOT verify behavioral claims ("docs say it
returns X"). Honest scope, advisory output only.
"""
from __future__ import annotations

import re

_IDENT = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})\(?\)?`")
_DOC_SUFFIX = (".md", ".txt", ".rst")


def find_conflicts(files: dict[str, str]) -> list[str]:
    """Backticked identifiers cited in docs but absent from fetched code files."""
    docs = {p: t for p, t in files.items()
            if p.lower().endswith(_DOC_SUFFIX) or "llms" in p.lower() or "readme" in p.lower()}
    code = "\n".join(t for p, t in files.items() if p not in docs)
    if not code.strip():
        return []  # nothing structural to check against
    missing: list[str] = []
    seen: set[str] = set()
    for text in docs.values():
        for match in _IDENT.finditer(text):
            name = match.group(1)
            if name in seen:
                continue
            seen.add(name)
            if name not in code:
                missing.append(name)
    return sorted(missing)[:40]


def render_conflict_report(missing: list[str]) -> str:
    if not missing:
        return (
            "# Conflict Report\n\n"
            "Structural check: no doc-cited identifiers missing from fetched source "
            "(or there was no code to check against).\n\n"
            "Structural-only: this does not verify behavioral claims.\n"
        )
    lines = [
        "# Conflict Report", "",
        "Doc-cited identifiers NOT found in fetched source (structural mismatch — "
        "verify before trusting the docs):", "",
    ]
    lines += [f"- `{name}`" for name in missing]
    lines += ["", "Structural-only: cannot verify behavioral claims (e.g. return values)."]
    return "\n".join(lines) + "\n"
