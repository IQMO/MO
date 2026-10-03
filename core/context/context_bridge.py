"""Provider-facing context bridge for MO dynamic guidance.

The bridge turns MO's many local context sources into one prioritized system
context block. It does not make memory/graph/profile data proof; it labels
source authority so the provider can obey current task/system/evidence rules
before softer orientation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import re
from typing import Iterable

from ..utils.text_utils import cap_text as _cap_text
from .text import unique_text


@dataclass(frozen=True)
class ContextSource:
    """A named dynamic context source with provider-obedience metadata."""

    key: str
    title: str
    content: str
    priority: int
    proof_status: str
    max_chars: int = 0
    records: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextBridgeResult:
    """Rendered bridge plus small metrics for monitors/tests."""

    text: str
    source_chars: dict[str, int]
    rendered_source_chars: dict[str, int]
    included_keys: tuple[str, ...]
    included_record_counts: dict[str, int]


_REVIEW_WORD_RE = re.compile(r"\b(review|audit|inspect|findings?|risk|evidence|verify|verification)\b", re.I)
_CONCISE_WORD_RE = re.compile(r"\b(concise|brief|short|compact|direct)\b", re.I)
_RENDER_CACHE: dict[tuple[str, str, int, int], str] = {}


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
    record_counts: dict[str, int] = {}

    for source in sources:
        content = str(source.content or "").strip()
        if not content:
            continue
        records = tuple(str(record).strip() for record in source.records if str(record).strip())
        source_chars[source.key] = len(_join_parts([content, *records]))
        cache_key = (source.key, hashlib.sha1(content.encode("utf-8", errors="ignore")).hexdigest(), source.max_chars or 0, source.priority)
        deduped = _RENDER_CACHE.get(cache_key)
        if deduped is None:
            # Skill instructions and discovery paths must remain complete.
            # Admit these blocks whole, including repeated scoped rules.
            if source.key in {"skills", "skill_catalog"}:
                deduped = content
            else:
                capped = _cap_text(content, source.max_chars or 0, marker=f"[{source.key} context truncated]")
                deduped = capped if source.priority <= 2 else _dedupe_lines(capped)
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
                records=records,
            )
        )

    parts: list[str] = [
        "### MO Active Context Bridge",
        "Priority 1 — Non-negotiable contract",
        "- The internal system prompt, sandbox/tool rules, taskboard truth, and the explicit current user request win over profile, memory, local skills, graph, and old session context.",
        "- Scope rule: stay within the operator's request and restrictions; verification and blockers never authorize broader work.",
    ]

    included: list[ContextSource] = []
    omitted: list[ContextSource] = []
    ordered = sorted(cleaned, key=lambda item: item.priority)
    # Authority order is not permission to consume the entire delivery budget.
    # Reserve a useful excerpt (or one complete record) for each later source
    # before expanding earlier guidance. Contracts and selected skills stay whole.
    minimum_sizes = []
    for source in ordered:
        if source.records:
            content = next((
                _join_parts([source.content, record]) for record in source.records
                if not source.max_chars or len(_join_parts([source.content, record])) <= source.max_chars
            ), _join_parts([source.content, *source.records]))
        elif source.priority == 1 or source.key in {"skills", "skill_catalog"}:
            content = source.content
        else:
            content = source.content[:512]
        minimum_sizes.append(len(_render_source(replace(source, content=content))) + 2)

    for index, source in enumerate(ordered):
        remaining = max(0, max_chars - len(_join_parts(parts))) if max_chars else 0
        allowance = remaining
        if max_chars and source.priority > 1 and source.key != "skills":
            allowance = min(remaining, max(minimum_sizes[index], remaining - sum(minimum_sizes[index + 1:])))
        source_limit = len(_join_parts(parts)) + allowance if max_chars else 0
        if source.key in {"skills", "skill_catalog"} and source.max_chars and len(source.content) > source.max_chars:
            omitted.append(source)
            continue
        if source.records:
            # Recall consists of ranked, independently useful records. Admit
            # complete excerpts in the space left by higher-priority sources,
            # rather than truncating one combined block or dropping all hits.
            admitted_records: list[str] = []
            for record in source.records:
                content = _join_parts([source.content, *admitted_records, record])
                if source.max_chars and len(content) > source.max_chars:
                    continue
                rendered = _render_source(replace(source, content=content))
                if source_limit and len(_join_parts([*parts, rendered])) > source_limit:
                    continue
                admitted_records.append(record)
            if not admitted_records:
                omitted.append(source)
                continue
            record_counts[source.key] = len(admitted_records)
            content = _join_parts([source.content, *admitted_records])
            missing = len(source.records) - len(admitted_records)
            if missing:
                marked = content + f"\n[{source.key}: {missing} record(s) omitted after budget]"
                if (
                    (not source.max_chars or len(marked) <= source.max_chars)
                    and (not source_limit or len(_join_parts([*parts, _render_source(replace(source, content=marked))])) <= source_limit)
                ):
                    content = marked
            source = replace(source, content=content)
        rendered = _render_source(source)
        candidate = _join_parts([*parts, rendered])
        if source_limit and len(candidate) > source_limit and source.priority > 1 and not source.records and source.key not in {"skills", "skill_catalog"}:
            # Admit a labeled excerpt of current task guidance when its whole
            # block exceeds its share. Contracts and selected skills stay
            # intact and source records remain independently atomic above.
            available = len(source.content) - (len(candidate) - source_limit)
            if available >= 256:
                source = replace(source, content=_cap_text(
                    source.content, available, marker=f"[{source.key} guidance excerpt; remainder omitted after budget]",
                ))
                rendered = _render_source(source)
                candidate = _join_parts([*parts, rendered])
        if source_limit and len(candidate) > source_limit:
            omitted.append(source)
            continue
        rendered_chars[source.key] = len(rendered)
        included.append(source)
        parts.append(rendered)

    conflicts = _resolved_conflicts(user_input, included)
    if conflicts:
        conflict_block = "\n".join(["Resolved conflicts", *(f"- {item}" for item in conflicts)])
        if not max_chars or len(_join_parts([*parts, conflict_block])) <= max_chars:
            parts.append(conflict_block)

    if omitted:
        keys = ", ".join(source.key for source in omitted[:8])
        suffix = f" (+{len(omitted) - 8} more)" if len(omitted) > 8 else ""
        marker = f"[context bridge omitted after budget: {keys}{suffix}]"
        if not max_chars or len(_join_parts([*parts, marker])) <= max_chars:
            parts.append(marker)

    text = _join_parts(parts)

    return ContextBridgeResult(
        text=text,
        source_chars=source_chars,
        rendered_source_chars=rendered_chars,
        included_keys=tuple(source.key for source in included),
        included_record_counts=record_counts,
    )


def _join_parts(parts: Iterable[str]) -> str:
    return "\n\n".join(part.strip() for part in parts if part and part.strip()).strip()


def _render_source(source: ContextSource) -> str:
    return (
        f"Priority {source.priority} — {source.title}\n"
        f"Proof status: {source.proof_status}\n"
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

    if any(source.key in {"memory", "code_graph", "project_knowledge", "project_history"} for source in sources):
        conflicts.append(
            "Memory/graph hints orient file selection, not current-state proof. Reuse retained source and passing checks for an unchanged candidate; inspect only missing or changed evidence before claims."
        )

    if "skills" in text_by_key:
        conflicts.append(
            "Local skill guidance applies only when its trigger truly matches; current user scope, sandbox/tool rules, and taskboard evidence still win."
        )

    if "workspace" in text_by_key:
        conflicts.append(
            "Workspace/worker notes are coordination context, not proof of code correctness; mention them only when relevant."
        )

    return unique_text(conflicts)


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
    return re.sub(r"\s+", " ", clean).strip().lower()
