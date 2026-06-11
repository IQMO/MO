from __future__ import annotations

try:
    from rich.markup import escape as rich_escape
except ImportError:
    def rich_escape(value: str) -> str:
        return str(value).replace("[", r"\[")

from core.tasking.task_board import TaskBoard


def task_board_fragments_from_text(board_text: str, *, root_prefix: str = "     ", skip_summary: bool = False, scroll_from_bottom: int = 0, visible_rows: int = 0) -> list[tuple[str, str]]:
    """Return TUI fragments for an already-rendered plain task board.

    This is display-only: task truth remains owned by Gateway/Agent/TaskBoard.

    When *visible_rows* > 0 and the board has more lines, the output is sliced
    according to *scroll_from_bottom* (0 = show bottom, like terminal scroll).
    """
    if not board_text:
        return [("", "")]
    fragments: list[tuple[str, str]] = []
    lines = str(board_text).splitlines()
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
    }
    for index, line in enumerate(selected):
        s = line.strip()
        style = marker_styles.get(s[:1], "class:task-info")
        prefix = root_prefix if index == 0 and not (total_lines > visible) else "     "
        # Use the summary prefix on first line regardless of slicing
        if index == 0 and start > 0:
            prefix = root_prefix
        fragments.append((style, f"{prefix}{line}\n"))
    return fragments or [("", "")]


def render_plain(board: TaskBoard) -> str:
    s = board.summary()
    lines = [f"{s['total']} tasks ({s['done']} done, {s['open']} open)"]
    from core.tasking.task_board import status_marker
    for task in s["tasks"]:
        suffix = ""
        prefix = status_marker(task["status"])
        if task["status"] == "blocked" and task["blocker"]:
            suffix = f" — {task['blocker']}"
        title = str(task.get("title", ""))
        if len(title) > 100:
            title = title[:97] + "..."
        lines.append(f"  {prefix} {title}{suffix}")
        if task.get("evidence"):
            last_ev = str(task["evidence"][-1])
            if len(last_ev) > 100:
                last_ev = last_ev[:97] + "..."
            lines.append(f"       ↳ {last_ev}")
    return "\n".join(lines)


def render_rich(board: TaskBoard) -> str:
    s = board.summary()
    lines: list[str] = []
    count_line = f"[dim]{s['total']} tasks ({s['done']} done, {s['open']} open)[/dim]"
    lines.append(count_line)

    for task in s["tasks"]:
        title = str(task.get("title", ""))
        if len(title) > 100:
            title = title[:97] + "..."
        title = rich_escape(title)
        blocker = rich_escape(str(task["blocker"])) if task["blocker"] else ""
        if task["status"] == "completed":
            lines.append(f"    [green]√[/green] [dim]{title}[/dim]")
        elif task["status"] == "active":
            lines.append(f"    [orange1]→[/orange1] {title}")
        elif task["status"] == "blocked":
            suffix = f" [dim]— {blocker}[/dim]" if blocker else ""
            lines.append(f"    [red]![/red] {title}{suffix}")
        else:
            lines.append(f"    [dim]□ {title}[/dim]")
            
        if task.get("evidence"):
            last_ev = str(task["evidence"][-1])
            if len(last_ev) > 100:
                last_ev = last_ev[:97] + "..."
            last_ev = rich_escape(last_ev)
            lines.append(f"       [dim]↳ {last_ev}[/dim]")
    return "\n".join(lines)

