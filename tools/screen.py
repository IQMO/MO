"""Native screen perception for MO computer-use.

Step 1 of the computer-use capability: ``capture_screen`` lets MO see what is
currently on the operator's display. The screenshot is handed to MO's
vision-capable provider through the tool-result image channel (see
``Session.add_tool_result`` + the provider image-content support), not as
unusable text.

Cross-platform via Pillow's ``ImageGrab`` (already a MO dependency) — no new
package. Wide screens are downscaled so vision token cost stays bounded.
"""
from __future__ import annotations

import base64
import os
import tempfile
from typing import Any

# Sentinel the agent loop scans for to lift the saved screenshot into the
# model's vision context as an image part instead of leaving a file path as text.
SCREEN_IMAGE_MARKER = "__MO_SCREEN_IMAGE__"

# Downscale very wide screens; ~1280px keeps text legible to the model while
# holding the per-frame image token cost down. On-demand capture only.
MAX_WIDTH = 1280


def capture_screen_to_file(max_width: int = MAX_WIDTH) -> tuple[str, int, int]:
    """Grab the primary screen, downscale, write a PNG to a temp file.

    Returns ``(path, width, height)``. Raises on capture failure (no display,
    permission) so the caller can report honestly.
    """
    from PIL import ImageGrab

    img = ImageGrab.grab().convert("RGB")
    width, height = img.size
    if max_width and width > max_width:
        ratio = max_width / float(width)
        img = img.resize((max_width, max(1, int(height * ratio))))
        width, height = img.size
    fd, path = tempfile.mkstemp(prefix="mo_screen_", suffix=".png")
    os.close(fd)
    img.save(path, format="PNG")
    return path, width, height


def load_image_data_uri(path: str) -> str | None:
    """Read a saved screenshot back as a base64 PNG data URI (or None)."""
    try:
        with open(path, "rb") as handle:
            return "data:image/png;base64," + base64.b64encode(handle.read()).decode()
    except Exception:
        return None


def execute_capture_screen(arguments: dict[str, Any]) -> str:
    """Tool executor: capture the screen and return a short confirmation plus a
    marker the agent loop resolves into the model's vision context."""
    try:
        path, width, height = capture_screen_to_file()
    except Exception as exc:  # noqa: BLE001
        return f"Error: screen capture failed: {type(exc).__name__}: {exc}"
    return f"[screen captured {width}x{height}]\n{SCREEN_IMAGE_MARKER}:{path}"
