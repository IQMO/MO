"""Project-local scratch lifecycle helpers.

``tmp/`` is an ignored, non-authoritative workspace.  These helpers identify
only live paths already attributed to a caller; they never scan or delete the
shared scratch tree.  That distinction keeps cleanup safe when several MO or
concurrent sessions share one checkout.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ScratchFinding:
    path: str
    kind: str


def live_project_scratch_paths(
    project_root: str | Path,
    paths: Iterable[Any],
) -> tuple[str, ...]:
    """Return existing ``tmp/`` files from ``paths`` as repo-relative names.

    Entries may be plain paths or the ``(path, content)`` pairs used by the
    turn-state edit ledger.  Paths outside the project-local ``tmp/`` tree,
    deleted paths, and the ``tmp`` directory itself are ignored.
    """
    try:
        root = Path(project_root).expanduser().resolve(strict=False)
        scratch_root = (root / "tmp").resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return ()

    found: set[str] = set()
    for entry in paths or ():
        raw = entry[0] if isinstance(entry, (list, tuple)) and entry else entry
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            candidate = Path(text).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            resolved = candidate.resolve(strict=False)
            relative = resolved.relative_to(scratch_root)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if not relative.parts or not resolved.exists():
            continue
        found.add((Path("tmp") / relative).as_posix())
    return tuple(sorted(found, key=str.casefold))


def classify_live_project_scratch_paths(
    project_root: str | Path,
    paths: Iterable[Any],
) -> tuple[ScratchFinding, ...]:
    """Classify only the caller-attributed live paths; never scan shared scratch."""
    return tuple(
        ScratchFinding(path=path, kind=_scratch_kind(path))
        for path in live_project_scratch_paths(project_root, paths)
    )


def scratch_path_was_requested(user_text: str, path: str) -> bool:
    """Return whether the operator explicitly named this scratch destination."""
    requested = str(user_text or "").replace("\\", "/").casefold()
    relative = str(path or "").replace("\\", "/").lstrip("./").casefold()
    return bool(relative and relative in requested)


def unrequested_live_scratch_paths(
    project_root: str | Path,
    paths: Iterable[Any],
    *,
    user_text: str = "",
) -> tuple[str, ...]:
    """Return live scratch paths that were not exact operator destinations."""
    return tuple(
        path
        for path in live_project_scratch_paths(project_root, paths)
        if not scratch_path_was_requested(user_text, path)
    )


def unrequested_live_scratch_findings(
    project_root: str | Path,
    paths: Iterable[Any],
    *,
    user_text: str = "",
) -> tuple[ScratchFinding, ...]:
    return tuple(
        finding
        for finding in classify_live_project_scratch_paths(project_root, paths)
        if not scratch_path_was_requested(user_text, finding.path)
    )


def _scratch_kind(path: str) -> str:
    relative = Path(str(path or "").replace("\\", "/"))
    parts = tuple(part.casefold() for part in relative.parts)
    name = relative.name.casefold()
    if len(parts) >= 2 and parts[:2] == ("tmp", "maintainers"):
        return "maintainer scratch"
    if len(parts) >= 2 and parts[:2] == ("tmp", "ruff-cache"):
        return "tool cache"
    if name == "mo_graph_export.json":
        return "derived graph export"
    if relative.suffix.casefold() in {".docx", ".md", ".pdf", ".pptx", ".xlsx"}:
        return "possible durable output"
    return "scratch"
