"""Lightweight cube-branded dashboard card for the resident MO Desktop panel.

The renderer is pure PIL and consumes only compact, already-synthesized data.
It owns no polling, persistence, runtime state, or actions.  Four stable views
(Home, Work, You, Systems) share a content-sized card with stable tab geometry and return named
hit regions to the resident panel owner.
"""
from __future__ import annotations

from typing import Any

from interface.desktop_ui import DesktopVisualState

CONTENT_W = 300
VIEWS = ("overview", "work", "personal", "systems")


from interface.desktop_brand import draw_four_cube_mark
from mo_desktop.card import fit_text as _fit


def render_dashboard_card(
    *,
    data: dict,
    palette: dict,
    fonts: tuple,
    ss: int,
    visuals: DesktopVisualState,
    shadow_pad: int = 28,
    shadow_alpha: int = 150,
    shadow_blur: int = 8,
) -> tuple:
    """Render one compact dashboard view and its declarative hit regions.

    ``data`` accepts a selected ``view`` and canonical ``views`` payloads. Each
    view may supply at most four inline metrics, three detail rows, and four
    delegated owner actions; absent metrics reserve no boxes.
    """
    from PIL import ImageDraw

    from mo_desktop import card

    font, bfont, sfont, _ifont = fonts
    if not isinstance(visuals, DesktopVisualState):
        raise TypeError("dashboard renderer requires DesktopVisualState")
    panel_padding = int(visuals.metrics.panel_padding)
    panel_radius = int(visuals.metrics.panel_corner_radius)
    button_radius = int(visuals.metrics.button_corner_radius)
    C = palette
    view = str(data.get("view") or "overview").strip().lower()
    if view not in VIEWS:
        view = "overview"
    payload = _view_payload(data, view)
    tiles = payload["tiles"]
    rows = payload["rows"]
    owner_actions = [
        dict(action) for action in list(payload.get("actions") or [])[:4]
        if isinstance(action, dict)
    ]
    comms = list(data.get("comms") or [])[:3]
    apps = list(data.get("apps") or [])[:4] if view == "overview" else []
    systemcare = data.get("systemcare") if isinstance(data.get("systemcare"), dict) else {}

    def mix(a: Any, b: Any, amount: float) -> tuple[int, int, int]:
        return tuple(int(a[index] + (b[index] - a[index]) * amount) for index in range(3))

    tile_c = mix(C["card"], C["text"], 0.07)
    active_c = mix(C["card"], C["cyan"], 0.14)
    divider = (*C["edge"], 255)

    card_w = CONTENT_W + 2 * panel_padding
    # All tabs share the largest actual content height, not a padded metric grid.
    metric_height = len(tiles) * 22
    row_height = 40
    comm_height = 28 if view == "overview" and comms else 0
    action_columns = 2 if len(owner_actions) > 2 else max(1, len(owner_actions))
    app_height = 25 * len(apps) + (20 if apps else 0)
    card_h = max(_content_height(data, tab) for tab in VIEWS)
    W = (card_w + 2 * shadow_pad) * ss
    H = (card_h + 2 * shadow_pad) * ss
    pad = shadow_pad * ss
    box = [pad, pad, W - pad, H - pad]

    image = card.new_canvas(W, H)
    image = card.draw_card(
        image,
        tuple(box),
        radius=panel_radius * ss,
        fill=(*C["card"], 255),
        edge=(*C["edge"], 255),
        edge_highlight=(*C["cyan"], 110),
        edge_width=max(1, ss),
        shadow_alpha=shadow_alpha,
        shadow_blur=shadow_blur * ss,
        shadow_dy=6 * ss,
    )
    draw = ImageDraw.Draw(image)
    hits: dict[str, tuple[int, int, int, int]] = {}
    left = pad + panel_padding * ss
    right = box[2] - panel_padding * ss

    def hit_box(x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
        return (int(x0 / ss), int(y0 / ss), int(x1 / ss), int(y1 / ss))

    # Header: the established four-cube MO mark, drawn by its one owner.
    header_y = pad + 10 * ss
    draw_four_cube_mark(
        draw,
        left,
        header_y,
        cube_size=8 * ss,
        gap=3 * ss,
        radius=2 * ss,
        fill=(*C["cyan"], 255),
        shade=(*mix(C["cyan"], C["ink"], 0.28), 255),
        line_width=max(1, ss),
    )
    draw.text((left + 28 * ss, header_y + 1 * ss), "MO", font=bfont, fill=(*C["text"], 255))
    status = str(payload.get("status") or data.get("status") or "Dashboard")
    status_tone = str(payload.get("tone") or "neutral")
    status_color = C["ok"] if status_tone == "good" else (C["amber"] if status_tone == "attention" else C["muted"])
    status_text = _fit(draw, status, 158 * ss, sfont)
    status_width = int(draw.textlength(status_text, font=sfont))
    draw.ellipse([right - status_width - 11 * ss, header_y + 8 * ss, right - status_width - 5 * ss, header_y + 14 * ss], fill=(*status_color, 255))
    draw.text((right - status_width, header_y + 4 * ss), status_text, font=sfont, fill=(*C["muted"], 255))

    # Compact local switch: no new window and no new refresh loop.
    tabs_y = pad + 39 * ss
    tab_gap = 4 * ss
    tab_w = int((right - left - 3 * tab_gap) / 4)
    tab_labels = {"overview": "Home", "work": "Work", "personal": "You", "systems": "Systems"}
    for index, item in enumerate(VIEWS):
        x0 = left + index * (tab_w + tab_gap)
        selected = item == view
        draw.rounded_rectangle(
            [x0, tabs_y, x0 + tab_w, tabs_y + 25 * ss],
            radius=button_radius * ss,
            fill=(*(active_c if selected else tile_c), 255),
            outline=(*(C["cyan"] if selected else C["edge"]), 255),
            width=max(1, ss),
        )
        label = tab_labels[item]
        label_w = int(draw.textlength(label, font=sfont))
        draw.text((x0 + (tab_w - label_w) // 2, tabs_y + 6 * ss), label, font=sfont, fill=(*(C["cyan"] if selected else C["muted"]), 255))
        hits[f"dash:view:{item}"] = hit_box(x0, tabs_y, x0 + tab_w, tabs_y + 25 * ss)

    # Quiet inline metrics, not a grid of empty counters.
    metric_y = tabs_y + 34 * ss
    for index, tile in enumerate(tiles):
        y0 = metric_y + index * 22 * ss
        tone = str(tile.get("tone") or ("attention" if tile.get("warn") else "neutral"))
        tone_color = C["ok"] if tone == "good" else (C["amber"] if tone == "attention" else C["cyan"])
        label = _fit(draw, str(tile.get("label") or ""), 200 * ss, sfont)
        value = _fit(draw, str(tile.get("value") or "—"), 82 * ss, bfont)
        draw.text((left, y0 + 3 * ss), label, font=sfont, fill=(*C["muted"], 255))
        draw.text((right - draw.textlength(value, font=bfont), y0), value, font=bfont, fill=(*tone_color, 255))

    rows_y = metric_y + metric_height * ss + 5 * ss
    for index, row in enumerate(rows):
        y0 = rows_y + index * row_height * ss
        if index:
            draw.line([left, y0 - 2 * ss, right, y0 - 2 * ss], fill=divider, width=max(1, ss))
        label = _fit(draw, str(row.get("label") or ""), CONTENT_W * ss, sfont)
        value_x = left
        systemcare_controls = bool(
            view == "systems"
            and str(row.get("label") or "").strip().casefold() == "systemcare"
            and systemcare.get("available")
        )
        value_right = right - (
            88 * ss if systemcare_controls else 0
        )
        value = _fit(draw, str(row.get("value") or ""), max(20 * ss, value_right - value_x), sfont)
        draw.text((left, y0 + 2 * ss), label, font=sfont, fill=(*C["muted"], 255))
        value_color = C["amber"] if row.get("tone") == "attention" else (C["ok"] if row.get("tone") == "good" else C["text"])
        draw.text((value_x, y0 + 18 * ss), value, font=sfont, fill=(*value_color, 255))
        if systemcare_controls:
            button_y = y0 + 16 * ss
            scan_x = right - 84 * ss
            open_x = right - 40 * ss
            scan_text = "Cancel" if systemcare.get("active") else "Scan"
            scan_hit = "dash:systemcare:cancel" if systemcare.get("active") else "dash:systemcare:scan"
            for x0, width, text_value, hit in (
                (scan_x, 40 * ss, scan_text, scan_hit),
                (open_x, 40 * ss, "Open", "dash:systemcare:open"),
            ):
                draw.rounded_rectangle(
                    [x0, button_y, x0 + width, button_y + 19 * ss],
                    radius=button_radius * ss,
                    fill=(*tile_c, 255),
                    outline=(*C["edge"], 255),
                    width=max(1, ss),
                )
                text_w = int(draw.textlength(text_value, font=sfont))
                draw.text((x0 + (width - text_w) // 2, button_y + 3 * ss), text_value, font=sfont, fill=(*C["cyan"], 255))
                hits[hit] = hit_box(x0, button_y, x0 + width, button_y + 19 * ss)

    # Delegated owner controls.  The card only emits hit regions; the Desktop
    # adapter resolves each descriptor through the existing command, Gateway-turn,
    # or surface owner. Four controls use a compact two-by-two grid rather than
    # shrinking labels into unreadable one-line buttons.
    comm_y = rows_y + len(rows) * row_height * ss
    if comm_height:
        draw.text((left, comm_y + 4 * ss), "Quick links", font=sfont, fill=(*C["muted"], 255))
        link_width = 67 * ss
        link_gap = 3 * ss
        for index, comm in enumerate(comms):
            x0 = right - (len(comms) - index) * (link_width + link_gap) + link_gap
            label = _fit(draw, str(comm.get("label") or comm.get("icon") or "?"), link_width - 8 * ss, sfont)
            draw.rounded_rectangle(
                [x0, comm_y, x0 + link_width, comm_y + 23 * ss],
                radius=button_radius * ss, fill=(*tile_c, 255),
                outline=(*C["edge"], 255), width=max(1, ss),
            )
            label_width = int(draw.textlength(label, font=sfont))
            draw.text((x0 + (link_width - label_width) // 2, comm_y + 4 * ss),
                      label, font=sfont, fill=(*C["cyan"], 255))
            hits[f"dash:comm:{index}"] = hit_box(x0, comm_y, x0 + link_width, comm_y + 23 * ss)
    app_y = comm_y + comm_height * ss
    if apps:
        label = "Desktop apps · full list in tray" if data.get("apps_total", 0) > len(apps) else "Your Desktop apps"
        draw.text((left, app_y), label, font=sfont, fill=(*C["muted"], 255))
        for index, app in enumerate(apps):
            y0 = app_y + (20 + index * 25) * ss
            draw.text((left, y0), _fit(draw, str(app["label"]), CONTENT_W * ss, font), font=font, fill=(*C["cyan"], 255))
            hits["dash:app:" + str(app["id"])] = hit_box(left, y0, right, y0 + 23 * ss)
    action_y = app_y + app_height * ss + 3 * ss
    action_gap = 5 * ss
    action_w = int((right - left - max(0, action_columns - 1) * action_gap) / action_columns)
    for index, action in enumerate(owner_actions):
        action_row, action_column = divmod(index, action_columns)
        x0 = left + action_column * (action_w + action_gap)
        y0 = action_y + action_row * (23 * ss + action_gap)
        draw.rounded_rectangle(
            [x0, y0, x0 + action_w, y0 + 23 * ss],
            radius=button_radius * ss,
            fill=(*active_c, 255),
            outline=(*C["cyan"], 255),
            width=max(1, ss),
        )
        label = _fit(draw, str(action.get("label") or "Open"), max(12 * ss, action_w - 12 * ss), sfont)
        label_w = int(draw.textlength(label, font=sfont))
        draw.text((x0 + (action_w - label_w) // 2, y0 + 5 * ss), label, font=sfont, fill=(*C["cyan"], 255))
        hits[f"dash:action:{index}"] = hit_box(x0, y0, x0 + action_w, y0 + 23 * ss)

    # Context row routes to the existing terminal owner on every view. The old
    # personal-view "Improve MO" tile pointed at the removed MO Files Profile
    # location and had no registered action, so it was a silent no-op.
    context_y = pad + (card_h - 39) * ss
    draw.line([left, context_y - 3 * ss, right, context_y - 3 * ss], fill=divider, width=max(1, ss))
    terminal = data.get("terminal") if isinstance(data.get("terminal"), dict) else {}
    terminal_state = str(terminal.get("state") or "loading")
    context_label = {
        "following": "Following terminal",
        "choose": "Choose terminal",
        "available": "Terminal available",
        "offline": "Terminal offline",
        "none": "No live terminal",
        "loading": "Checking terminals",
    }.get(terminal_state, "Terminal")
    context_detail = str(terminal.get("detail") or "")
    context_color = C["ok"] if terminal_state == "following" else (C["amber"] if terminal_state in {"choose", "offline"} else C["muted"])
    context_hit = "dash:terminal"
    draw.text((left, context_y + 7 * ss), context_label, font=font, fill=(*C["text"], 255))
    detail = _fit(draw, context_detail, 147 * ss, sfont)
    draw.text((left + 135 * ss, context_y + 9 * ss), detail, font=sfont, fill=(*context_color, 255))
    hits[context_hit] = hit_box(left, context_y, right, context_y + 31 * ss)

    return card.finish(image, ss), hits


def _view_payload(data: dict[str, Any], view: str) -> dict[str, Any]:
    views = data.get("views") if isinstance(data.get("views"), dict) else {}
    supplied = views.get(view) if isinstance(views.get(view), dict) else {}
    return {
        "status": supplied.get("status") or view.title(),
        "tone": supplied.get("tone") or "neutral",
        "tiles": [tile for tile in list(supplied.get("tiles") or [])[:4]
                  if str(tile.get("value", "")).lower() not in {"", "0", "0/0", "idle", "none"}],
        "rows": [row for row in list(supplied.get("rows") or [])[:3]
                 if row.get("label") or row.get("value")],
        "actions": [
            dict(action) for action in list(supplied.get("actions") or [])[:4]
            if isinstance(action, dict)
        ],
    }


def _content_height(data: dict[str, Any], view: str) -> int:
    payload = _view_payload(data, view)
    count = len(payload["actions"])
    action_rows = (count + 1) // 2 if count > 2 else bool(count)
    app_count = min(4, len(data.get("apps") or [])) if view == "overview" else 0
    return (133 + len(payload["tiles"]) * 22 + len(payload["rows"]) * 40
            + (28 if view == "overview" and data.get("comms") else 0)
            + (25 * app_count + 20 if app_count else 0) + action_rows * 28)
