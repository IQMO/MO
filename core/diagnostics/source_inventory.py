"""Deterministic, import-free repository source discovery for diagnostics.

Git owns the public file boundary. Local callers may additionally include new
non-ignored files and the explicit ignored ``tests/`` maintainer overlay. The
inventory reads bytes and UTF-8 text only; it never imports or executes product
modules.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..runtime.subprocess_flags import run_with_file_capture


_IGNORED_OVERLAY_SUFFIXES = {".pyc", ".pyo"}
_IGNORED_OVERLAY_PARTS = {"__pycache__", ".pytest_cache"}


@dataclass(frozen=True)
class InventoryProblem:
    source: str
    reason: str

    def render(self) -> str:
        return f"{self.source}: {self.reason}"


@dataclass(frozen=True)
class SourceDocument:
    relative_path: str
    data: bytes
    text: str | None
    sha256: str
    normalized_sha256: str | None
    line_count: int
    origin: str

    @property
    def suffix(self) -> str:
        return Path(self.relative_path).suffix.lower()


@dataclass(frozen=True)
class SourceInventory:
    root: Path
    documents: tuple[SourceDocument, ...]
    problems: tuple[InventoryProblem, ...]

    @property
    def text_documents(self) -> tuple[SourceDocument, ...]:
        return tuple(document for document in self.documents if document.text is not None)

    @property
    def binary_documents(self) -> tuple[SourceDocument, ...]:
        return tuple(document for document in self.documents if document.text is None)


def _git_paths(root: Path, *arguments: str) -> list[str]:
    if not arguments:
        raise ValueError("git subcommand is required")
    completed = run_with_file_capture(
        ["git", arguments[0], "-z", *arguments[1:]],
        cwd=str(root), timeout=15, errors="strict",
    )
    completed.check_returncode()
    return [item for item in completed.stdout.split("\0") if item]


def _overlay_path_allowed(relative_path: str) -> bool:
    path = Path(relative_path)
    return (
        not any(part in _IGNORED_OVERLAY_PARTS for part in path.parts)
        and path.suffix.lower() not in _IGNORED_OVERLAY_SUFFIXES
    )


def discover_source_paths(
    root: Path,
    *,
    include_untracked: bool = True,
    include_test_overlay: bool = False,
) -> list[tuple[str, str]]:
    """Return sorted ``(relative_path, origin)`` rows from the Git boundary."""
    root = root.resolve()
    origins: dict[str, str] = {path: "tracked" for path in _git_paths(root, "ls-files")}
    # A tracked path deleted in the candidate worktree is no longer source to
    # inspect. CI sees the post-commit tree, so excluding it here makes the same
    # candidate reviewable before commit without turning an intentional removal
    # into a false unreadable-file problem.
    for path in _git_paths(root, "ls-files", "--deleted"):
        origins.pop(path, None)
    if include_untracked:
        for path in _git_paths(root, "ls-files", "--others", "--exclude-standard"):
            origins.setdefault(path, "untracked")
    if include_test_overlay:
        overlay = _git_paths(
            root,
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--",
            "tests",
        )
        for path in overlay:
            if _overlay_path_allowed(path):
                origins.setdefault(path, "test-overlay")
    return sorted(origins.items())


def normalize_text(text: str) -> str:
    """Normalize line endings and trailing whitespace without changing content."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).rstrip()


def _decode_text(data: bytes) -> str | None:
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def load_source_inventory(
    root: Path,
    *,
    include_untracked: bool = True,
    include_test_overlay: bool = False,
    paths: Iterable[tuple[str, str]] | None = None,
) -> SourceInventory:
    """Read the requested repository boundary without importing any source."""
    root = root.resolve()
    discovered = list(paths) if paths is not None else discover_source_paths(
        root,
        include_untracked=include_untracked,
        include_test_overlay=include_test_overlay,
    )
    documents: list[SourceDocument] = []
    problems: list[InventoryProblem] = []
    seen: set[str] = set()
    for relative_path, origin in sorted(discovered):
        relative_path = relative_path.replace("\\", "/")
        if relative_path in seen:
            problems.append(InventoryProblem(relative_path, "duplicate inventory path"))
            continue
        seen.add(relative_path)
        source = (root / relative_path).resolve()
        try:
            source.relative_to(root)
        except ValueError:
            problems.append(InventoryProblem(relative_path, "path escapes repository root"))
            continue
        try:
            data = source.read_bytes()
        except OSError as exc:
            problems.append(InventoryProblem(relative_path, f"unreadable ({type(exc).__name__})"))
            continue
        text = _decode_text(data)
        normalized_sha256 = None
        line_count = 0
        if text is not None:
            normalized = normalize_text(text)
            normalized_sha256 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            line_count = len(text.splitlines())
        documents.append(
            SourceDocument(
                relative_path=relative_path,
                data=data,
                text=text,
                sha256=hashlib.sha256(data).hexdigest(),
                normalized_sha256=normalized_sha256,
                line_count=line_count,
                origin=origin,
            )
        )
    return SourceInventory(root, tuple(documents), tuple(problems))


def inventory_summary(inventory: SourceInventory) -> dict[str, int]:
    origins: dict[str, int] = {"tracked": 0, "untracked": 0, "test-overlay": 0}
    for document in inventory.documents:
        origins[document.origin] = origins.get(document.origin, 0) + 1
    return {
        **origins,
        "total": len(inventory.documents),
        "text": len(inventory.text_documents),
        "binary": len(inventory.binary_documents),
        "bytes": sum(len(document.data) for document in inventory.documents),
        "lines": sum(document.line_count for document in inventory.documents),
        "problems": len(inventory.problems),
    }
