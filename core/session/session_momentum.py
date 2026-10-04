"""Deterministic session momentum compaction for MO context health.

This module is intentionally provider-free. At user-turn boundaries it compacts
old, completed tool chains into orientation summaries. Before a pressure handoff,
it can archive bulky observations while retaining active decisions and receipts.
User requests and plain assistant replies stay verbatim; archived observations
and summaries are historical evidence, not current-state proof.
"""
from __future__ import annotations

import json
import os
import re
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_create_bytes
from ..runtime.backend_monitor import get_monitor, redact_monitor_text
from .handoff import context_pressure
from .session import SESSION_MOMENTUM_SUMMARY_PREFIX
from ..utils.number_utils import as_int as _as_int


_TOOL_ISSUE_PREFIXES = (
    "[aborted]",
    "[approval required]",
    "[cancelled]",
    "[canceled]",
    "[lane locked]",
    "[lookup read-only]",
    "[path blocked]",
    "[safety block]",
    "[shell blocked]",
    "[tool arguments invalid]",
    "[tool arguments truncated]",
    "[tool blocked]",
)
_NONZERO_EXIT_RE = re.compile(r"(?im)^\[exit code\s+(-?\d+)\]\s*$")


def _message_chars(messages: list[dict[str, Any]]) -> int:
    try:
        return sum(len(json.dumps(m, default=str, ensure_ascii=False)) for m in messages)
    except Exception:
        return sum(len(str(m)) for m in messages)


def _preview(value: Any, limit: int = 220) -> str:
    return redact_monitor_text(" ".join(str(value or "").split()), limit)


def _tool_result_has_issue(value: str) -> bool:
    """Identify real result failures without treating every bracketed header as one."""
    text = str(value or "")
    leading = text.lstrip().casefold()
    if leading.startswith("error:") or leading.startswith(_TOOL_ISSUE_PREFIXES):
        return True
    if re.search(r"(?im)^traceback \(most recent call last\):", text):
        return True
    return any(int(match.group(1)) != 0 for match in _NONZERO_EXIT_RE.finditer(text))


def _tool_call_info(call: dict[str, Any]) -> tuple[str, str]:
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(fn.get("name") or call.get("name") or "tool")
    raw_args = fn.get("arguments") if "arguments" in fn else call.get("arguments")
    args: dict[str, Any] = {}
    if isinstance(raw_args, dict):
        args = raw_args
    elif isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                args = parsed
        except Exception:
            args = {}
    parts: list[str] = []
    for key in ("path", "file_path", "command", "query", "url", "root"):
        if key in args and args.get(key) not in (None, ""):
            parts.append(f"{key}={_preview(args.get(key), 90)}")
    summary = f"{name}({', '.join(parts)})" if parts else name
    return name, summary


def _tool_results_complete(messages: list[dict[str, Any]], idx: int, expected_ids: list[str]) -> tuple[bool, int, int, int, int]:
    """Return completion and bounded result accounting for one tool round."""
    seen: set[str] = set()
    result_count = 0
    result_chars = 0
    issue_count = 0
    while idx < len(messages) and messages[idx].get("role") == "tool":
        msg = messages[idx]
        tid = str(msg.get("tool_call_id") or "")
        if tid:
            seen.add(tid)
        text = str(msg.get("content") or "")
        result_count += 1
        result_chars += len(text)
        if _tool_result_has_issue(text):
            issue_count += 1
        idx += 1
    required = {tid for tid in expected_ids if tid}
    return required.issubset(seen), idx, result_count, result_chars, issue_count


