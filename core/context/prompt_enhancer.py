"""Prompt enhancement helpers for MO input handoff.

Ctrl+E is an input-buffer rewrite only: it never sends, creates task truth, or
marks progress. The local preview corrects the operator's own text without canned
clauses; the provider-backed pass can then use bounded profile, workflow,
work-pattern, and current-project context for a genuine same-scope rewrite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils.text_utils import cap_text

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
        (r"\bfulyl\b", "fully"),
        (r"\bintergrated\b", "integrated"),
        (r"\brefrecnses\b", "references"),
        (r"\bsomthing\b", "something"),
        (r"\bdouplciating\b", "duplicating"),
        (r"\bover-egneirning\b", "over-engineering"),
        (r"\bcoppy\b", "copy"),
        (r"\btelmplate\b", "template"),
        (r"\bmicrcle\b", "miracle"),
        (r"\bhim lets\b", "let's"),
    )
)

_PREFIX_RE = re.compile(
    r"^\s*(?:hey\s+|hi\s+|mo\s*,?\s*|please\s+|pls\s+|can you\s+|could you\s+|would you\s+|i want you to\s+|i would like to\s+|i want to\s+|i want\s+|i need you to\s+|help me\s+)+",
    re.I,
)
_FILLER_RE = re.compile(r"\s+for me\b", re.I)
_SPACE_RE = re.compile(r"\s+")
_WINDOWS_ABS_PATH_RE = re.compile(r"(?i)\b[A-Z]:[\\/](?:[^\s\]\[(){}<>\"'`]+[\\/]?)+")
_PRIVATE_HOME_PATH_RE = re.compile(
    r"(?i)(?<![\w/\\])(?:~[/\\]\.mo|/(?:Users|home)/[^/\s]+/.mo)(?:[/\\][^\s\]\[(){}<>\"'`]*)*"
)
_ABS_UNIX_PRIVATE_PATH_RE = re.compile(
    r"(?i)(?<![\w/\\])/(?:Users|home|root|opt|srv|var|mnt|Volumes)(?:/[^\s\]\[(){}<>\"'`]*)+"
)
_PRIVATE_HOST_TARGET_RE = re.compile(r"(?i)\b[\w.-]+@(?:\d{1,3}(?:\.\d{1,3}){3}|[\w.-]+\.[a-z]{2,})\b")
_CURRENT_OPERATOR_RE = re.compile(r"(?im)^(Current operator:\s*).+$")
_QUESTION_START_RE = re.compile(
    r"^(?:what|where|why|when|who|how|is|are|was|were|do|does|did|can|could|should|would)\b",
    re.I,
)
_CASE_FIXES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.I), replacement)
    for pattern, replacement in (
        (r"\bim\b", "I'm"),
        (r"\bi'm\b", "I'm"),
        (r"\bive\b", "I've"),
        (r"\bi've\b", "I've"),
        (r"\bid\b", "I'd"),
        (r"\bi'd\b", "I'd"),
        (r"\bi\b", "I"),
        (r"\bdoesnt\b", "doesn't"),
        (r"\bdont\b", "don't"),
        (r"\bcant\b", "can't"),
        (r"\bwont\b", "won't"),
        (r"\bdidnt\b", "didn't"),
        (r"\bisnt\b", "isn't"),
        (r"\barent\b", "aren't"),
        (r"\bwasnt\b", "wasn't"),
        (r"\bwerent\b", "weren't"),
        (r"\bctrl\s*\+\s*e\b", "Ctrl+E"),
        (r"\bctrl[-\s]*e\b", "Ctrl+E"),
        (r"\bmo\b", "MO"),
        (r"\bcopy pasted\b", "copy-pasted"),
        (r"\btask\s+board\b", "taskboard"),
    )
)


@dataclass(frozen=True)
class PromptProfileGuidance:
    direct: bool = False
    concise: bool = False
    evidence_first: bool = False
    preserve_scope: bool = False
    avoid_clarifying: bool = False
    anti_overengineering: bool = False


def _looks_non_english(text: str) -> bool:
    """True when the text is mostly non-ASCII letters (e.g. Arabic).

    The English typo/prefix/filler rules and keyword templates would corrupt or
    ignore non-Latin input, so callers skip them and preserve the language as-is.
    """
    letters = [c for c in str(text or "") if c.isalpha()]
    if not letters:
        return False
    non_ascii = sum(1 for c in letters if ord(c) > 127)
    return non_ascii >= max(1, len(letters) // 2)


def clean_prompt_text(text: str) -> str:
    """Return typo-corrected operator text without broadening scope."""
    value = str(text or "").strip()
    if _looks_non_english(value):
        # Preserve the operator's language verbatim; only normalize whitespace.
        return _SPACE_RE.sub(" ", value).strip()
    for pattern, replacement in _TYPO_FIXES:
        value = pattern.sub(replacement, value)
    value = _PREFIX_RE.sub("", value)
    value = _FILLER_RE.sub("", value)
    return _SPACE_RE.sub(" ", value).strip(" .")


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
    # behavior.md is the active compact projection; learning.md is the event
    # ledger and is read only for explicit learning/profile inspection.
    for name in ("operator.md", "thinking_model.md", "terms.md", "behavior.md"):
        try:
            text = (pdir / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            if name == "behavior.md":
                from ..profile import active_behavior_rules_excerpt

                active_rules = active_behavior_rules_excerpt(text, max_chars=1200)
                parts.append(active_rules or text[:1200])
            else:
                parts.append(text[:1200])
    return "\n".join(parts)


def _cap_text(text: str, max_chars: int) -> str:
    return cap_text(text, max_chars, marker="[context truncated]")


def sanitize_prompt_enhancement_context(text: str) -> str:
    """Remove local-private identifiers from provider prompt-enhancement context."""
    value = str(text or "")
    try:
        from ..tooling.sandbox import redact_sensitive_text

        value = redact_sensitive_text(value)
    except Exception:
        pass
    value = _CURRENT_OPERATOR_RE.sub(r"\1[private profile]", value)
    value = _PRIVATE_HOME_PATH_RE.sub("[private path]", value)
    value = _ABS_UNIX_PRIVATE_PATH_RE.sub("[private path]", value)
    value = _PRIVATE_HOST_TARGET_RE.sub("[private host]", value)
    return _WINDOWS_ABS_PATH_RE.sub("[private path]", value)


def _guidance_flags(guidance: PromptProfileGuidance) -> list[str]:
    flags: list[str] = []
    if guidance.direct:
        flags.append("direct")
    if guidance.concise:
        flags.append("concise")
    if guidance.evidence_first:
        flags.append("evidence-first")
    if guidance.preserve_scope:
        flags.append("preserve requested scope")
    if guidance.avoid_clarifying:
        flags.append("avoid clarifying questions unless blocked")
    if guidance.anti_overengineering:
        flags.append("avoid over-engineering and duplicate mechanisms")
    return flags


def _relevant_profile_shorthand(profile: Any, user_input: str, *, limit: int = 8) -> list[str]:
    """Return profile term labels already present in the draft, without definitions."""
    matcher = getattr(profile, "matching_term_definitions", None)
    if not callable(matcher):
        return []
    try:
        matches = matcher(user_input, limit=limit)
    except Exception:
        return []
    return [term for term, _definition in matches]


def _safe_profile_enhancement_context(profile: Any, user_input: str) -> str:
    """Provider-safe profile guidance: derived flags only, no profile excerpts."""
    if profile is None:
        return ""
    lines: list[str] = []
    flags = _guidance_flags(profile_prompt_guidance(profile))
    if flags:
        lines.append("Style preferences: " + ", ".join(flags) + ".")
    terms = _relevant_profile_shorthand(profile, user_input)
    if terms:
        lines.append("Preserve operator shorthand exactly when present: " + ", ".join(terms) + ".")
    return "\n".join(lines)


def _context_section(title: str, body: str, *, max_chars: int) -> str:
    text = _cap_text(body, max_chars)
    return f"### {title}\n{text}" if text else ""


def build_prompt_enhancement_context(
    profile: Any,
    user_input: str,
    *,
    project_cwd: str | Path | None = None,
    max_chars: int = 3600,
) -> str:
    """Bundle bounded profile, work-pattern, and project guidance.

    Raw profile excerpts, project paths, and deploy details stay out. Project
    guidance reuses the canonical project-context owner and is treated as
    untrusted orientation for preserving local terminology and conventions.
    """
    sections: list[str] = []
    section = _context_section(
        "Operator Guidance Context",
        _safe_profile_enhancement_context(profile, user_input),
        max_chars=700,
    )
    if section:
        sections.append(section)
    try:
        from .work_patterns import build_work_pattern_context

        section = _context_section(
            "MO Work Pattern Context",
            build_work_pattern_context(user_input),
            max_chars=900,
        )
        if section:
            sections.append(section)
    except Exception:
        pass
    if project_cwd:
        try:
            from .project_context import build_project_context

            project_text = build_project_context(project_cwd, max_chars=1000)
            project_root = str(Path(project_cwd).expanduser().resolve(strict=False))
            if project_root:
                project_text = re.sub(
                    re.escape(project_root),
                    "[private path]",
                    project_text,
                    flags=re.IGNORECASE,
                )
            section = _context_section(
                "Current Project Context (untrusted orientation)",
                project_text,
                max_chars=1000,
            )
            if section:
                sections.append(section)
        except Exception:
            pass
    return _cap_text(sanitize_prompt_enhancement_context("\n\n".join(sections)), max_chars)


def _sentence_case(text: str) -> str:
    value = str(text or "")
    chars = list(value)
    capitalize_next = True
    for index, char in enumerate(chars):
        if char.isalpha() and capitalize_next:
            chars[index] = char.upper()
            capitalize_next = False
        elif char in ".?!":
            capitalize_next = True
        elif not char.isspace() and char not in "\"'([{":
            capitalize_next = False
    return "".join(chars)


def _shape_prompt_text(text: str) -> str:
    value = _SPACE_RE.sub(" ", str(text or "")).strip()
    value = re.sub(r"\s+([,.;:?!])", r"\1", value)
    value = re.sub(r"([,.;:?!])(?=\S)", r"\1 ", value)
    for pattern, replacement in _CASE_FIXES:
        value = pattern.sub(replacement, value)
    value = _sentence_case(value)
    if value and value[-1] not in ".?!":
        value += "?" if _QUESTION_START_RE.match(value) else "."
    return value


def enhance_prompt(text: str, profile: Any = None) -> str:
    """Return the instant, deterministic Ctrl+E preview/fallback.

    This pass corrects and shapes the operator's own text only. It deliberately
    avoids canned objective/scope/verification clauses: genuine expansion belongs
    to the provider rewrite, which can use current project context. ``profile`` is
    retained for API compatibility but no private profile text is needed here.
    """
    del profile
    clean = clean_prompt_text(str(text or "").strip())
    if not clean:
        return ""
    if _looks_non_english(clean):
        return clean
    return _shape_prompt_text(clean)
