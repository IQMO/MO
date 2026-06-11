"""Deterministic prompt enhancement for MO input handoff.

This is intentionally local and small: it rewrites rough operator text into a
clearer prompt without sending it to a provider, changing task truth, or starting
work. The TUI can load the enhanced text back into the input buffer for explicit
operator approval before Enter sends it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TYPO_FIXES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.I), replacement)
    for pattern, replacement in (
        (r"\blgos\b", "logs"),
        (r"\bmeterics\b", "metrics"),
        (r"\bcomphernsive\b", "comprehensive"),
        (r"\bconfrim\b", "confirm"),
        (r"\bdelpoy\b", "deploy"),
        (r"\bfixses\b", "fixes"),
        (r"\btrulyl\b", "truly"),
        (r"\bturly\b", "truly"),
        (r"\basnwear\b", "answer"),
        (r"\badrseed\b", "addressed"),
        (r"\badressed\b", "addressed"),
        (r"\bcodebse\b", "codebase"),
        (r"\bcodenase\b", "codebase"),
        (r"\binvestiging\b", "investigating"),
        (r"\binstuctions\b", "instructions"),
        (r"\binstrcutions\b", "instructions"),
        (r"\bpelase\b", "please"),
        (r"\bverfiy\b", "verify"),
    )
)

_PREFIX_RE = re.compile(
    r"^\s*(?:mo\s*,?\s*|please\s+|pls\s+|can you\s+|could you\s+|would you\s+|i want you to\s+|i would like to\s+|i want to\s+|i want\s+|i need you to\s+|help me\s+)+",
    re.I,
)
_FILLER_RE = re.compile(r"\s+for me\b", re.I)
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class PromptProfileGuidance:
    direct: bool = False
    concise: bool = False
    evidence_first: bool = False
    preserve_scope: bool = False
    avoid_clarifying: bool = False
    anti_overengineering: bool = False


def clean_prompt_text(text: str) -> str:
    """Return typo-corrected operator text without broadening scope."""
    value = str(text or "").strip()
    for pattern, replacement in _TYPO_FIXES:
        value = pattern.sub(replacement, value)
    value = _PREFIX_RE.sub("", value)
    value = _FILLER_RE.sub("", value)
    value = _SPACE_RE.sub(" ", value).strip(" .")
    return value


def profile_prompt_guidance(profile: Any = None) -> PromptProfileGuidance:
    """Derive local prompt-shaping preferences from MO profile files."""
    text = _profile_text(profile).lower()
    return PromptProfileGuidance(
        direct=any(marker in text for marker in ("direct", "action over discussion", "start with the answer")),
        concise=any(marker in text for marker in ("concise", "short", "1-4 short lines", "not polished ai phrasing")),
        evidence_first=any(marker in text for marker in ("evidence-first", "evidence-backed", "verify current reality", "verify files", "runtime truth")),
        preserve_scope=any(marker in text for marker in ("preserve", "do not broaden", "goal frame", "anti-over-engineering", "simplest working")),
        avoid_clarifying=any(marker in text for marker in ("hates excessive clarifying", "ask only", "without asking")),
        anti_overengineering=any(marker in text for marker in ("anti-over-engineering", "simplest working", "not fragmented", "consolidated")),
    )


def _profile_text(profile: Any = None) -> str:
    pdir: Path | None = None
    profile_path = getattr(profile, "_path", None) if profile is not None else None
    if profile_path:
        candidate = Path(profile_path).parent / "profile"
        if candidate.exists():
            pdir = candidate
    if pdir is None:
        return ""

    parts: list[str] = []
    for name in ("operator.md", "thinking_model.md", "terms.md", "learning.md"):
        try:
            text = (pdir / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            parts.append(text[:1200])
    return "\n".join(parts)


def _profile_tail(guidance: PromptProfileGuidance) -> str:
    parts: list[str] = []
    if guidance.direct or guidance.concise:
        parts.append("keep the answer direct and concise")
    if guidance.preserve_scope:
        parts.append("do not broaden scope")
    if guidance.anti_overengineering:
        parts.append("prefer the smallest maintainable change")
    if guidance.avoid_clarifying:
        parts.append("ask only if blocked or risk changes")
    if not parts:
        return ""
    return ", ".join(parts)


def _append_profile_tail(sentence: str, guidance: PromptProfileGuidance) -> str:
    tail = _profile_tail(guidance)
    if not tail:
        return sentence
    return sentence.rstrip(" .") + f"; {tail}."


def enhance_prompt(text: str, profile: Any = None) -> str:
    """Create a clear prompt the operator can review before sending.

    The enhancer does not claim work, does not create task progress, and does not
    call the model. It only clarifies intent and appends MO's evidence-first
    constraints using local operator-profile guidance when available.
    """
    clean = clean_prompt_text(text)
    if not clean:
        return ""
    guidance = profile_prompt_guidance(profile)
    lowered = clean.lower()
    leading = clean[0].upper() + clean[1:]
    profile_tail = _profile_tail(guidance)
    profile_clause = f" Also {profile_tail}." if profile_tail else ""
    if all(word in lowered for word in ("logs", "metrics", "performance")):
        result = (
            "Audit logs, metrics, and performance comprehensively. Map all log stores, "
            "provider/tool/Ghost audit coverage, monitor events, token usage, latency, "
            "empty-response/stuck cases, and report concrete fixes with evidence."
        )
        return _append_profile_tail(result, guidance)
    if "codebase" in lowered and any(word in lowered for word in ("investigate", "review", "audit", "inspect", "scan")):
        return (
            f"{leading}. Inspect relevant codebase files first, "
            "verify findings with commands where useful, and report evidence, risks, blockers, and next fixes."
            f"{profile_clause}"
        )
    if any(word in lowered for word in ("build", "create", "implement", "add", "new", "fix", "debug", "write", "make")):
        return (
            f"{leading}. Keep scope tight, preserve the requested outcome, inspect existing context first, "
            "make the smallest safe change, verify it, and report evidence and blockers."
            f"{profile_clause}"
        )
    return (
        f"{leading}. Keep the scope tight, inspect relevant context first, "
        "verify before claiming completion, and report evidence, blockers, and unknowns."
        f"{profile_clause}"
    )
