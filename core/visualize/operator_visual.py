"""Operator-facing visual paint channel.

This channel displays images, dashboards and charts to the operator. A tool
renders an ANSI string, stores it behind ``OPERATOR_VISUAL_MARKER``, and the
turn loop (:func:`resolve`) paints it while keeping the large ANSI payload out
of model context. Model image input uses the separate perception channel.

One channel for provider visuals (``show_viz``, ``show_image``,
``generate_image``, and ``edit_image``). The surface owns display: a provided
``on_operator_visual`` callback (the TUI appends
it INSIDE the transcript via ``_add_ansi_block``) or, with no callback (print
mode / headless), stdout.
"""
from __future__ import annotations

import threading
import uuid

OPERATOR_VISUAL_MARKER = "__MO_TERMINAL_VISUAL__"
# Sibling of the ANSI marker, but carrying an original image FILE path (not a
# process-local ANSI reference) so each surface renders it natively — terminal inline,
# MO Desktop in its cube-attached panel.
OPERATOR_IMAGE_MARKER = "__MO_OPERATOR_IMAGE__"

_PENDING_VISUALS: dict[str, str] = {}
_VISUAL_LOCK = threading.Lock()


def _split_marker_result(result: str, marker: str) -> tuple[str, str] | None:
    """Accept only one exact, final marker line emitted by this channel."""
    if not isinstance(result, str):
        return None
    lines = result.splitlines()
    prefix = f"{marker}:"
    if not lines or not lines[-1].startswith(prefix):
        return None
    if any(marker in line for line in lines[:-1]):
        return None
    path = lines[-1][len(prefix):].strip()
    if not path:
        return None
    return "\n".join(lines[:-1]).strip(), path


def emit_operator_image(path: str, *, label: str = "image") -> str:
    """Return a result line asking the active surface to SHOW an image FILE.

    Unlike emit_ansi (which stores an ANSI render), this carries the original
    image path so each surface displays it its own way. The model receives a
    saved-artifact confirmation; the marker and path are stripped before it sees
    the result. Display is best-effort and owned by the active surface.
    """
    p = str(path or "").strip()
    if not p:
        return f"[{label}: nothing to show]"
    return f"[{label} saved for the operator]\n{OPERATOR_IMAGE_MARKER}:{p}"


def emit_operator_ephemeral_image(path: str, *, label: str = "image") -> str:
    """Show a short-lived image without describing it as a saved artifact."""
    p = str(path or "").strip()
    if not p:
        return f"[{label}: nothing to show]"
    return f"[{label} shown to the operator]\n{OPERATOR_IMAGE_MARKER}:{p}"


def resolve_image(result: str, on_operator_image=None) -> str:
    """Resolve one exact final image marker emitted by a trusted image tool.

    Unlike ``resolve``, the file is a saved artifact (never a temp) so it is not
    deleted here. With no callback the marker is simply stripped — a surface that
    cannot show an image degrades to the clean confirmation text.
    """
    marker = _split_marker_result(result, OPERATOR_IMAGE_MARKER)
    if marker is None:
        return result
    head, path = marker
    clean = head or "[image saved for the operator]"
    if on_operator_image is not None:
        try:
            on_operator_image(path)
        except Exception:
            pass
    return clean


def emit_ansi(ansi: str, *, label: str = "visual") -> str:
    """Stash a rendered ANSI visual and return the model-facing result line.

    The ANSI stays in this process until its single-use reference is consumed;
    the model gets only ``label`` and no temporary file is needed.
    """
    if not isinstance(ansi, str) or not ansi.strip():
        return f"[{label}: nothing to show]"
    reference = uuid.uuid4().hex
    with _VISUAL_LOCK:
        _PENDING_VISUALS[reference] = ansi
    return f"[{label} shown in the terminal]\n{OPERATOR_VISUAL_MARKER}:{reference}"


def resolve(result: str, on_operator_visual=None) -> str:
    """Consume and paint one process-local ANSI reference."""
    marker = _split_marker_result(result, OPERATOR_VISUAL_MARKER)
    if marker is None:
        return result
    head, reference = marker
    clean = head or "[visual shown in the terminal]"
    with _VISUAL_LOCK:
        ansi = _PENDING_VISUALS.pop(reference, None)
    if ansi is None:
        return clean
    if on_operator_visual is not None:
        try:
            on_operator_visual(ansi)
        except Exception:
            pass
    else:
        try:
            print("\n" + ansi + "\n")
        except Exception:
            pass
    return clean


def resolve_tool_result(
    tool_name: str,
    result: str,
    *,
    on_operator_visual=None,
    on_operator_image=None,
) -> str:
    """Resolve markers only for the exact tools that can emit each channel."""
    name = str(tool_name or "").strip()
    if name == "show_viz":
        return resolve(result, on_operator_visual)
    if name in {"show_image", "generate_image", "edit_image"}:
        return resolve_image(result, on_operator_image)
    return result
