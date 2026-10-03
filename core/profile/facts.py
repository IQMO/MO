"""Safe, exact-ID lifecycle for durable operator facts."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..gates.threat_scan import scan_text
from ..state.paths import resolve_state_path
from ..utils.atomic_write import atomic_write_text
from ..utils.text_safety import contains_secret_value
from . import profile_transaction_lock


ALLOWED_CATEGORIES = frozenset({"server", "repo", "access", "credential", "deploy", "project", "preference"})
_FACT_RE = re.compile(r"^- \[([^\]]+)\]\s+(.+?)(?:\s+<!-- fact:[a-f0-9]{10} -->)?$")
_HEADER = (
    "# Operator Operational Facts (auto-captured)\n\n"
    "Durable facts the operator shared — servers, repos, access, deploy methods, "
    "project paths, credential LOCATIONS (never values). MO records these autonomously.\n"
)


@dataclass(frozen=True)
class ProfileFact:
    id: str
    category: str
    fact: str


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _fact_id(category: str, fact: str) -> str:
    normalized = f"{category.strip().lower()}\0{_clean(fact).strip(' .;:-').lower()}"
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _path(profile: Any = None, config: dict | None = None) -> tuple[Path, Path]:
    profile_db = str(getattr(profile, "_path", "") or "").strip()
    if profile_db:
        db = Path(profile_db)
        return db.parent / "profile" / "facts.md", db
    db = Path(resolve_state_path("memory/mo.db", config))
    return Path(resolve_state_path("memory/profile/facts.md", config)), db


def validate_profile_fact(category: str, fact: str, evidence: str = "") -> str:
    category = _clean(category).lower()
    fact = _clean(fact)
    evidence = _clean(evidence)
    if category not in ALLOWED_CATEGORIES:
        return "category must be one of: " + " | ".join(sorted(ALLOWED_CATEGORIES))
    if not fact:
        return "need a concrete one-line fact"
    if len(fact) > 200:
        return "keep it to one short line (<=200 chars)"
    if contains_secret_value(fact) or contains_secret_value(evidence):
        return "it contained a secret value; record only credential location/status"
    if re.search(r"(?:^|\s)#{1,6}\s|```|\b(?:system|assistant|user)\s*:|ignore (?:previous|prior|above)|disregard .*instruction|override .*instruction", fact, re.I):
        return "looks like instruction/markdown content, not a plain operational fact"
    if re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", fact) or re.search(r"\bssh://|\bssh\s+\S+@|@\d{1,3}(?:\.\d{1,3}){3}", fact, re.I):
        return "store a host alias/location/status, not a raw IP or SSH connection string"
    scan = scan_text(f"{fact}\n{evidence}", surface="profile fact")
    if scan.blocked:
        return f"failed the safety scan ({scan.reason()})"
    return ""


def list_profile_facts(*, profile: Any = None, config: dict | None = None) -> list[ProfileFact]:
    path, _db = _path(profile, config)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[ProfileFact] = []
    for line in lines:
        match = _FACT_RE.match(line.strip())
        if not match:
            continue
        category = _clean(match.group(1)).lower()
        fact = re.sub(r"\s+<!-- fact:[a-f0-9]{10} -->$", "", _clean(match.group(2)))
        out.append(ProfileFact(_fact_id(category, fact), category, fact))
    return out


def record_profile_fact(category: str, fact: str, evidence: str = "", *, profile: Any = None, config: dict | None = None) -> tuple[str, ProfileFact | None]:
    category, fact = _clean(category).lower(), _clean(fact)
    reason = validate_profile_fact(category, fact, evidence)
    if reason:
        return reason, None
    path, db = _path(profile, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = ProfileFact(_fact_id(category, fact), category, fact)
    with profile_transaction_lock(db):
        existing = path.read_text(encoding="utf-8") if path.exists() else _HEADER
        if any(item.id == entry.id for item in _parse_text(existing)):
            return "already recorded", entry
        line = f"- [{category}] {fact} <!-- fact:{entry.id} -->"
        atomic_write_text(path, existing.rstrip() + "\n" + line + "\n", encoding="utf-8")
    _invalidate(profile)
    return "recorded", entry


def update_profile_fact(fact_id: str, *, category: str, fact: str, profile: Any = None, config: dict | None = None) -> tuple[str, ProfileFact | None]:
    fact_id = _clean(fact_id).lower()
    category, fact = _clean(category).lower(), _clean(fact)
    reason = validate_profile_fact(category, fact)
    if reason:
        return reason, None
    path, db = _path(profile, config)
    with profile_transaction_lock(db):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return "not found", None
        replacement = ProfileFact(_fact_id(category, fact), category, fact)
        if replacement.id != fact_id and any(item.id == replacement.id for item in _parse_text("\n".join(lines))):
            return "duplicate", replacement
        changed = False
        for index, line in enumerate(lines):
            parsed = _parse_line(line)
            if parsed and parsed.id == fact_id:
                lines[index] = f"- [{category}] {fact} <!-- fact:{replacement.id} -->"
                changed = True
                break
        if not changed:
            return "not found", None
        atomic_write_text(path, "\n".join(lines).rstrip() + "\n", encoding="utf-8")
    _invalidate(profile)
    return "updated", replacement


def forget_profile_fact(fact_id: str, *, profile: Any = None, config: dict | None = None) -> bool:
    fact_id = _clean(fact_id).lower()
    path, db = _path(profile, config)
    with profile_transaction_lock(db):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        kept = [line for line in lines if not ((_parse_line(line)) and _parse_line(line).id == fact_id)]
        if len(kept) == len(lines):
            return False
        atomic_write_text(path, "\n".join(kept).rstrip() + "\n", encoding="utf-8")
    _invalidate(profile)
    return True


def _parse_line(line: str) -> ProfileFact | None:
    match = _FACT_RE.match(str(line or "").strip())
    if not match:
        return None
    category = _clean(match.group(1)).lower()
    fact = re.sub(r"\s+<!-- fact:[a-f0-9]{10} -->$", "", _clean(match.group(2)))
    return ProfileFact(_fact_id(category, fact), category, fact)


def _parse_text(text: str) -> list[ProfileFact]:
    return [entry for line in str(text or "").splitlines() if (entry := _parse_line(line))]


def _invalidate(profile: Any) -> None:
    if profile is None:
        return
    profile._profile_cache_text = None
