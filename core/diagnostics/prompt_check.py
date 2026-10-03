"""Validate MO's shared and surface-specific provider prompt contract."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re

_POLICY_START = "<!-- mo-prompt-policies:start -->"
_POLICY_END = "<!-- mo-prompt-policies:end -->"
_POLICY_RE = re.compile(r"^\[MO-POLICY:([A-Z][A-Z0-9_]*)\]\s+\S")
_REQUIRED_POLICIES = {
    "AUTHORITY",
    "CURRENT_SCOPE",
    "EVIDENCE",
    "IDENTITY",
    "INJECTION",
    "PRIVACY",
    "RUNTIME",
}
_MAX_SHARED_CHARS = 2_500
_MAX_TERMINAL_CHARS = 50_000
_MAX_DESKTOP_CHARS = 12_000
_TERMINAL_ONLY = ("`complete_task`", "`set_plan`", "When /goal")
_DESKTOP_ONLY = ("__MO_OPTIONS__", "small overlay bubble")
_OBSOLETE_TERMS = ("mo_monitor.py", "../core/claim_verification.py", "five-read allowlist")


@dataclass(frozen=True)
class PromptProblem:
    source: str
    reason: str
    detail: str = ""

    def render(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"{self.source}: {self.reason}{suffix}"


def _read(path: Path, problems: list[PromptProblem]) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        problems.append(PromptProblem(path.as_posix(), "missing prompt source", str(exc)))
        return ""


def _policy_ids(shared: str, problems: list[PromptProblem]) -> list[str]:
    lines = shared.splitlines()
    try:
        start = lines.index(_POLICY_START)
        end = lines.index(_POLICY_END, start + 1)
    except ValueError:
        problems.append(PromptProblem("core/prompts/shared.md", "missing policy markers"))
        return []
    ids: list[str] = []
    for line in lines[start + 1 : end]:
        if not line.strip():
            continue
        match = _POLICY_RE.match(line)
        if match is None:
            problems.append(PromptProblem("core/prompts/shared.md", "invalid policy row", line))
            continue
        ids.append(match.group(1))
    return ids


def check_prompt_contract(root: Path) -> list[PromptProblem]:
    """Return prompt ownership, parity, size, and stale-reference problems."""
    root = root.resolve()
    problems: list[PromptProblem] = []
    shared = _read(root / "core" / "prompts" / "shared.md", problems)
    terminal = _read(root / "core" / "prompts" / "system.md", problems)

    ids = _policy_ids(shared, problems)
    duplicates = sorted({policy_id for policy_id in ids if ids.count(policy_id) > 1})
    for policy_id in duplicates:
        problems.append(PromptProblem("core/prompts/shared.md", "duplicate policy id", policy_id))
    missing = sorted(_REQUIRED_POLICIES - set(ids))
    extra = sorted(set(ids) - _REQUIRED_POLICIES)
    for policy_id in missing:
        problems.append(PromptProblem("core/prompts/shared.md", "missing required policy", policy_id))
    for policy_id in extra:
        problems.append(PromptProblem("core/prompts/shared.md", "unregistered policy id", policy_id))

    try:
        from core.prompts.system_prompt import load_internal_system_prompt
        from mo_desktop.persona import MO_DESKTOP_PERSONA, mo_desktop_system_message

        composed_terminal = load_internal_system_prompt()
        composed_desktop = mo_desktop_system_message()
        desktop = MO_DESKTOP_PERSONA.strip()
    except Exception as exc:
        problems.append(PromptProblem("prompt composition", "could not load prompts", str(exc)))
        composed_terminal = composed_desktop = desktop = ""

    expected_prefix = f"{shared}\n\n" if shared else ""
    for source, composed in (
        ("Terminal composed prompt", composed_terminal),
        ("Desktop composed prompt", composed_desktop),
    ):
        if expected_prefix and not composed.startswith(expected_prefix):
            problems.append(PromptProblem(source, "shared kernel is not the exact leading source"))
        for policy_id in _REQUIRED_POLICIES:
            if composed.count(f"[MO-POLICY:{policy_id}]") != 1:
                problems.append(PromptProblem(source, "shared policy must appear exactly once", policy_id))

    for term in _DESKTOP_ONLY:
        if term in terminal:
            problems.append(PromptProblem("core/prompts/system.md", "Desktop policy leaked into Terminal source", term))
    for term in _TERMINAL_ONLY:
        if term in desktop:
            problems.append(PromptProblem("mo_desktop/persona.py", "Terminal policy leaked into Desktop persona", term))
    for source, text in (
        ("core/prompts/shared.md", shared),
        ("core/prompts/system.md", terminal),
        ("mo_desktop/persona.py", desktop),
    ):
        for term in _OBSOLETE_TERMS:
            if term in text:
                problems.append(PromptProblem(source, "obsolete prompt reference", term))

    for source, size, limit in (
        ("core/prompts/shared.md", len(shared), _MAX_SHARED_CHARS),
        ("core/prompts/system.md", len(terminal), _MAX_TERMINAL_CHARS),
        ("mo_desktop/persona.py", len(desktop), _MAX_DESKTOP_CHARS),
    ):
        if size > limit:
            problems.append(PromptProblem(source, f"prompt size {size} exceeds governed ceiling {limit}"))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    problems = check_prompt_contract(args.root)
    if problems:
        print(f"[prompts] {len(problems)} contract problem(s)")
        for problem in problems:
            print(problem.render())
        return 1
    print("[prompts] shared kernel, surface ownership, and size contract valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
