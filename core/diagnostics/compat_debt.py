"""Enforce that every retained compatibility path is registered debt with a removal condition.

MO keeps no silent legacy fallbacks. A replaced code path may survive only as a
one-line marker of the exact form

    # COMPAT(<id>): replaced-by <owner>; remove-when <condition>

and only while <id> is listed in ``REGISTERED_COMPAT`` below. The check fails on:
an unmarked debt phrase, a malformed marker, an unregistered marker id, and a
registered id whose marker no longer exists (deletion must retire the registry
row in the same change). Runs standalone: ``python -m core.diagnostics.compat_debt``.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from .source_inventory import load_source_inventory

# The only sanctioned survival form for a replaced path. id: lowercase slug.
_MARKER = re.compile(
    r"COMPAT\((?P<id>[a-z0-9][a-z0-9-]*)\):\s*replaced-by\s+"
    r"(?P<owner>[^;]+);\s*remove-when\s+(?P<condition>.+)"
)
_MARKER_ANYWHERE = re.compile(r"(?<![A-Za-z0-9_])COMPAT\(", re.IGNORECASE)

# Loose phrases that historically hid unregistered debt in product code. A line
# carrying a valid COMPAT marker is exempt; anything else fails.
_DEBT_PHRASES = re.compile(
    r"kept during migration|legacy fallback\b|kept for backwards? compat(?:ibility)?|"
    r"compatibility (?:adapter|path|route|fallback)\b",
    re.IGNORECASE,
)

# Registered compatibility debt: id -> short human note. Adding a marker requires
# a row here; deleting the marked code requires deleting the row in the same change.
REGISTERED_COMPAT: dict[str, str] = {
    "mo-design-v1-boardless": (
        "read-only support for saved design/v1 artifacts without a Board; retire after supported "
        "artifacts have moved to design/v2 or newer"
    ),
    "state-home-upgraders": (
        "explicit --init old-home upgraders (credential + layout); retire when the operator "
        "declares no supported old-home restore path"
    ),
    "operator-pack-env-alias": (
        "MO_OPERATOR_PACK env alias for MO_LOCAL_EXTENSION_ROOT; retire after the owner pack "
        "drops the old name"
    ),
    "live-host-legacy-identity": (
        "label-hash instance identity for pre-key Live Control hosts; retire when supported "
        "pre-key hosts have aged out"
    ),
    "android-update-legacy-env": (
        "seven-field Android updater environment alongside the generated release manifest; "
        "retire when all maintained deployments use MO_ANDROID_UPDATE_MANIFEST"
    ),
    "context-savings-v0": (
        "pre-result-cap session metadata stored under compression; retire after supported saved "
        "sessions have been rewritten or aged out"
    ),
    "markdown-skill-v0": (
        "pre-frontmatter profile skill files; retire after supported profile skills have been "
        "rewritten to the current SKILL.md contract"
    ),
    "desktop-tray-voice-key": (
        "tray_enabled previously lived in the voice block; retire after supported Desktop configs "
        "have migrated to mo_desktop.tray_enabled"
    ),
    "continuity-metadata-v0": (
        "privacy-safe handling for session/taskboard/event rows without current owner ids and for "
        "raw source slots; retire after supported rows are rewritten and the event TTL elapses"
    ),
    "mail-job-placeholder-20260928": (
        "Gmail-specific placeholder in saved Hub jobs; retire when no supported saved jobs contain it"
    ),
    "memory-desktop-policy-rows": (
        "database repair for Desktop turns indexed with provider-only policy text; retire after "
        "supported profiles have completed the repair migration"
    ),
    "worker-panel-workspace-config": (
        "interface.workspace.enabled read once after worker activity moved to interface.activity.enabled; "
        "retire after the documented configuration migration window"
    ),
}

_EXCLUDED_PATHS = frozenset({"core/diagnostics/compat_debt.py"})


@dataclass(frozen=True)
class CompatProblem:
    source: str
    line: int
    reason: str

    def render(self) -> str:
        return f"{self.source}:{self.line}: {self.reason}"


def check_text(source: str, text: str) -> tuple[list[CompatProblem], set[str]]:
    """Validate one file's marker discipline; return problems and seen marker ids."""
    problems: list[CompatProblem] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if "COMPAT(<id>)" in line:
            continue
        if _MARKER_ANYWHERE.search(line):
            match = _MARKER.search(line)
            if not match:
                problems.append(CompatProblem(source, number, "malformed COMPAT marker (need 'COMPAT(<id>): replaced-by <owner>; remove-when <condition>')"))
                continue
            marker_id = match.group("id")
            seen.add(marker_id)
            if marker_id not in REGISTERED_COMPAT:
                problems.append(CompatProblem(source, number, f"unregistered COMPAT id {marker_id!r}; add it to core/diagnostics/compat_debt.py REGISTERED_COMPAT with a removal plan"))
        elif _DEBT_PHRASES.search(line):
            problems.append(CompatProblem(source, number, "unmarked compatibility debt phrase; delete the old path or mark it with a registered COMPAT marker"))
    return problems, seen


def check_repository(root: Path, *, include_untracked: bool = True) -> list[CompatProblem]:
    problems: list[CompatProblem] = []
    seen: set[str] = set()
    inventory = load_source_inventory(root, include_untracked=include_untracked)
    problems.extend(CompatProblem(item.source, 0, item.reason) for item in inventory.problems)
    for document in inventory.text_documents:
        rel = document.relative_path
        if rel in _EXCLUDED_PATHS or rel.endswith("CHANGELOG.md"):
            continue
        file_problems, file_seen = check_text(rel, document.text or "")
        problems.extend(file_problems)
        seen.update(file_seen)
    for stale in sorted(set(REGISTERED_COMPAT) - seen):
        problems.append(CompatProblem("core/diagnostics/compat_debt.py", 1, f"registered COMPAT id {stale!r} has no marker left in product code; retire the registry row"))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--tracked-only", action="store_true", help="exclude new non-ignored files")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    problems = check_repository(root, include_untracked=not args.tracked_only)
    for problem in problems:
        print(problem.render())
    print(f"compat-debt: {len(problems)} problem(s), {len(REGISTERED_COMPAT)} registered entr(y/ies)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
