"""The ``Skill`` dataclass and everything about parsing a skill file: frontmatter
splitting, the activation contract, mastery-counter frontmatter edits, and the
in-memory dedupe/scoring helpers that operate on parsed ``Skill`` values.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._util import (
    _as_int,
    _backtick_values,
    _coerce_triggers,
    _first_heading,
    _meaningful_words,
    _normalize_trigger,
    _parse_scalar,
    _unquote,
)

@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    triggers: tuple[str, ...]
    body: str
    source: str
    provenance: str = "authored"
    scope: str = ""
    approval: str = ""
    mastery: dict[str, Any] = field(default_factory=dict)
    generated: bool = False
    candidate_id: str = ""
    # Role binding (optional): a skill that declares ``role`` can govern an
    # interactive conversation, background worker, or scheduled turn. Its body
    # supplies the role perspective; role_tools may scope MCP tools, while normal
    # Agent/sandbox rules remain authoritative for core tools and actuation.
    role: str = ""
    role_tools: tuple[str, ...] = ()
    role_lane: str = ""
    role_verify: str = ""
    project_root: str = ""


def _parse_skill(path: Path) -> Skill | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return None
    if not raw.strip():
        return None
    if path.name.lower() == "skill.md" and raw.lstrip().startswith("---"):
        meta_text, body = _split_frontmatter(raw)
        meta = _parse_frontmatter(meta_text)
        name = str(meta.get("name") or _first_heading(body) or path.parent.name.replace("-", " ")).strip()
        description = str(meta.get("description") or "").strip()
        triggers = _coerce_triggers(meta.get("triggers"))
        if not triggers:
            triggers = tuple(sorted(_meaningful_words(f"{name} {description}")))[:8]
        if not triggers:
            return None
        if _skill_contract_issues({**meta, "triggers": triggers}, body):
            return None
        mastery = {
            key: meta.get(key)
            for key in ("mastery_uses", "mastery_successes", "mastery_corrections", "last_used_at", "created_at")
            if key in meta
        }
        return Skill(
            name=name,
            description=description,
            triggers=triggers,
            body=body.strip(),
            source=str(path),
            provenance=str(meta.get("provenance") or "authored"),
            scope=str(meta.get("scope") or ""),
            approval=str(meta.get("approval") or ""),
            mastery=mastery,
            generated=bool(meta.get("candidate_id")),
            candidate_id=str(meta.get("candidate_id") or "").strip(),
            role=str(meta.get("role") or "").strip(),
            role_tools=_coerce_triggers(meta.get("role_tools")),
            role_lane=str(meta.get("role_lane") or "").strip(),
            role_verify=str(meta.get("role_verify") or "").strip(),
            project_root=str(meta.get("project_root") or "").strip(),
        )
    return None


def _iter_skill_files(roots: list[str | os.PathLike]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            base = Path(root).expanduser()
        except Exception:
            continue
        paths: list[Path] = []
        if base.is_file():
            paths = [base]
        elif base.is_dir():
            main = base / "SKILL.md"
            if main.exists():
                paths.append(main)
            paths.extend(
                sorted(
                    path
                    for path in base.glob("*/SKILL.md")
                    if not path.parent.name.casefold().endswith(".retired")
                )
            )
        for path in paths:
            key = str(path.resolve(strict=False)).casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
    return out


def _split_frontmatter(raw: str) -> tuple[str, str]:
    text = raw.lstrip()
    if not text.startswith("---"):
        return "", raw
    rest = text[3:].lstrip("\r\n")
    idx = rest.find("\n---")
    if idx < 0:
        return "", raw
    meta = rest[:idx]
    body = rest[idx + len("\n---"):].lstrip("\r\n")
    return meta.strip("\n"), body


def _parse_frontmatter(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_key = ""
    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if current_key and stripped.startswith("- "):
            data.setdefault(current_key, []).append(_unquote(stripped[2:].strip()))
            continue
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", stripped)
        if not match:
            current_key = ""
            continue
        key, value = match.group(1), match.group(2).strip()
        current_key = key
        if value == "":
            data[key] = []
        elif value.startswith("[") and value.endswith("]"):
            data[key] = [_unquote(item.strip()) for item in value[1:-1].split(",") if item.strip()]
        else:
            data[key] = _parse_scalar(value)
    return data


def _skill_contract_issues(meta: dict[str, Any], body: str) -> list[str]:
    triggers = set(_coerce_triggers(meta.get("triggers")))
    issues: list[str] = []
    if not triggers:
        issues.append("missing triggers")
        return issues
    exact, forbidden = _activation_contract_triggers(body)
    if exact:
        missing = sorted(exact - triggers)
        extra = sorted(triggers - exact)
        blocked = sorted(triggers & forbidden)
        if missing:
            issues.append("exact activation trigger missing: " + ", ".join(missing))
        if extra:
            issues.append("exact activation skill has extra triggers: " + ", ".join(extra))
        if blocked:
            issues.append("forbidden trigger listed: " + ", ".join(blocked))
    return issues


def _activation_contract_triggers(body: str) -> tuple[set[str], set[str]]:
    exact: set[str] = set()
    forbidden: set[str] = set()
    for raw in str(body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        has_exact_contract = (
            "activate only" in low
            or ("only when" in low and ("explicit" in low or "exact" in low) and ("trigger" in low or "write" in low or "type" in low))
        )
        has_forbidden = any(phrase in low for phrase in ("do not activate for", "don't activate for", "never activate for"))
        if not has_exact_contract and not has_forbidden:
            continue
        if has_forbidden:
            before, after = _split_forbidden_activation(line)
        else:
            before, after = line, ""
        if has_exact_contract:
            exact.update(_normalize_trigger(item) for item in _backtick_values(before) if _normalize_trigger(item))
        if has_forbidden:
            forbidden.update(_normalize_trigger(item) for item in _backtick_values(after) if _normalize_trigger(item))
    exact -= forbidden
    return exact, forbidden


def _split_forbidden_activation(line: str) -> tuple[str, str]:
    match = re.search(r"(?i)\b(?:do not|don't|never)\s+activate\s+for\b", line)
    if not match:
        return line, ""
    return line[:match.start()], line[match.end():]


def _bump_frontmatter_int(text: str, field: str) -> str:
    return _set_frontmatter_int(text, field, _frontmatter_int(text, field) + 1)


def _set_frontmatter_int(text: str, field: str, value: int) -> str:
    if not text.lstrip().startswith("---"):
        return text
    pattern = re.compile(rf"^({re.escape(field)}:\s*)-?\d+\s*$", re.M)
    if pattern.search(text):
        return pattern.sub(rf"\g<1>{int(value)}", text, count=1)
    idx = text.find("\n---", 3)
    if idx < 0:
        return text
    return text[:idx] + f"\n{field}: {int(value)}" + text[idx:]


def _frontmatter_int(text: str, field: str) -> int:
    match = re.search(rf"^{re.escape(field)}:\s*(-?\d+)\s*$", str(text or ""), flags=re.M)
    return _as_int(match.group(1)) if match else 0


def skill_matches_project(skill: Skill, project_cwd: str | None = None) -> bool:
    """Global packs remain global; a bound pack belongs to one canonical project."""
    if not skill.project_root:
        return True
    from ..graph.structural_graph import project_root

    return Path(skill.project_root).expanduser().resolve() == project_root(project_cwd)


def _dedupe_skills(skills: list[Skill]) -> list[Skill]:
    out: list[Skill] = []
    seen_names: set[tuple[str, str]] = set()
    seen_confirmed_rules: set[str] = set()
    for skill in skills:
        name_key = (skill.name.casefold(), os.path.normcase(skill.project_root))
        rule_key = (
            " ".join(skill.description.casefold().split())
            if skill.generated and skill.provenance == "confirmed-learning"
            else ""
        )
        if name_key in seen_names or (rule_key and rule_key in seen_confirmed_rules):
            continue
        seen_names.add(name_key)
        if rule_key:
            seen_confirmed_rules.add(rule_key)
        out.append(skill)
    return out


def _mastery_bonus(skill: Skill) -> int:
    successes = _as_int(skill.mastery.get("mastery_successes"))
    corrections = _as_int(skill.mastery.get("mastery_corrections"))
    if successes <= 0 and corrections <= 0:
        return 0
    return max(-2, min(3, successes - corrections))
