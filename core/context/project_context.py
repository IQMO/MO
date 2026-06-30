"""Project-local instruction discovery for called-from-anywhere MO runs.

MO may read project instruction files, but it must not create project-local
state by default. Private state belongs under MO's runtime home.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..runtime.backend_monitor import redact_monitor_text

PROJECT_CONTEXT_FILES = ("AGENTS.md",)


@dataclass(frozen=True)
class ProjectContextFile:
    path: Path
    content: str


def discover_project_context_files(start: str | Path, *, names: Iterable[str] = PROJECT_CONTEXT_FILES) -> tuple[Path, ...]:
    """Return project instruction files from ancestors nearest-last.

    We collect at most one matching file name per directory while walking from
    filesystem root down to the current project so broader parent policy appears
    before more specific child policy. Nothing is created.
    """
    start_path = Path(start).expanduser().resolve(strict=False)
    current = start_path if start_path.is_dir() else start_path.parent
    wanted = tuple(dict.fromkeys(str(name) for name in names if str(name or "").strip()))
    if not wanted:
        return ()

    chain = [current, *current.parents]
    found: list[Path] = []
    for directory in reversed(chain):
        for name in wanted:
            path = directory / name
            try:
                if path.is_file():
                    found.append(path)
            except OSError:
                continue
    return tuple(found)


def build_project_context(start: str | Path, *, max_chars: int = 3200) -> str:
    """Render compact project instructions for provider context.

    This is policy/orientation for the target project. It is not proof of current
    file contents, test status, or runtime state.
    """
    files = []
    for path in discover_project_context_files(start):
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            continue
        if text:
            files.append(ProjectContextFile(path=path, content=text))
    if not files:
        return ""

    parts = [
        "### Project-local instructions",
        "Read-only startup context from AGENTS.md. Do not create or edit project instruction files unless the operator explicitly asks. Live checks still win.",
    ]
    for item in files:
        parts.append(f"## {redact_monitor_text(str(item.path), 220)}\n{redact_monitor_text(item.content, max(400, max_chars // max(1, len(files))))}")
    text = "\n\n".join(part.strip() for part in parts if part.strip())
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 42)].rstrip() + "\n[project context truncated]"
    return text
