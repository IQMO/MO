"""Skills — local domain best-practice packs read before acting on a matching task.

A read-before-acting pattern: read the relevant best-practice pack before writing code
or running work. Kept deliberately minimal — no marketplace, no install flow, no public
`/skill` surface. A skill is just a markdown pack with a trigger line; when a turn
matches, the relevant pack(s) are injected into the context bridge so MO follows the
encoded best practice instead of rediscovering it.

Packs ship under the repo `skills/` dir and the operator may add more under
`~/.mo/skills`. Detail that is too long/situational for the always-on system prompt
lives here and loads only when relevant — keeping the prompt lean while deepening
quality on the tasks each pack covers.

File format (dependency-free parse):

    # Title
    description: one-line summary
    triggers: keyword, another phrase, regex-free terms
    ---
    <best-practice body>
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_MAX_BODY_CHARS = 1600
_GREETINGS = frozenset({
    "hi", "hello", "hey", "yo", "thanks", "thank you", "ok", "okay", "yes", "no",
})


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    triggers: tuple[str, ...]
    body: str
    source: str


def _parse_skill(path: Path) -> Skill | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return None
    if not raw.strip():
        return None
    header, _, body = raw.partition("\n---\n")
    if not body:
        # No explicit separator: treat the first paragraph as header lines.
        body = raw
    name = ""
    description = ""
    triggers: list[str] = []
    for line in header.splitlines():
        s = line.strip()
        if not name and s.startswith("# "):
            name = s[2:].strip()
        elif s.lower().startswith("description:"):
            description = s.split(":", 1)[1].strip()
        elif s.lower().startswith("triggers:"):
            triggers = [t.strip().lower() for t in s.split(":", 1)[1].split(",") if t.strip()]
    if not name:
        name = path.stem.replace("_", " ").replace("-", " ")
    if not triggers:
        return None  # untriggerable pack is never selectable; skip it
    return Skill(
        name=name,
        description=description,
        triggers=tuple(triggers),
        body=body.strip()[:_MAX_BODY_CHARS],
        source=str(path),
    )


def default_skill_roots(project_cwd: str | None = None, runtime_home: str | None = None) -> list[str]:
    """Shipped skills, profile-owned skills, and project-local skills."""
    roots: list[str] = [str(Path(__file__).resolve().parent.parent / "skills")]
    if runtime_home:
        roots.append(str(Path(runtime_home).expanduser() / "skills"))
    if project_cwd:
        roots.append(str(Path(project_cwd).expanduser() / "skills"))
    return roots


def load_skills(roots: list[str | os.PathLike]) -> list[Skill]:
    """Load all skill files from the given roots (project skills dir, ~/.mo/skills)."""
    skills: list[Skill] = []
    seen: set[str] = set()
    for root in roots:
        try:
            base = Path(root).expanduser()
        except Exception:
            continue
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if path.name.lower() in {"readme.md", "index.md"}:
                continue
            skill = _parse_skill(path)
            if skill and skill.name.lower() not in seen:
                seen.add(skill.name.lower())
                skills.append(skill)
    return skills


def should_include_skills(user_input: str) -> bool:
    text = str(user_input or "").strip().lower().strip("!.?")
    return bool(text) and text not in _GREETINGS


def _match_score(skill: Skill, text_lower: str) -> int:
    return sum(1 for t in skill.triggers if t and t in text_lower)


def select_skills_context(
    user_input: str,
    roots: list[str | os.PathLike],
    *,
    max_skills: int = 2,
    max_chars: int = 2200,
) -> str:
    """Return a formatted context block of the most relevant skill pack(s), or ''."""
    if not should_include_skills(user_input):
        return ""
    skills = load_skills(roots)
    if not skills:
        return ""
    text_lower = str(user_input or "").lower()
    scored = [(s, _match_score(s, text_lower)) for s in skills]
    matched = sorted([(s, sc) for s, sc in scored if sc > 0], key=lambda x: -x[1])[:max_skills]
    if not matched:
        return ""
    parts = ["### Relevant skill packs — follow these before acting on this task"]
    for skill, _sc in matched:
        head = f"\n**{skill.name}**" + (f" — {skill.description}" if skill.description else "")
        parts.append(head)
        parts.append(skill.body)
    out = "\n".join(parts).strip()
    if len(out) > max_chars:
        out = out[:max_chars].rsplit("\n", 1)[0] + "\n…"
    return out
