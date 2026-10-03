from __future__ import annotations

try:
    from rich.markup import escape as rich_escape
except ImportError:
    def rich_escape(value: str) -> str:
        return str(value).replace("[", r"\[")

from core.tasking.task_board import TaskBoard
from .activity import clip_text_to_cells, duration_text


def completed_board_duration_text(board: TaskBoard | None) -> str:
    """Return a frozen duration only for a fully completed real task board."""
    if board is None:
        return ""
    try:
        summary = board.summary()
        total = int(summary.get("total") or 0)
        done = int(summary.get("done") or 0)
        open_count = int(summary.get("open") or 0)
        started = float(summary.get("created_at") or 0.0)
        finished = float(summary.get("updated_at") or 0.0)
    except (AttributeError, TypeError, ValueError):
        return ""
    if total <= 0 or done != total or open_count or summary.get("state") != "completed":
        return ""
    if started <= 0 or finished < started:
        return ""
    rendered = duration_text(finished - started)
    return rendered[:-2] if rendered.endswith("m0s") else rendered


def task_board_fragments_from_text(
    board_text: str,
    *,
    root_prefix: str = "     ",
    skip_summary: bool = False,
    scroll_from_bottom: int = 0,
    visible_rows: int = 0,
    completion_duration: str = "",
    edit_additions: int = 0,
    edit_deletions: int = 0,
    columns: int = 0,
) -> list[tuple[str, str]]:
    """Return TUI fragments for an already-rendered plain task board.

    This is display-only: task truth remains owned by Gateway/Agent/TaskBoard.

    When *visible_rows* > 0 and the board has more lines, the output is sliced
    according to *scroll_from_bottom* (0 = show bottom, like terminal scroll).
    *columns* clips live rows with an explicit ellipsis before the terminal edge.
    """
    if not board_text:
        return [("", "")]
    fragments: list[tuple[str, str]] = []
    lines = str(board_text).splitlines()
    duration = str(completion_duration or "").strip()
    added = max(0, int(edit_additions or 0))
    removed = max(0, int(edit_deletions or 0))
    completion_details = [duration] if duration else []
    if added or removed:
        completion_details.append(f"+{added} -{removed}")
    if completion_details and lines and not skip_summary:
        first = lines[0]
        close = first.rfind(")")
        if "tasks" in first and "(" in first and close >= 0:
            lines[0] = f"{first[:close]}, {', '.join(completion_details)}{first[close:]}"
    if skip_summary and lines:
        first = lines[0].strip()
        if "tasks" in first and "(" in first:
            lines = lines[1:]

    total_lines = len(lines)
    visible = max(1, int(visible_rows or 0)) if visible_rows else total_lines
    max_from_bottom = max(0, total_lines - visible)
    adjusted_scroll = max(0, min(max_from_bottom, int(scroll_from_bottom or 0)))
    start = max(0, total_lines - visible - adjusted_scroll)
    selected = lines[start : start + visible]

    # Scroll indicator when content is clipped
    if total_lines > visible:
        indicator = f"  [↑{start + 1}-{start + len(selected)}/{total_lines} scroll Ctrl+↑/↓]"
        fragments.append(("class:dim", f"{indicator}\n"))

    from core.tasking.task_board import STATUS_MARKERS
    marker_styles = {
        STATUS_MARKERS["completed"]: "class:task-done",
        STATUS_MARKERS["active"]: "class:task-active",
        STATUS_MARKERS["blocked"]: "class:task-blocked",
        STATUS_MARKERS["pending"]: "class:task-pending",
        STATUS_MARKERS["cancelled"]: "class:dim",
    }
    for index, line in enumerate(selected):
        s = line.strip()
        style = marker_styles.get(s[:1], "class:task-info")
        prefix = root_prefix if index == 0 and not (total_lines > visible) else "     "
        # Use the summary prefix on first line regardless of slicing
        if index == 0 and start > 0:
            prefix = root_prefix
        rendered = f"{prefix}{line}"
        if columns:
            rendered = clip_text_to_cells(rendered, columns)
        fragments.append((style, f"{rendered}\n"))
    return fragments or [("", "")]


def render_plain(board: TaskBoard) -> str:
    """Return core's renderer-neutral snapshot for terminal composition."""
    return board.render()


def render_rich(board: TaskBoard) -> str:
    s = board.summary()
    lines: list[str] = []
    parked = s["state"] == "abandoned" and bool(s["open"])
    lifecycle = " · paused/resumable" if parked else ""
    if s["cancelled"]:
        lifecycle += f" · {s['cancelled']} cancelled"
    count_line = f"[dim]{s['total']} tasks ({s['done']} done, {s['open']} open){lifecycle}[/dim]"
    lines.append(count_line)

    for task in s["tasks"]:
        title = str(task.get("title", ""))
        if len(title) > 100:
            title = title[:97] + "..."
        title = rich_escape(title)
        blocker = rich_escape(str(task["blocker"])) if task["blocker"] else ""
        if parked and task["status"] == "active":
            lines.append(f"    [dim]□ {title}[/dim]")
        elif task["status"] == "completed":
            lines.append(f"    [green]√[/green] [dim]{title}[/dim]")
        elif task["status"] == "cancelled":
            lines.append(f"    [dim]× {title} (cancelled)[/dim]")
        elif task["status"] == "active":
            lines.append(f"    [orange1]→[/orange1] {title}")
        elif task["status"] == "blocked":
            suffix = f" [dim]— {blocker}[/dim]" if blocker else ""
            lines.append(f"    [red]![/red] {title}{suffix}")
        else:
            lines.append(f"    [dim]□ {title}[/dim]")
    return "\n".join(lines)
