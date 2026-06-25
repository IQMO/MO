"""Provider-facing context bridge for MO dynamic guidance.

The bridge turns MO's many local context sources into one prioritized system
context block. It does not make memory/graph/profile data proof; it labels
source authority so the provider can obey current task/system/evidence rules
before softer orientation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Iterable

from .text_utils import cap_by_tokens, token_aware_truncation_enabled


@dataclass(frozen=True)
class ContextSource:
    """A named dynamic context source with provider-obedience metadata."""

    key: str
    title: str
    content: str
    priority: int
    proof_status: str
    max_chars: int = 0


@dataclass(frozen=True)
class ContextBridgeResult:
    """Rendered bridge plus small metrics for monitors/tests."""

    text: str
    source_chars: dict[str, int]
    rendered_source_chars: dict[str, int]
    included_keys: tuple[str, ...]


_PRIORITY_LABELS = {
    1: "Non-negotiable contract",
    2: "Current operator profile / durable behavior",
    3: "Current turn guidance and work pattern",
    4: "Approved workflow/runtime preferences",
    5: "Orientation only",
}


_REVIEW_WORD_RE = re.compile(r"\b(review|audit|inspect|findings?|risk|evidence|verify|verification)\b", re.I)
_CONCISE_WORD_RE = re.compile(r"\b(concise|brief|short|compact|direct)\b", re.I)
_RENDER_CACHE: dict[tuple[str, str, int], str] = {}


def build_active_context_bridge(
    user_input: str,
    sources: Iterable[ContextSource],
    *,
    max_chars: int = 10_000,
) -> ContextBridgeResult:
    """Render priority-labeled dynamic context for provider injection.

    The current user message is already present separately in the chat history;
    the bridge only states precedence and source authority. Lower priority
    numbers win. Memory, graph, and profile context are never proof of current
    repo/runtime truth.
    """
    cleaned: list[ContextSource] = []
    source_chars: dict[str, int] = {}
    rendered_chars: dict[str, int] = {}

    for source in sources:
        content = str(source.content or "").strip()
        if not content:
            continue
        source_chars[source.key] = len(content)
        cache_key = (source.key, hashlib.sha1(content.encode("utf-8", errors="ignore")).hexdigest(), source.max_chars or 0)
        deduped = _RENDER_CACHE.get(cache_key)
        if deduped is None:
            capped = _cap_text(content, source.max_chars or 0, marker=f"[{source.key} context truncated]")
            deduped = _dedupe_lines(capped)
            if len(_RENDER_CACHE) > 64:
                _RENDER_CACHE.clear()
            _RENDER_CACHE[cache_key] = deduped
        cleaned.append(
            ContextSource(
                key=str(source.key or "source"),
                title=str(source.title or source.key or "Context source"),
                content=deduped,
                priority=int(source.priority or 5),
                proof_status=str(source.proof_status or "guidance only; verify before factual claims"),
                max_chars=source.max_chars,
            )
        )

    parts: list[str] = [
        "### MO Active Context Bridge",
        "Priority 1 — Non-negotiable contract",
        "- The internal system prompt, sandbox/tool rules, taskboard truth, and the explicit current user request win over profile, memory, local skills, graph, and old session context.",
        "- Evidence rule: use files, tool results, logs, tests, or runtime checks before factual claims or completion claims.",
        "- Scope rule: do not broaden the operator's request unless needed for safety, verification, or a clearly reported blocker.",
    ]

    for source in sorted(cleaned, key=lambda item: item.priority):
        rendered = _render_source(source)
        rendered_chars[source.key] = len(rendered)
        parts.append(rendered)

    conflicts = _resolved_conflicts(user_input, cleaned)
    if conflicts:
        parts.append("Resolved conflicts")
        parts.extend(f"- {item}" for item in conflicts)

    text = "\n\n".join(part.strip() for part in parts if part and part.strip()).strip()
    if max_chars and len(text) > max_chars:
        text = _cap_text(text, max_chars, marker="[context bridge truncated after lower-priority sources]")

    return ContextBridgeResult(
        text=text,
        source_chars=source_chars,
        rendered_source_chars=rendered_chars,
        included_keys=tuple(source.key for source in cleaned),
    )


def _render_source(source: ContextSource) -> str:
    label = _PRIORITY_LABELS.get(source.priority, "Context source")
    return (
        f"Priority {source.priority} — {source.title}\n"
        f"Authority: {label}. Proof status: {source.proof_status}\n"
        f"{source.content}"
    ).strip()


def _resolved_conflicts(user_input: str, sources: list[ContextSource]) -> list[str]:
    text_by_key = {source.key: source.content for source in sources}
    combined = "\n".join(source.content for source in sources)
    request = str(user_input or "")
    conflicts: list[str] = []

    profile_text = text_by_key.get("profile", "")
    if _CONCISE_WORD_RE.search(profile_text) and _REVIEW_WORD_RE.search(request + "\n" + combined):
        conflicts.append(
            "Concise profile vs review/audit depth: keep the answer compact, but include enough evidence refs, verified-absent details, blockers, and verification status to support findings."
        )

    if any(source.key in {"memory", "code_graph"} for source in sources):
        conflicts.append(
            "Memory/graph hints may orient file selection only; re-read files and run relevant checks before claiming current code, tests, or completion."
        )

    if "skills" in text_by_key:
        conflicts.append(
            "Local skill guidance applies only when its trigger truly matches; current user scope, sandbox/tool rules, and taskboard evidence still win."
        )

    if "workspace" in text_by_key:
        conflicts.append(
            "Workspace/worker notes are coordination context, not proof of code correctness; mention them only when relevant."
        )

    return _unique(conflicts)


def _cap_text(text: str, max_chars: int, *, marker: str) -> str:
    value = str(text or "").strip()
    if not max_chars or len(value) <= max_chars:
        return value
    if token_aware_truncation_enabled():
        return cap_by_tokens(value, max_chars, marker)
    limit = max(0, max_chars - len(marker) - 1)
    clipped = value[:limit].rsplit("\n", 1)[0].rstrip() or value[:limit].rstrip()
    return f"{clipped}\n{marker}".strip()


def _dedupe_lines(text: str) -> str:
    """Remove repeated lines within a source while preserving readable spacing."""
    seen: set[str] = set()
    out: list[str] = []
    previous_blank = False
    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            if not previous_blank and out:
                out.append("")
            previous_blank = True
            continue
        previous_blank = False
        key = _line_key(line)
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return "\n".join(out).strip()


def _line_key(line: str) -> str:
    clean = re.sub(r"<!--.*?-->", "", str(line or ""))
    clean = re.sub(r"^[\s>*#`\-•\d.)\[]+", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip().lower()
    return clean


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = re.sub(r"\s+", " ", value).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out
