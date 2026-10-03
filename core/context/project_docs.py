"""Explicit, public-safe starter files for a project's documentation contract."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


AGENTS_STARTER = """# Project Instructions

Status: current

## Purpose and scope

- Describe what this project does and which directories own each major area.
- Keep machine-local state, credentials, private operator data, and deployment secrets out of this repository.

## Sources of truth

- Current source files and tests are authoritative for implemented behavior.
- `README.md` owns public setup and supported-user guidance.
- `AGENTS.md` owns repository maintenance rules.
- When present, `docs/README.md` owns the documentation map and status vocabulary.
- Verify changing claims with live files, tests, logs, or runtime evidence.

## Change discipline

- Understand existing callers, state, persistence, interfaces, and tests before changing behavior.
- Prefer targeted, compatible changes and remove a legacy path only after its callers and replacement are verified.
- Preserve unrelated work in a shared checkout.

## Verification

- Run the smallest relevant checks first, followed by the repository's complete gate when the change warrants it.
- Report evidence and remaining external acceptance separately.

## Documentation status

Use one explicit label for material claims: `current`, `proposed`, `historical`, `generated`, or `obsolete`.
"""


DOCS_README_STARTER = """# Documentation Map

Status: current

Documentation is orientation, not proof of live behavior. Recheck source, tests, logs, or runtime state before making operational claims.

| Authority | Purpose | Expected status |
| --- | --- | --- |
| `README.md` | Public setup, supported behavior, and user guidance | current |
| `AGENTS.md` | Repository maintenance contract | current |
| `docs/README.md` | Documentation routing and status vocabulary | current |
| `docs/architecture/` | Architecture boundaries and stable design notes | current or historical |
| `docs/decisions/` | Dated decisions and their consequences | current or historical |
| `PROJECT-MAP.md` | Generated structural orientation when the project provides it | generated |

## Status vocabulary

- `current` — describes the supported implementation now.
- `proposed` — a plan that is not yet implemented.
- `historical` — retained context that is no longer current guidance.
- `generated` — derived output that must be refreshed from its source.
- `obsolete` — no longer authoritative and awaiting removal or archival.

Never store credentials, private profile data, machine-specific paths, or deployment secrets in tracked documentation.
"""


@dataclass(frozen=True)
class ProjectDocsStarterReport:
    project_path: Path
    created: tuple[Path, ...]
    existing: tuple[Path, ...]


def create_project_docs_starter(project_path: str | Path) -> ProjectDocsStarterReport:
    """Create absent public documentation starters without overwriting files."""
    project = Path(project_path).expanduser().resolve(strict=False)
    if not project.is_dir():
        raise ValueError(f"Project path is not a directory: {project}")

    docs_dir = project / "docs"
    if docs_dir.exists() and not docs_dir.is_dir():
        raise ValueError(f"Documentation path is not a directory: {docs_dir}")
    docs_dir.mkdir(exist_ok=True)

    created: list[Path] = []
    existing: list[Path] = []
    _write_absent(project / "AGENTS.md", AGENTS_STARTER, created, existing)
    _write_absent(docs_dir / "README.md", DOCS_README_STARTER, created, existing)
    return ProjectDocsStarterReport(
        project_path=project,
        created=tuple(created),
        existing=tuple(existing),
    )


def create_project_rule_starter(project_path: str | Path) -> ProjectDocsStarterReport:
    """Create only the project rule contract without adding documentation files."""
    project = Path(project_path).expanduser().resolve(strict=False)
    if not project.is_dir():
        raise ValueError(f"Project path is not a directory: {project}")
    created: list[Path] = []
    existing: list[Path] = []
    _write_absent(project / "AGENTS.md", AGENTS_STARTER, created, existing)
    return ProjectDocsStarterReport(
        project_path=project,
        created=tuple(created),
        existing=tuple(existing),
    )


def render_project_docs_starter_report(report: ProjectDocsStarterReport) -> str:
    lines = [f"Project documentation starter: {report.project_path}"]
    if report.created:
        lines.append("  created: " + ", ".join(path.relative_to(report.project_path).as_posix() for path in report.created))
    if report.existing:
        lines.append(
            "  preserved existing: "
            + ", ".join(path.relative_to(report.project_path).as_posix() for path in report.existing)
        )
    return "\n".join(lines)


def _write_absent(path: Path, content: str, created: list[Path], existing: list[Path]) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError:
        existing.append(path)
    else:
        created.append(path)
