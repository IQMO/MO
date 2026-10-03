"""Project-local instruction discovery for called-from-anywhere MO runs.

Discovery and rendering never write project files. The tool dispatcher owns
authorized starter creation; private state belongs under MO's runtime home.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..runtime.backend_monitor import redact_monitor_text

PROJECT_CONTEXT_FILES = ("AGENTS.md",)


def _external_control_roots() -> tuple[Path, ...]:
    """Return external agent state roots that are not MO project ancestors."""
    roots = [Path.home() / ".codex"]
    configured = str(os.environ.get("CODEX_HOME") or "").strip()
    if configured:
        roots.append(Path(configured).expanduser())
    return tuple(dict.fromkeys(root.resolve(strict=False) for root in roots))


def _is_external_control_root(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return False
    return resolved in _external_control_roots()


@dataclass(frozen=True)
class ProjectContextFile:
    path: Path
    content: str


@dataclass(frozen=True)
class ProjectRuleSnapshot:
    """The applicable project-rule files and their content fingerprints for one turn."""

    project_root: Path
    scope_path: Path
    files: tuple[Path, ...]
    digests: tuple[tuple[str, str], ...]
    contents: tuple[ProjectContextFile, ...] = ()
    unreadable: tuple[Path, ...] = ()
    created: tuple[Path, ...] = ()
    creation_error: str = ""
    rechecks: int = 0

    @property
    def available(self) -> bool:
        return bool(self.contents) and not self.unreadable


def _read_project_rules(paths: tuple[Path, ...]):
    """Read each contract once so its content and fingerprint describe the same bytes."""
    digests: list[tuple[str, str]] = []
    contents: list[ProjectContextFile] = []
    unreadable: list[Path] = []
    for path in paths:
        try:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            contents.append(ProjectContextFile(path, raw.decode("utf-8", errors="replace").strip()))
        except OSError as exc:
            digest = f"unreadable:{type(exc).__name__}"
            unreadable.append(path)
        digests.append((str(path), digest))
    return tuple(digests), tuple(contents), tuple(unreadable)


def resolve_project_rules(start: str | Path) -> ProjectRuleSnapshot:
    """Read the applicable contract without creating or changing project files."""
    from ..graph.structural_graph import project_root

    scope = Path(start).expanduser().resolve(strict=False)
    if scope.is_file():
        scope = scope.parent
    root = project_root(scope) if scope.is_dir() else scope
    paths = discover_project_context_files(scope)
    digests, contents, unreadable = _read_project_rules(paths)
    return ProjectRuleSnapshot(
        project_root=root,
        scope_path=scope,
        files=paths,
        digests=digests,
        contents=contents,
        unreadable=unreadable,
    )


def project_rules_changed(snapshot: ProjectRuleSnapshot, current: ProjectRuleSnapshot) -> bool:
    """Return whether applicable project rules changed since the turn snapshot."""
    return current.files != snapshot.files or current.digests != snapshot.digests


def render_project_rule_status(snapshot: ProjectRuleSnapshot) -> str:
    """Render a compact, user-facing status for a project-work final report."""
    if snapshot.unreadable:
        sources = ", ".join(str(path) for path in snapshot.unreadable)
        return f"Project rules: could not read {sources}; rule review is incomplete."
    if snapshot.creation_error:
        return f"Project rules: no starter created ({snapshot.creation_error})."
    if snapshot.created:
        created = ", ".join(str(path) for path in snapshot.created)
        return f"Project rules: created starter {created}."
    if snapshot.rechecks:
        sources = ", ".join(str(path) for path in snapshot.files) or "none"
        return f"Project rules: changed during work; current sources supplied for final review ({sources})."
    if not snapshot.files:
        return "Project rules: none found."
    sources = ", ".join(str(path) for path in snapshot.files)
    return f"Project rules: checked ({sources})."


def render_project_rule_context(snapshot: ProjectRuleSnapshot) -> str:
    """Supply the read snapshot, or preview the starter before any project action."""
    if snapshot.files:
        context = render_project_context_files(snapshot.contents)
        if snapshot.unreadable:
            context += "\n\n" + render_project_rule_status(snapshot)
        return context.strip()
    from .project_docs import AGENTS_STARTER

    return (
        "### Project-local instructions\n"
        "No applicable AGENTS.md was found. Read-only inspection leaves the project unchanged. "
        "Before an authorized project edit or execution, the dispatcher will try to create "
        "this starter through the existing write permissions, without overwriting rules. "
        "Follow it for that work; current system/user authority wins.\n\n"
        + AGENTS_STARTER
    )


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
        if _is_external_control_root(directory):
            continue
        for name in wanted:
            path = directory / name
            try:
                if path.is_file():
                    found.append(path)
            except OSError:
                continue
    return tuple(found)


def discover_nearest_project_context_files(start: str | Path, *, names: Iterable[str] = PROJECT_CONTEXT_FILES) -> tuple[Path, ...]:
    """Return instruction files from the nearest ancestor that has any.

    AGENTS.md is the default project contract. Other filenames are only read
    when the caller explicitly supplies them.
    """
    start_path = Path(start).expanduser().resolve(strict=False)
    current = start_path if start_path.is_dir() else start_path.parent
    wanted = tuple(dict.fromkeys(str(name) for name in names if str(name or "").strip()))
    if not wanted:
        return ()

    for directory in [current, *current.parents]:
        if _is_external_control_root(directory):
            continue
        found: list[Path] = []
        for name in wanted:
            path = directory / name
            try:
                if path.is_file():
                    found.append(path)
            except OSError:
                continue
        if found:
            return tuple(found)
    return ()


def load_project_context_files(start: str | Path, *, nearest: bool = False) -> tuple[ProjectContextFile, ...]:
    paths = discover_nearest_project_context_files(start) if nearest else discover_project_context_files(start)
    _, contents, _ = _read_project_rules(paths)
    return tuple(item for item in contents if item.content)


def render_project_context_files(
    files: Iterable[ProjectContextFile],
    *,
    max_chars: int = 3200,
    title: str = "### Project-local instructions",
) -> str:
    items = tuple(item for item in files if str(item.content or "").strip())
    if not items:
        return ""

    parts = [
        title,
        (
            "Applicable AGENTS.md files, parent before child. Follow these project instructions; "
            "current system/user authority wins. Read omitted files completely with read_file "
            "before work in their scope; reuse already-read instructions while unchanged. "
            "Before reporting, check the work against these rules and assess whether they need an update. "
            "Do not edit these files unless explicitly requested."
        ),
    ]
    for index, item in enumerate(items):
        heading = f"## {redact_monitor_text(str(item.path), 500)}"
        content = redact_monitor_text(item.content, len(item.content) + 20)
        block = f"{heading}\n{content}"
        # An arbitrary excerpt is not an instruction contract: omitted middle
        # rules can qualify both its beginning and its end. Preserve full files
        # when they fit, otherwise expose the existing read_file route.
        remaining_files = len(items) - index - 1
        reserve = sum(len(str(other.path)) + 90 for other in items[-remaining_files:]) if remaining_files else 0
        if max_chars and len("\n\n".join([*parts, block])) + reserve > max_chars:
            block = f"{heading}\n[Content omitted; read completely if not already read for this work or if changed.]"
        if max_chars and len("\n\n".join([*parts, block])) > max_chars:
            parts.append("[Further ancestor instructions omitted; discover AGENTS.md along the project path.]")
            break
        parts.append(block)
    return "\n\n".join(parts)


def build_project_context(start: str | Path, *, max_chars: int = 3200) -> str:
    """Render compact project instructions for provider context.

    This is policy/orientation for the target project. It is not proof of current
    file contents, test status, or runtime state.
    """
    return render_project_context_files(load_project_context_files(start), max_chars=max_chars)
