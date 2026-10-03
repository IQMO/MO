"""Display-only activity, status-bar, and footer helpers."""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import os
import re
import time
from typing import Any

from .formatting import activity_label, brand_spinner_frame, format_k, idle_status_text, token_status_from_agent
from .transcript_view import cell_width, split_cells


def board_summary_text(
    board_text: str,
    *,
    edit_additions: int = 0,
    edit_deletions: int = 0,
) -> str:
    if not board_text:
        return ""
    first = board_text.splitlines()[0].strip()
    if "tasks" not in first or "(" not in first:
        return ""
    added = max(0, int(edit_additions or 0))
    removed = max(0, int(edit_deletions or 0))
    if added or removed:
        close = first.rfind(")")
        if close >= 0:
            first = f"{first[:close]}, +{added} -{removed}{first[close:]}"
    return first


def board_summary_fragments(
    board_text: str,
    *,
    edit_additions: int = 0,
    edit_deletions: int = 0,
    base_style: str = "class:activity",
) -> list[tuple[str, str]]:
    """Render the count line while preserving skin-owned edit colors."""
    summary = board_summary_text(board_text)
    if not summary:
        return []
    added = max(0, int(edit_additions or 0))
    removed = max(0, int(edit_deletions or 0))
    if not (added or removed):
        return [(base_style, summary)]
    close = summary.rfind(")")
    if close < 0:
        return [(base_style, summary)]
    return [
        (base_style, f"{summary[:close]}, "),
        ("class:diff-add", f"+{added}"),
        (base_style, " "),
        ("class:diff-del", f"-{removed}"),
        (base_style, summary[close:]),
    ]


