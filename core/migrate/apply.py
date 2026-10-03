"""Tier-2/3 migration writes.

Every write is safe by construction:
- append-only with a provenance marker -> idempotent, never clobbers existing
  files (re-running skips what is already imported);
- source text is scanned first; any file carrying a secret is SKIPPED, not
  written (secret VALUES are never copied into MO);
- MO state resolves through ``resolve_state_path`` (never a bare cwd path);
- provider credentials are referenced, never copied.

Nothing here runs automatically. The ``migrate`` tool's ``apply`` action calls
it only after the operator has opted in and approved the plan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .registry import SourceAgent, resolve_source

ASSETS = ("profile", "rules", "skills", "provider")


@dataclass
class ApplyResult:
    ok: bool
    label: str = ""
    asset: str = ""
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    message: str = ""


def _state_path(rel: str) -> Path:
    from core.state.paths import resolve_state_path
    return Path(resolve_state_path(rel))


def _read(home: Path, rel: str, cap: int = 8000) -> str:
    p = home / rel
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:cap] if p.is_file() else ""
    except OSError:
        return ""


def _has_secret(text: str) -> bool:
    """True if the untrusted text carries a BLOCK finding (e.g. a secret value)."""
    from core.skills.importing.risk import scan_source_text
    return scan_source_text(text, surface="migration_import").has_block


def _append_section(path: Path, source_label: str, title: str, body: str) -> bool:
    """Append a provenance-marked section. Returns False if already present."""
    from core.utils.atomic_write import atomic_write_text
    marker = f"<!-- migrated:{source_label}:{title} -->"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    base = existing.rstrip() if existing.strip() else f"# {path.stem}"
    section = f"\n\n## Imported from {source_label} — {title}\n{marker}\n\n{body.strip()}\n"
    atomic_write_text(path, base + section, encoding="utf-8")
    return True


def _migrate_profile(agent: SourceAgent, home: Path, result: ApplyResult) -> None:
    for sf in agent.files:
        if sf.kind not in {"identity", "persona", "memory"}:
            continue
        text = _read(home, sf.rel)
        if not text.strip():
            result.skipped.append(f"{sf.rel} (not present)")
            continue
        if _has_secret(text):
            result.skipped.append(f"{sf.rel} (contained a secret — redact and re-import)")
            continue
        dest = _state_path(f"memory/profile/{sf.target or 'operator.md'}")
        if _append_section(dest, agent.label, sf.rel, text):
            result.done.append(f"{sf.rel} -> memory/profile/{sf.target}")
        else:
            result.skipped.append(f"{sf.rel} (already imported)")


def _migrate_rules(agent: SourceAgent, home: Path, result: ApplyResult, project_cwd: str | None) -> None:
    rules = [sf for sf in agent.files if sf.kind == "rules"]
    if not rules:
        result.notes.append("no rules file to migrate")
        return
    if not project_cwd:
        result.skipped.append("rules (no active project path — open MO in the target project to import AGENTS.md)")
        return
    dest = Path(project_cwd).expanduser() / "AGENTS.md"
    for sf in rules:
        text = _read(home, sf.rel)
        if not text.strip():
            result.skipped.append(f"{sf.rel} (not present)")
            continue
        if _has_secret(text):
            result.skipped.append(f"{sf.rel} (contained a secret — redact and re-import)")
            continue
        if _append_section(dest, agent.label, sf.rel, text):
            result.done.append(f"{sf.rel} -> {dest}")
        else:
            result.skipped.append(f"{sf.rel} (already imported)")


def _migrate_skills(agent: SourceAgent, home: Path, result: ApplyResult) -> None:
    if not agent.skills_dir:
        result.notes.append("no skills directory known for this source")
        return
    src = home / agent.skills_dir
    if not src.is_dir():
        result.skipped.append(f"{agent.skills_dir}/ (no skills found)")
        return
    dest_dir = _state_path(f"skills/{agent.key}-imports")
    dest_dir.mkdir(parents=True, exist_ok=True)
    from core.utils.atomic_write import atomic_write_text
    count = 0
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _has_secret(text):
            result.skipped.append(f"skills/{path.name} (contained a secret)")
            continue
        out = dest_dir / path.name
        if out.exists():
            result.skipped.append(f"skills/{path.name} (already imported)")
            continue
        atomic_write_text(out, text, encoding="utf-8")
        count += 1
    if count:
        result.done.append(f"{count} skill file(s) -> skills/{agent.key}-imports/ (untrusted, pending your promotion)")
        result.notes.append("imported skills are untrusted and NOT active — review, then promote the ones you want")


def _migrate_provider(agent: SourceAgent, home: Path, result: ApplyResult) -> None:
    # Reference only: never copy a key value. Record where the keys live and that
    # they must be re-entered/relocated into the scoped provider credential file.
    note = agent.provider_note or "put each provider API key in ~/.mo/credentials/providers.env (reference, never copied)"
    facts = _state_path("memory/profile/facts.md")
    line = f"migrating from {agent.label}: {note}"
    if _append_section(facts, agent.label, "provider", line):
        result.done.append("recorded provider key locations (reference only) in memory/profile/facts.md")
    else:
        result.skipped.append("provider note (already recorded)")
    result.notes.append("provider keys are referenced, never copied — set them in ~/.mo/credentials/providers.env, then MO reads them")


def apply_asset(name: str, asset: str, *, home_override: str | None = None, project_cwd: str | None = None) -> ApplyResult:
    """Apply one asset class (or 'all') for a named source. Safe + idempotent."""
    agent = resolve_source(name)
    if agent is None:
        return ApplyResult(False, asset=asset, message=f"No migration adapter for '{name}'.")
    home = agent.resolve_home(home_override)
    if home is None:
        return ApplyResult(False, label=agent.label, asset=asset,
                           message=f"No {agent.label} data found. Tell me the source path if it's elsewhere.")
    asset = str(asset or "").strip().lower()
    result = ApplyResult(True, label=agent.label, asset=asset)
    targets = ASSETS if asset in {"all", ""} else (asset,)
    unknown = [a for a in targets if a not in ASSETS]
    if unknown:
        return ApplyResult(False, label=agent.label, asset=asset,
                           message=f"Unknown asset '{unknown[0]}'. Choose from: {', '.join(ASSETS)} (or 'all').")
    for a in targets:
        if a == "profile":
            _migrate_profile(agent, home, result)
        elif a == "rules":
            _migrate_rules(agent, home, result, project_cwd)
        elif a == "skills":
            _migrate_skills(agent, home, result)
        elif a == "provider":
            _migrate_provider(agent, home, result)
    return result


def render_apply(result: ApplyResult) -> str:
    if not result.ok:
        return result.message or "Migration write failed."
    lines = [f"Migrated {result.asset} from {result.label}:"]
    lines += [f"- moved: {item}" for item in result.done] or ["- moved: nothing new"]
    if result.skipped:
        lines += [f"- skipped: {item}" for item in result.skipped]
    if result.notes:
        lines += [f"- note: {item}" for item in result.notes]
    lines.append(
        "Writes are append-only and idempotent; re-running is safe. Verify the result, then "
        "continue or restart MO to reinitialize with the migrated state."
    )
    return "\n".join(lines)
