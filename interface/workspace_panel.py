"""Prompt-toolkit views for worker activity and the split-terminal workspace."""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
import time
from typing import Any

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout import ScrollablePane
from prompt_toolkit.layout.containers import (
    DynamicContainer,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension

from .activity import fit_fragments_to_cells, status_line_mode
from .formatting import brand_spinner_frame
from .transcript_view import cell_width, fit_cells, wrap_fragment_line
from .workspace import STATUS_GLYPHS, WorkspaceTile, active_sources

MAX_ACTIVITY_ROWS = 6
MIN_GRID_COLUMNS = 20
MIN_GRID_ROWS = 3
NARROW_GRID_BREAKPOINT = 82
LEAD_W = 3
LABEL_W = 12
STATUS_W = 9
_FIXED_W = LEAD_W + LABEL_W + STATUS_W
_STATUS_STYLE = {
    "working": "class:activity",
    "blocked": "class:task-blocked",
    "done": "class:status-done",
    "idle": "class:dim",
}
_TITLE_STYLES = tuple(f"class:workspace-title-{index}" for index in range(1, 7))


@dataclass(frozen=True)
class WorkspaceGridPlan:
    """One bounded responsive projection of canonical pane order."""

    row_count: int
    column_count: int
    column_widths: tuple[int, ...]
    row_heights: tuple[int, ...]
    content_rows: int


def workspace_grid_shape(
    count: int,
    terminal_columns: int,
    terminal_rows: int,
) -> tuple[int, int]:
    """Choose rows/columns without letting pane geometry exceed the viewport."""
    count = max(1, int(count or 1))
    columns = max(1, int(terminal_columns or 1))
    rows = max(1, int(terminal_rows or 1))
    preferred_columns = 1 if columns < NARROW_GRID_BREAKPOINT else min(2, count)
    columns_that_fit = max(1, min(count, (columns + 1) // (MIN_GRID_COLUMNS + 1)))
    rows_that_fit = max(1, (rows + 1) // (MIN_GRID_ROWS + 1))
    columns_needed_for_height = min(count, ceil(count / rows_that_fit))
    column_count = min(
        columns_that_fit,
        max(preferred_columns, columns_needed_for_height),
    )
    return ceil(count / column_count), column_count


def _axis_sizes(total: int, parts: int) -> tuple[int, ...]:
    """Distribute one axis exactly, reserving one cell between each part."""
    parts = max(1, int(parts))
    usable = max(parts, int(total) - (parts - 1))
    size, remainder = divmod(usable, parts)
    return tuple(size + (1 if index < remainder else 0) for index in range(parts))


def workspace_grid_plan(
    count: int,
    terminal_columns: int,
    terminal_rows: int,
) -> WorkspaceGridPlan:
    """Return exact tile sizes and a scroll height when minimum rows cannot fit."""
    columns = max(1, int(terminal_columns or 1))
    rows = max(1, int(terminal_rows or 1))
    row_count, column_count = workspace_grid_shape(count, columns, rows)
    minimum_content_rows = row_count * MIN_GRID_ROWS + (row_count - 1)
    content_rows = max(rows, minimum_content_rows)
    return WorkspaceGridPlan(
        row_count=row_count,
        column_count=column_count,
        column_widths=_axis_sizes(columns, column_count),
        row_heights=_axis_sizes(content_rows, row_count),
        content_rows=content_rows,
    )


def _activity_header_fragments(count: int, *, columns: int) -> list[tuple[str, str]]:
    return [("class:workspace-title", fit_cells(f"  activity · {count} active", columns))]


def _activity_row_fragments(source: Any, *, columns: int) -> list[tuple[str, str]]:
    detail_width = max(0, columns - _FIXED_W)
    glyph = STATUS_GLYPHS.get(source.status, "·")
    fragments = [
        (f"class:{source.style_class}", fit_cells(f" {glyph} ", LEAD_W)),
        (f"class:{source.style_class}", fit_cells(source.label, LABEL_W)),
        ("class:dim", fit_cells(source.detail, detail_width)),
        (_STATUS_STYLE.get(source.status, "class:dim"), fit_cells(source.status, STATUS_W)),
    ]
    clipped, _used = fit_fragments_to_cells(fragments, columns)
    return clipped


def activity_panel_fragments(agent: Any, *, columns: int) -> list[tuple[str, str]]:
    """Render the true-worker panel from the canonical registry projection."""
    columns = max(20, int(columns or 80))
    sources = active_sources(agent)
    if not sources:
        return []
    output = list(_activity_header_fragments(len(sources), columns=columns))
    for source in sources[:MAX_ACTIVITY_ROWS]:
        output.append(("", "\n"))
        output.extend(_activity_row_fragments(source, columns=columns))
    hidden = len(sources) - min(MAX_ACTIVITY_ROWS, len(sources))
    if hidden:
        output.extend((("", "\n"), ("class:dim", fit_cells(f"  +{hidden} more", columns))))
    return output


def activity_row_count(agent: Any) -> int:
    sources = active_sources(agent)
    if not sources:
        return 0
    return 1 + min(MAX_ACTIVITY_ROWS, len(sources)) + (
        1 if len(sources) > MAX_ACTIVITY_ROWS else 0
    )


def tile_title_fragments(
    tile: WorkspaceTile,
    *,
    width: int = 80,
) -> list[tuple]:
    style = _TITLE_STYLES[(tile.position - 1) % len(_TITLE_STYLES)]
    if tile.focused:
        style += " class:workspace-focused"
    owner = tile.title or ("MO" if tile.pane_id == "main" else tile.destination.label)
    parts = (
        (owner, "main")
        if tile.pane_id == "main"
        else (owner, tile.worker_id or tile.pane_id, tile.detail)
    )
    text = " " + " · ".join(part for part in parts if part)
    return [(style, fit_cells(text, max(1, int(width))))]


def _tile_body_rows(
    tile: WorkspaceTile,
    *,
    width: int | None = None,
) -> list[tuple[tuple[str, str], ...]]:
    rows = list(tile.lines)
    if tile.error:
        rows.append((("class:workspace-error", f"  {tile.error}"),))
    if (tile.pane_id == "main" or tile.destination.value == "host") and width is not None:
        # MO and MO-host panes contain logical transcript lines, unlike local
        # PTYs whose VT screen owns exact wrapping. Reflow those logical rows so
        # narrow splits do not silently clip prose or startup information.
        rows = [
            tuple(visual_row)
            for row in rows
            for visual_row in wrap_fragment_line(list(row), max(8, int(width)))
        ]
    if not rows:
        message = "  terminal ready" if tile.pane_id != "main" else "  MO ready"
        rows = [(("class:dim", message),)]
    return rows


def tile_body_fragments(
    rows: list[tuple[tuple[str, str], ...]],
    *,
    visible_rows: int,
    scroll_offset: int = 0,
) -> list[tuple[str, str]]:
    visible = max(1, int(visible_rows))
    offset = min(max(0, int(scroll_offset)), max(0, len(rows) - visible))
    end = len(rows) - offset
    rows = rows[max(0, end - visible):end]
    output: list[tuple[str, str]] = []
    for line_index, line in enumerate(rows):
        if line_index:
            output.append(("", "\n"))
        output.extend((style, text) for style, text in line)
    return output


def pane_input_fragments(
    controller: Any,
    tile: WorkspaceTile,
    *,
    width: int,
) -> list[tuple[str, str]]:
    """Project one pane's saved draft while another pane owns the live editor."""
    try:
        draft = str(controller.input_draft(tile.pane_id) or "")
    except Exception:
        draft = ""
    text = draft.splitlines()[-1] if draft else "focus to type"
    style = "" if draft else "class:input-placeholder"
    return [
        ("class:mo-marker", " ❯ "),
        (style, fit_cells(text, max(1, int(width) - 3))),
    ]


def _tile_container(
    tui: Any,
    controller: Any,
    tile: WorkspaceTile,
    *,
    columns: int,
    rows: int,
    composer: Any = None,
    composer_rows: Any = 1,
):
    columns = max(1, int(columns))
    rows = max(2, int(rows))
    owns_composer = tile.pane_id == "main" or tile.destination.value == "host"
    # Keep inactive MO-owned composer chrome visible while a local PTY owns the
    # live BufferControl. Only one pane remains editable; this projection keeps
    # split geometry stable instead of making pane 1 jump when focus changes.
    shows_composer = owns_composer and (composer is not None or not tile.focused)
    editor_rows = 0
    if shows_composer:
        requested_rows = composer_rows() if callable(composer_rows) else composer_rows
        editor_rows = min(max(1, int(requested_rows)), max(1, rows - 3))
    owns_mo_chrome = tile.pane_id == "main"
    status_visible = owns_mo_chrome and status_line_mode(tui) != "hidden"
    body_rows = max(
        1,
        rows
        - 1
        - int(status_visible)
        - int(owns_mo_chrome)
        - editor_rows
        - (2 if editor_rows else 0),
    )
    controller.resize_pane(tile.pane_id, columns=columns, rows=body_rows)
    body_lines = _tile_body_rows(tile, width=columns)
    set_scroll_limit = getattr(controller, "set_pane_scroll_limit", None)
    if callable(set_scroll_limit):
        set_scroll_limit(
            tile.pane_id,
            max(0, len(body_lines) - body_rows),
        )
    get_scroll_offset = getattr(controller, "pane_scroll_offset", None)

    def render_body() -> FormattedText:
        offset = get_scroll_offset(tile.pane_id) if callable(get_scroll_offset) else 0
        return FormattedText(tile_body_fragments(
            body_lines, visible_rows=body_rows, scroll_offset=offset,
        ))

    title = Window(
        content=FormattedTextControl(
            lambda tile=tile, columns=columns: FormattedText(
                tile_title_fragments(tile, width=columns)
            )
        ),
        height=1,
        dont_extend_height=True,
        style="class:workspace-title",
    )
    body = Window(
        content=FormattedTextControl(render_body),
        wrap_lines=False,
        always_hide_cursor=True,
        style="class:workspace-body",
    )
    children = [title, body]
    if status_visible:
        children.append(Window(
            content=FormattedTextControl(
                lambda tui=tui, columns=columns: FormattedText(
                    tui._get_status_bar_fragments(columns=columns)
                )
            ),
            height=1,
            dont_extend_height=True,
            wrap_lines=False,
            style="class:app-bg",
        ))
    if editor_rows:
        pane_input = composer if tile.focused else Window(
            content=FormattedTextControl(
                lambda controller=controller, tile=tile, columns=columns: FormattedText(
                    pane_input_fragments(controller, tile, width=columns)
                )
            ),
            height=editor_rows,
            dont_extend_height=True,
            wrap_lines=False,
            style="class:app-bg",
        )
        separator_style = "class:activity" if tile.focused else "class:separator"
        children.extend((
            Window(height=1, char="─", style=separator_style),
            pane_input,
            Window(height=1, char="─", style=separator_style),
        ))
    if owns_mo_chrome:
        children.append(Window(
            content=FormattedTextControl(
                lambda tui=tui, columns=columns: FormattedText(
                    tui._get_footer_fragments(columns=columns)
                )
            ),
            height=1,
            dont_extend_height=True,
            wrap_lines=False,
            style="class:app-bg",
        ))
    return HSplit(
        children,
        width=Dimension.exact(columns),
        height=Dimension.exact(rows),
        style="class:workspace-body",
    )


def _responsive_grid(
    tui: Any,
    controller: Any,
    tiles: list[WorkspaceTile],
    *,
    columns: int,
    rows: int,
    composer: Any = None,
    composer_rows: Any = 1,
):
    columns = max(1, int(columns))
    rows = max(1, int(rows))
    plan = workspace_grid_plan(len(tiles), columns, rows)
    scrolling = plan.content_rows > rows
    content_columns = max(1, columns - 1) if scrolling else columns
    if content_columns != columns:
        plan = workspace_grid_plan(len(tiles), content_columns, rows)

    grid_rows = []
    for row_index in range(plan.row_count):
        row_height = plan.row_heights[row_index]
        row_items = []
        row_start = row_index * plan.column_count
        remaining = len(tiles) - row_start
        if remaining == 1 and plan.column_count > 1:
            # A lone final pane owns the row instead of leaving one or more
            # skin-painted dead slots beside it.
            row_items.append(
                _tile_container(
                    tui,
                    controller,
                    tiles[row_start],
                    columns=content_columns,
                    rows=row_height,
                    composer=composer,
                    composer_rows=composer_rows,
                )
            )
        else:
            for column_index in range(plan.column_count):
                if column_index:
                    row_items.append(
                        Window(width=1, char="│", style="class:workspace-border")
                    )
                tile_index = row_start + column_index
                if tile_index < len(tiles):
                    row_items.append(
                        _tile_container(
                            tui,
                            controller,
                            tiles[tile_index],
                            columns=plan.column_widths[column_index],
                            rows=row_height,
                            composer=composer,
                            composer_rows=composer_rows,
                        )
                    )
                else:
                    row_items.append(
                        Window(
                            width=Dimension.exact(plan.column_widths[column_index]),
                            height=Dimension.exact(row_height),
                            style="class:workspace-body",
                        )
                    )
        grid_rows.append(
            VSplit(
                row_items,
                padding=0,
                width=Dimension.exact(content_columns),
                height=Dimension.exact(row_height),
            )
        )
        if row_index < plan.row_count - 1:
            grid_rows.append(
                Window(height=1, char="─", style="class:workspace-border")
            )

    grid = HSplit(
        grid_rows,
        width=Dimension.exact(content_columns),
        height=Dimension.exact(plan.content_rows),
    )
    if not scrolling:
        return grid

    viewport = ScrollablePane(
        grid,
        width=Dimension.exact(columns),
        height=Dimension.exact(rows),
        max_available_height=plan.content_rows,
        keep_cursor_visible=False,
        keep_focused_window_visible=False,
        show_scrollbar=True,
        display_arrows=True,
    )
    focused_index = next(
        (index for index, tile in enumerate(tiles) if tile.focused),
        0,
    )
    focused_row = focused_index // plan.column_count
    row_top = sum(plan.row_heights[:focused_row]) + focused_row
    row_bottom = row_top + plan.row_heights[focused_row]
    viewport.vertical_scroll = max(0, row_bottom - rows)
    return viewport


def _health_percent(value: float | None) -> str:
    return "—" if value is None else f"{max(0.0, min(100.0, float(value))):.0f}%"


def _health_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    amount = max(0, int(value))
    if amount >= 1024 ** 3:
        return f"{amount / (1024 ** 3):.1f} GB"
    return f"{amount / (1024 ** 2):.0f} MB"


def health_load_status(
    cpu_percent: float | None,
    memory_percent: float | None,
) -> tuple[str, str, str]:
    """Return the evidence-based status chip used by the Health overview."""
    observed = [float(value) for value in (cpu_percent, memory_percent) if value is not None]
    if not observed:
        return "unavailable", "·", "No readings"
    if len(observed) != 2:
        return "partial", "·", "Partial readings"
    peak = max(observed)
    if peak >= 85.0:
        return "pressure", "!", "High resource use"
    if peak >= 65.0:
        return "working", "●", "Under load"
    return "normal", "✓", "Low resource use"


def _append_health_row(
    output: list[tuple[str, str]],
    fragments: list[tuple[str, str]],
    *,
    width: int,
) -> None:
    for row in wrap_fragment_line(fragments, max(1, int(width))) or [[]]:
        if output:
            output.append(("", "\n"))
        output.extend(row or [("", "")])


def workspace_terminal_name(tile: WorkspaceTile) -> str:
    """One human display name for the rail and Health; IDs still own routing."""
    detail = " ".join(str(tile.detail or "").split())
    if tile.title == "MO" and " · " in detail:
        return f"MO · {detail.split(' · ', 1)[0]}"
    if tile.pane_id == "main":
        return "MO · main"
    if tile.title == "MO":
        parts = ["MO", str(tile.worker_id or "").strip(), detail]
        return " · ".join(part for part in parts if part)
    return tile.title or "Terminal"


def workspace_terminal_state(state: str) -> tuple[str, str]:
    state = str(state or "").lower()
    if state in {"working", "starting", "closing"}:
        return brand_spinner_frame(), state
    if state in {"blocked", "error", "failed"}:
        return "!", state
    if state in {"done", "completed"}:
        return "✓", "done"
    return "○", {"running": "running", "idle": "idle", "exited": "exited"}.get(state, "unknown")


def _discovered_terminal_name(terminal: Any) -> str:
    instance = str(getattr(terminal, "instance_id", "") or "")
    label = " ".join(str(getattr(terminal, "label", "") or "").split())
    if not label or label == f"MO Terminal · {instance}":
        return f"MO · {instance[:8]}" if instance else "MO terminal"
    return label[:80]


def health_panel_fragments(
    tiles: list[WorkspaceTile],
    snapshot: Any,
    *,
    width: int,
    running_error: str = "",
    health_hosts: tuple[Any, ...] = (),
    local_machine_key: str = "",
    runtime: dict[str, Any] | None = None,
    now: float | None = None,
) -> list[tuple[str, str]]:
    """Render one quiet snapshot of machine, terminal, and runtime health."""
    from mo_everywhere.live_control import RESOURCE_STALE_SECONDS

    now = time.monotonic() if now is None else now
    output: list[tuple[str, str]] = []

    def row(text: str | list[tuple[str, str]], style: str = "class:mo-response") -> None:
        fragments = [(style, text)] if isinstance(text, str) else text
        _append_health_row(output, [("", " "), *fragments], width=width)

    def heading(text: str) -> None:
        row("")
        row(text, "class:response-heading")

    def age_text(age: float) -> str:
        seconds = max(0, int(age))
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        return f"{minutes // 60}h ago"

    def observation_style(state: str) -> str:
        if state in {"unavailable", "disconnected"}:
            return "class:workspace-error"
        if state in {"partial", "stale", "no reading"}:
            return "class:task-active"
        if state in {"warming", "collecting"}:
            return "class:info"
        return "class:dim"

    def resources_row(cpu, memory, count) -> None:
        if cpu is None and memory is None and count is None:
            return
        processes = "—" if count is None else str(count)
        unit = "process" if count == 1 else "processes"
        row([
            ("class:dim", "  CPU "),
            ("class:mo-response", _health_percent(cpu)),
            ("class:dim", " · "),
            ("class:mo-response", _health_bytes(memory)),
            ("class:dim", " · "),
            ("class:mo-response", f"{processes} {unit}"),
        ])

    def machine_resources_row(data: Any) -> None:
        getter = data.get if isinstance(data, dict) else lambda key: getattr(data, key, None)
        values = (
            getter("system_cpu_percent"),
            getter("memory_percent"),
            getter("memory_used_bytes"),
            getter("memory_total_bytes"),
        )
        if all(value is None for value in values):
            return
        row([
            ("class:dim", "  CPU "),
            ("class:mo-response", _health_percent(values[0])),
            ("class:dim", " · Memory "),
            ("class:mo-response", _health_percent(values[1])),
            ("class:dim", " · "),
            ("class:mo-response", (
                f"{_health_bytes(values[2])} / {_health_bytes(values[3])}"
            )),
        ])

    def host_age(host: Any) -> float:
        sample = getattr(host, "resources", None) or {}
        return float(sample.get("sample_age_seconds", 0)) + max(0.0, now - host.observed_at)

    def host_state(host: Any) -> str:
        if not getattr(host, "connected", True):
            return "disconnected"
        sample = getattr(host, "resources", None)
        if not sample:
            return "no reading" if getattr(host, "resources_supported", False) else "unsupported"
        return "stale" if host_age(host) > RESOURCE_STALE_SECONDS else sample["state"]

    def host_preference(host: Any) -> tuple[bool, bool, float]:
        return (getattr(host, "connected", True), bool(getattr(host, "resources", None)), -host_age(host))

    def observation_note(state: str) -> str:
        return {
            "ready": "",
            "warming": "Sampling CPU",
            "partial": "Some readings unavailable",
            "unavailable": "Readings unavailable",
            "unsupported": "Readings unsupported",
            "unmeasured": "Not measured",
            "stale": "Stale snapshot",
            "disconnected": "Disconnected",
            "no reading": "No reading received",
            "collecting": "Collecting",
        }.get(state, state)

    def machine(name: str, data: Any, state: str, age: float | None) -> None:
        getter = data.get if isinstance(data, dict) else lambda key: getattr(data, key, None)
        cpu, memory = getter("system_cpu_percent"), getter("memory_percent")
        load_state, glyph, load = health_load_status(cpu, memory)
        note = observation_note(state)
        label = note or f"{glyph} {load}"
        if age is not None:
            label += f" · {age_text(age)}"
        if note:
            style = observation_style(state)
        elif load_state == "pressure":
            style = "class:workspace-error"
        elif load_state == "working":
            style = "class:task-active"
        else:
            style = "class:dim"
        row([
            ("class:response-bullet-head", name),
            ("class:dim", " · "),
            (style, label),
        ])
        machine_resources_row(data)

    row([("class:response-heading", "Health"), ("class:dim", " · snapshot")])
    local_age = max(0.0, now - snapshot.sampled_at) if snapshot is not None else None
    local_state = getattr(snapshot, "state", "collecting")
    if local_age is not None and local_age > 5.0:
        local_state = "stale"
    heading("Machines")
    machine("This machine", snapshot, local_state, local_age if local_state == "stale" else None)

    hosts = tuple(health_hosts)
    by_instance = {host.instance_id: host for host in hosts}
    groups: dict[str, Any] = {}
    locations: dict[str, str] = {}
    for host in hosts:
        key = getattr(host, "machine_key", "") or getattr(host, "host_id", "") or host.instance_id
        if local_machine_key and key == local_machine_key:
            locations[host.instance_id] = "This machine"
            continue
        previous = groups.get(key)
        if previous is None or host_preference(host) > host_preference(previous):
            groups[key] = host
    for index, (key, host) in enumerate(groups.items(), 1):
        name = f"MO host {index}" if len(groups) > 1 else "MO host"
        for member in hosts:
            member_key = getattr(member, "machine_key", "") or getattr(member, "host_id", "") or member.instance_id
            if member_key == key:
                locations[member.instance_id] = name
        row("")
        platform = getattr(host, "platform_family", "")
        machine(name + (f" · {platform}" if platform else ""),
                getattr(host, "resources", None), host_state(host),
                host_age(host) if getattr(host, "resources", None) else None)
    if running_error:
        row(f"Host refresh failed · {running_error}", "class:workspace-error")

    heading("Terminals")
    samples = getattr(snapshot, "trees", {}) or {}
    for tile in tiles:
        location = locations.get(tile.worker_id, tile.destination.label)
        _, state_label = workspace_terminal_state(tile.state)
        lifecycle_style = _STATUS_STYLE.get(state_label, "class:mo-response")
        if state_label in {"error", "failed"}:
            lifecycle_style = "class:workspace-error"
        row([
            ("class:response-bullet-head", f"{tile.position} {workspace_terminal_name(tile)}"),
            ("class:dim", f" · {tile.project_name}" if tile.project_name else ""),
            ("class:dim", f" · {location} · "),
            (lifecycle_style, state_label),
        ])
        if tile.destination.value == "host":
            host = by_instance.get(tile.worker_id)
            sample = (getattr(host, "resources", None) or {}).get("process", {})
            state = host_state(host) if host is not None else "no reading"
            if state == "ready":
                state = sample.get("state", "unavailable")
            resources_row(sample.get("cpu_percent"), sample.get("memory_bytes"), sample.get("process_count"))
            note = observation_note(state)
            if note and host is not None and getattr(host, "resources", None):
                note = (note + " · " if note else "") + age_text(host_age(host))
        else:
            sample = samples.get(tile.pane_id)
            state = "stale" if local_state == "stale" else getattr(sample, "state", "collecting")
            resources_row(getattr(sample, "cpu_percent", None),
                          getattr(sample, "memory_bytes", None),
                          getattr(sample, "process_count", None))
            note = observation_note(state)
        if note:
            row(f"  {note}", "class:dim" if state == "ready" else observation_style(state))
        if tile.error:
            row(tile.error, "class:workspace-error")

    local = [samples.get(tile.pane_id) for tile in tiles if tile.destination.value == "local"]

    def total(field):
        values = [getattr(sample, field, None) for sample in local]
        return sum(values) if values and all(value is not None for value in values) else None

    if len(local) > 1:
        totals = (total("cpu_percent"), total("memory_bytes"), total("process_count"))
        total_state = "ready" if all(value is not None for value in totals) else "partial"
        row([("class:response-bullet-head", "Local total"),
             ("class:dim", " · includes child processes")])
        resources_row(*totals)
        note = observation_note(total_state)
        if note:
            row(f"  {note}", observation_style(total_state))

    if runtime and runtime.get("rows"):
        heading("Runtime")
        rows = tuple(runtime["rows"])
        label, detail = next(
            (item for item in rows if item[0] == "Selected provider"),
            rows[0],
        )
        row([("class:response-bullet-head", f"{label} · "),
             ("class:mo-response", detail)])
    row([("class:dim", "More detail · "),
         ("class:palette-command", "/status"),
         ("class:dim", " · "),
         ("class:palette-command", "/doctor"),
         ("class:dim", " · "),
         ("class:palette-command", "/everywhere status")])
    return output


def launcher_fragments(controller: Any, *, width: int) -> list[tuple[str, str]]:
    width = max(1, int(width))
    rows: list[tuple[str, str]] = [
        ("class:workspace-launcher-title", " New terminal"),
    ]
    for index, destination in enumerate(controller.launcher_destinations):
        style = (
            "class:workspace-launcher-selected"
            if index == controller.launcher_index
            else "class:workspace-launcher"
        )
        rows.append((style, f" {'›' if index == controller.launcher_index else ' '} {destination.label}"))
    output: list[tuple[str, str]] = []
    for index, (style, text) in enumerate(rows):
        if index:
            output.append(("", "\n"))
        output.append((style, fit_cells(text, width)))
    return output


_RAIL_HEADING = "class:workspace-title bold"
_RAIL_SELECTED = "class:workspace-launcher-selected"
_RUNNING_KINDS = frozenset({"pane", "running", "window"})


def _clip(text: str, width: int) -> str:
    return fit_cells(text, max(0, int(width))).rstrip() if width > 0 else ""


def _rail_line(prefix: str, left: str, right: str, width: int, *, left_style: str = "",
               right_style: str = "class:dim") -> list[tuple[str, str]]:
    """One rail row: ``left`` after ``prefix`` and ``right`` flush with the rail's right edge."""
    right = _clip(right, max(0, width - cell_width(prefix) - 2))
    left = _clip(left, max(1, width - cell_width(prefix) - cell_width(right) - (1 if right else 0)))
    pad = max(0, width - cell_width(prefix) - cell_width(left) - cell_width(right))
    return [("", prefix), (left_style, left), ("", " " * pad), (right_style, right)]


def _rail_rows(controller: Any, width: int) -> tuple[list[list[tuple[str, str]]], int]:
    """The rail's lines (RUNNING terminals, then PROJECTS) and the cursor's line."""
    projects = {project.key: project for project in controller.projects}
    pane_tiles = {tile.pane_id: tile for tile in controller.tiles(all_projects=True, include_output=False)}
    running = {terminal.instance_id: terminal for terminal in controller.running_terminals}
    windows = {str(row.get("instance_id")): row for row in getattr(controller, "local_terminals", ()) or ()}
    turn_of = getattr(controller, "terminal_turn", None)
    entries = controller.rail_entries
    running_count = sum(1 for kind, _identity in entries if kind in _RUNNING_KINDS)
    pane_counts: dict[str, int] = {}
    for tile in pane_tiles.values():
        pane_counts[tile.project_name] = pane_counts.get(tile.project_name, 0) + 1
    lines: list[list[tuple[str, str]]] = []
    cursor = 0
    heading_running = heading_projects = False
    for index, (kind, identity) in enumerate(entries):
        selected = controller.rail_index == index
        prefix = " \u203a " if selected else "   "
        detail = ""
        if kind in _RUNNING_KINDS and not heading_running:
            heading_running = True
            lines += [[], [("", " "), (_RAIL_HEADING, "RUNNING"), ("class:dim", f"  {running_count}")]]
        if kind == "project" and not heading_projects:
            heading_projects = True
            error = str(controller.running_terminals_error or "")
            if error:
                lines.append([("class:workspace-error", _clip("   " + error, width))])
            lines += [[], [("", " "), (_RAIL_HEADING, "PROJECTS")]]
        if kind == "pane":
            tile = pane_tiles[identity]
            marker, state = workspace_terminal_state(tile.state)
            doing = turn_of(tile.worker_id) if callable(turn_of) else ""
            where = "server" if tile.destination.value == "host" else ""
            row = _rail_line(prefix, f"{marker} {workspace_terminal_name(tile)}", state, width,
                             right_style=_STATUS_STYLE.get(state, "class:dim"))
            detail = " \u00b7 ".join(part for part in (tile.project_name, doing or where) if part)
        elif kind == "running":
            terminal = running[identity]
            row = _rail_line(prefix, f"\u25cc {_discovered_terminal_name(terminal)}", "attach", width)
            path = str(getattr(terminal, "project_path", "") or "")
            detail = " \u00b7 ".join(part for part in (path.rstrip("/\\").rsplit("/", 1)[-1] if path else "", "server") if part)
        elif kind == "window":
            terminal = windows[identity]
            turn = terminal.get("turn") if isinstance(terminal.get("turn"), dict) else {}
            busy = bool(turn.get("busy"))
            glyph = "\u25cf" if busy else "\u25cb"
            row = _rail_line(prefix, f"{glyph} MO \u00b7 {identity[:8]}",
                             "working" if busy else "idle", width,
                             right_style=_STATUS_STYLE["working" if busy else "idle"])
            folder = str(terminal.get("cwd") or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            detail = " \u00b7 ".join(part for part in (folder, str(turn.get("request") or "") or "another window") if part)
        elif kind == "project":
            project = projects[identity]
            source = "no folder" if not project.path else (
                "server" if project.destination.value == "host" else "local")
            count = pane_counts.get(project.name, 0) if project.key == controller.selected_project.key else 0
            row = _rail_line("\u203a" if selected else " ", project.name,
                             source + (f" \u00b7 {count}" if count else ""), width)
            if project.key == controller.selected_project.key and not selected:
                row = [("class:workspace-title-1", text) for _style, text in row]
        elif kind == "new":
            row = [("", prefix if selected else " "), ("class:workspace-launcher-title", "+ New terminal")]
        else:
            row = [("", prefix), ("", "Health")]
        if selected:
            cursor = len(lines)
            used = sum(cell_width(text) for _style, text in row)
            row = [(f"{style} {_RAIL_SELECTED}".strip(), text) for style, text in row]
            row.append((_RAIL_SELECTED, " " * max(0, width - used)))
        lines.append(row)
        if detail:
            lines.append([("", "     "), ("class:dim", _clip(detail, width - 5))])
    return lines, cursor


def workspace_rail_fragments(
    controller: Any,
    tiles: list[WorkspaceTile],
    *,
    width: int,
) -> list[tuple[str, str]]:
    """Render the rail's list: + New terminal, Health, every RUNNING MO terminal (this window's
    panes, the server's, other windows on this machine), then PROJECTS. Key help is the footer."""
    # Host discovery mutates project/terminal rows off the UI thread. Read
    # one coherent view under its existing owner lock; no PTY output is read.
    with controller._lock:
        if controller.launcher_open:
            return launcher_fragments(controller, width=width)
        lines, _cursor = _rail_rows(controller, width)
    output: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        if index:
            output.append(("", "\n"))
        output.extend(line or [("", "")])
    return output


def workspace_rail_cursor_line(controller: Any, *, width: int) -> int:
    with controller._lock:
        if controller.launcher_open:
            return controller.launcher_index + 1
        return _rail_rows(controller, width)[1]


_RAIL_HELP = {
    "pane": ("Enter switch \u00b7 x close \u00b7 Esc", "Enter \u00b7 x close"),
    "main": ("Enter switch \u00b7 1-9 \u00b7 Esc", "Enter \u00b7 1-9 \u00b7 Esc"),
    "running": ("Enter attach \u00b7 Esc", "Enter attach"),
    "window": ("Enter show \u00b7 Esc", "Enter show"),
    "project": ("Enter select \u00b7 Esc", "Enter select"),
    "new": ("Enter choose \u00b7 Esc", "Enter choose"),
    "health": ("Enter open \u00b7 Esc", "Enter open"),
    "health_open": ("Alt+\u2191\u2193 scroll \u00b7 Esc", "Alt+\u2191\u2193 scroll"),
    "launcher": ("\u2191\u2193 choose \u00b7 Enter open \u00b7 Esc back", "Enter open \u00b7 Esc back"),
}


def workspace_rail_footer(controller: Any, *, width: int) -> list[tuple[str, str]]:
    """The rail's one pinned key-help row for the current selection."""
    with controller._lock:
        if controller.launcher_open:
            key = "launcher"
        else:
            kind, identity = controller.rail_entries[controller.rail_index]
            key = "main" if (kind, identity) == ("pane", "main") else kind
            if kind == "health" and bool(getattr(controller, "health_open", False)):
                key = "health_open"
    full, short = _RAIL_HELP.get(key, ("Enter \u00b7 Esc", "Enter"))
    text = full if cell_width(full) <= width - 1 else short
    return [("class:dim", fit_cells(" " + text, width))]


def workspace_available_rows(tui: Any) -> int:
    """Fill the terminal after reserving only window-wide workspace overlays."""
    terminal_rows = max(2, int(tui._terminal_rows() or 24))
    available = max(3, terminal_rows)
    palette = getattr(tui, "_palette", None)
    if palette is None or not getattr(palette, "open", False):
        return available
    try:
        fragments = palette.get_fragments(columns=max(1, int(tui._terminal_columns() or 80)))
        palette_rows = 1 + sum(str(text).count("\n") for _style, text in fragments) if fragments else 0
    except Exception:
        palette_rows = 12
    return max(3, available - max(1, min(12, palette_rows)))


def build_workspace_grid(tui: Any, composer: Any = None, composer_rows: Any = 1):
    """Render canonical pane order and the optional vertical terminal rail."""
    controller = getattr(tui, "_workspace", None)
    if controller is None:
        return Window(content=FormattedTextControl(text=""))
    tiles = controller.tiles()
    grid_tiles = (
        [tile for tile in tiles if tile.focused]
        if bool(getattr(controller, "pane_full_window", False))
        else tiles
    )
    columns = max(1, int(tui._terminal_columns() or 80))
    rows = workspace_available_rows(tui)
    rail_open = bool(getattr(controller, "rail_open", False))
    rail_width = min(32, max(20, columns // 3), max(1, columns - 2)) if rail_open else 0
    grid_columns = max(1, columns - rail_width - (1 if rail_open else 0))
    health_open = bool(getattr(controller, "health_open", False))
    if health_open:
        def render_health() -> FormattedText:
            try:
                snapshot = controller.health_snapshot()
            except Exception:
                snapshot = None
            fragments = health_panel_fragments(
                controller.tiles(all_projects=True, include_output=False),
                snapshot,
                width=grid_columns,
                running_error=str(
                    getattr(controller, "running_terminals_error", "") or ""
                ),
                health_hosts=tuple(getattr(controller, "health_hosts", ()) or ()),
                local_machine_key=str(getattr(controller, "local_machine_key", "") or ""),
                runtime=getattr(controller, "health_runtime", None),
            )
            lines: list[list[tuple[str, str]]] = [[]]
            for style, text in fragments:
                if text == "\n":
                    lines.append([])
                else:
                    lines[-1].append((style, text))
            height = max(1, rows - 1)
            set_limit = getattr(controller, "set_pane_scroll_limit", None)
            offset = 0
            if callable(set_limit):
                set_limit("health", max(0, len(lines) - height))
                offset = controller.pane_scroll_offset("health")
            visible: list[tuple[str, str]] = []
            for line in lines[offset:offset + height]:
                if visible:
                    visible.append(("", "\n"))
                visible.extend(line or [("", "")])
            if rows > 1:
                visible.extend([("", "\n"), ("class:dim", fit_cells(" Alt+↑/↓ scroll · Alt+Home/End", grid_columns))])
            return FormattedText(visible)

        grid = Window(
            content=FormattedTextControl(render_health),
            width=Dimension.exact(grid_columns),
            height=Dimension.exact(rows),
            wrap_lines=False,
            always_hide_cursor=True,
            style="class:workspace-body",
        )
    elif not grid_tiles:
        project = controller.selected_project
        empty_message = (
            "No terminals in this project.\n  Ctrl+B · New terminal" if project.path else
            "This project's folder is not recorded.\n  Ask MO to record its folder before opening a terminal."
        )
        grid = Window(
            content=FormattedTextControl(
                text=f"\n  {project.name}\n\n  {empty_message}"
            ),
            width=Dimension.exact(grid_columns),
            height=Dimension.exact(rows),
            style="class:workspace-body",
            wrap_lines=True,
            always_hide_cursor=True,
        )
    else:
        raw_terminal_active = getattr(controller, "raw_terminal_active", None)
        focused_terminal = bool(raw_terminal_active()) if callable(raw_terminal_active) else False
        grid = _responsive_grid(
            tui,
            controller,
            grid_tiles,
            columns=grid_columns,
            rows=rows,
            composer=None if focused_terminal else composer,
            composer_rows=composer_rows,
        )
    if not rail_open:
        return grid
    list_rows = max(1, rows - 1)
    rail_list = Window(
        content=FormattedTextControl(
            lambda: FormattedText(
                workspace_rail_fragments(controller, tiles, width=rail_width)
            ),
            # The window scrolls to keep the cursor's line visible in a long list.
            get_cursor_position=lambda: Point(0, workspace_rail_cursor_line(controller, width=rail_width)),
        ),
        width=Dimension.exact(rail_width),
        height=Dimension.exact(list_rows),
        dont_extend_width=True,
        wrap_lines=False,
        always_hide_cursor=True,
        style="class:workspace-launcher",
    )
    rail = HSplit([
        rail_list,
        Window(
            content=FormattedTextControl(lambda: FormattedText(workspace_rail_footer(controller, width=rail_width))),
            width=Dimension.exact(rail_width),
            height=Dimension.exact(1),
            dont_extend_width=True,
            always_hide_cursor=True,
            style="class:workspace-launcher",
        ),
    ], width=Dimension.exact(rail_width)) if rows > 1 else rail_list
    return VSplit([
        rail,
        Window(width=1, char="│", style="class:workspace-border"),
        grid,
    ])


def workspace_grid_container(tui: Any, composer: Any = None, composer_rows: Any = 1):
    cached_key = None
    cached_grid = None

    def get_grid():
        nonlocal cached_key, cached_grid
        app = getattr(tui, "_app", None)
        controller = getattr(tui, "_workspace", None)
        if app is None or not hasattr(app, "render_counter"):
            return build_workspace_grid(tui, composer, composer_rows)
        raw_terminal_active = getattr(controller, "raw_terminal_active", None)
        # PTK visits dynamic children repeatedly for sizing, rendering, focus,
        # and key bindings. All those visits must share one frame's layout.
        # Geometry/focus also invalidate it between frames so input routing can
        # immediately find the composer after a pane or viewport change.
        key = (
            app.render_counter,
            getattr(controller, "focused_pane_id", None),
            getattr(controller, "count", 0),
            getattr(controller, "rail_open", False),
            getattr(controller, "health_open", False),
            getattr(controller, "pane_full_window", False),
            bool(raw_terminal_active()) if callable(raw_terminal_active) else False,
            tui._terminal_columns(),
            workspace_available_rows(tui),
            composer_rows() if callable(composer_rows) else composer_rows,
        )
        if cached_grid is None or key != cached_key:
            cached_grid = build_workspace_grid(tui, composer, composer_rows)
            cached_key = key
        return cached_grid

    return DynamicContainer(get_grid)
