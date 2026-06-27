"""Display-only activity, status-bar, and footer helpers."""
from __future__ import annotations

import os
import re
import time
from typing import Any

from .formatting import activity_label, format_k, idle_status_text, moon_phase_frame, token_status_from_agent
from .transcript_view import cell_width


def board_summary_text(board_text: str) -> str:
    if not board_text:
        return ""
    first = board_text.splitlines()[0].strip()
    return first if "tasks" in first and "(" in first else ""


def elapsed_seconds_text(started_at: float | None, *, now: float | None = None) -> str:
    if not started_at:
        return ""
    current = time.time() if now is None else float(now)
    elapsed = max(0, int(current - started_at))
    return f"{elapsed}s"


def goal_elapsed_text(started_at: float | None, *, now: float | None = None) -> str:
    if not started_at:
        return "0s"
    current = time.time() if now is None else float(now)
    elapsed = max(0, int(current - started_at))
    mins, secs = divmod(elapsed, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h{mins}m"
    if mins:
        return f"{mins}m{secs}s"
    return f"{secs}s"


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
    now: float | None = None,
    moon_style: str = "",
) -> list[tuple[str, str]]:
    if not (busy or (goal_worker_active and not goal_backgrounded)):
        return [("", "")]
    current = time.time() if now is None else float(now)
    frame = moon_phase_frame(current)
    if busy:
        label = activity_label(activity_text or "working")
        elapsed = elapsed_seconds_text(activity_started_at, now=current)
        summary = board_summary_text(board_text)
    else:
        label = "Goal Working…"
        elapsed = goal_elapsed_text(goal_started_at, now=current)
        summary = board_summary_text("" if goal_backgrounded else goal_board_text)
    details = [item for item in (elapsed, summary) if item]
    detail_text = " · ".join(details)
    suffix = f" ({detail_text})" if detail_text else ""
    mo_style = moon_style if moon_style else "class:activity"
    return [("class:spinner", f" {frame} "), (mo_style, "MO"), ("class:activity", f" {label}{suffix}")]


def notification_items(*, ghost_unread_count: int, goal_worker_active: bool, goal_done_unread: bool, pending_count: int, prt_done_unread: bool = False, goal_progress: str = "") -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    if prt_done_unread:
        items.append(("class:notification-prt", "PRT ready: Alt+G"))
    if ghost_unread_count:
        suffix = f" ({ghost_unread_count})" if ghost_unread_count > 1 else ""
        items.append(("class:notification-idle", f"Ghost replied{suffix}: Alt+G"))
    if goal_worker_active:
        progress = str(goal_progress or "").strip()
        suffix = f" {progress}" if progress else ""
        items.append(("class:notification-goal", f"Goal running{suffix}"))
    elif goal_done_unread:
        items.append(("class:notification-goal", "Goal done"))
    if pending_count:
        items.append(("class:notification-worker", f"Queued ({pending_count})"))
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
    model_label = f"{status.provider_name} / {status.model}".strip(" /")
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
        saved_part = f" ◎~{format_k(status.saved_tokens_est)} ({pct}%)"
    base = f"{prefix}{token_part}{saved_part} · {model_label}{reasoning_text}"
    # Official DeepSeek API only: show live account balance (cached, non-blocking).
    try:
        from core.provider.deepseek_balance import balance_text as _ds_balance
        _bal = _ds_balance(getattr(agent, "active_provider", None))
        if _bal:
            base = f"{base} · {_bal}"
    except Exception:
        pass
    frags = [("class:footer", base)]
    # Live self-update notice — cached + non-blocking, same render-safe pattern as
    # the balance above. Surfaces "N commits behind upstream" with no user action.
    try:
        from core.update.check import update_status_text as _upd_text
        _cfg = getattr(agent, "config", {})
        _on = (_cfg.get("update", {}) if isinstance(_cfg, dict) else {}).get("check", True)
        _upd = _upd_text(enabled=bool(_on))
        if _upd:
            frags.extend([("class:footer", " · "), ("class:info", f"{_upd} (/update)")])
    except Exception:
        pass
    if notice_frag:
        frags.extend([("class:footer", " • "), notice_frag])
    return frags


def footer_fragments(left_frags: list[tuple[str, str]], *, columns: int, right: str = "", right_style: str = "") -> list[tuple[str, str]]:
    right_text = str(right or "")
    right_gap = 1 if right_text else 0
    max_left = max(1, columns - cell_width(right_text) - right_gap)

    total_len = 0
    out = []
    for style, text in left_frags:
        if total_len + cell_width(text) > max_left:
            allowed = max_left - total_len
            if allowed > 0:
                out.append((style, text[:max(1, allowed - 1)] + "…"))
            break
        out.append((style, text))
        total_len += cell_width(text)

    pad = max(0, columns - total_len - cell_width(right_text))
    out.append(("class:footer", " " * pad))
    if right_text:
        r_style = right_style if right_style else "class:palette-hint"
        out.append((r_style, right_text))
    return out


def status_bar_fragments(left_frags: list[tuple[str, str]], right: str, *, columns: int) -> list[tuple[str, str]]:
    left_len = sum(cell_width(text) for _, text in left_frags)
    pad = max(0, columns - left_len - cell_width(right))
    out = list(left_frags)
    out.extend([("", " " * pad), ("class:palette-hint", right)])
    return out


def status_left_fragments(*, notice_text: str, notice_until: float, idle_style: str = "class:notification-idle", hint_text: str = "", now: float | None = None) -> tuple[list[tuple[str, str]], bool]:
    current = time.time() if now is None else float(now)
    if notice_text and current <= notice_until:
        # Check for error/critical
        style = "class:notification-critical" if "error" in notice_text.lower() or "aborted" in notice_text.lower() else "class:activity"
        return [(style, f"{moon_phase_frame(current)} {notice_text}")], True
    if hint_text:
        return [("class:notification-idle", f"💡 {hint_text}")], False
    return [(idle_style, idle_status_text(current))], False
