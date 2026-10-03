"""Small multi-label desktop annotation overlay.

Avoids a full-screen transparent window on Windows. Each label is its own small
topmost card near the UIA element bounds, so DWM compositing stays predictable.
"""
from __future__ import annotations

import json
import sys
from typing import Any

def show_annotations(items: list[dict[str, Any]], seconds: float = 5.0) -> None:
    from interface.desktop_label import (
        label_position, label_work_area, render_desktop_label_card, show_desktop_labels,
    )
    from mo_desktop.visuals import load_and_publish_desktop_visual_state

    visuals = load_and_publish_desktop_visual_state(None, refresh_skin=True)
    labels = []
    for item in items[:12]:
        try:
            left, top, _right, bottom = [int(v) for v in item.get("bounds", [0, 0, 0, 0])[:4]]
        except (TypeError, ValueError):
            left = top = bottom = 0
        label = f"{item.get('ref', '?')}  {item.get('label') or item.get('role') or 'element'}"
        if len(label) > 70:
            label = label[:67] + "..."
        work = label_work_area(left, top)
        image = render_desktop_label_card(label, visuals, strong=True, max_width=min(480, work[2]-work[0]))
        x, y = label_position((left, top, left, bottom), image.size, work)
        labels.append((image, x, y))
    show_desktop_labels(labels, seconds, visuals)


if __name__ == "__main__":
    raw = sys.argv[1] if len(sys.argv) > 1 else "[]"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    try:
        data = json.loads(raw)
    except Exception:
        data = []
    show_annotations(data if isinstance(data, list) else [], secs)
