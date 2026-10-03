"""Migration coverage plan — the approval-gate report.

A read-only dry preview across every asset class: what WOULD move, what needs
re-auth, and what CANNOT move. Reads only presence/counts (not full content), so
it is cheap and safe to show before the operator approves the write.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .registry import known_source_keys, resolve_source


@dataclass
class Coverage:
    ok: bool
    label: str = ""
    home: str = ""
    will_move: list[str] = field(default_factory=list)
    needs_input: list[str] = field(default_factory=list)
    reauth: list[str] = field(default_factory=list)
    cannot_move: list[str] = field(default_factory=list)
    message: str = ""


def build_coverage(name: str, *, home_override: str | None = None, project_cwd: str | None = None) -> Coverage:
    agent = resolve_source(name)
    if agent is None:
        return Coverage(False, message=(
            f"No migration adapter for '{name}'. Known sources: {', '.join(known_source_keys())}."
        ))
    home = agent.resolve_home(home_override)
    if home is None:
        return Coverage(False, label=agent.label, message=(
            f"No {agent.label} data found. Tell me the source path if it's elsewhere."
        ))

    cov = Coverage(True, label=agent.label, home=str(home))
    has_rules = False
    for sf in agent.files:
        present = (home / sf.rel).is_file()
        if sf.kind == "rules":
            has_rules = has_rules or present
            continue
        if present:
            cov.will_move.append(f"{sf.rel} -> {sf.maps_to}")
    if has_rules:
        if project_cwd:
            cov.will_move.append("AGENTS.md -> this project's AGENTS.md (merged, never overwritten)")
        else:
            cov.needs_input.append("rules: open MO inside the target project so I can merge its AGENTS.md")
    # Skills
    if agent.skills_dir and (home / agent.skills_dir).is_dir():
        count = sum(1 for p in (home / agent.skills_dir).rglob("*")
                    if p.is_file() and p.suffix.lower() in {".md", ".txt"})
        if count:
            cov.will_move.append(f"{count} skill file(s) -> skills/{agent.key}-imports/ (untrusted, you promote)")
    # Provider + secrets are reference/re-auth, never copied.
    cov.reauth.append(agent.provider_note or "provider API keys — set in ~/.mo/credentials/providers.env (reference, never copied)")
    cov.reauth.extend(agent.reauth)
    cov.cannot_move.extend(agent.unsupported)
    return cov


def render_coverage(cov: Coverage) -> str:
    if not cov.ok:
        return cov.message or "Nothing to plan."
    lines = [
        f"Migration plan — {cov.label} ({cov.home}).",
        "Approve this and I'll write it; nothing is written until you say go.",
        "",
        "Will move into MO:",
    ]
    lines += [f"- {item}" for item in cov.will_move] or ["- (nothing found to move)"]
    if cov.needs_input:
        lines += ["", "Needs a bit from you first:"]
        lines += [f"- {item}" for item in cov.needs_input]
    if cov.reauth:
        lines += ["", "Re-auth in MO (never copied — use the matching scoped file under ~/.mo/credentials/):"]
        lines += [f"- {item}" for item in cov.reauth]
    if cov.cannot_move:
        lines += ["", "Cannot move (declared, not silently dropped):"]
        lines += [f"- {item}" for item in cov.cannot_move]
    lines += [
        "",
        "Say 'go' (or name the parts you want) to apply. After a full move, restart MO so it "
        "reinitializes with the migrated profile and connections.",
    ]
    return "\n".join(lines)
