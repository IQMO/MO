"""Keep one phone frame as an ordinary MO artifact.

MO needs to see the phone it is working on: a frame it can attach to a turn is
what lets it verify an app it just built instead of inferring from logs. That
frame goes through the existing attachment home, category routing and provenance
index, so it appears in MO Files beside everything else and needs no store of
its own.

Recording deliberately lives outside MO. Windows records a window natively and
streaming tools already capture one, so duplicating that here would add an
encoder and a container format to maintain for no gain.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from core.state.attachments import (
    record_attachment,
    unique_attachment_path,
)


def _stamp(now: float | None = None) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))


PNG_SIGNATURE = bytes([0x89]) + b"PNG"
JPEG_SIGNATURE = bytes([0xFF, 0xD8, 0xFF])
_SIGNATURES = {PNG_SIGNATURE: ".png", JPEG_SIGNATURE: ".jpg"}


def _label(label: str) -> str:
    clean = "".join(
        char if char.isalnum() or char in "-_" else "-" for char in str(label or "")
    ).strip("-")
    return (clean or "phone")[:40]


def frame_path(config: dict[str, Any] | None, label: str, suffix: str) -> Path:
    """Reserve a categorized path for one still frame."""
    return unique_attachment_path(config or {}, f"{_label(label)}-{_stamp()}{suffix}")


def register(config: dict[str, Any] | None, saved: Path) -> None:
    """Record provenance for a written artifact, best effort.

    A missing index entry must never lose the file itself.
    """
    try:
        record_attachment(config or {}, saved, origin="phone_capture")
    except Exception:
        pass


def write_frame(config: dict[str, Any] | None, label: str, frame: bytes) -> Path:
    """Write one phone screenshot as an ordinary MO attachment."""
    suffix = next(
        (value for marker, value in _SIGNATURES.items() if frame.startswith(marker)),
        "",
    )
    if not suffix:
        raise ValueError("frame is not a PNG or JPEG")
    target = frame_path(config, label, suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(frame)
    register(config, target)
    return target