def _match_completed_tool_chain(messages: list[dict[str, Any]], start: int, cutoff: int) -> tuple[int, dict[str, Any], int, int] | None:
    """Match one old completed tool chain.

    Returns (end_idx, summary_message, before_chars, after_chars).  The returned
    chain always ends before ``cutoff`` so recent/current work is untouched.
    """
    if start >= cutoff or start >= len(messages):
        return None
    idx = start
    if messages[idx].get("role") != "assistant" or not messages[idx].get("tool_calls"):
        return None

    tool_summaries: list[str] = []
    result_count = 0
    result_chars = 0
    issue_count = 0
    end = 0  # boundary after the last COMPLETE old tool round

    while idx < cutoff:
        assistant_msg = messages[idx]
        if assistant_msg.get("role") != "assistant" or not assistant_msg.get("tool_calls"):
            # Non-tool-calling message: can't extend the chain. Close at whatever
            # complete rounds we already gathered (end stays put); never a match
            # if this is the very first message (end == 0 → rejected below).
            break
        calls = [c for c in (assistant_msg.get("tool_calls") or []) if isinstance(c, dict)]
        if not calls:
            break
        expected_ids = [str(c.get("id") or "") for c in calls if c.get("id")]
        round_summaries = [_tool_call_info(call)[1] for call in calls]
        idx += 1
        complete, idx, count, chars, issues = _tool_results_complete(messages, idx, expected_ids)
        if not complete or idx > cutoff:
            # Partial round, or its results reach into the recent window: stop
            # WITHOUT this round so current/incomplete work is never compacted.
            break
        if idx >= len(messages):
            # Nothing follows these results at all — the model has not responded to
            # them yet (in-flight current work). Never compact them.
            break
        # This whole round is old + complete AND the model has moved past it (a later
        # message exists) → a safe compaction boundary. Agentic turns emit
        # assistant(tool_calls)→tool with no intermediate text, so most chains close
        # at the recent-window cutoff or a new user turn, NOT at a trailing
        # assistant-text message — requiring that text left long tool runs (100s of
        # KB of old results) permanently un-compacted.
        tool_summaries.extend(round_summaries)
        result_count += count
        result_chars += chars
        issue_count += issues
        end = idx
        if idx >= cutoff:
            break
        next_msg = messages[idx]
        if next_msg.get("role") == "assistant" and next_msg.get("tool_calls"):
            continue
        # Plain replies and user requests retain their full content and metadata.
        # Only tool rounds belong in the orientation summary.
        break

    if not end or end > cutoff or result_count <= 0:
        return None
    chain = messages[start:end]
    before_chars = _message_chars(chain)
    unique_tools = []
    for item in tool_summaries:
        if item not in unique_tools:
            unique_tools.append(item)
    assistant_progress: list[str] = []
    for message in chain:
        if message.get("role") != "assistant":
            continue
        progress = _preview(message.get("content"), 520)
        if progress and progress not in assistant_progress:
            assistant_progress.append(progress)
    lines = [SESSION_MOMENTUM_SUMMARY_PREFIX]
    lines.extend([
        "- Tools run: " + "; ".join(unique_tools[:10]) + (f"; +{len(unique_tools) - 10} more" if len(unique_tools) > 10 else ""),
        f"- Tool result footprint before compaction: {result_count} result message(s), {result_chars:,} chars.",
    ])
    if assistant_progress:
        lines.append("- Prior assistant progress (orientation, not proof):")
        lines.extend(f"  - {item}" for item in assistant_progress[-3:])
    if issue_count:
        lines.append(f"- Prior tool issues/blocks observed: {issue_count}; re-check before relying on this result.")
    lines.append("- Historical tool evidence is not current-state proof; reuse verification only for an unchanged candidate.")
    # This is runtime-owned context for later provider reasoning, never prior
    # assistant speech or a user-visible answer.
    summary = {"role": "system", "content": "\n".join(lines)}
    after_chars = _message_chars([summary])
    if after_chars >= before_chars:
        return None
    return end, summary, before_chars, after_chars


def _archive_chain(archive_dir: Path, chain: list[dict[str, Any]], counter: int) -> str:
    """Persist a chain's full messages to disk before compaction; return the path.

    The archive is the recovery lane for exact tool outputs: the in-session
    summary is orientation only, but the archived JSON keeps every byte so MO
    (or the operator) can read the original results back with read_file.
    """
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    path = archive_dir / f"{stamp}_chain{counter}.json"
    suffix = 0
    payload = json.dumps(chain, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    while True:
        try:
            atomic_create_bytes(path, payload)
            return str(path)
        except FileExistsError:
            suffix += 1
            path = archive_dir / f"{stamp}_chain{counter}_{suffix}.json"


def _history_compaction_cutoff(messages: list[dict[str, Any]], keep_recent: int) -> int:
    """Exclude both recent messages and the latest user turn from compaction."""
    cutoff = max(0, len(messages) - max(0, keep_recent))
    latest_user = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
        0,
    )
    return min(cutoff, latest_user)


