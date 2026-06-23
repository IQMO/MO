"""Deterministic session momentum compaction for MO context health.

This module is intentionally provider-free.  It compacts only old, completed
assistant/tool chains into orientation summaries so MO can reduce active context
before resorting to a handoff.  It never treats compacted content as proof:
summary messages explicitly tell the next provider call to re-run tools for
current facts.
"""
from __future__ import annotations

import json
import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from ..atomic_write import atomic_write_json
from ..backend_monitor import get_monitor, redact_monitor_text
from .handoff import context_pressure
from ..number_utils import as_int as _as_int


def _message_chars(messages: list[dict[str, Any]]) -> int:
    try:
        return sum(len(json.dumps(m, default=str, ensure_ascii=False)) for m in messages)
    except Exception:
        return sum(len(str(m)) for m in messages)


def _preview(value: Any, limit: int = 220) -> str:
    return redact_monitor_text(" ".join(str(value or "").split()), limit)


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
    """Return (complete, next_idx, result_count, chars, issue_count)."""
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
        low = text.lower()
        if text.startswith(("Error:", "[")) or "blocked" in low or "traceback" in low:
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
    user_preview = ""
    if messages[idx].get("role") == "user":
        if idx + 1 >= cutoff or messages[idx + 1].get("role") != "assistant" or not messages[idx + 1].get("tool_calls"):
            return None
        user_preview = _preview(messages[idx].get("content"), 260)
        idx += 1
    elif messages[idx].get("role") != "assistant" or not messages[idx].get("tool_calls"):
        return None

    tool_summaries: list[str] = []
    result_count = 0
    result_chars = 0
    issue_count = 0
    final_preview = ""
    end = 0

    while idx < cutoff:
        assistant_msg = messages[idx]
        if assistant_msg.get("role") != "assistant" or not assistant_msg.get("tool_calls"):
            return None
        calls = [c for c in (assistant_msg.get("tool_calls") or []) if isinstance(c, dict)]
        if not calls:
            return None
        expected_ids = [str(c.get("id") or "") for c in calls if c.get("id")]
        for call in calls:
            _name, summary = _tool_call_info(call)
            tool_summaries.append(summary)
        idx += 1
        complete, idx, count, chars, issues = _tool_results_complete(messages, idx, expected_ids)
        if not complete:
            return None
        result_count += count
        result_chars += chars
        issue_count += issues
        if idx >= cutoff:
            return None
        next_msg = messages[idx]
        if next_msg.get("role") == "assistant" and next_msg.get("tool_calls"):
            continue
        if next_msg.get("role") == "assistant":
            final_preview = _preview(next_msg.get("content"), 360)
            end = idx + 1
            break
        return None

    if not end or end > cutoff:
        return None
    chain = messages[start:end]
    before_chars = _message_chars(chain)
    unique_tools = []
    for item in tool_summaries:
        if item not in unique_tools:
            unique_tools.append(item)
    lines = [
        "[SESSION MOMENTUM COMPACTED COMPLETED TOOL CHAIN — orientation only, not proof]",
    ]
    if user_preview:
        lines.append(f"- Prior user request: {user_preview}")
    lines.extend([
        "- Tools run: " + "; ".join(unique_tools[:10]) + (f"; +{len(unique_tools) - 10} more" if len(unique_tools) > 10 else ""),
        f"- Tool result footprint before compaction: {result_count} result message(s), {result_chars:,} chars.",
    ])
    if issue_count:
        lines.append(f"- Prior tool issues/blocks observed: {issue_count}; re-check before relying on this result.")
    if final_preview:
        lines.append(f"- Prior assistant outcome: {final_preview}")
    lines.append("- Re-run/read current files/tests before factual claims; append-only tool audit remains the durable receipt.")
    # Keep the STRUCTURE of read_file-of-Python results (signatures/docstrings; bodies
    # dropped — the original is archived above and read_file-recoverable). On real code
    # this is a ~90% cut while the model keeps a navigable map of what it read; MO's
    # per-format tool compressor does 0% on source files (no consecutive duplicate lines).
    from ..code_skeleton import code_skeleton
    skeletons: list[str] = []
    for m in chain:
        if m.get("role") != "tool":
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        sk = code_skeleton(content)
        if sk:
            skeletons.append(sk)
        if len(skeletons) >= 3:
            break
    if skeletons:
        lines.append("- Structure of read code (bodies dropped; read_file to recover full):")
        for sk in skeletons:
            lines.append("```python\n" + sk + "\n```")
    summary = {"role": "assistant", "content": "\n".join(lines)}
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
    while path.exists():
        suffix += 1
        path = archive_dir / f"{stamp}_chain{counter}_{suffix}.json"
    atomic_write_json(path, chain, indent=2, ensure_ascii=False, default=str)
    return str(path)


def _old_tool_result_chars(messages: list[dict[str, Any]], keep_recent: int) -> int:
    """Total chars of tool-result messages older than the keep_recent window."""
    cutoff = max(0, len(messages) - max(0, keep_recent))
    return sum(
        len(str(m.get("content") or ""))
        for m in messages[:cutoff]
        if isinstance(m, dict) and m.get("role") == "tool"
    )


