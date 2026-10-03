"""Validate local links in public tracked Markdown without external tooling."""
from __future__ import annotations

import argparse
import html
import os
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .source_inventory import discover_source_paths
from ..utils.markdown import headings, prose_lines

_MARKDOWN_LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
_REFERENCE_LINK = re.compile(r"^\s*\[[^\]\n]+\]:\s*(<[^>\n]+>|\S+)")
_HTML_LINK = re.compile(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_EXTERNAL_SCHEMES = {"data", "http", "https", "mailto"}


@dataclass(frozen=True)
class LinkProblem:
    source: Path
    line: int
    target: str
    reason: str

    def render(self, root: Path) -> str:
        source = self.source.relative_to(root).as_posix()
        return f"{source}:{self.line}: {self.reason}: {self.target}"


def public_source_paths(root: Path) -> list[Path]:
    """Return tracked plus new non-ignored files, excluding private overlays."""
    root = root.resolve()
    return [
        (root / relative_path).resolve()
        for relative_path, _origin in discover_source_paths(
            root,
            include_untracked=True,
            include_test_overlay=False,
        )
        if (root / relative_path).is_file()
    ]


def public_markdown_paths(root: Path, source_paths: list[Path] | None = None) -> list[Path]:
    """Return public Markdown from the same candidate file boundary as CI."""
    paths = public_source_paths(root) if source_paths is None else source_paths
    return [path for path in paths if path.suffix.lower() == ".md"]


def _public_link_targets(root: Path, source_paths: list[Path]) -> set[Path]:
    """Include candidate files and their directories, never ignored local trees."""
    root = root.resolve()
    targets = {root}
    for source in source_paths:
        source = source.resolve()
        targets.add(source)
        for parent in source.parents:
            try:
                parent.relative_to(root)
            except ValueError:
                break
            targets.add(parent)
    return targets


def _path_has_exact_case(root: Path, path: Path) -> bool:
    """Reject links that work on Windows but break on case-sensitive GitHub."""
    try:
        # `resolve()` may replace the caller's spelling with the on-disk case on
        # case-insensitive filesystems, which would make this check vacuous.
        # `abspath()` collapses `..` while retaining the link text's component
        # spelling. Containment was already proven with `resolve()`.
        spelled_path = Path(os.path.abspath(str(path)))
        spelled_root = Path(os.path.abspath(str(root)))
        parts = spelled_path.relative_to(spelled_root).parts
    except ValueError:
        return False
    current = root.resolve()
    for part in parts:
        try:
            names = {child.name for child in current.iterdir()}
        except OSError:
            return False
        if part not in names:
            return False
        current = current / part
    return True


def _local_reference(source: Path, root: Path, raw_target: str) -> tuple[Path, str] | None:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1:target.index(">")]
    else:
        # An optional Markdown title follows whitespace. Paths containing spaces
        # must use angle brackets, which keeps this split deterministic.
        target = target.split(maxsplit=1)[0]
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme.lower() in _EXTERNAL_SCHEMES or target.startswith("//"):
        return None
    path_text = urllib.parse.unquote(parsed.path)
    fragment = urllib.parse.unquote(parsed.fragment)
    if not path_text:
        return (source, fragment) if fragment else None
    if path_text.startswith("/"):
        return root / path_text.lstrip("/"), fragment
    return source.parent / path_text, fragment


def _markdown_anchors(text: str) -> set[str]:
    """Recognize ATX/setext headings and explicit HTML anchors, not render Markdown."""
    anchors: set[str] = set()
    heading_ids: set[str] = set()
    lines = prose_lines(text)
    for _number, line in lines:
        for tag in re.findall(r"<[^>]+>", line):
            anchors.update(re.findall(r'''\b(?:id|name)\s*=\s*["']([^"']+)["']''', tag, re.I))
    for heading in headings(lines):
        title = str(heading["title"])
        title = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", title)
        title = re.sub(r"<[^>]*>", "", title)
        title = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", title)
        title = html.unescape(re.sub(r"[*`~]", "", title)).strip().lower()
        slug = "".join(
            "-" if char == " " else char
            for char in title
            if char in " -_" or unicodedata.category(char)[0] not in "PSCZ"
        )
        candidate = slug
        suffix = 0
        while candidate in heading_ids:
            suffix += 1
            candidate = f"{slug}-{suffix}"
        heading_ids.add(candidate)
        anchors.add(candidate)
    return anchors


def check_documentation_links(
    root: Path,
    paths: list[Path] | None = None,
    *,
    public_targets: set[Path] | None = None,
) -> list[LinkProblem]:
    root = root.resolve()
    problems: list[LinkProblem] = []
    anchors_by_path: dict[Path, set[str]] = {}
    sources = public_markdown_paths(root) if paths is None else paths
    for source in sources:
        text = source.read_text(encoding="utf-8")
        anchors_by_path[source] = _markdown_anchors(text)
        for line_number, line in prose_lines(text):
            raw_targets = [match.group(1) for match in _MARKDOWN_LINK.finditer(line)]
            reference = _REFERENCE_LINK.match(line)
            if reference:
                raw_targets.append(reference.group(1))
            raw_targets.extend(match.group(1) for match in _HTML_LINK.finditer(line))
            for raw_target in raw_targets:
                reference = _local_reference(source, root, raw_target)
                if reference is None:
                    continue
                target, fragment = reference
                try:
                    target.resolve().relative_to(root)
                except ValueError:
                    problems.append(LinkProblem(source, line_number, raw_target, "link leaves repository"))
                    continue
                if not target.exists():
                    problems.append(LinkProblem(source, line_number, raw_target, "missing local target"))
                elif not _path_has_exact_case(root, target):
                    problems.append(LinkProblem(source, line_number, raw_target, "target case mismatch"))
                elif public_targets is not None and target.resolve() not in public_targets:
                    problems.append(
                        LinkProblem(source, line_number, raw_target, "target is outside public source boundary")
                    )
                elif fragment and target.is_file() and target.suffix.lower() == ".md":
                    if target not in anchors_by_path:
                        anchors_by_path[target] = _markdown_anchors(target.read_text(encoding="utf-8"))
                    if fragment not in anchors_by_path[target]:
                        problems.append(LinkProblem(source, line_number, raw_target, "missing Markdown anchor"))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    source_paths = public_source_paths(root)
    markdown_paths = public_markdown_paths(root, source_paths)
    link_problems = check_documentation_links(
        root,
        markdown_paths,
        public_targets=_public_link_targets(root, source_paths),
    )
    from .capability_check import check_capability_contract

    capability_problems = check_capability_contract(root)
    if link_problems or capability_problems:
        print(
            f"[docs] {len(link_problems)} broken local link(s); "
            f"{len(capability_problems)} capability contract problem(s)"
        )
        for problem in link_problems:
            print(problem.render(root))
        for problem in capability_problems:
            print(problem.render())
        return 1
    print(
        f"[docs] local links valid across {len(markdown_paths)} public Markdown files; "
        "capability ledger and command coverage valid"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
