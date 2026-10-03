"""Validate MO's public capability ledger against live command metadata."""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_LEDGER_START = "<!-- mo-capabilities:ledger:start -->"
_LEDGER_END = "<!-- mo-capabilities:ledger:end -->"
_COMMANDS_START = "<!-- mo-capabilities:commands:start -->"
_COMMANDS_END = "<!-- mo-capabilities:commands:end -->"
_COMMAND_CLASSES = {"public", "internal"}


@dataclass(frozen=True)
class CapabilityProblem:
    line: int
    reason: str
    entry: str = ""

    def render(self) -> str:
        suffix = f": {self.entry}" if self.entry else ""
        return f"CAPABILITIES.md:{self.line}: {self.reason}{suffix}"


def _marked_lines(
    lines: list[str], start: str, end: str, problems: list[CapabilityProblem]
) -> list[tuple[int, str]]:
    try:
        start_index = lines.index(start)
    except ValueError:
        problems.append(CapabilityProblem(1, "missing section marker", start))
        return []
    try:
        end_index = lines.index(end, start_index + 1)
    except ValueError:
        problems.append(CapabilityProblem(start_index + 1, "missing section marker", end))
        return []
    return [(index + 1, lines[index]) for index in range(start_index + 1, end_index)]


def _cells(line: str) -> list[str]:
    if not line.lstrip().startswith("|"):
        return []
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _code(value: str) -> str:
    value = value.strip()
    return value[1:-1] if len(value) >= 2 and value.startswith("`") and value.endswith("`") else ""


def _default_command_specs() -> tuple[object, ...]:
    from interface.command_registry import COMMANDS

    return COMMANDS


def _expected_command_class(spec: object) -> str:
    if bool(getattr(spec, "help", True)) or bool(getattr(spec, "palette", True)):
        return "public"
    return "internal"


def check_capability_contract(
    root: Path, *, command_specs: Iterable[object] | None = None
) -> list[CapabilityProblem]:
    """Return structural and live-registry problems in ``CAPABILITIES.md``."""
    root = root.resolve()
    from .docs_check import _path_has_exact_case
    path = root / "CAPABILITIES.md"
    if not path.is_file():
        return [CapabilityProblem(1, "missing public capability contract")]

    lines = path.read_text(encoding="utf-8").splitlines()
    problems: list[CapabilityProblem] = []
    ledger_lines = _marked_lines(lines, _LEDGER_START, _LEDGER_END, problems)
    command_lines = _marked_lines(lines, _COMMANDS_START, _COMMANDS_END, problems)

    capabilities: dict[str, int] = {}
    for line_number, line in ledger_lines:
        cells = _cells(line)
        if not cells:
            continue
        if len(cells) != 8:
            problems.append(CapabilityProblem(line_number, "capability row must have 8 fields", line))
            continue
        capability_id = _code(cells[0])
        if not capability_id.startswith("CAP-"):
            problems.append(CapabilityProblem(line_number, "invalid capability id", cells[0]))
            continue
        if capability_id in capabilities:
            problems.append(CapabilityProblem(line_number, "duplicate capability id", capability_id))
        capabilities[capability_id] = line_number
        if any(not value for value in cells[1:]):
            problems.append(CapabilityProblem(line_number, "capability row has an empty field", capability_id))
        # Only literal repository paths in the runtime-owner column. Prose,
        # dotted symbols, wildcard patterns, and private state are not resolved.
        for owner in re.findall(r"`([^`]+)`", cells[2]):
            if ("/" not in owner and not owner.endswith((".py", ".md"))) or any(char in owner for char in "*?[]") or "://" in owner:
                continue
            target = root / owner
            try:
                target.resolve().relative_to(root)
            except ValueError:
                problems.append(CapabilityProblem(line_number, "runtime owner path leaves repository", owner))
                continue
            if not target.exists():
                problems.append(CapabilityProblem(line_number, "missing runtime owner path", owner))
            elif not _path_has_exact_case(root, target):
                problems.append(CapabilityProblem(line_number, "runtime owner path case mismatch", owner))

    documented: dict[str, tuple[str, str, int]] = {}
    documented_order: list[str] = []
    for line_number, line in command_lines:
        cells = _cells(line)
        if not cells:
            continue
        if len(cells) != 3:
            problems.append(CapabilityProblem(line_number, "command row must have 3 fields", line))
            continue
        name = _code(cells[0])
        classification = cells[1]
        capability_id = _code(cells[2])
        if not name.startswith("/"):
            problems.append(CapabilityProblem(line_number, "invalid command root", cells[0]))
            continue
        if name in documented:
            problems.append(CapabilityProblem(line_number, "duplicate command root", name))
            continue
        documented[name] = (classification, capability_id, line_number)
        documented_order.append(name)
        if classification not in _COMMAND_CLASSES:
            problems.append(CapabilityProblem(line_number, "invalid command class", classification))
        if capability_id not in capabilities:
            problems.append(CapabilityProblem(line_number, "unknown capability reference", capability_id))

    specs = tuple(command_specs) if command_specs is not None else _default_command_specs()
    expected_order = [str(getattr(spec, "name", "")) for spec in specs]
    expected = {str(getattr(spec, "name", "")): _expected_command_class(spec) for spec in specs}
    for name in expected_order:
        if name not in documented:
            problems.append(CapabilityProblem(1, "command missing from capability contract", name))
            continue
        actual_class, _, line_number = documented[name]
        if actual_class != expected[name]:
            problems.append(
                CapabilityProblem(
                    line_number,
                    f"command class mismatch; expected {expected[name]}",
                    name,
                )
            )
    for name, (_, _, line_number) in documented.items():
        if name not in expected:
            problems.append(CapabilityProblem(line_number, "unknown command root", name))
    if documented_order and documented_order != expected_order:
        problems.append(CapabilityProblem(1, "command rows must follow command-registry order"))

    for discovery_path in (root / "README.md", root / "MAP.md"):
        try:
            discovery_text = discovery_path.read_text(encoding="utf-8")
        except OSError:
            problems.append(CapabilityProblem(1, "missing capability discovery authority", discovery_path.name))
            continue
        if "(CAPABILITIES.md)" not in discovery_text:
            problems.append(CapabilityProblem(1, "public document does not link capability contract", discovery_path.name))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    problems = check_capability_contract(args.root)
    if problems:
        print(f"[capabilities] {len(problems)} contract problem(s)")
        for problem in problems:
            print(problem.render())
        return 1
    print("[capabilities] product ledger and command coverage valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
