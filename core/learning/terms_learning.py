"""Conservative operator-term learning for MO profile files.

Terms are definitions/orientation, not behavior rules. This module only records
explicit shorthand definitions from the operator, and never records prompt
instructions or secret-bearing values as terms.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..text_safety import contains_secret_value
from ..threat_scan import scan_text


@dataclass(frozen=True)
class TermDefinition:
    term: str
    definition: str


_TERM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bwhen\s+i\s+say\s+[`\"']?([^`\"'\n,;:]{2,40})[`\"']?\s*,?\s*(?:i\s+)?mean\s+[`\"']?([^`\"'\n]{3,180})",
        re.I,
    ),
    re.compile(
        r"\bwhen\s+i\s+use\s+(?:the\s+)?(?:term\s+|shorthand\s+)?[`\"']?([^`\"'\n,;:]{2,40})[`\"']?\s*,?\s*(?:it\s+)?means?\s+[`\"']?([^`\"'\n]{3,180})",
        re.I,
    ),
    re.compile(
        r"\bby\s+[`\"']?([^`\"'\n,;:]{2,40})[`\"']?\s+i\s+mean\s+[`\"']?([^`\"'\n]{3,180})",
        re.I,
    ),
    re.compile(
        r"\b(?:term|shorthand)\s+[`\"']?([^`\"'\n,;:]{2,40})[`\"']?\s+means?\s+[`\"']?([^`\"'\n]{3,180})",
        re.I,
    ),
)

_TERM_STOPWORDS = {
    "it", "this", "that", "these", "those", "you", "me", "i", "we", "they", "thing", "stuff", "work",
}


def extract_term_definitions(user_text: str) -> list[TermDefinition]:
    """Extract explicit operator shorthand definitions from user text."""
    text = str(user_text or "").strip()
    if not text:
        return []
    found: list[TermDefinition] = []
    seen: set[str] = set()
    for pattern in _TERM_PATTERNS:
        for match in pattern.finditer(text):
            term = _clean_term(match.group(1))
            definition = _clean_definition(match.group(2))
            if not _valid_definition(term, definition):
                continue
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(TermDefinition(term=term, definition=definition))
    return found


def record_terms_learning(profile: Any, user_text: str) -> list[str]:
    """Write explicit durable terms to memory/profile/terms.md.

    Returns the term names added or updated. Normal conversation and unsafe
    definitions return an empty list.
    """
    terms = extract_term_definitions(user_text)
    if not terms or not profile:
        return []
    profile_path = getattr(profile, "_path", None)
    if not profile_path:
        return []
    if hasattr(profile, "ensure_operator_profile"):
        try:
            profile.ensure_operator_profile()
        except Exception:
            return []

    terms_path = Path(profile_path).parent / "profile" / "terms.md"
    terms_path.parent.mkdir(parents=True, exist_ok=True)
    if not terms_path.exists():
        terms_path.write_text("# Operator Terms\n\n", encoding="utf-8")

    try:
        existing = terms_path.read_text(encoding="utf-8")
    except OSError:
        return []

    updated = existing.rstrip()
    if "## Learned Terms" not in updated:
        updated = updated + "\n\n## Learned Terms"

    changed: list[str] = []
    for item in terms:
        material = f"{item.term}: {item.definition}"
        scan = scan_text(material, surface="operator term")
        if scan.blocked or contains_secret_value(material):
            continue
        line = f"- **{item.term}** — {item.definition}"
        term_re = re.compile(rf"(?m)^- \*\*{re.escape(item.term)}\*\* — .*$", re.I)
        if term_re.search(updated):
            if line in updated:
                continue
            updated = term_re.sub(line, updated, count=1)
            changed.append(item.term)
            continue
        updated += "\n" + line
        changed.append(item.term)

    if not changed:
        return []
    try:
        terms_path.write_text(updated.rstrip() + "\n", encoding="utf-8")
        if hasattr(profile, "_profile_cache_mtimes"):
            profile._profile_cache_mtimes = None
        if hasattr(profile, "_profile_cache_text"):
            profile._profile_cache_text = None
    except OSError:
        return []
    return changed


def _clean_term(value: str) -> str:
    text = " ".join(str(value or "").strip().strip("`\"' .,:;()[]{}").split())
    return text[:40].strip()


def _clean_definition(value: str) -> str:
    text = " ".join(str(value or "").strip().strip("`\"' .,:;()[]{}").split())
    # Stop at common sentence continuations to avoid swallowing later instructions.
    text = re.split(r"\s+(?:and|but)\s+(?:do|don't|never|always|also)\b", text, maxsplit=1, flags=re.I)[0].strip()
    return text[:180].strip()


def _valid_definition(term: str, definition: str) -> bool:
    if not term or not definition:
        return False
    term_words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_+/-]*", term)
    if not term_words or len(term_words) > 5:
        return False
    if term.lower() in _TERM_STOPWORDS:
        return False
    if len(definition) < 3 or len(definition.split()) > 24:
        return False
    # Definitions are not behavior rules.
    if re.search(r"\b(ignore|override|bypass|disregard|forget)\b.{0,80}\b(system|developer|instruction|rules?)\b", definition, re.I):
        return False
    return True