def duration_text(seconds: float | int) -> str:
    """Format an elapsed duration compactly for live terminal clocks."""
    elapsed = max(0, int(seconds or 0))
    mins, secs = divmod(elapsed, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h{mins}m"
    if mins:
        return f"{mins}m{secs}s"
    return f"{secs}s"


def elapsed_seconds_text(started_at: float | None, *, now: float | None = None) -> str:
    if not started_at:
        return ""
    current = time.time() if now is None else float(now)
    return duration_text(current - started_at)


def goal_elapsed_text(started_at: float | None, *, now: float | None = None) -> str:
    if not started_at:
        return "0s"
    current = time.time() if now is None else float(now)
    return duration_text(current - started_at)


def goal_progress_text(agent: Any) -> str:
    """Return conservative goal completion percentage from live GoalPlan truth."""
    plan = getattr(agent, "_goal_plan", None)
    if not plan:
        return ""
    steps = list(getattr(plan, "steps", []) or [])
    total = len(steps)
    if total <= 0:
        return ""
    try:
        completed = int(getattr(plan, "completed_count", lambda: 0)() or 0)
    except Exception:
        completed = sum(1 for step in steps if str(getattr(step, "status", "") or "") == "completed")
    pct = int(round((max(0, min(completed, total)) / total) * 100))
    return f"{pct}%"


def activity_fragments(
    *,
    busy: bool,
    goal_worker_active: bool,
    goal_backgrounded: bool,
    activity_text: str = "",
    activity_started_at: float | None = None,
    board_text: str = "",
    goal_board_text: str = "",
    goal_started_at: float | None = None,
    edit_additions: int = 0,
    edit_deletions: int = 0,
    now: float | None = None,
    method_style: str = "",
) -> list[tuple[str, str]]:
    if not (busy or (goal_worker_active and not goal_backgrounded)):
        return [("", "")]
    current = time.time() if now is None else float(now)
    frame = brand_spinner_frame(current)
    if busy:
        label = activity_label(activity_text or "working")
        elapsed = elapsed_seconds_text(activity_started_at, now=current)
        summary = board_summary_text(
            board_text,
            edit_additions=edit_additions,
            edit_deletions=edit_deletions,
        )
    else:
        label = "Goaling"
        elapsed = goal_elapsed_text(goal_started_at, now=current)
        summary = board_summary_text("" if goal_backgrounded else goal_board_text)
    details = [item for item in (elapsed, summary) if item]
    detail_text = " · ".join(details)
    suffix = f" ({detail_text})" if detail_text else ""
    # A distinctive MO method pulses the entire line as one unified signal.
    # Generic work keeps the skin's ordinary spinner/activity semantics.
    glow = method_style or ""
    spinner_style = glow or "class:spinner"
    text_style = glow or "class:activity"
    fragments = [(spinner_style, f" {frame} "), (text_style, "MO")]
    if busy and summary and (edit_additions or edit_deletions):
        summary_fragments = board_summary_fragments(
            board_text,
            edit_additions=edit_additions,
            edit_deletions=edit_deletions,
        )
        if len(summary_fragments) > 1:
            if glow:
                summary_fragments = [(glow, text) for _style, text in summary_fragments]
            elapsed_prefix = f"{elapsed} · " if elapsed else ""
            fragments.append((text_style, f" {label} ({elapsed_prefix}"))
            fragments.extend(summary_fragments)
            fragments.append((text_style, ")"))
            return fragments
    fragments.append((text_style, f" {label}{suffix}"))
    return fragments


def notification_items(*, goal_worker_active: bool, goal_done_unread: bool, pending_count: int, steer_count: int = 0, prt_done_unread: bool | str = False, goal_progress: str = "", transient_notes: "tuple[str, ...]" = ()) -> list[tuple[str, str]]:
    """Build compact footer notifications owned by the footer/status lane.

    Durable learning is deliberately not accepted here.  The transcript activity
    lane owns those events so they remain readable instead of competing with
    compact status notifications or duplicating the same message in two places.
    """
    items: list[tuple[str, str]] = []
    for note in transient_notes or ():
        text = str(note or "").strip()
        if text:
            items.append(("class:notification-learning", text))
    if prt_done_unread:
        status = str(prt_done_unread).strip().casefold()
        label = (
            f"PRT {status}"
            if status in {"clean", "unresolved", "incomplete", "failed", "attention"}
            else "PRT finished"
        )
        items.append(("class:notification-prt", label))
    if goal_worker_active:
        progress = str(goal_progress or "").strip()
        suffix = f" {progress}" if progress else ""
        items.append(("class:notification-goal", f"Goal running{suffix}"))
    elif goal_done_unread:
        items.append(("class:notification-goal", "Goal done"))
    if pending_count:
        items.append(("class:notification-worker", f"Queued ({pending_count})"))
    if steer_count:
        items.append(("class:notification-worker", f"Steer pending ({steer_count})"))
    return items


def footer_notification_fragment(items: list[tuple[str, str]], *, now: float | None = None) -> tuple[str, str] | None:
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    current = time.time() if now is None else float(now)
    index = int(current / 2) % len(items)
    return items[index]


def compact_path_for_footer(path: str, *, max_chars: int = 28) -> str:
    """Return a stable first+tail project path for the footer."""
    raw = str(path or "").strip()
    if not raw:
        return ""
    value = raw.replace("/", "\\") if re.match(r"^[A-Za-z]:[/\\]", raw) or "\\" in raw else raw
    limit = max(8, int(max_chars or 28))
    if len(value) <= limit:
        return value
    sep = "\\" if "\\" in value else "/"
    if re.match(r"^[A-Za-z]:\\", value):
        head = value[:3]
        parts = [part for part in value[3:].split(sep) if part]
    elif value.startswith("~" + sep):
        head = "~" + sep
        parts = [part for part in value[2:].split(sep) if part]
    elif value.startswith(sep):
        head = sep
        parts = [part for part in value[1:].split(sep) if part]
    else:
        split = [part for part in value.split(sep) if part]
        head = (split[0] + sep) if len(split) > 1 else ""
        parts = split[1:] if len(split) > 1 else split
    tail_parts = parts[-2:] if len(parts) >= 2 else parts[-1:]
    tail = sep.join(tail_parts) if tail_parts else value[-max(1, limit - len(head) - 1):]
    candidate = f"{head}…{sep}{tail}" if head else f"…{sep}{tail}"
    if len(candidate) <= limit:
        return candidate
    last = tail_parts[-1] if tail_parts else tail
    candidate = f"{head}…{sep}{last}" if head else f"…{sep}{last}"
    if len(candidate) <= limit:
        return candidate
    keep = max(1, limit - len(head) - 2)
    return f"{head}…{last[-keep:]}"


def footer_left_fragments(agent: Any, *, notice_frag: tuple[str, str] | None = None) -> list[tuple[str, str]]:
    status = token_status_from_agent(agent)
    model_short = str(status.model or "")
    if model_short and status.provider_name:
        prefix = str(status.provider_name).lower()
        if model_short.lower().startswith(prefix + "-"):
            model_short = model_short[len(prefix) + 1:]
    model_label = f"{status.provider_name} / {model_short}".strip(" /")
    reasoning = str(status.reasoning or "").strip()
    reasoning_text = f" · {reasoning}" if reasoning else ""
    project = compact_path_for_footer(str(getattr(agent, "project_cwd", "") or os.environ.get("MO_PROJECT_CWD", "") or ""))
    prefix = f"{project} · " if project else ""
    token_part = f"↑{format_k(status.input_tokens)} ↓{format_k(status.output_tokens)}"
    # Compression savings
    saved_part = ""
    if status.saved_tokens_est > 0:
        total_est = status.input_tokens + status.saved_tokens_est
        pct = round(status.saved_tokens_est / max(1, total_est) * 100, 1)
        pct_str = f"{pct}%" if pct >= 0.1 else "<0.1%"
        saved_part = f" ◎~{format_k(status.saved_tokens_est)} ({pct_str})"
    base = f"{prefix}{token_part}{saved_part} · {model_label}{reasoning_text}"
    # Official DeepSeek API only: show live account balance (cached, non-blocking).
    try:
        from core.provider.deepseek_balance import balance_text as _ds_balance
        _bal = _ds_balance(getattr(agent, "active_provider", None))
        if _bal:
            base = f"{base} · {_bal}"
    except Exception:
        pass
    # OpenAI Codex OAuth has no balance for this auth, so show live quota usage
    # (percent used + reset) captured from response headers (cached, non-blocking).
    try:
        from core.provider.codex_usage import usage_text as _codex_usage
        _use = _codex_usage(getattr(agent, "active_provider", None))
        if _use:
            base = f"{base} · {_use}"
    except Exception:
        pass
    frags = [("class:footer", base)]
    # Live self-update notice — cached + non-blocking, same render-safe pattern as
    # the balance above. Surfaces "N commits behind upstream" with no user action.
    try:
        from core.update.check import update_status_text as _upd_text
        _cfg = getattr(agent, "config", {})
        _on = (_cfg.get("update", {}) if isinstance(_cfg, dict) else {}).get("check", DEFAULT_PREFERENCES["update.check"])
        _upd = _upd_text(enabled=bool(_on))
        if _upd:
            frags.extend([("class:footer", " · "), ("class:info", f"{_upd} (/update)")])
    except Exception:
        pass
    if notice_frag:
        frags.extend([("class:footer", " • "), notice_frag])
    return frags


def clip_text_to_cells(text: str, max_cells: int) -> str:
    """Clip text to a display-cell budget, reserving one cell for ellipsis."""
    limit = max(0, int(max_cells or 0))
    value = str(text or "")
    if limit <= 0:
        return ""
    if cell_width(value) <= limit:
        return value
    if limit == 1:
        return "…"
    chunk = split_cells(value, limit - 1)[0].rstrip()
    while chunk and cell_width(chunk) > limit - 1:
        chunk = chunk[:-1].rstrip()
    return f"{chunk}…"


def fit_fragments_to_cells(fragments: list[tuple[str, str]], max_cells: int) -> tuple[list[tuple[str, str]], int]:
    """Return fragments clipped to max_cells and their rendered cell width."""
    remaining = max(0, int(max_cells or 0))
    out: list[tuple[str, str]] = []
    used = 0
    for style, text in fragments:
        if remaining <= 0:
            break
        clipped = clip_text_to_cells(text, remaining)
        if not clipped and text:
            break
        out.append((style, clipped))
        clipped_width = cell_width(clipped)
        used += clipped_width
        remaining -= clipped_width
        if clipped != str(text or ""):
            break
    return out, used


_WORKER_KIND_LABELS: dict[str, str] = {
    "goal": "Goal",
    "prt": "PRT",
    "shell": "Tests",
    "worker": "Worker",
}

_WORKER_KIND_STYLES: dict[str, str] = {
    "goal": "source-goal",
    "prt": "source-prt",
    "shell": "source-worker",
    "worker": "source-worker",
}

_WORKER_STATE_GLYPHS: dict[str, str] = {
    "running": "◐",
    "offered": "◐",
    "blocked": "!",
    "completed": "✓",
    "cancelled": "·",
    "paused": "·",
}

# Internal worker kinds that should not appear in the footer cluster.
_INTERNAL_WORKER_KINDS = frozenset({"mapper"})


def workspace_active_fragments(agent: Any) -> list[tuple[str, str]]:
    """Footer "who's active now" cluster from TRUE workers, coloured per source.

    Display-only. Reads the worker registry (``agent.workers.active()``) so the
    footer can never drift from the workspace panel. Returns ``[]`` when nothing
    true-active, so the caller falls back to the existing worker-status string.
    """
    workers = getattr(getattr(agent, "workers", None), "active", None)
    if not callable(workers):
        return []
    try:
        records = list(workers())
    except Exception:
        return []
    frags: list[tuple[str, str]] = []
    for record in records:
        kind = str(getattr(record, "kind", "") or "").lower()
        if kind in _INTERNAL_WORKER_KINDS:
            continue
        label = _WORKER_KIND_LABELS.get(kind)
        if not label:
            continue
        style = f"class:{_WORKER_KIND_STYLES.get(kind, 'source-generic')}"
        state = str(getattr(record, "state", "") or "").lower()
        glyph = _WORKER_STATE_GLYPHS.get(state, "·")
        if frags:
            frags.append(("class:footer", " · "))
        frags.append((style, f"{glyph} {label}"))
    return frags


def footer_fragments(left_frags: list[tuple[str, str]], *, columns: int, right: str = "", right_style: str = "", right_fragments: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    columns = max(1, int(columns or 1))
    if right_fragments:
        # Coloured right cluster (workspace active sources). Mirrors the same
        # right-anchored width/padding contract as the string path below, so a
        # long left side is cell-clipped before the right cluster is appended.
        right_clipped, right_width = fit_fragments_to_cells(list(right_fragments), columns)
        right_gap = 1 if right_width and right_width < columns else 0
        max_left = max(0, columns - right_width - right_gap)
        out, total_len = fit_fragments_to_cells(left_frags, max_left)
        pad = max(0, columns - total_len - right_width)
        out.append(("class:footer", " " * pad))
        out.extend(right_clipped)
        return out
    right_text = str(right or "")
    right_text = clip_text_to_cells(right_text, columns)
    right_width = cell_width(right_text)
    right_gap = 1 if right_text and right_width < columns else 0
    max_left = max(0, columns - right_width - right_gap)

    out, total_len = fit_fragments_to_cells(left_frags, max_left)

    pad = max(0, columns - total_len - right_width)
    out.append(("class:footer", " " * pad))
    if right_text:
        r_style = right_style if right_style else "class:palette-hint"
        out.append((r_style, right_text))
    return out


def status_bar_fragments(left_frags: list[tuple[str, str]], right: str, *, columns: int) -> list[tuple[str, str]]:
    columns = max(1, int(columns or 1))
    right_text = clip_text_to_cells(right, columns)
    right_width = cell_width(right_text)
    out, left_len = fit_fragments_to_cells(left_frags, max(0, columns - right_width))
    pad = max(0, columns - left_len - right_width)
    out.extend([("", " " * pad), ("class:palette-hint", right_text)])
    return out


def status_line_mode(state: Any, *, now: float | None = None) -> str:
    """Return ``notice``, ``idle``, or ``hidden`` for the one status row."""
    current = time.time() if now is None else float(now)
    notice_active = bool(
        getattr(state, "_notice_text", "")
        and current <= float(getattr(state, "_notice_until", 0.0) or 0.0)
    )
    if notice_active:
        return "notice"
    foreground_work = bool(
        getattr(state, "busy", False)
        or (
            getattr(state, "_goal_worker_active", False)
            and not getattr(state, "_goal_backgrounded", False)
        )
    )
    return "hidden" if foreground_work else "idle"


def status_left_fragments(*, notice_text: str, notice_until: float, idle_style: str = "class:notification-idle", hint_text: str = "", now: float | None = None) -> tuple[list[tuple[str, str]], bool]:
    current = time.time() if now is None else float(now)
    if notice_text and current <= notice_until:
        # Check for error/critical
        style = "class:notification-critical" if "error" in notice_text.lower() or "aborted" in notice_text.lower() else "class:activity"
        # A notice is not running work; the foreground activity lane owns spinners.
        return [(style, notice_text)], True
    if hint_text:
        return [("class:notification-idle", f"💡 {hint_text}")], False
    return [(idle_style, idle_status_text(current))], False
