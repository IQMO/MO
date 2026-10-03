"""Advisory static checks for frontend design anti-patterns.

This is intentionally small and dependency-free. It catches deterministic
frontend risks; visual judgment still needs a render/screenshot/browser check.
"""
from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path


FRONTEND_EXTENSIONS = {
    ".astro",
    ".css",
    ".html",
    ".htm",
    ".jsx",
    ".scss",
    ".svelte",
    ".tsx",
    ".vue",
}

_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", "dist", "build", ".venv", "venv"}


@dataclass(frozen=True)
class DesignFinding:
    path: Path
    line: int
    code: str
    severity: str
    message: str


_PATTERNS: tuple[tuple[str, str, re.Pattern[str], str], ...] = (
    (
        "gradient-text",
        "warn",
        re.compile(r"(?:-webkit-)?background-clip\s*:\s*text", re.I),
        "gradient text is a generic visual tell; use it only with a clear product reason",
    ),
    (
        "glass-default",
        "warn",
        re.compile(r"(?:-webkit-)?backdrop-filter\s*:", re.I),
        "glass/blur surfaces read generic unless the existing design system uses them",
    ),
    (
        "scroll-js",
        "warn",
        re.compile(r"\.addEventListener\s*\(\s*['\"]scroll['\"]", re.I),
        "scroll listeners need a performance reason; prefer CSS or IntersectionObserver",
    ),
)


def _number_after_property(line: str, property_name: str) -> float | None:
    match = re.search(
        rf"(?<![\w-]){re.escape(property_name)}\s*:\s*(-?\d+(?:\.\d+)?)([a-z%]*)",
        line,
        re.I,
    )
    if not match or match.group(2) == "%":
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _iter_frontend_files(paths: list[str | Path]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_file() and path.suffix.lower() in FRONTEND_EXTENSIONS:
            files.append(path)
        elif path.is_dir():
            for root, dirnames, filenames in os.walk(path):
                dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
                root_path = Path(root)
                for filename in filenames:
                    child = root_path / filename
                    if child.suffix.lower() in FRONTEND_EXTENSIONS:
                        files.append(child)
    return sorted(set(files))


def _check_text(path: Path, text: str) -> list[DesignFinding]:
    findings: list[DesignFinding] = []
    motion_line = 0
    has_reduced_motion = "prefers-reduced-motion" in text

    for line_no, line in enumerate(text.splitlines(), start=1):
        for code, severity, pattern, message in _PATTERNS:
            if pattern.search(line):
                findings.append(DesignFinding(path, line_no, code, severity, message))

        radius = _number_after_property(line, "border-radius")
        if radius is not None and radius >= 32:
            findings.append(
                DesignFinding(
                    path,
                    line_no,
                    "huge-radius",
                    "warn",
                    "large radii often create generic pill/card UI; match detected tokens first",
                )
            )

        z_index = _number_after_property(line, "z-index")
        if z_index is not None and z_index >= 1000:
            findings.append(
                DesignFinding(
                    path,
                    line_no,
                    "z-index-stack",
                    "warn",
                    "high z-index values usually hide stacking bugs; keep the layer scale intentional",
                )
            )

        if re.search(r"\b(?:animation|animation-name)\s*:|@keyframes\b", line, re.I) and not motion_line:
            motion_line = line_no

    if motion_line and not has_reduced_motion:
        findings.append(
            DesignFinding(
                path,
                motion_line,
                "reduced-motion",
                "warn",
                "animation/keyframes need a prefers-reduced-motion fallback",
            )
        )
    return findings


def check_paths(paths: list[str | Path]) -> list[DesignFinding]:
    findings: list[DesignFinding] = []
    for path in _iter_frontend_files(paths):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            findings.append(DesignFinding(path, 0, "read-error", "warn", f"could not read file: {exc}"))
            continue
        findings.extend(_check_text(path, text))
    return findings


def render_findings(findings: list[DesignFinding]) -> str:
    if not findings:
        return "[design-check] no findings"
    return "\n".join(
        f"{finding.path}:{finding.line}: {finding.severity} {finding.code}: {finding.message}"
        for finding in findings
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run advisory static checks for frontend design anti-patterns.")
    parser.add_argument("paths", nargs="+", help="Frontend files or directories to scan.")
    parser.add_argument("--fail-on", choices=("none", "warn"), default="none", help="Exit non-zero when findings exist.")
    args = parser.parse_args(argv)

    findings = check_paths(args.paths)
    print(render_findings(findings))
    return 1 if findings and args.fail_on == "warn" else 0


if __name__ == "__main__":
    raise SystemExit(main())