def compact_completed_tool_chains(
    session: Any,
    *,
    keep_recent: int = 18,
    max_chains: int = 4,
    archive_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Compact old completed tool chains in-place.

    Recent messages are preserved verbatim.  Unfinished chains are skipped.
    When ``archive_dir`` is given, each compacted chain's full messages are
    written there first and the summary references the archive path.
    The return dict includes a ``truth_boundary`` key so downstream consumers
    can verify the anti-hallucination contract was satisfied.
    """
    from ..consistency_boundary import truth_boundary as _tb

    messages = [m for m in list(getattr(session, "messages", []) or []) if isinstance(m, dict)]
    if len(messages) <= max(2, keep_recent):
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
    cutoff = max(0, len(messages) - max(0, keep_recent))
    before_chars = _message_chars(messages)
    new_messages: list[dict[str, Any]] = []
    compacted = 0
    saved_chars = 0
    i = 0
    archived_paths: list[str] = []
    while i < len(messages):
        if compacted < max_chains and i < cutoff:
            matched = _match_completed_tool_chain(messages, i, cutoff)
            if matched:
                end, summary, chain_before, chain_after = matched
                if archive_dir is not None:
                    try:
                        archive_path = _archive_chain(Path(archive_dir), messages[i:end], compacted + 1)
                        archived_paths.append(archive_path)
                        summary["content"] += f"\n- Full tool results archived: {archive_path} (read_file to recover exact outputs)."
                    except Exception:
                        traceback.print_exc()
                new_messages.append(summary)
                saved_chars += max(0, chain_before - chain_after)
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
    # Extract tool name evidence from compacted chains for truth-boundary
    compacted_tools: list[str] = []
    for m in new_messages:
        c = str(m.get("content", ""))
        if "Tools run:" in c:
            for line in c.splitlines():
                if line.startswith("- Tools run:"):
                    raw = line.replace("- Tools run:", "").strip()
                    compacted_tools.extend(t.strip() for t in raw.split(";") if t.strip())
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
            evidence_preserved=compacted_tools[:20],
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
        from ..path_defaults import resolve_state_path
        cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
        resolved = resolve_state_path("logs/compacted_chains", cfg)
        return Path(resolved) if resolved else None
    except Exception:
        traceback.print_exc()
        return None


def maybe_compact_session(
    agent: Any,
    *,
    stage: str,
    latest_user: str = "",
    extra_context: str = "",
    monitor: Any = None,
    force: bool = False,
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
    metrics = context_pressure(agent, extra_context=extra_context)
    pressure = float(metrics.get("pressure") or 0.0)
    message_ratio = float(metrics.get("message_ratio") or 0.0)
    trimmed = _as_int(metrics.get("trimmed_messages_count"))
    threshold = float(agent_cfg.get("context_momentum_compact_threshold", 0.45) or 0.45)
    threshold = min(0.80, max(0.25, threshold))
    keep_recent = _as_int(agent_cfg.get("context_momentum_keep_recent", 18), 18)
    max_chains = _as_int(agent_cfg.get("context_momentum_max_chains", 4), 4)
    # Tool-result aging: oversized old tool results justify compaction on their
    # own.  One compaction pass costs a single prefix-cache miss but removes the
    # bulk from every later provider call, so the threshold is deliberately high.
    tool_chars_threshold = _as_int(agent_cfg.get("context_momentum_tool_chars_threshold", 48_000), 48_000)
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
    old_tool_chars = _old_tool_result_chars(
        [m for m in list(getattr(session, "messages", []) or []) if isinstance(m, dict)],
        keep_recent,
    )
    tool_chars_exceeded = tool_chars_threshold > 0 and old_tool_chars >= tool_chars_threshold
    if not force and pressure < threshold and message_ratio < threshold and trimmed <= 0 and not tool_chars_exceeded:
        return {"changed": False, "reason": "below_threshold", "pressure": pressure, "message_ratio": message_ratio, "old_tool_chars": old_tool_chars}
    if force or pressure >= 0.60 or message_ratio >= 0.60:
        keep_recent = min(keep_recent, _as_int(agent_cfg.get("context_momentum_aggressive_keep_recent", 12), 12))
        max_chains = max(max_chains, _as_int(agent_cfg.get("context_momentum_aggressive_max_chains", 6), 6))
    archive_dir = _chain_archive_dir(agent)
    result = compact_completed_tool_chains(session, keep_recent=keep_recent, max_chains=max_chains, archive_dir=archive_dir)
    result["old_tool_chars"] = old_tool_chars
    result["tool_chars_trigger"] = tool_chars_exceeded
    result["resolved_hint"] = resolved_hint
    result.update({"stage": stage, "pressure": pressure, "message_ratio": message_ratio, "force": bool(force), "latest_user_preview": _preview(latest_user, 160)})
    if result.get("changed"):
        saved = _as_int(result.get("saved_chars"))
        setattr(agent, "session_compaction_total_ops", _as_int(getattr(agent, "session_compaction_total_ops", 0)) + 1)
        setattr(agent, "session_compaction_total_saved", _as_int(getattr(agent, "session_compaction_total_saved", 0)) + saved)
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
                "truth_boundary": tb,
            })
    return result