def _compact_observation_round(
    messages: list[dict[str, Any]], start: int, cutoff: int,
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Age bulk observations without replacing active decisions or receipts.

    Keep the original calls and every non-observation result. The caller archives
    the full round atomically before adopting the excerpted result messages.
    """
    message = messages[start]
    if message.get("role") != "assistant" or not message.get("tool_calls"):
        return None
    calls = {
        str(call.get("id") or ""): _tool_call_info(call)[0]
        for call in message["tool_calls"] if isinstance(call, dict)
    }
    complete, end, _, _, _ = _tool_results_complete(messages, start + 1, list(calls))
    if not complete or end > cutoff or end >= len(messages):
        return None
    replacements = [message]
    excerpts: list[dict[str, Any]] = []
    for original in messages[start + 1:end]:
        name = calls.get(str(original.get("tool_call_id") or ""), "")
        content = original.get("content")
        if (
            name in {"read_file", "grep", "find_files", "code_search", "find_callers", "find_callees"}
            and isinstance(content, str)
            and len(content) > 8_000
            and not content.startswith("[Archived observation")
            and not _tool_result_has_issue(content)
        ):
            excerpt = dict(original)
            excerpt["content"] = (
                f"[Archived observation: {name}; {len(content):,} characters originally read]\n"
                "The exact call above and the archive identify this already inspected result. "
                "Retain the findings and edits recorded later in this conversation; do not "
                "repeat discovery. Recover a specific omitted passage only if needed, and "
                "check source changes before treating this historical observation as current.\n"
                f"{content[:1600]}\n[observation excerpt: middle archived]\n{content[-800:]}"
            )
            replacements.append(excerpt)
            excerpts.append(excerpt)
        else:
            replacements.append(original)
    return (end, replacements, excerpts) if excerpts else None


def _old_tool_result_chars(
    messages: list[dict[str, Any]],
    keep_recent: int,
    *,
    include_active_observations: bool = False,
    include_resolved_active: bool = False,
    max_chains: int = 4,
) -> int:
    """Total recoverable tool-result chars visible to the requested compactor."""
    cutoff = _history_compaction_cutoff(messages, keep_recent)
    historical_total = sum(
        len(str(m.get("content") or ""))
        for m in messages[:cutoff]
        if isinstance(m, dict) and m.get("role") == "tool"
    )
    if not include_active_observations:
        return historical_total

    # At a provider checkpoint the compactor follows a different path from the
    # user-turn boundary above: it can excerpt eligible observations and, after
    # an admitted completion, replace the resolved current chain. Count those
    # exact candidates in the same order and under the same shared chain cap.
    # Historical bytes that this pass cannot change must not wake it.
    chain_start = 0
    chain_cutoff = 0
    if include_resolved_active:
        chain_start, chain_cutoff = _active_resolved_range(messages)

    total = 0
    compacted = 0
    observation_cutoff = max(0, len(messages) - max(2, keep_recent))
    i = 0
    while i < len(messages):
        if compacted < max_chains and chain_start <= i < chain_cutoff:
            matched = _match_completed_tool_chain(messages, i, chain_cutoff)
            if matched:
                end, _summary, before_chars, after_chars = matched
                total += max(0, before_chars - after_chars)
                compacted += 1
                i = end
                continue
        if compacted < max_chains and i < observation_cutoff:
            observed = _compact_observation_round(messages, i, observation_cutoff)
            if observed:
                end, replacements, _excerpts = observed
                total += max(0, _message_chars(messages[i:end]) - _message_chars(replacements))
                compacted += 1
                i = end
                continue
        i += 1
    return total


def _active_resolved_range(messages: list[dict[str, Any]]) -> tuple[int, int]:
    """Return the current-turn range resolved by the latest completion call.

    The caller must already know that ``complete_task`` succeeded. The call and
    its result remain outside the returned range as the exact transition
    receipt. A user steer starts a new range so active compaction never folds
    evidence from the other side of that steer into the same resolved chain.
    """
    cutoff = 0
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") != "assistant":
            continue
        names = {
            _tool_call_info(call)[0]
            for call in message.get("tool_calls") or []
            if isinstance(call, dict)
        }
        if "complete_task" in names:
            cutoff = index
            break
    if cutoff <= 0:
        return 0, 0
    latest_user = next(
        (
            index
            for index in range(cutoff - 1, -1, -1)
            if messages[index].get("role") == "user"
        ),
        -1,
    )
    return latest_user + 1, cutoff


def compact_completed_tool_chains(
    session: Any,
    *,
    keep_recent: int = 18,
    max_chains: int = 4,
    archive_dir: str | Path | None = None,
    min_saved_chars: int = 0,
    include_active_observations: bool = False,
    include_resolved_active: bool = False,
) -> dict[str, Any]:
    """Compact old completed tool chains in-place.

    By default preserve the latest user turn verbatim. At a provider checkpoint,
    ``include_active_observations`` may archive older bulk observations in that
    turn, retaining exact calls, user/assistant prose, mutations, and receipts.
    When the runtime has an authoritative successful ``complete_task`` result,
    ``include_resolved_active`` also compacts the completed current-turn tool
    chain before that exact transition receipt. It never crosses a user steer.
    Every replaced chain requires an archive. Full messages are written there
    first and the summary references the archive path; unavailable storage
    leaves the session unchanged.
    ``min_saved_chars`` can reject a candidate without mutating or archiving it.
    The return dict includes a ``truth_boundary`` key so downstream consumers
    can verify the anti-hallucination contract was satisfied.
    """
    from ..gates.consistency_boundary import truth_boundary as _tb

    if archive_dir is None:
        return {"changed": False, "reason": "archive_unavailable"}
    messages = [m for m in list(getattr(session, "messages", []) or []) if isinstance(m, dict)]
    resolved_active_enabled = bool(
        include_active_observations and include_resolved_active
    )
    if len(messages) <= max(2, keep_recent) and not resolved_active_enabled:
        return {
            "changed": False,
            "reason": "too_few_messages",
            "before_messages": len(messages),
            "after_messages": len(messages),
            "truth_boundary": _tb(
                deterministic=True,
                labeled=True,
                evidence_preserved=[],
                loss_accounted={},
            ),
        }
    # Active whole-chain aging requires both an exact archive and the caller's
    # authoritative successful-completion signal. Keep the complete_task call
    # and result verbatim and never cross a user steer.
    chain_start = 0
    if include_active_observations:
        if resolved_active_enabled:
            chain_start, cutoff = _active_resolved_range(messages)
        else:
            cutoff = 0
    else:
        cutoff = _history_compaction_cutoff(messages, keep_recent)
    observation_cutoff = (
        max(0, len(messages) - max(2, keep_recent))
        if include_active_observations else cutoff
    )
    before_chars = _message_chars(messages)
    new_messages: list[dict[str, Any]] = []
    compacted = 0
    saved_chars = 0
    i = 0
    archived_paths: list[str] = []
    evidence_preserved: list[str] = []
    pending_archives: list[tuple[list[dict[str, Any]], list[dict[str, Any]], int]] = []
    while i < len(messages):
        if compacted < max_chains and chain_start <= i < cutoff:
            matched = _match_completed_tool_chain(messages, i, cutoff)
            if matched:
                end, summary, chain_before, chain_after = matched
                for call in messages[i].get("tool_calls") or []:
                    if isinstance(call, dict):
                        name = _tool_call_info(call)[0]
                        anchor = f"tool:{name}" if name else ""
                        if anchor and anchor not in evidence_preserved:
                            evidence_preserved.append(anchor)
                pending_archives.append(([summary], messages[i:end], compacted + 1))
                new_messages.append(summary)
                saved_chars += max(0, chain_before - chain_after)
                compacted += 1
                i = end
                continue
        if compacted < max_chains and i < observation_cutoff:
            observed = _compact_observation_round(messages, i, observation_cutoff)
            if observed:
                end, replacements, excerpts = observed
                excerpt_ids = {
                    str(excerpt.get("tool_call_id") or "")
                    for excerpt in excerpts
                    if isinstance(excerpt, dict)
                }
                for call in messages[i].get("tool_calls") or []:
                    if not isinstance(call, dict) or str(call.get("id") or "") not in excerpt_ids:
                        continue
                    name = _tool_call_info(call)[0]
                    anchor = f"tool:{name}" if name else ""
                    if anchor and anchor not in evidence_preserved:
                        evidence_preserved.append(anchor)
                pending_archives.append((excerpts, messages[i:end], compacted + 1))
                new_messages.extend(replacements)
                saved_chars += _message_chars(messages[i:end]) - _message_chars(replacements)
                compacted += 1
                i = end
                continue
        new_messages.append(messages[i])
        i += 1
    if compacted <= 0:
        return {
            "changed": False,
            "reason": "no_completed_old_tool_chains",
            "before_messages": len(messages),
            "after_messages": len(messages),
            "truth_boundary": _tb(
                deterministic=True,
                labeled=True,
                evidence_preserved=["session messages (no change)"],
                loss_accounted={},
            ),
        }
    minimum = max(0, _as_int(min_saved_chars))
    if saved_chars < minimum:
        return {
            "changed": False,
            "reason": "insufficient_savings",
            "compacted_chains": compacted,
            "potential_saved_chars": saved_chars,
            "minimum_saved_chars": minimum,
            "before_messages": len(messages),
            "after_messages": len(messages),
            "truth_boundary": _tb(
                deterministic=True,
                labeled=True,
                evidence_preserved=["session messages (no change)"],
                loss_accounted={},
            ),
        }
    archive_failed = False
    for summaries, chain, counter in pending_archives:
        try:
            archive_path = _archive_chain(Path(archive_dir), chain, counter)
            archived_paths.append(archive_path)
            archive_anchor = f"archive:{archive_path}"
            if archive_anchor not in evidence_preserved:
                evidence_preserved.append(archive_anchor)
            for summary in summaries:
                summary["content"] += (
                    f"\n- Full tool results archived: {archive_path} "
                    "(recover only a specifically named prior result; never reread "
                    "this archive wholesale or as general context. Use read_file "
                    "view=evidence with offset/limit; paging omits provider replay metadata)."
                )
        except Exception:
            traceback.print_exc()
            archive_failed = True
            break
    if archive_failed:
        for archived_path in archived_paths:
            try:
                Path(archived_path).unlink(missing_ok=True)
            except OSError:
                pass
        return {
            "changed": False,
            "reason": "archive_failed",
            "compacted_chains": compacted,
            "before_messages": len(messages),
            "after_messages": len(messages),
            "truth_boundary": _tb(
                deterministic=True,
                labeled=True,
                evidence_preserved=["session messages (no change)"],
                loss_accounted={},
            ),
        }
    saved_chars = before_chars - _message_chars(new_messages)
    setattr(session, "messages", new_messages)
    setattr(session, "last_compacted_at", time.time())
    setattr(session, "compacted_messages_count", _as_int(getattr(session, "compacted_messages_count", 0)) + (len(messages) - len(new_messages)))
    return {
        "changed": True,
        "compacted_chains": compacted,
        "archived_paths": archived_paths,
        "before_messages": len(messages),
        "after_messages": len(new_messages),
        "before_chars": before_chars,
        "after_chars": _message_chars(new_messages),
        "saved_chars": saved_chars,
        "truth_boundary": _tb(
            deterministic=True,
            labeled=True,
            evidence_preserved=evidence_preserved[:20],
            loss_accounted={
                "before_messages": len(messages),
                "after_messages": len(new_messages),
                "saved_chars": saved_chars,
            },
        ),
    }


def _chain_archive_dir(agent: Any) -> Path | None:
    """Resolve the compacted-chain archive dir, or None when archiving is off.

    Archiving is suppressed under pytest (MO_CHAIN_ARCHIVE_FORCE=1 overrides)
    to match the audit-writer pollution guards.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("MO_CHAIN_ARCHIVE_FORCE") != "1":
        return None
    try:
        from ..state.paths import SESSION_ROOT_DIR, resolve_state_path
        cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
        resolved = resolve_state_path("logs/compacted_chains", cfg)
        archive_dir = Path(resolved) if resolved else None
        if archive_dir is not None and not getattr(agent, "_chain_archive_pruned", False):
            sessions = Path(resolve_state_path(SESSION_ROOT_DIR, cfg))
            _prune_unreferenced_chain_archives(archive_dir, sessions)
            setattr(agent, "_chain_archive_pruned", True)
        return archive_dir
    except Exception:
        traceback.print_exc()
        return None


_ARCHIVE_NAME_RE = re.compile(r"[0-9T_-]+_chain\d+(?:_\d+)?\.json")


def _prune_unreferenced_chain_archives(
    archive_dir: Path,
    sessions_dir: Path,
    *,
    retention_seconds: float | None = None,
    now: float | None = None,
) -> int:
    """Delete only old chain archives no saved session can recover.

    Protect transitive references from saved sessions and recent archives.
    Resolve all references before deleting anything; unreadable evidence makes
    this pass ineligible to prune. Cycles are visited only once.
    """
    try:
        from .sessions import SLOT_RETENTION_SECONDS

        retention = float(SLOT_RETENTION_SECONDS if retention_seconds is None else retention_seconds)
        current = time.time() if now is None else float(now)
        referenced: set[str] = set()
        from .sessions import iter_all_session_paths

        for session_path in iter_all_session_paths(sessions_dir):
            text = session_path.read_text(encoding="utf-8")
            referenced.update(_ARCHIVE_NAME_RE.findall(text))
        archives = list(archive_dir.glob("*.json"))
        archive_root = archive_dir.resolve()
        for archive in archives:
            if archive.resolve().parent != archive_root:
                return 0
            if current - archive.stat().st_mtime <= retention:
                referenced.add(archive.name)
        pending = list(referenced)
        while pending:
            archive = archive_dir / pending.pop()
            if archive.resolve().parent != archive_root:
                return 0
            text = archive.read_text(encoding="utf-8")
            children = set(_ARCHIVE_NAME_RE.findall(text)) - referenced
            referenced.update(children)
            pending.extend(children)
        removed = 0
        for archive in archives:
            try:
                if archive.name in referenced or current - archive.stat().st_mtime <= retention:
                    continue
                archive.unlink()
                removed += 1
            except OSError:
                continue
        return removed
    except Exception:
        return 0


def maybe_compact_session(
    agent: Any,
    *,
    stage: str,
    latest_user: str = "",
    extra_context: str = "",
    monitor: Any = None,
    force: bool = False,
    tools: list[dict] | None = None,
    pressure_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run conservative deterministic momentum compaction when pressure warrants."""
    is_fg = getattr(agent, "_is_foreground_session", None)
    if callable(is_fg) and not is_fg():
        return {"changed": False, "reason": "not_foreground"}
    session = getattr(agent, "session", None)
    if session is None:
        return {"changed": False, "reason": "no_session"}
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    agent_cfg = cfg.get("agent", {}) if isinstance(cfg.get("agent", {}), dict) else {}
    enabled = bool(agent_cfg.get("context_momentum_compact_enabled", True))
    if not enabled:
        return {"changed": False, "reason": "disabled"}
    metrics = pressure_metrics if pressure_metrics is not None else context_pressure(
        agent, extra_context=extra_context, tools=tools,
    )
    pressure = float(metrics.get("pressure") or 0.0)
    message_ratio = float(metrics.get("message_ratio") or 0.0)
    threshold = float(agent_cfg.get("context_momentum_compact_threshold", 0.45) or 0.45)
    threshold = min(0.80, max(0.25, threshold))
    keep_recent = _as_int(agent_cfg.get("context_momentum_keep_recent", 18), 18)
    max_chains = _as_int(agent_cfg.get("context_momentum_max_chains", 4), 4)
    # Tool-result aging: oversized old tool results justify compaction on their
    # own. The high threshold and matching minimum-savings gate are the
    # hysteresis: another pass requires another threshold of recoverable output.
    tool_chars_threshold = _as_int(agent_cfg.get("context_momentum_tool_chars_threshold", 40_000), 40_000)
    # A runtime "work resolved" hint (set by complete_task) lowers the
    # old-content bar for one check, so resolved tool chains are freed
    # proactively instead of waiting for full pressure. Still requires meaningful
    # old content (reduced, not zeroed) so freed bytes justify the single
    # prefix-cache miss — no eager-compaction cost regression. Consumed once.
    resolved_hint = bool(getattr(agent, "_work_resolved_hint", False))
    if resolved_hint:
        setattr(agent, "_work_resolved_hint", False)
        factor = float(agent_cfg.get("context_momentum_resolved_threshold_factor", 0.5) or 0.5)
        factor = min(1.0, max(0.25, factor))
        tool_chars_threshold = int(tool_chars_threshold * factor)
    include_active_observations = stage == "turn_health"
    include_resolved_active = include_active_observations and resolved_hint
    old_tool_chars = _old_tool_result_chars(
        [m for m in list(getattr(session, "messages", []) or []) if isinstance(m, dict)],
        keep_recent,
        include_active_observations=include_active_observations,
        include_resolved_active=include_resolved_active,
        max_chains=max_chains,
    )
    tool_chars_exceeded = tool_chars_threshold > 0 and old_tool_chars >= tool_chars_threshold
    pressure_trigger = pressure >= threshold or message_ratio >= threshold
    # ``trimmed_messages_count`` is a lifetime diagnostic counter. It can rise
    # when an interrupted tail is quarantined and stays non-zero afterward, so
    # treating it as present pressure makes every later turn compact at almost
    # zero usage. Trim recovery belongs to the handoff path; momentum compaction
    # runs only for current pressure or meaningfully aged tool output.
    if not force and pressure < threshold and message_ratio < threshold and not tool_chars_exceeded:
        return {"changed": False, "reason": "below_threshold", "pressure": pressure, "message_ratio": message_ratio, "old_tool_chars": old_tool_chars}
    if force or pressure >= 0.60 or message_ratio >= 0.60:
        keep_recent = min(keep_recent, _as_int(agent_cfg.get("context_momentum_aggressive_keep_recent", 12), 12))
        max_chains = max(max_chains, _as_int(agent_cfg.get("context_momentum_aggressive_max_chains", 6), 6))
    archive_dir = _chain_archive_dir(agent)
    min_saved_chars = 0 if force or pressure_trigger else tool_chars_threshold
    result = compact_completed_tool_chains(
        session,
        keep_recent=keep_recent,
        max_chains=max_chains,
        archive_dir=archive_dir,
        min_saved_chars=min_saved_chars,
        include_active_observations=include_active_observations,
        include_resolved_active=include_resolved_active,
    )
    result["old_tool_chars"] = old_tool_chars
    result["tool_chars_trigger"] = tool_chars_exceeded
    result["resolved_hint"] = resolved_hint
    result.update({"stage": stage, "pressure": pressure, "message_ratio": message_ratio, "force": bool(force), "latest_user_preview": _preview(latest_user, 160)})
    if result.get("changed"):
        saved = _as_int(result.get("saved_chars"))
        cache_epoch = _as_int(getattr(agent, "session_compaction_total_ops", 0)) + 1
        setattr(agent, "session_compaction_total_ops", cache_epoch)
        setattr(agent, "session_compaction_total_saved", _as_int(getattr(agent, "session_compaction_total_saved", 0)) + saved)
        result["cache_epoch"] = cache_epoch
        mon = monitor or get_monitor()
        if mon:
            mon.emit("session_compact", result)
            tb = result.get("truth_boundary", {}) if isinstance(result.get("truth_boundary"), dict) else {}
            mon.emit("session_event", {
                "kind": "session_compact",
                "stage": stage,
                "saved_chars": saved,
                "compacted_chains": _as_int(result.get("compacted_chains")),
                "before_messages": _as_int(result.get("before_messages")),
                "after_messages": _as_int(result.get("after_messages")),
                "cache_epoch": cache_epoch,
                "truth_boundary": tb,
            })
    return result
