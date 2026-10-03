"""Writing skill packs to disk: hand-authored packs, learned conventions, and
promotions from approved workflow candidates / confirmed learning suggestions.
Every write goes through ``write_skill_pack`` so the frontmatter contract and the
supporting-file safety checks are enforced in one place.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_text
from ._util import (
    _candidate_skill_body,
    _candidate_triggers,
    _meaningful_words,
    _normalize_trigger,
    _one_line,
    _safe_support_path,
    _scope_path_globs,
    _skill_name_from_candidate,
    _slug,
    _stable_id_suffix,
    _yaml_quote,
    skill_root_lock,
    skills_root,
)
from .model import _parse_frontmatter, _set_frontmatter_int, _skill_contract_issues, _split_frontmatter

_MAX_SOURCE_TEXT_CHARS = 12000


def _grounded_section(evidence: Any) -> str:
    """Render a learning's own turn evidence as a ``## Grounded in`` block.

    Auto-promoted safe learnings are BEHAVIORAL rules (evidence_first,
    clean_finish, communication_concise) — they make no code-behavior claim, so
    their honest grounding is the observed turns that justify them, not a
    fabricated file:line. Returns "" when the suggestion carries no evidence
    (never invent a citation)."""
    if not isinstance(evidence, (list, tuple)) or not evidence:
        return ""
    cites: list[str] = []
    for item in list(evidence)[:3]:
        if not isinstance(item, dict):
            continue
        turn_id = _one_line(str(item.get("turn_id") or ""), 60).strip()
        snippet = _one_line(str(item.get("snippet") or ""), 160).strip()
        if turn_id and snippet:
            cites.append(f"- {turn_id}: {snippet}")
        elif turn_id or snippet:
            cites.append(f"- {turn_id or snippet}")
    if not cites:
        return ""
    return "\n## Grounded in\n" + "\n".join(cites) + "\n"


def write_skill_pack(
    *,
    root: str | Path,
    name: str,
    description: str,
    triggers: tuple[str, ...] | list[str],
    body: str,
    provenance: str = "authored",
    approval: str = "",
    scope: str = "",
    candidate_id: str = "",
    source_kind: str = "",
    supporting_files: dict[str, str] | None = None,
    project_root: str = "",
    role: str = "",
    role_tools: tuple[str, ...] | list[str] | str = (),
    role_lane: str = "",
    role_verify: str = "",
) -> Path:
    """Write a SKILL.md pack and optional read-only supporting markdown files."""
    clean_name = _one_line(name, 90) or "MO Skill"
    slug = _slug(clean_name)
    root_path = Path(root).expanduser()
    trigger_values = tuple(dict.fromkeys(_normalize_trigger(t) for t in triggers if _normalize_trigger(t)))
    frontmatter = [
        "---",
        f"name: {_yaml_quote(clean_name)}",
        f"description: {_yaml_quote(_one_line(description, 220))}",
        "triggers:",
    ]
    frontmatter.extend(f"  - {_yaml_quote(item)}" for item in (trigger_values or (_normalize_trigger(clean_name),)))
    frontmatter.extend([
        f"provenance: {_yaml_quote(provenance)}",
        f"approval: {_yaml_quote(approval)}",
    ])
    if scope:
        frontmatter.append(f"scope: {_yaml_quote(scope)}")
    if project_root:
        project_root = Path(project_root).expanduser().resolve().as_posix()
        frontmatter.append(f"project_root: {_yaml_quote(project_root)}")
    role_id = _one_line(role, 90).strip()
    raw_role_tools = (role_tools,) if isinstance(role_tools, str) else tuple(role_tools or ())
    role_tool_values = tuple(dict.fromkeys(
        " ".join(str(item or "").split()).lower()[:160]
        for item in raw_role_tools
        if " ".join(str(item or "").split()).lower().startswith("mcp__")
    ))[:32]
    role_lane_value = _one_line(role_lane, 80).strip()
    role_verify_value = _one_line(role_verify, 400).strip()
    if (role_tool_values or role_lane_value or role_verify_value) and not role_id:
        raise ValueError("role metadata requires a non-empty role identifier")
    if role_id:
        frontmatter.append(f"role: {_yaml_quote(role_id)}")
    if role_tool_values:
        frontmatter.append("role_tools:")
        frontmatter.extend(f"  - {_yaml_quote(item)}" for item in role_tool_values)
    if role_lane_value:
        frontmatter.append(f"role_lane: {_yaml_quote(role_lane_value)}")
    if role_verify_value:
        frontmatter.append(f"role_verify: {_yaml_quote(role_verify_value)}")
    if candidate_id:
        frontmatter.append(f"candidate_id: {_yaml_quote(candidate_id)}")
    if source_kind:
        frontmatter.append(f"source_kind: {_yaml_quote(source_kind)}")
    frontmatter.extend([
        "mastery_uses: 0",
        "mastery_successes: 0",
        "mastery_corrections: 0",
        f"created_at: {int(time.time())}",
        "---",
        "",
    ])
    text = "\n".join(frontmatter) + str(body or "").strip() + "\n"
    issues = _skill_contract_issues(_parse_frontmatter("\n".join(frontmatter[1:-2])), str(body or ""))
    if issues:
        raise ValueError("invalid skill pack: " + "; ".join(issues))
    with skill_root_lock(root_path):
        dest = _skill_destination(root_path, slug, candidate_id, name=clean_name, project_root=project_root)
        existing_path = dest / "SKILL.md"
        if str(candidate_id or "").strip() and dest != root_path / slug:
            existing_name = _skill_name_at(existing_path)
            unique_name = existing_name or f"{clean_name} [{_stable_id_suffix(candidate_id)}]"
            text = _replace_frontmatter_value(text, "name", _yaml_quote(unique_name))
        if existing_path.exists():
            try:
                text = _preserve_mastery(text, existing_path.read_text(encoding="utf-8"))
            except OSError:
                pass
        atomic_write_text(existing_path, text, encoding="utf-8")
        for rel, content in (supporting_files or {}).items():
            safe_rel = _safe_support_path(rel)
            if not safe_rel:
                continue
            support_path = dest / safe_rel
            support_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(support_path, str(content or "")[:_MAX_SOURCE_TEXT_CHARS], encoding="utf-8")
    return dest / "SKILL.md"


def _skill_destination(root: Path, slug: str, candidate_id: str, *, name: str, project_root: str = "") -> Path:
    """Return a stable path without letting distinct candidates overwrite."""
    base = root / (f"{slug}-{_stable_id_suffix(project_root)}" if project_root else slug)
    clean_id = str(candidate_id or "").strip()
    matches: list[Path] = []
    if root.is_dir():
        for existing in sorted(root.glob("*/SKILL.md")):
            if existing.parent.name.casefold().endswith(".retired"):
                continue
            try:
                meta_text, _body = _split_frontmatter(existing.read_text(encoding="utf-8"))
            except OSError:
                continue
            meta = _parse_frontmatter(meta_text)
            bound = str(meta.get("project_root") or "")
            if (Path(bound).resolve() if bound else None) != (Path(project_root) if project_root else None):
                continue
            existing_id = str(meta.get("candidate_id") or "").strip()
            if clean_id and existing_id == clean_id:
                return existing.parent
            if not clean_id and not existing_id and str(meta.get("name") or "").strip().casefold() == name.casefold():
                matches.append(existing.parent)
    if len(matches) > 1:
        raise ValueError("ambiguous existing skill name; reconcile the exact source packs before writing")
    if matches:
        return matches[0]
    if not clean_id and (base / "SKILL.md").exists():
        raise ValueError("skill destination belongs to another pack; use its existing identity or a distinct name")
    if not clean_id or not (base / "SKILL.md").exists():
        return base
    existing_id = _candidate_id_at(base / "SKILL.md")
    if existing_id == clean_id:
        return base
    digest = _stable_id_suffix(clean_id, length=64)
    for width in (8, 12, 16, 24, 64):
        candidate = root / f"{slug}-{digest[:width]}"
        path = candidate / "SKILL.md"
        if not path.exists() or _candidate_id_at(path) == clean_id:
            return candidate
    raise ValueError("unable to allocate a unique skill-pack path")


def _candidate_id_at(path: Path) -> str:
    try:
        meta_text, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    return str(_parse_frontmatter(meta_text).get("candidate_id") or "").strip()


def _skill_name_at(path: Path) -> str:
    try:
        meta_text, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    return str(_parse_frontmatter(meta_text).get("name") or "").strip()


def _replace_frontmatter_value(text: str, field: str, rendered_value: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{field}:"):
            lines[index] = f"{field}: {rendered_value}"
            break
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _preserve_mastery(rendered: str, existing: str) -> str:
    meta_text, _body = _split_frontmatter(existing)
    meta = _parse_frontmatter(meta_text)
    for field in (
        "mastery_uses",
        "mastery_successes",
        "mastery_corrections",
        "last_used_at",
        "created_at",
    ):
        if field not in meta:
            continue
        try:
            rendered = _set_frontmatter_int(rendered, field, int(meta[field]))
        except (TypeError, ValueError):
            continue
    return rendered


def validate_skill_pack(path: str | Path) -> list[str]:
    """Return contract issues for a physical SKILL.md pack.

    The validator is intentionally focused on machine-checkable contracts. In
    particular, when a skill body says it activates only for an exact phrase,
    the frontmatter triggers must match that phrase and must not include broad
    aliases. This keeps hand-authored packs from over-firing at runtime.
    """
    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot read skill pack: {exc}"]
    if source.name.lower() != "skill.md":
        return ["skill pack must be named SKILL.md"]
    meta_text, body = _split_frontmatter(raw)
    if not meta_text:
        return ["missing SKILL.md frontmatter"]
    meta = _parse_frontmatter(meta_text)
    return _skill_contract_issues(meta, body)


def write_convention(
    *,
    name: str,
    rule: str,
    scope: str,
    evidence: str = "",
    confidence: str = "high",
    profile: Any | None = None,
    config: dict[str, Any] | None = None,
    project_cwd: str | None = None,
) -> Path:
    """Persist a project-bound convention in the existing private skill root.

    Evidence-gated AUTONOMY: MO writes this itself (no operator confirm) when it has a
    durable rule for a code area. But a convention is only durable with a SCOPE (file-globs)
    AND a rule; without a real path-glob scope it is just noise and is rejected. The
    autonomous twin of ``select_conventions_context`` - what MO writes here surfaces by
    location on later turns in this project. Storage stays private across runs."""
    from ..graph.structural_graph import project_root
    clean_rule = _one_line(rule, 500).strip()
    clean_scope = " ".join(str(scope or "").split()).strip()
    if not clean_rule or not _scope_path_globs(clean_scope):
        raise ValueError("convention requires a non-empty rule and a file-glob scope")
    body = clean_rule
    if evidence:
        body += f"\n\nEvidence: {_one_line(evidence, 400).strip()}"
    # The declared code location activates a convention. Arbitrary body words
    # (including negations) are not task triggers; keep explicit name lookup.
    triggers = (_normalize_trigger(_one_line(name, 90) or "MO convention"),)
    return write_skill_pack(
        root=skills_root(profile, config=config),
        name=_one_line(name, 90) or "MO convention",
        description=clean_rule[:180],
        triggers=triggers,
        body=body,
        provenance="learned-convention",
        approval=f"autonomous:{confidence}",
        scope=clean_scope,
        project_root=str(project_root(project_cwd)),
    )


def write_skill_pack_from_candidate(
    candidate: dict[str, Any],
    *,
    profile: Any | None = None,
    runtime_home: str | None = None,
    config: dict[str, Any] | None = None,
) -> Path:
    """Promote an approved candidate into a real local SKILL.md pack."""
    root = skills_root(profile, runtime_home=runtime_home, config=config)
    source_text = str(candidate.get("source_text") or "")
    body = _candidate_skill_body(candidate, include_reference=bool(source_text))
    supporting: dict[str, str] = {}
    if source_text:
        supporting["references/source.md"] = source_text
    path = write_skill_pack(
        root=root,
        name=_skill_name_from_candidate(candidate),
        description=_one_line(candidate.get("trigger") or candidate.get("behavior") or "", 220),
        triggers=_candidate_triggers(candidate),
        body=body,
        provenance=str(
            candidate.get("provenance")
            or candidate.get("source_kind")
            or "workflow-candidate"
        ),
        approval="explicit",
        candidate_id=str(candidate.get("id") or ""),
        source_kind=str(candidate.get("source_kind") or ""),
        supporting_files=supporting,
    )
    return path


def write_skill_pack_from_suggestion(
    suggestion: dict[str, Any],
    *,
    profile: Any | None = None,
    runtime_home: str | None = None,
    config: dict[str, Any] | None = None,
) -> Path:
    """Write a confirmed learning suggestion as a generated local skill pack."""
    kind = _one_line(suggestion.get("kind", "learning"), 80)
    recommendation = _one_line(suggestion.get("recommendation", ""), 500)
    approval = "automatic-safe" if suggestion.get("auto_promoted") else "explicit"
    root = skills_root(profile, runtime_home=runtime_home, config=config)
    triggers = tuple(sorted(_meaningful_words(f"{kind} {recommendation}")))[:10]
    grounded = _grounded_section(suggestion.get("evidence"))
    body = (
        "Use this learned skill only when it fits the current turn.\n\n"
        f"## Learned Behavior\n{recommendation}\n"
        f"{grounded}\n"
        "Current user scope, sandbox/tool rules, and taskboard evidence still win.\n"
    )
    path = write_skill_pack(
        root=root,
        name=f"Learned {kind.replace('_', ' ')}",
        description=recommendation[:220],
        triggers=triggers or (kind,),
        body=body,
        provenance="confirmed-learning",
        approval=approval,
        candidate_id=str(suggestion.get("id") or ""),
    )
    return path
